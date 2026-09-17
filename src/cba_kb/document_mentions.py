"""Deterministic document-to-player mention relations.

This layer links archived documents to existing opaque player identities.  It
never changes document evidence, rights metadata, canonical facts, or identity
relations.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .consumer_package import CONSUMER_TARGETS, normalize_authorizations
from .consumer_projection import project_document
from .document_lane import ReviewRequired, rights_decision
from .evidence_ledger import canonical_bytes
from .player_identity import (
    PlayerIdentityError,
    normalize_name,
    validate_player_uid,
    validate_registry,
)


SCHEMA_VERSION = 1
MENTION_VERSION = "v1.8"
MENTION_STATUSES = frozenset({"same", "not_same", "undecided"})
MENTION_ROLES = frozenset({"subject", "mentioned"})
MENTION_METHODS = frozenset({
    "exact_name",
    "alias",
    "manual",
    "external_id",
})
MENTION_CONFIDENCE_LEVELS = frozenset({
    "HIGH",
    "MEDIUM",
    "LOW",
    "UNKNOWN",
})
_DOC_ID = re.compile(r"doc_[0-9a-f]{24}\Z")
_PROJECTION_SCOPE = {
    "release_id": "mention-rights-authority",
    "as_of": "1970-01-01T00:00:00Z",
    "provenance": "upstream-v1.7-rights-authority",
}


class DocumentMentionError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise DocumentMentionError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise DocumentMentionError(f"{label}_INVALID")
    return value.strip()


def validate_doc_id(value):
    value = _required_text(value, "DOC_ID")
    if not _DOC_ID.fullmatch(value):
        raise DocumentMentionError("DOC_ID_INVALID")
    return value


def _required_token(value, label):
    value = _required_text(value, label)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise DocumentMentionError(f"{label}_INVALID")
    return value


def _required_player_uid(value):
    try:
        return validate_player_uid(value)
    except PlayerIdentityError as exc:
        raise DocumentMentionError("PLAYER_UID_INVALID") from exc


def validate_mention(value):
    if not isinstance(value, dict):
        raise DocumentMentionError("MENTION_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "doc_id",
        "player_uid",
        "mention_status",
        "mention_role",
        "mention_method",
        "mention_confidence",
        "evidence_ref",
    }
    if set(value) != required:
        raise DocumentMentionError("MENTION_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise DocumentMentionError("MENTION_SCHEMA_VERSION_INVALID")
    status = _required_text(value["mention_status"], "MENTION_STATUS")
    if status not in MENTION_STATUSES:
        raise DocumentMentionError("MENTION_STATUS_INVALID")
    role = _required_text(value["mention_role"], "MENTION_ROLE")
    if role not in MENTION_ROLES:
        raise DocumentMentionError("MENTION_ROLE_INVALID")
    method = _required_text(value["mention_method"], "MENTION_METHOD")
    if method not in MENTION_METHODS:
        raise DocumentMentionError("MENTION_METHOD_INVALID")
    confidence = _required_token(
        value["mention_confidence"],
        "MENTION_CONFIDENCE",
    )
    if confidence not in MENTION_CONFIDENCE_LEVELS:
        raise DocumentMentionError("MENTION_CONFIDENCE_INVALID")
    if status == "same" and method in {"exact_name", "alias"}:
        raise DocumentMentionError("MENTION_AUTOMATIC_SAME_FORBIDDEN")
    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": validate_doc_id(value["doc_id"]),
        "player_uid": _required_player_uid(value["player_uid"]),
        "mention_status": status,
        "mention_role": role,
        "mention_method": method,
        "mention_confidence": confidence,
        "evidence_ref": _required_text(
            value["evidence_ref"],
            "MENTION_EVIDENCE_REF",
        ),
    }


def _mention_sort_key(value):
    return (
        value["doc_id"],
        value["player_uid"],
        value["mention_role"],
        value["mention_status"],
    )


def _document_sort_key(value):
    return value["doc_id"]


def _normalize_targets(value):
    if not isinstance(value, list):
        raise DocumentMentionError("AUTHORIZED_TARGETS_LIST_REQUIRED")
    targets = sorted({
        _required_text(item, "AUTHORIZED_TARGET")
        for item in value
    })
    if not set(targets) <= set(CONSUMER_TARGETS):
        raise DocumentMentionError("AUTHORIZED_TARGET_INVALID")
    return targets


def _normalize_document(value, *, authorized_targets=None):
    if not isinstance(value, dict):
        raise DocumentMentionError("DOCUMENT_OBJECT_REQUIRED")
    doc_id = validate_doc_id(value.get("doc_id"))
    rights_value = value.get("rights")
    parsed = {
        "capture_channel": value.get("capture_channel"),
        "rights": rights_value,
    }
    if not isinstance(rights_value, dict):
        parsed["rights"] = {
            "classification": rights_value,
            "public_export_allowed": value.get(
                "public_export_allowed",
                False,
            ),
            "evidence": value.get("rights_evidence", []),
        }
    try:
        rights = rights_decision(
            parsed,
            public_export_allowed=value.get("public_export_allowed"),
            evidence=value.get("rights_evidence"),
        )
    except ReviewRequired as exc:
        raise DocumentMentionError(exc.reason) from exc
    projection = project_document(
        {
            "doc_id": doc_id,
            "source_ref": value.get("source_ref") or f"mention-source:{doc_id}",
            "rights": rights["classification"],
            "public_export_allowed": rights["public_export_allowed"],
            "body_status": value.get("body_status", "AVAILABLE"),
        },
        _PROJECTION_SCOPE,
    )
    if authorized_targets is None:
        authorized_targets = value.get("authorized_targets", [])
    targets = _normalize_targets(authorized_targets)
    return {
        "doc_id": doc_id,
        "rights": rights,
        "rights_availability": projection["availability"],
        "authorized_targets": targets,
    }


def _authorization_state(document, target):
    if document["rights"]["classification"] == "public":
        return "AUTHORIZED"
    if document["rights_availability"] == "BLOCKED":
        return "BLOCKED"
    if target in document["authorized_targets"]:
        return "AUTHORIZED"
    return "UNAUTHORIZED"


def _mention_target_coverage(documents, mentions, target):
    documents_by_id = {
        item["doc_id"]: item
        for item in documents
    }
    document_level_reachable = sorted(
        item["doc_id"] for item in documents
    )
    confirmed_player_centric = []
    undecided_document_level = []
    not_same_document_level = []
    unauthorized = []
    blocked = []
    for mention in mentions:
        document = documents_by_id[mention["doc_id"]]
        pair = [mention["doc_id"], mention["player_uid"]]
        state = _authorization_state(document, target)
        if state == "UNAUTHORIZED":
            unauthorized.append(pair)
        elif state == "BLOCKED":
            blocked.append(pair)
        if mention["mention_status"] == "undecided":
            undecided_document_level.append(pair)
        elif mention["mention_status"] == "not_same":
            not_same_document_level.append(pair)
        elif (
            mention["mention_status"] == "same"
            and state == "AUTHORIZED"
        ):
            confirmed_player_centric.append(pair)
    return {
        "document_level_reachable": document_level_reachable,
        "confirmed_player_centric": sorted(confirmed_player_centric),
        "undecided_document_level": sorted(undecided_document_level),
        "not_same_document_level": sorted(not_same_document_level),
        "unauthorized": sorted(unauthorized),
        "blocked": sorted(blocked),
        "counts": {
            "documents_reachable": len(document_level_reachable),
            "confirmed_player_centric": len(confirmed_player_centric),
            "undecided": len(undecided_document_level),
            "not_same": len(not_same_document_level),
            "unauthorized": len(unauthorized),
            "blocked": len(blocked),
        },
    }


def _coverage(documents, mentions):
    by_target = {
        target: _mention_target_coverage(documents, mentions, target)
        for target in CONSUMER_TARGETS
    }
    status_counts = {
        status: sum(
            item["mention_status"] == status
            for item in mentions
        )
        for status in sorted(MENTION_STATUSES)
    }
    return {
        "documents_declared": len(documents),
        "documents_reachable": len(documents),
        "mentions_declared": len(mentions),
        "confirmed": status_counts["same"],
        "not_same": status_counts["not_same"],
        "undecided": status_counts["undecided"],
        "unauthorized_relation_count": sum(
            value["counts"]["unauthorized"]
            for value in by_target.values()
        ),
        "blocked_relation_count": sum(
            value["counts"]["blocked"]
            for value in by_target.values()
        ),
        "silent_drop_count": (
            len(mentions)
            - sum(status_counts.values())
        ),
        "by_target": by_target,
    }


def _document_index(value):
    if not isinstance(value, list):
        raise DocumentMentionError("DOCUMENT_COLLECTION_REQUIRED")
    documents = [_normalize_document(item) for item in value]
    documents.sort(key=_document_sort_key)
    doc_ids = [item["doc_id"] for item in documents]
    if len(doc_ids) != len(set(doc_ids)):
        raise DocumentMentionError("DOCUMENT_ID_DUPLICATE")
    return documents, {item["doc_id"]: item for item in documents}


def _normalized_mentions(value):
    if not isinstance(value, list):
        raise DocumentMentionError("MENTION_COLLECTION_REQUIRED")
    mentions = [validate_mention(item) for item in value]
    mentions.sort(key=_mention_sort_key)
    keys = [
        (item["doc_id"], item["player_uid"])
        for item in mentions
    ]
    if len(keys) != len(set(keys)):
        raise DocumentMentionError("DOCUMENT_PLAYER_MENTION_DUPLICATE")
    return mentions


def build_mention_artifact(
    documents,
    mentions,
    *,
    authorizations=None,
):
    if not isinstance(documents, list):
        raise DocumentMentionError("DOCUMENT_COLLECTION_REQUIRED")
    normalized_authorizations = normalize_authorizations(authorizations)
    authorized_by_doc = {}
    for _, doc_id in normalized_authorizations:
        authorized_by_doc.setdefault(doc_id, set())
    for (target, doc_id) in normalized_authorizations:
        authorized_by_doc[doc_id].add(target)

    normalized_documents = []
    for item in documents:
        if not isinstance(item, dict):
            raise DocumentMentionError("DOCUMENT_OBJECT_REQUIRED")
        normalized_documents.append(_normalize_document(
            item,
            authorized_targets=sorted(authorized_by_doc.get(
                item.get("doc_id"),
                set(),
            )),
        ))
    normalized_documents.sort(key=_document_sort_key)
    doc_ids = [item["doc_id"] for item in normalized_documents]
    if len(doc_ids) != len(set(doc_ids)):
        raise DocumentMentionError("DOCUMENT_ID_DUPLICATE")

    normalized_mentions = _normalized_mentions(mentions)
    known_documents = set(doc_ids)
    if any(
        item["doc_id"] not in known_documents
        for item in normalized_mentions
    ):
        raise DocumentMentionError("MENTION_DOCUMENT_UNKNOWN")
    if set(authorized_by_doc) - known_documents:
        raise DocumentMentionError("MENTION_AUTHORIZATION_OUT_OF_SCOPE")

    core = {
        "schema_version": SCHEMA_VERSION,
        "mention_version": MENTION_VERSION,
        "documents": normalized_documents,
        "mentions": normalized_mentions,
        "coverage": _coverage(normalized_documents, normalized_mentions),
    }
    return {
        **core,
        "artifact_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_mention_artifact(value):
    if not isinstance(value, dict):
        raise DocumentMentionError("MENTION_ARTIFACT_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "mention_version",
        "documents",
        "mentions",
        "coverage",
        "artifact_sha256",
    }
    if set(value) != required:
        raise DocumentMentionError("MENTION_ARTIFACT_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise DocumentMentionError("MENTION_ARTIFACT_VERSION_INVALID")
    if value["mention_version"] != MENTION_VERSION:
        raise DocumentMentionError("MENTION_ARTIFACT_VERSION_INVALID")
    documents, _ = _document_index(value["documents"])
    mentions = _normalized_mentions(value["mentions"])
    if any(
        item["doc_id"] not in {doc["doc_id"] for doc in documents}
        for item in mentions
    ):
        raise DocumentMentionError("MENTION_DOCUMENT_UNKNOWN")
    expected_coverage = _coverage(documents, mentions)
    if value["coverage"] != expected_coverage:
        raise DocumentMentionError("MENTION_COVERAGE_MISMATCH")
    core = {
        "schema_version": SCHEMA_VERSION,
        "mention_version": MENTION_VERSION,
        "documents": documents,
        "mentions": mentions,
        "coverage": expected_coverage,
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value["artifact_sha256"]:
        raise DocumentMentionError("MENTION_ARTIFACT_HASH_MISMATCH")
    return {**core, "artifact_sha256": value["artifact_sha256"]}


def serialize_mention_artifact(value):
    return canonical_bytes(validate_mention_artifact(value))


def load_mention_artifact_bytes(data):
    try:
        value = json.loads(data.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise DocumentMentionError("MENTION_ARTIFACT_JSON_INVALID") from exc
    return validate_mention_artifact(value)


def load_mention_artifact(path):
    try:
        return load_mention_artifact_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise DocumentMentionError("MENTION_ARTIFACT_READ_FAILED") from exc


def mention_summary(value):
    value = validate_mention_artifact(value)
    coverage = value["coverage"]
    return {
        "schema_version": SCHEMA_VERSION,
        "mention_version": MENTION_VERSION,
        "artifact_sha256": value["artifact_sha256"],
        "documents": coverage["documents_declared"],
        "mentions": coverage["mentions_declared"],
        "confirmed": coverage["confirmed"],
        "not_same": coverage["not_same"],
        "undecided": coverage["undecided"],
        "unauthorized_relation_count": coverage[
            "unauthorized_relation_count"
        ],
        "blocked_relation_count": coverage["blocked_relation_count"],
        "silent_drop_count": coverage["silent_drop_count"],
        "by_target": {
            target: data["counts"]
            for target, data in coverage["by_target"].items()
        },
    }


def discover_document_mentions(
    text,
    identity_registry,
    *,
    doc_id,
    roles=None,
):
    """Return undecided candidate mentions only; never assert same."""
    text = _required_text(text, "DOCUMENT_TEXT")
    doc_id = validate_doc_id(doc_id)
    registry = validate_registry(identity_registry)
    roles = roles or {}
    if not isinstance(roles, dict):
        raise DocumentMentionError("MENTION_ROLES_OBJECT_REQUIRED")
    normalized_roles = {}
    for player_uid, role in roles.items():
        player_uid = _required_player_uid(player_uid)
        role = _required_text(role, "MENTION_ROLE")
        if role not in MENTION_ROLES:
            raise DocumentMentionError("MENTION_ROLE_INVALID")
        normalized_roles[player_uid] = role

    normalized_text = unicodedata.normalize("NFKC", text)
    aliases_by_uid = {}
    for alias in registry["aliases"]:
        aliases_by_uid.setdefault(alias["player_uid"], []).append(
            normalize_name(alias["alias"]),
        )
    text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    mentions = []
    for player in registry["players"]:
        if player["status"] != "ACTIVE":
            continue
        player_uid = player["player_uid"]
        canonical_name = normalize_name(player["canonical_name"])
        alias_matches = sorted(set(
            alias
            for alias in aliases_by_uid.get(player_uid, [])
            if alias in normalized_text
        ))
        if canonical_name in normalized_text:
            method = "exact_name"
            confidence = "MEDIUM"
        elif alias_matches:
            method = "alias"
            confidence = "LOW"
        else:
            continue
        mentions.append(validate_mention({
            "schema_version": SCHEMA_VERSION,
            "doc_id": doc_id,
            "player_uid": player_uid,
            "mention_status": "undecided",
            "mention_role": normalized_roles.get(player_uid, "mentioned"),
            "mention_method": method,
            "mention_confidence": confidence,
            "evidence_ref": f"candidate:document-text-sha256:{text_hash}",
        }))
    return sorted(mentions, key=_mention_sort_key)
