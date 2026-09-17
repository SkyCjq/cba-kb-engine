"""Deterministic, facts-only player profiles built from frozen MASTER rows."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import digest
from .consumer_projection import event_coverage
from .evidence_ledger import canonical_bytes
from .master import HEADERS, inspect
from .player_identity import (
    CONFIDENCE_LEVELS,
    LINK_METHODS,
    PlayerIdentityError,
    validate_player_uid,
    validate_registry,
)


SCHEMA_VERSION = 1
PROFILE_VERSION = "v1.7"
PROFILE_V2_VERSION = "v2.0"
PLAYER_RECORD_VIEW = "PLAYER_RECORD_VIEW"
PLAYER_RECORD_VIEW_VERSION = "v1.8"
PROFILE_STATUSES = frozenset({"READY", "REVIEW_REQUIRED"})
IDENTITY_LOOKUP = "EXACT_NAME_ONLY"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PlayerProfileError(RuntimeError):
    pass


def generator_sha256():
    return digest(Path(__file__).read_bytes())


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PlayerProfileError(f"{label}_REQUIRED")
    return value.strip()


def _required_sha256(value, label):
    value = _required_text(value, label)
    if not _SHA256.fullmatch(value):
        raise PlayerProfileError(f"{label}_SHA256_INVALID")
    return value


def _validated_rows(rows):
    if not isinstance(rows, list):
        raise PlayerProfileError("MASTER_ROWS_REQUIRED")
    validated = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PlayerProfileError(f"MASTER_ROW_OBJECT_REQUIRED:{index}")
        missing = [key for key in HEADERS if key not in row]
        if missing:
            raise PlayerProfileError(f"MASTER_ROW_SCHEMA_INVALID:{index}")
        record_key = row.get("record_key")
        if not isinstance(record_key, str) or not record_key:
            raise PlayerProfileError(f"MASTER_RECORD_KEY_REQUIRED:{index}")
        if record_key in seen:
            raise PlayerProfileError("MASTER_RECORD_KEY_DUPLICATE")
        seen.add(record_key)
        validated.append({key: row[key] for key in HEADERS})
    return validated


def normalize_record_key_selector(value):
    if isinstance(value, dict):
        value = value.get("record_keys")
    if not isinstance(value, list) or not value:
        raise PlayerProfileError("EXPLICIT_RECORD_KEY_SELECTOR_REQUIRED")
    selectors = []
    for item in value:
        selector = _required_text(item, "RECORD_KEY")
        selectors.append(selector)
    if len(set(selectors)) != len(selectors):
        raise PlayerProfileError("EXPLICIT_RECORD_KEY_SELECTOR_DUPLICATE")
    return sorted(selectors)


def load_record_key_selector(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlayerProfileError("RECORD_KEY_SELECTOR_INVALID") from exc
    return normalize_record_key_selector(value)


def discover_exact_name(rows, name):
    """Return candidate rows only; this never establishes person identity."""
    name = _required_text(name, "PLAYER_NAME")
    candidates = [
        row for row in _validated_rows(rows)
        if row.get("player") == name
    ]
    candidates.sort(
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    clubs = {row.get("club_id") for row in candidates}
    if not candidates:
        status = "NOT_FOUND"
    elif len(candidates) == 1 and len(clubs) == 1:
        status = "CANDIDATE_ONLY"
    else:
        status = "REVIEW_REQUIRED"
    return {
        "schema_version": SCHEMA_VERSION,
        "lookup": IDENTITY_LOOKUP,
        "name": name,
        "status": status,
        "candidate_count": len(candidates),
        "candidate_record_keys": [
            row["record_key"] for row in candidates
        ],
        "candidates": candidates,
        "canonical_selector_allowed": False,
        "identity_assertion": "NONE",
    }


def build_profile(
    rows,
    *,
    record_keys,
    release_id,
    as_of,
    source_master_sha256,
    generator_sha=None,
    source_master_file_id=None,
):
    rows = _validated_rows(rows)
    selectors = normalize_record_key_selector(record_keys)
    release_id = _required_text(release_id, "RELEASE_ID")
    as_of = _required_text(as_of, "AS_OF")
    source_master_sha256 = _required_sha256(
        source_master_sha256, "SOURCE_MASTER_SHA256",
    )
    generator_sha = _required_sha256(
        generator_sha or generator_sha256(), "GENERATOR_SHA",
    )
    by_key = {row["record_key"]: row for row in rows}
    missing = sorted(set(selectors) - set(by_key))
    selected = sorted(
        (by_key[key] for key in selectors if key in by_key),
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    status = "READY" if not missing else "REVIEW_REQUIRED"
    core = {
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "status": status,
        "release_id": release_id,
        "as_of": as_of,
        "source_master_sha256": source_master_sha256,
        "source_master_file_id": source_master_file_id,
        "generator_sha": generator_sha,
        "selector_source": "EXPLICIT_RECORD_KEY",
        "record_keys": selectors,
        "rows": selected,
        "coverage": {
            "selector_record_keys": len(selectors),
            "included_record_keys": len(selected),
            "missing_record_keys": missing,
            "duplicate_record_keys": 0,
            "identity_registry_used": False,
        },
    }
    return {**core, "profile_sha256": digest(canonical_bytes(core))}


def build_profile_from_master(
    master,
    *,
    record_keys,
    release_id,
    as_of,
    generator_sha=None,
    source_master_file_id=None,
):
    rows, summary = inspect(master)
    return build_profile(
        rows,
        record_keys=record_keys,
        release_id=release_id,
        as_of=as_of,
        source_master_sha256=summary["sha256"],
        generator_sha=generator_sha,
        source_master_file_id=source_master_file_id,
    )


def validate_profile(value):
    if not isinstance(value, dict):
        raise PlayerProfileError("PROFILE_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "profile_version",
        "status",
        "release_id",
        "as_of",
        "source_master_sha256",
        "generator_sha",
        "selector_source",
        "record_keys",
        "rows",
        "coverage",
        "profile_sha256",
    }
    if not required <= set(value):
        raise PlayerProfileError("PROFILE_INCOMPLETE")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["profile_version"] != PROFILE_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["status"] not in PROFILE_STATUSES:
        raise PlayerProfileError("PROFILE_STATUS_INVALID")
    if value["selector_source"] != "EXPLICIT_RECORD_KEY":
        raise PlayerProfileError("PROFILE_SELECTOR_SOURCE_INVALID")
    normalize_record_key_selector(value["record_keys"])
    rows = _validated_rows(value["rows"])
    selected_keys = [row["record_key"] for row in rows]
    if len(selected_keys) != len(set(selected_keys)):
        raise PlayerProfileError("PROFILE_RECORD_KEY_DUPLICATE")
    if not set(selected_keys) <= set(value["record_keys"]):
        raise PlayerProfileError("PROFILE_RECORD_KEY_OUT_OF_SCOPE")
    expected_order = sorted(
        rows,
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    if rows != expected_order:
        raise PlayerProfileError("PROFILE_SORT_INVALID")
    if "player_uid" in value:
        raise PlayerProfileError("STABLE_PLAYER_UID_FORBIDDEN")
    digest_input = {key: value[key] for key in value if key != "profile_sha256"}
    if digest(canonical_bytes(digest_input)) != value["profile_sha256"]:
        raise PlayerProfileError("PROFILE_HASH_MISMATCH")
    return value


def _master_row_sort_key(row):
    return (
        str(row.get("season") or ""),
        str(row.get("club_id") or ""),
        str(row.get("record_key") or ""),
    )


def _link_sort_key(link):
    return (
        link["record_key"],
        link["player_uid"],
        link["link_status"],
    )


def _identity_link_map(identity_registry):
    try:
        registry = validate_registry(identity_registry)
    except PlayerIdentityError as exc:
        raise PlayerProfileError(str(exc)) from exc
    links_by_record = {}
    for link in registry["record_links"]:
        links_by_record.setdefault(link["record_key"], []).append({
            "player_uid": link["player_uid"],
            "link_status": link["link_status"],
            "method": link["method"],
            "confidence": link["confidence"],
            "evidence_refs": list(link["evidence_refs"]),
        })
    for record_key, links in links_by_record.items():
        links.sort(key=lambda item: (
            item["player_uid"],
            item["link_status"],
        ))
        if sum(item["link_status"] == "same" for item in links) > 1:
            raise PlayerProfileError("PLAYER_RECORD_MULTIPLE_SAME_LINKS")
    return registry, links_by_record


def build_player_record_view(
    rows,
    *,
    identity_registry,
):
    """Build the deterministic read-only PLAYER_RECORD_VIEW projection."""
    rows = _validated_rows(rows)
    registry, links_by_record = _identity_link_map(identity_registry)
    view_rows = []
    for row in sorted(rows, key=_master_row_sort_key):
        links = links_by_record.get(row["record_key"], [])
        same = [
            item for item in links
            if item["link_status"] == "same"
        ]
        undecided = any(
            item["link_status"] == "undecided"
            for item in links
        )
        not_same = any(
            item["link_status"] == "not_same"
            for item in links
        )
        if same:
            status = "same"
            confirmed_player_uid = same[0]["player_uid"]
        elif undecided:
            status = "undecided"
            confirmed_player_uid = None
        elif not_same:
            status = "not_same"
            confirmed_player_uid = None
        else:
            status = "UNLINKED"
            confirmed_player_uid = None
        view_rows.append({
            **{key: row[key] for key in HEADERS},
            "confirmed_player_uid": confirmed_player_uid,
            "identity_link_status": status,
            "identity_links": links,
        })
    core = {
        "schema_version": SCHEMA_VERSION,
        "view_version": PLAYER_RECORD_VIEW_VERSION,
        "view_name": PLAYER_RECORD_VIEW,
        "identity_registry_sha256": registry["registry_sha256"],
        "rows": view_rows,
    }
    return {**core, "view_sha256": digest(canonical_bytes(core))}


def _event_selection_v2(event_spec):
    if event_spec is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "selected": False,
            "season": None,
            "team": None,
            "covered": [],
            "unresolved": [],
            "critical_gap": False,
            "source_status": {},
            "as_of": None,
        }
    if not isinstance(event_spec, dict):
        raise PlayerProfileError("EVENT_SELECTION_OBJECT_REQUIRED")
    required = {"season", "team", "events", "sources", "as_of"}
    if not required <= set(event_spec):
        raise PlayerProfileError("EVENT_SELECTION_INCOMPLETE")
    return {
        **event_coverage(
            season=event_spec["season"],
            team=event_spec["team"],
            events=event_spec["events"],
            sources=event_spec["sources"],
            as_of=event_spec["as_of"],
        ),
        "selected": True,
    }


def _mention_sort_key(item):
    return (
        item["doc_id"],
        item["mention_role"],
        item["mention_status"],
        item["mention_method"],
    )


def build_profile_v2(
    rows,
    *,
    player_uid,
    identity_registry,
    mention_artifact,
    release_id,
    as_of,
    source_master_sha256,
    event_spec=None,
    generator_sha=None,
    source_master_file_id=None,
):
    from .document_mentions import (
        DocumentMentionError,
        validate_mention_artifact,
    )

    rows = _validated_rows(rows)
    try:
        player_uid = validate_player_uid(player_uid)
        registry = validate_registry(identity_registry)
        mention_artifact = validate_mention_artifact(mention_artifact)
    except (PlayerIdentityError, DocumentMentionError) as exc:
        raise PlayerProfileError(str(exc)) from exc
    release_id = _required_text(release_id, "RELEASE_ID")
    as_of = _required_text(as_of, "AS_OF")
    source_master_sha256 = _required_sha256(
        source_master_sha256,
        "SOURCE_MASTER_SHA256",
    )
    generator_sha = _required_sha256(
        generator_sha or generator_sha256(),
        "GENERATOR_SHA",
    )

    registry_players = {
        item["player_uid"]: item
        for item in registry["players"]
    }
    if player_uid not in registry_players:
        raise PlayerProfileError("PROFILE_PLAYER_UID_NOT_FOUND")
    if registry_players[player_uid]["status"] != "ACTIVE":
        raise PlayerProfileError("PROFILE_PLAYER_UID_NOT_ACTIVE")

    view = build_player_record_view(
        rows,
        identity_registry=registry,
    )
    view_by_key = {
        item["record_key"]: item
        for item in view["rows"]
    }
    registry_record_keys = {
        link["record_key"]
        for link in registry["record_links"]
    }
    if registry_record_keys - set(view_by_key):
        raise PlayerProfileError("PROFILE_IDENTITY_RECORD_NOT_IN_MASTER")
    selected_links = [
        link for link in registry["record_links"]
        if link["player_uid"] == player_uid
    ]
    selected_links.sort(key=_link_sort_key)
    same_keys = sorted(
        (
            link["record_key"]
            for link in selected_links
            if link["link_status"] == "same"
        ),
        key=lambda record_key: _master_row_sort_key(
            view_by_key[record_key],
        ),
    )
    undecided_keys = sorted(
        link["record_key"]
        for link in selected_links
        if link["link_status"] == "undecided"
    )
    not_same_keys = sorted(
        link["record_key"]
        for link in selected_links
        if link["link_status"] == "not_same"
    )
    registration_history = [
        {key: view_by_key[record_key][key] for key in HEADERS}
        for record_key in same_keys
    ]
    all_linked_record_keys = {
        link["record_key"]
        for link in registry["record_links"]
    }
    registry_not_same_record_keys = sorted({
        link["record_key"]
        for link in registry["record_links"]
        if link["link_status"] == "not_same"
    })
    unlinked_record_keys = sorted(
        row["record_key"] for row in rows
        if row["record_key"] not in all_linked_record_keys
    )
    unresolved_identity_links = [
        {
            "record_key": link["record_key"],
            "method": link["method"],
            "confidence": link["confidence"],
            "evidence_refs": list(link["evidence_refs"]),
        }
        for link in selected_links
        if link["link_status"] == "undecided"
    ]

    target_mentions = [
        item for item in mention_artifact["mentions"]
        if item["player_uid"] == player_uid
    ]
    target_mentions.sort(key=_mention_sort_key)
    confirmed_documents = [
        {
            "doc_id": item["doc_id"],
            "mention_status": item["mention_status"],
            "mention_role": item["mention_role"],
            "mention_method": item["mention_method"],
            "mention_confidence": item["mention_confidence"],
            "evidence_ref": item["evidence_ref"],
        }
        for item in target_mentions
        if item["mention_status"] == "same"
    ]
    unresolved_mentions = [
        {
            "doc_id": item["doc_id"],
            "mention_status": item["mention_status"],
            "mention_role": item["mention_role"],
            "mention_method": item["mention_method"],
            "mention_confidence": item["mention_confidence"],
            "evidence_ref": item["evidence_ref"],
        }
        for item in target_mentions
        if item["mention_status"] == "undecided"
    ]
    not_same_mentions = [
        {
            "doc_id": item["doc_id"],
            "mention_status": item["mention_status"],
            "mention_role": item["mention_role"],
            "mention_method": item["mention_method"],
            "mention_confidence": item["mention_confidence"],
            "evidence_ref": item["evidence_ref"],
        }
        for item in target_mentions
        if item["mention_status"] == "not_same"
    ]
    all_document_ids = sorted(
        item["doc_id"] for item in mention_artifact["documents"]
    )
    mentioned_document_ids = sorted({
        item["doc_id"] for item in target_mentions
    })
    document_coverage = {
        "documents_in_artifact": all_document_ids,
        "documents_with_target_mentions": mentioned_document_ids,
        "documents_without_target_mentions": sorted(
            set(all_document_ids) - set(mentioned_document_ids)
        ),
        "confirmed_document_ids": sorted({
            item["doc_id"] for item in confirmed_documents
        }),
        "unresolved_document_ids": sorted({
            item["doc_id"] for item in unresolved_mentions
        }),
        "not_same_document_ids": sorted({
            item["doc_id"] for item in not_same_mentions
        }),
        "confirmed_mention_count": len(confirmed_documents),
        "unresolved_mention_count": len(unresolved_mentions),
        "not_same_mention_count": len(not_same_mentions),
    }
    events = _event_selection_v2(event_spec)
    identity_coverage = {
        "master_record_count": len(rows),
        "registry_record_link_count": len(registry["record_links"]),
        "confirmed_record_count": len(same_keys),
        "undecided_record_count": len(undecided_keys),
        "selected_not_same_record_count": len(not_same_keys),
        "not_same_record_count": len(registry_not_same_record_keys),
        "unlinked_record_count": len(unlinked_record_keys),
        "confirmed_record_keys": same_keys,
        "undecided_record_keys": undecided_keys,
        "selected_not_same_record_keys": not_same_keys,
        "not_same_record_keys": registry_not_same_record_keys,
        "unlinked_record_keys": unlinked_record_keys,
        "full_history_coverage_complete": False,
    }
    status = (
        "REVIEW_REQUIRED"
        if undecided_keys or unresolved_mentions or not same_keys
        else "READY"
    )
    core = {
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_V2_VERSION,
        "status": status,
        "identity_selector": "player_uid",
        "player_uid": player_uid,
        "release_id": release_id,
        "as_of": as_of,
        "source_master_sha256": source_master_sha256,
        "source_master_file_id": source_master_file_id,
        "generator_sha": generator_sha,
        "identity_registry_sha256": registry["registry_sha256"],
        "mention_artifact_sha256": mention_artifact["artifact_sha256"],
        "registration_history": registration_history,
        "record_keys": same_keys,
        "unresolved_identity_links": unresolved_identity_links,
        "confirmed_documents": confirmed_documents,
        "unresolved_mentions": unresolved_mentions,
        "not_same_mentions": not_same_mentions,
        "identity_coverage": identity_coverage,
        "document_coverage": document_coverage,
        "event_coverage": events,
        "unresolved_gaps": list(events["unresolved"]),
    }
    return {**core, "profile_sha256": digest(canonical_bytes(core))}


def _profile_string_list(value, label):
    if not isinstance(value, list):
        raise PlayerProfileError(f"{label}_LIST_REQUIRED")
    if any(not isinstance(item, str) or not item for item in value):
        raise PlayerProfileError(f"{label}_STRING_REQUIRED")
    if value != sorted(value) or len(value) != len(set(value)):
        raise PlayerProfileError(f"{label}_ORDER_OR_DUPLICATE_INVALID")
    return value


def _validate_identity_coverage(coverage, record_keys):
    required = {
        "master_record_count",
        "registry_record_link_count",
        "confirmed_record_count",
        "undecided_record_count",
        "selected_not_same_record_count",
        "not_same_record_count",
        "unlinked_record_count",
        "confirmed_record_keys",
        "undecided_record_keys",
        "selected_not_same_record_keys",
        "not_same_record_keys",
        "unlinked_record_keys",
        "full_history_coverage_complete",
    }
    if not isinstance(coverage, dict) or set(coverage) != required:
        raise PlayerProfileError("PROFILE_V2_IDENTITY_COVERAGE_INVALID")
    count_fields = (
        "master_record_count",
        "registry_record_link_count",
        "confirmed_record_count",
        "undecided_record_count",
        "selected_not_same_record_count",
        "not_same_record_count",
        "unlinked_record_count",
    )
    if any(
        not isinstance(coverage[field], int) or coverage[field] < 0
        for field in count_fields
    ):
        raise PlayerProfileError("PROFILE_V2_IDENTITY_COVERAGE_INVALID")
    key_fields = (
        "confirmed_record_keys",
        "undecided_record_keys",
        "selected_not_same_record_keys",
        "not_same_record_keys",
        "unlinked_record_keys",
    )
    for field in key_fields:
        _profile_string_list(coverage[field], f"PROFILE_V2_{field.upper()}")
    expected_counts = {
        "confirmed_record_count": coverage["confirmed_record_keys"],
        "undecided_record_count": coverage["undecided_record_keys"],
        "selected_not_same_record_count": coverage[
            "selected_not_same_record_keys"
        ],
        "not_same_record_count": coverage["not_same_record_keys"],
        "unlinked_record_count": coverage["unlinked_record_keys"],
    }
    for field, values in expected_counts.items():
        if coverage[field] != len(values):
            raise PlayerProfileError(
                "PROFILE_V2_IDENTITY_COVERAGE_COUNT_MISMATCH",
            )
    if coverage["confirmed_record_keys"] != record_keys:
        raise PlayerProfileError("PROFILE_V2_IDENTITY_COVERAGE_MISMATCH")
    selected_sets = [
        set(coverage["confirmed_record_keys"]),
        set(coverage["undecided_record_keys"]),
        set(coverage["selected_not_same_record_keys"]),
    ]
    if any(
        selected_sets[left] & selected_sets[right]
        for left in range(3)
        for right in range(left + 1, 3)
    ):
        raise PlayerProfileError("PROFILE_V2_IDENTITY_COVERAGE_OVERLAP")
    if not set(coverage["selected_not_same_record_keys"]) <= set(
        coverage["not_same_record_keys"],
    ):
        raise PlayerProfileError("PROFILE_V2_IDENTITY_SELECTED_NOT_SAME_INVALID")
    if set(coverage["unlinked_record_keys"]) & (
        set(coverage["confirmed_record_keys"])
        | set(coverage["undecided_record_keys"])
        | set(coverage["not_same_record_keys"])
    ):
        raise PlayerProfileError("PROFILE_V2_IDENTITY_UNLINKED_OVERLAP")
    if coverage["full_history_coverage_complete"] is not False:
        raise PlayerProfileError("FULL_HISTORY_COVERAGE_CLAIM_FORBIDDEN")
    return coverage


def _validate_unresolved_identity_links(value, coverage):
    if not isinstance(value, list):
        raise PlayerProfileError(
            "PROFILE_V2_UNRESOLVED_IDENTITY_LINKS_REQUIRED",
        )
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {
                "record_key",
                "method",
                "confidence",
                "evidence_refs",
            }
        ):
            raise PlayerProfileError(
                "PROFILE_V2_UNRESOLVED_IDENTITY_SCHEMA_INVALID",
            )
        _required_text(item["record_key"], "RECORD_KEY")
        if item["method"] not in LINK_METHODS:
            raise PlayerProfileError(
                "PROFILE_V2_UNRESOLVED_IDENTITY_METHOD_INVALID",
            )
        if item["confidence"] not in CONFIDENCE_LEVELS:
            raise PlayerProfileError(
                "PROFILE_V2_UNRESOLVED_IDENTITY_CONFIDENCE_INVALID",
            )
        _profile_string_list(
            item["evidence_refs"],
            "PROFILE_V2_UNRESOLVED_IDENTITY_EVIDENCE_REFS",
        )
    keys = [item["record_key"] for item in value]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise PlayerProfileError(
            "PROFILE_V2_UNRESOLVED_IDENTITY_ORDER_OR_DUPLICATE_INVALID",
        )
    if keys != coverage["undecided_record_keys"]:
        raise PlayerProfileError("PROFILE_V2_UNRESOLVED_IDENTITY_MISMATCH")
    return value


def _validate_mention_bucket(value, expected_status, label):
    from .document_mentions import (
        MENTION_CONFIDENCE_LEVELS,
        MENTION_METHODS,
        MENTION_ROLES,
        validate_doc_id,
    )

    if not isinstance(value, list):
        raise PlayerProfileError(f"{label}_REQUIRED")
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {
                "doc_id",
                "mention_status",
                "mention_role",
                "mention_method",
                "mention_confidence",
                "evidence_ref",
            }
        ):
            raise PlayerProfileError(f"{label}_SCHEMA_INVALID")
        validate_doc_id(item["doc_id"])
        if item["mention_status"] != expected_status:
            raise PlayerProfileError(
                "PROFILE_V2_MENTION_STATUS_BUCKET_MISMATCH",
            )
        if item["mention_role"] not in MENTION_ROLES:
            raise PlayerProfileError(f"{label}_ROLE_INVALID")
        if item["mention_method"] not in MENTION_METHODS:
            raise PlayerProfileError(f"{label}_METHOD_INVALID")
        if item["mention_confidence"] not in MENTION_CONFIDENCE_LEVELS:
            raise PlayerProfileError(f"{label}_CONFIDENCE_INVALID")
        _required_text(item["evidence_ref"], "MENTION_EVIDENCE_REF")
    if value != sorted(value, key=_mention_sort_key):
        raise PlayerProfileError(f"{label}_ORDER_INVALID")
    doc_ids = [item["doc_id"] for item in value]
    if len(doc_ids) != len(set(doc_ids)):
        raise PlayerProfileError(f"{label}_DUPLICATE")
    return value


def _validate_document_coverage(value, buckets, artifact_sha256):
    from .document_mentions import DocumentMentionError, validate_doc_id

    required = {
        "documents_in_artifact",
        "documents_with_target_mentions",
        "documents_without_target_mentions",
        "confirmed_document_ids",
        "unresolved_document_ids",
        "not_same_document_ids",
        "confirmed_mention_count",
        "unresolved_mention_count",
        "not_same_mention_count",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PlayerProfileError("PROFILE_V2_DOCUMENT_COVERAGE_INVALID")
    list_fields = (
        "documents_in_artifact",
        "documents_with_target_mentions",
        "documents_without_target_mentions",
        "confirmed_document_ids",
        "unresolved_document_ids",
        "not_same_document_ids",
    )
    for field in list_fields:
        _profile_string_list(
            value[field],
            f"PROFILE_V2_{field.upper()}",
        )
        for doc_id in value[field]:
            try:
                validate_doc_id(doc_id)
            except DocumentMentionError as exc:
                raise PlayerProfileError(
                    "PROFILE_V2_DOCUMENT_ID_INVALID",
                ) from exc
    expected_ids = {
        "confirmed_document_ids": sorted({
            item["doc_id"] for item in buckets["confirmed_documents"]
        }),
        "unresolved_document_ids": sorted({
            item["doc_id"] for item in buckets["unresolved_mentions"]
        }),
        "not_same_document_ids": sorted({
            item["doc_id"] for item in buckets["not_same_mentions"]
        }),
    }
    for field, expected in expected_ids.items():
        if value[field] != expected:
            raise PlayerProfileError(
                "PROFILE_V2_DOCUMENT_COVERAGE_BUCKET_MISMATCH",
            )
    counts = {
        "confirmed_mention_count": len(buckets["confirmed_documents"]),
        "unresolved_mention_count": len(buckets["unresolved_mentions"]),
        "not_same_mention_count": len(buckets["not_same_mentions"]),
    }
    for field, expected in counts.items():
        if value[field] != expected:
            raise PlayerProfileError(
                "PROFILE_V2_DOCUMENT_COVERAGE_COUNT_MISMATCH",
            )
    bucket_ids = [
        set(expected_ids["confirmed_document_ids"]),
        set(expected_ids["unresolved_document_ids"]),
        set(expected_ids["not_same_document_ids"]),
    ]
    if any(
        bucket_ids[left] & bucket_ids[right]
        for left in range(3)
        for right in range(left + 1, 3)
    ):
        raise PlayerProfileError("PROFILE_V2_MENTION_BUCKET_OVERLAP")
    target_ids = set().union(*bucket_ids)
    if set(value["documents_with_target_mentions"]) != target_ids:
        raise PlayerProfileError(
            "PROFILE_V2_DOCUMENT_TARGET_COVERAGE_MISMATCH",
        )
    artifact_ids = set(value["documents_in_artifact"])
    if not target_ids <= artifact_ids:
        raise PlayerProfileError(
            "PROFILE_V2_DOCUMENT_TARGET_OUT_OF_ARTIFACT",
        )
    if set(value["documents_without_target_mentions"]) != (
        artifact_ids - target_ids
    ):
        raise PlayerProfileError(
            "PROFILE_V2_DOCUMENT_UNMENTIONED_COVERAGE_MISMATCH",
        )
    if not isinstance(artifact_sha256, str):
        raise PlayerProfileError("MENTION_ARTIFACT_SHA256_INVALID")
    return value


def validate_profile_v2(value):
    if not isinstance(value, dict):
        raise PlayerProfileError("PROFILE_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "profile_version",
        "status",
        "identity_selector",
        "player_uid",
        "release_id",
        "as_of",
        "source_master_sha256",
        "source_master_file_id",
        "generator_sha",
        "identity_registry_sha256",
        "mention_artifact_sha256",
        "registration_history",
        "record_keys",
        "unresolved_identity_links",
        "confirmed_documents",
        "unresolved_mentions",
        "not_same_mentions",
        "identity_coverage",
        "document_coverage",
        "event_coverage",
        "unresolved_gaps",
        "profile_sha256",
    }
    if set(value) != required:
        raise PlayerProfileError("PROFILE_V2_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["profile_version"] != PROFILE_V2_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["status"] not in PROFILE_STATUSES:
        raise PlayerProfileError("PROFILE_STATUS_INVALID")
    if value["identity_selector"] != "player_uid":
        raise PlayerProfileError("PROFILE_SELECTOR_SOURCE_INVALID")
    _required_text(value["release_id"], "RELEASE_ID")
    _required_text(value["as_of"], "AS_OF")
    if value["source_master_file_id"] is not None:
        _required_text(value["source_master_file_id"], "SOURCE_MASTER_FILE_ID")
    try:
        validate_player_uid(value["player_uid"])
    except PlayerIdentityError as exc:
        raise PlayerProfileError(str(exc)) from exc
    _required_sha256(value["source_master_sha256"], "SOURCE_MASTER_SHA256")
    _required_sha256(value["generator_sha"], "GENERATOR_SHA")
    _required_sha256(
        value["identity_registry_sha256"],
        "IDENTITY_REGISTRY_SHA256",
    )
    _required_sha256(
        value["mention_artifact_sha256"],
        "MENTION_ARTIFACT_SHA256",
    )
    rows = _validated_rows(value["registration_history"])
    if rows != sorted(rows, key=_master_row_sort_key):
        raise PlayerProfileError("PROFILE_V2_REGISTRATION_SORT_INVALID")
    record_keys = _profile_string_list(
        value["record_keys"],
        "PROFILE_V2_RECORD_KEYS",
    )
    if record_keys != sorted(record_keys):
        raise PlayerProfileError("PROFILE_V2_RECORD_KEY_SORT_INVALID")
    if record_keys != sorted(row["record_key"] for row in rows):
        raise PlayerProfileError("PROFILE_V2_RECORD_KEYS_INVALID")
    coverage = _validate_identity_coverage(
        value["identity_coverage"],
        record_keys,
    )
    _validate_unresolved_identity_links(
        value["unresolved_identity_links"],
        coverage,
    )
    buckets = {
        "confirmed_documents": _validate_mention_bucket(
            value["confirmed_documents"],
            "same",
            "PROFILE_V2_CONFIRMED_DOCUMENTS",
        ),
        "unresolved_mentions": _validate_mention_bucket(
            value["unresolved_mentions"],
            "undecided",
            "PROFILE_V2_UNRESOLVED_MENTIONS",
        ),
        "not_same_mentions": _validate_mention_bucket(
            value["not_same_mentions"],
            "not_same",
            "PROFILE_V2_NOT_SAME_MENTIONS",
        ),
    }
    _validate_document_coverage(
        value["document_coverage"],
        buckets,
        value["mention_artifact_sha256"],
    )
    expected_status = (
        "REVIEW_REQUIRED"
        if (
            coverage["undecided_record_keys"]
            or value["unresolved_mentions"]
            or not value["registration_history"]
        )
        else "READY"
    )
    if value["status"] != expected_status:
        raise PlayerProfileError("PROFILE_V2_STATUS_SEMANTIC_MISMATCH")
    if not isinstance(value["event_coverage"], dict):
        raise PlayerProfileError("PROFILE_V2_EVENT_COVERAGE_INVALID")
    _profile_string_list(
        value["unresolved_gaps"],
        "PROFILE_V2_UNRESOLVED_GAPS",
    )
    if value["unresolved_gaps"] != value["event_coverage"].get(
        "unresolved",
    ):
        raise PlayerProfileError("PROFILE_V2_UNRESOLVED_GAPS_INVALID")
    digest_input = {
        key: item for key, item in value.items()
        if key != "profile_sha256"
    }
    if digest(canonical_bytes(digest_input)) != value["profile_sha256"]:
        raise PlayerProfileError("PROFILE_HASH_MISMATCH")
    return value
