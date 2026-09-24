"""Canonical consumer payloads and deterministic target packages."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from .common import atomic, digest
from .consumer_projection import (
    IDENTITY_SEMANTIC_STATES,
    derive_machine_counts,
    event_coverage,
    project_document,
    project_documents,
    project_identity_state,
    validate_machine_counts,
)
from .evidence_ledger import canonical_bytes
from .player_profile import validate_profile, validate_profile_v2


SCHEMA_VERSION = 1
PAYLOAD_VERSION = "v1.7"
NAVIGATION_SCHEMA_VERSION = "v1"
PACKAGE_GENERATION = "v17-navigation-repair-1"
CONSUMER_TARGETS = ("ChatGPT", "Gemini Notebook", "WorkBuddy")
AUTHORIZATION_FIELDS = frozenset({
    "doc_id",
    "target",
    "allowed_scope",
    "authorization_basis",
    "frozen_at",
})
AUTHORIZED_EVIDENCE_REQUIRED = frozenset({
    "doc_id",
    "sha256",
    "source_ref",
})
AUTHORIZED_EVIDENCE_ALLOWED = AUTHORIZED_EVIDENCE_REQUIRED | {
    "body",
    "content",
    "target",
}
TRANSPORT_ROUTE_FIELDS = frozenset({
    "base_files",
    "canonical_projection_files",
    "authorized_evidence_files",
})
TARGET_SLUGS = {
    "ChatGPT": "chatgpt",
    "Gemini Notebook": "gemini-notebook",
    "WorkBuddy": "workbuddy",
}


class ConsumerPackageError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ConsumerPackageError(f"{label}_REQUIRED")
    return value.strip()


def _scope(value):
    if not isinstance(value, dict):
        raise ConsumerPackageError("RELEASE_SCOPE_REQUIRED")
    required = {"release_id", "as_of", "provenance"}
    if not required <= set(value):
        raise ConsumerPackageError("RELEASE_SCOPE_INCOMPLETE")
    return {
        key: _required_text(value[key], key.upper())
        for key in sorted(required)
    }


def _validated_profile(value):
    if (
        isinstance(value, dict)
        and value.get("profile_version") == "v2.0"
    ):
        return validate_profile_v2(value)
    return validate_profile(value)


def _projection_input(document):
    if not isinstance(document, dict):
        raise ConsumerPackageError("DOCUMENT_OBJECT_REQUIRED")
    value = dict(document)
    rights = value.get("rights")
    if isinstance(rights, dict):
        value["rights"] = rights.get("classification", "unknown")
    value.setdefault("body_status", "AVAILABLE")
    value.setdefault("public_export_allowed", False)
    value.setdefault("metadata_scope", [
        "title", "published_at", "canonical_url",
    ])
    return value


def _source_index(sources):
    if not isinstance(sources, list):
        raise ConsumerPackageError("SOURCE_INDEX_LIST_REQUIRED")
    indexed = []
    seen = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ConsumerPackageError("SOURCE_INDEX_OBJECT_REQUIRED")
        source_id = _required_text(source.get("source_id"), "SOURCE_ID")
        if source_id in seen:
            raise ConsumerPackageError("SOURCE_INDEX_DUPLICATE")
        seen.add(source_id)
        indexed.append({
            key: source.get(key)
            for key in (
                "source_id",
                "type",
                "title",
                "url",
                "locator",
                "sha256",
                "rights",
                "release_id",
            )
        })
    return sorted(indexed, key=lambda item: item["source_id"])


def _event_selection(event_spec):
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
        raise ConsumerPackageError("EVENT_SELECTION_OBJECT_REQUIRED")
    required = {"season", "team", "events", "sources", "as_of"}
    if not required <= set(event_spec):
        raise ConsumerPackageError("EVENT_SELECTION_INCOMPLETE")
    coverage = event_coverage(
        season=event_spec["season"],
        team=event_spec["team"],
        events=event_spec["events"],
        sources=event_spec["sources"],
        as_of=event_spec["as_of"],
    )
    return {**coverage, "selected": True}


def build_consumer_payload(
    *,
    release_scope,
    profile,
    documents,
    sources,
    event_spec=None,
):
    scope = _scope(release_scope)
    profile = _validated_profile(profile)
    if not isinstance(documents, list):
        raise ConsumerPackageError("DOCUMENT_COLLECTION_REQUIRED")
    projections = project_documents(
        [_projection_input(document) for document in documents],
        scope,
    )
    source_index = _source_index(sources)
    events = _event_selection(event_spec)
    rights_coverage = {}
    availability_coverage = {}
    for projection in projections:
        rights = projection["rights"]
        availability = projection["availability"]
        rights_coverage[rights] = rights_coverage.get(rights, 0) + 1
        availability_coverage[availability] = (
            availability_coverage.get(availability, 0) + 1
        )
    if profile["profile_version"] == "v2.0":
        profile_coverage = {
            "profile_records_declared": (
                len(profile["registration_history"])
                + len(profile["unresolved_identity_links"])
            ),
            "profile_records_included": len(
                profile["registration_history"],
            ),
            "profile_missing_record_keys": [],
            "profile_identity_selector": profile["identity_selector"],
            "profile_unresolved_identity_record_keys": list(
                profile["identity_coverage"]["undecided_record_keys"],
            ),
            "profile_confirmed_document_count": len(
                profile["confirmed_documents"],
            ),
            "profile_unresolved_mention_count": len(
                profile["unresolved_mentions"],
            ),
        }
    else:
        profile_coverage = {
            "profile_records_declared": len(profile["record_keys"]),
            "profile_records_included": len(profile["rows"]),
            "profile_missing_record_keys": sorted(
                profile["coverage"]["missing_record_keys"],
            ),
        }
    core = {
        "schema_version": SCHEMA_VERSION,
        "payload_version": PAYLOAD_VERSION,
        "release_scope": scope,
        "player_profile": profile,
        "selected_document_projections": projections,
        "event_coverage": events,
        "source_index": source_index,
        "coverage": {
            **profile_coverage,
            "documents_declared": len(projections),
            "documents_projected": len(projections),
            "document_rights": dict(sorted(rights_coverage.items())),
            "document_availability": dict(
                sorted(availability_coverage.items()),
            ),
            "events_selected": events["selected"],
            "event_critical_gap": events["critical_gap"],
            "source_index_items": len(source_index),
        },
    }
    return {
        **core,
        "consumer_payload_sha256": digest(canonical_bytes(core)),
    }


def payload_bytes(payload):
    if not isinstance(payload, dict):
        raise ConsumerPackageError("PAYLOAD_OBJECT_REQUIRED")
    core = {
        key: payload[key]
        for key in payload
        if key != "consumer_payload_sha256"
    }
    if digest(canonical_bytes(core)) != payload.get(
        "consumer_payload_sha256",
    ):
        raise ConsumerPackageError("CONSUMER_PAYLOAD_HASH_MISMATCH")
    return canonical_bytes(payload) + b"\n"


def normalize_authorizations(value):
    if value is None:
        value = []
    if isinstance(value, dict):
        value = value.get("authorizations")
    if not isinstance(value, list):
        raise ConsumerPackageError("TARGET_AUTHORIZATIONS_REQUIRED")
    normalized = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != AUTHORIZATION_FIELDS:
            raise ConsumerPackageError("TARGET_AUTHORIZATION_INVALID")
        doc_id = _required_text(item["doc_id"], "AUTHORIZATION_DOC_ID")
        target = _required_text(item["target"], "AUTHORIZATION_TARGET")
        if target not in CONSUMER_TARGETS:
            raise ConsumerPackageError("AUTHORIZATION_TARGET_INVALID")
        key = (target, doc_id)
        if key in normalized:
            raise ConsumerPackageError("TARGET_AUTHORIZATION_DUPLICATE")
        normalized[key] = {
            "doc_id": doc_id,
            "target": target,
            "allowed_scope": _required_text(
                item["allowed_scope"], "AUTHORIZATION_ALLOWED_SCOPE",
            ),
            "authorization_basis": _required_text(
                item["authorization_basis"], "AUTHORIZATION_BASIS",
            ),
            "frozen_at": _required_text(
                item["frozen_at"], "AUTHORIZATION_FROZEN_AT",
            ),
        }
    return normalized


def normalize_authorized_evidence(value):
    if value is None:
        value = []
    if isinstance(value, dict):
        if "authorized_evidence" in value:
            value = value["authorized_evidence"]
        elif "doc_id" in value:
            value = [value]
        else:
            value = list(value.values())
    if not isinstance(value, list):
        raise ConsumerPackageError("AUTHORIZED_EVIDENCE_LIST_REQUIRED")
    normalized = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or not AUTHORIZED_EVIDENCE_REQUIRED <= set(item)
            or not set(item) <= AUTHORIZED_EVIDENCE_ALLOWED
        ):
            raise ConsumerPackageError("AUTHORIZED_EVIDENCE_INVALID")
        doc_id = _required_text(item["doc_id"], "EVIDENCE_DOC_ID")
        if doc_id in normalized:
            raise ConsumerPackageError("AUTHORIZED_EVIDENCE_DUPLICATE")
        source_ref = _required_text(item["source_ref"], "EVIDENCE_SOURCE_REF")
        digest_value = _required_text(item["sha256"], "EVIDENCE_SHA256")
        if not re.fullmatch(r"[0-9a-f]{64}", digest_value):
            raise ConsumerPackageError("EVIDENCE_SHA256_INVALID")
        body = item.get("body", item.get("content"))
        if not isinstance(body, str) or not body:
            raise ConsumerPackageError("AUTHORIZED_EVIDENCE_BODY_REQUIRED")
        if digest(body.encode("utf-8")) != digest_value:
            raise ConsumerPackageError("AUTHORIZED_EVIDENCE_HASH_MISMATCH")
        target = item.get("target")
        if target is not None and target not in CONSUMER_TARGETS:
            raise ConsumerPackageError("AUTHORIZED_EVIDENCE_TARGET_INVALID")
        normalized[doc_id] = {
            "doc_id": doc_id,
            "body": body,
            "sha256": digest_value,
            "source_ref": source_ref,
            "target": target,
        }
    return normalized


def _normalize_package_path(value, label):
    value = _required_text(value, label)
    if "\\" in value:
        raise ConsumerPackageError(f"{label}_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ConsumerPackageError(f"{label}_INVALID")
    return path.as_posix()


def normalize_transport_routes(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or not set(value) <= TRANSPORT_ROUTE_FIELDS:
        raise ConsumerPackageError("TRANSPORT_ROUTES_INVALID")
    normalized = {}
    for field in sorted(TRANSPORT_ROUTE_FIELDS):
        mapping = value.get(field, {})
        if not isinstance(mapping, dict):
            raise ConsumerPackageError(f"TRANSPORT_ROUTES_{field.upper()}_INVALID")
        normalized[field] = {}
        for key, physical in mapping.items():
            key = _required_text(key, f"{field.upper()}_KEY")
            normalized[field][key] = _normalize_package_path(
                physical,
                f"{field.upper()}_PATH",
            )
    return normalized


def normalize_target_transport_routes(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConsumerPackageError("TARGET_TRANSPORT_ROUTES_INVALID")
    if not set(value) <= set(CONSUMER_TARGETS):
        raise ConsumerPackageError("TARGET_TRANSPORT_ROUTES_INVALID")
    return {
        target: normalize_transport_routes(value.get(target, {}))
        for target in CONSUMER_TARGETS
    }


def _json_text(value):
    return canonical_bytes(value).decode("utf-8") + "\n"


def _base_file(logical, routes):
    return routes["base_files"].get(logical, logical)


def _apply_base_file_routes(files, routes):
    routed = {}
    for logical, content in files.items():
        physical = _base_file(logical, routes)
        if physical in routed:
            raise ConsumerPackageError("PACKAGE_FILENAME_COLLISION")
        routed[physical] = content
    return routed


def _canonical_projection_path(doc_id, routes, sharded):
    if sharded:
        return "documents/shard-000.json"
    return routes["canonical_projection_files"].get(
        doc_id,
        f"documents/{doc_id}.json",
    )


def _authorized_evidence_path(doc_id, routes):
    return routes["authorized_evidence_files"].get(
        doc_id,
        f"authorized-evidence/{doc_id}.json",
    )


def _put_package_file(files, path, content):
    if path in files:
        raise ConsumerPackageError("PACKAGE_FILENAME_COLLISION")
    files[path] = content


def _document_decisions(payload, target, authorizations, authorized_evidence):
    packaged = []
    blocked = []
    evidence = []
    for projection in payload["selected_document_projections"]:
        doc_id = projection["doc_id"]
        rights = projection["rights"]
        authorization = authorizations.get((target, doc_id))
        item_evidence = authorized_evidence.get(doc_id)
        if rights == "public":
            if item_evidence is not None:
                raise ConsumerPackageError(
                    "AUTHORIZED_EVIDENCE_FOR_PUBLIC_DOCUMENT",
                )
            packaged.append(projection)
        elif authorization is not None:
            if item_evidence is None:
                raise ConsumerPackageError("AUTHORIZED_EVIDENCE_MISSING")
            evidence_target = item_evidence.get("target")
            if evidence_target not in (None, target):
                raise ConsumerPackageError("AUTHORIZED_EVIDENCE_TARGET_MISMATCH")
            packaged.append(projection)
            evidence.append({
                **item_evidence,
                "rights": rights,
                "public_export_allowed": False,
                "canonical_projection_sha256": projection["projection_sha256"],
                "authorization": authorization,
            })
        else:
            if item_evidence is not None:
                raise ConsumerPackageError(
                    "AUTHORIZED_EVIDENCE_WITHOUT_AUTHORIZATION",
                )
            blocked.append({
                "doc_id": doc_id,
                "rights": rights,
                "reason": "TARGET_AUTHORIZATION_REQUIRED",
            })
    unexpected = sorted(set(authorized_evidence) - {
        item["doc_id"] for item in payload["selected_document_projections"]
    })
    if unexpected:
        raise ConsumerPackageError("AUTHORIZED_EVIDENCE_OUT_OF_SCOPE")
    return packaged, blocked, evidence


def _coverage_report(
    payload,
    target,
    packaged,
    blocked,
    authorization_count,
    evidence_count,
):
    declared = 5 + len(payload["selected_document_projections"])
    packaged_items = 5 + len(packaged)
    blocked_items = len(blocked)
    missing = declared - packaged_items - blocked_items
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "consumer_payload_sha256": payload["consumer_payload_sha256"],
        "declared_items": declared,
        "packaged_items": packaged_items,
        "explicitly_blocked_items": blocked_items,
        "missing": missing,
        "unexpected": 0,
        "duplicate": 0,
        "silent_truncation": 0,
        "target_authorizations": authorization_count,
        "authorized_evidence_items": evidence_count,
        "blocked": blocked,
        "status": (
            "PASS"
            if missing == 0 and blocked_items == len(blocked)
            else "FAIL"
        ),
    }


def _base_files(payload, target, coverage):
    readme = (
        f"# CBA-KB consumer package: {target}\n\n"
        f"Canonical payload SHA-256: {payload['consumer_payload_sha256']}\n\n"
        "This package is a deterministic projection of the canonical payload. "
        "Unavailable or unauthorized documents remain listed in the coverage "
        "report; package generation never silently truncates them. Any "
        "target-authorized private evidence is isolated in authorized-evidence/ "
        "and does not alter the canonical payload or public-export rights.\n"
    )
    return {
        "README.md": readme,
        "canonical_consumer_payload.json": _json_text(payload),
        "player_profile.json": _json_text(payload["player_profile"]),
        "source_index.json": _json_text(payload["source_index"]),
        "event_coverage.json": _json_text(payload["event_coverage"]),
        "coverage_report.json": _json_text(coverage),
    }


def _manifest(
    payload,
    target,
    files,
    blocked,
    coverage,
    authorization_count,
    authorized_evidence,
    canonical_paths,
    authorized_paths,
    navigation_contract_sha256,
    navigation_contract_file,
    canonical_payload_file,
):
    entries = [{
        "path": path,
        "sha256": digest(content.encode("utf-8")),
        "bytes": len(content.encode("utf-8")),
    } for path, content in sorted(files.items())]
    package_core = {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "consumer_payload_sha256": payload["consumer_payload_sha256"],
        "files": entries,
    }
    package_sha256 = digest(canonical_bytes(package_core))
    return {
        **package_core,
        "package_sha256": package_sha256,
        "package_generation": PACKAGE_GENERATION,
        "navigation_contract_sha256": navigation_contract_sha256,
        "navigation_contract_file": navigation_contract_file,
        "canonical_payload_file": canonical_payload_file,
        "target_authorizations": authorization_count,
        "authorized_evidence": [{
            "doc_id": item["doc_id"],
            "target": target,
            "path": authorized_paths.get(item["doc_id"]),
            "sha256": item["sha256"],
            "rights": item["rights"],
            "public_export_allowed": item["public_export_allowed"],
            "source_ref": item["source_ref"],
            "authorization": item["authorization"],
        } for item in sorted(
            authorized_evidence,
            key=lambda value: value["doc_id"],
        )],
        "canonical_projection_files": dict(sorted(canonical_paths.items())),
        "blocked_items": blocked,
        "coverage": coverage,
    }


def _package_size(files):
    return sum(len(value.encode("utf-8")) for value in files.values())


def _with_document_files(base, packaged, sharded, routes):
    files = dict(base)
    if sharded:
        _put_package_file(
            files,
            "documents/shard-000.json",
            _json_text(packaged),
        )
    else:
        for projection in packaged:
            _put_package_file(
                files,
                _canonical_projection_path(
                    projection["doc_id"],
                    routes,
                    False,
                ),
                _json_text(projection),
            )
    return files


def _authorized_evidence_payload(item):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "TARGET_AUTHORIZED_PRIVATE_EVIDENCE",
        "doc_id": item["doc_id"],
        "body": item["body"],
        "sha256": item["sha256"],
        "source_ref": item["source_ref"],
        "rights": item["rights"],
        "public_export_allowed": item["public_export_allowed"],
        "canonical_projection_sha256": item["canonical_projection_sha256"],
        "authorization": item["authorization"],
    }


def _with_authorized_evidence(files, authorized_evidence, routes):
    result = dict(files)
    for item in authorized_evidence:
        _put_package_file(
            result,
            _authorized_evidence_path(item["doc_id"], routes),
            _json_text(_authorized_evidence_payload(item)),
        )
    return result


def _navigation_contract(
    payload,
    target,
    canonical_paths,
    authorized_paths,
    authorized_evidence,
    routes,
):
    evidence_by_id = {
        item["doc_id"]: item for item in authorized_evidence
    }
    documents = {}
    for projection in payload["selected_document_projections"]:
        doc_id = projection["doc_id"]
        evidence = evidence_by_id.get(doc_id)
        documents[doc_id] = {
            "canonical_projection_file": canonical_paths.get(doc_id),
            "authorized_evidence_file": authorized_paths.get(doc_id),
            "authorized_evidence_sha256": (
                evidence["sha256"] if evidence is not None else None
            ),
            "source_ref": (
                evidence["source_ref"]
                if evidence is not None
                else projection["source_ref"]
            ),
            "allowed_scope": (
                evidence["authorization"]["allowed_scope"]
                if evidence is not None else None
            ),
            "authorization_basis": (
                evidence["authorization"]["authorization_basis"]
                if evidence is not None else None
            ),
            "rights": projection["rights"],
            "public_export_allowed": (
                evidence["public_export_allowed"]
                if evidence is not None
                else projection["rights"] == "public"
            ),
            "packaged": doc_id in canonical_paths,
        }
    profile_version = payload["player_profile"]["profile_version"]
    if profile_version == "v2.0":
        lookup_contract = {
            "player_uid": {
                "primary_source": _base_file(
                    "player_profile.json",
                    routes,
                ),
                "selector": "player_uid",
                "copy_values_exactly": True,
                "preserve_null": True,
            },
            "documents": documents,
        }
        identity_policy = {
            "identity_selector": "player_uid",
            "record_key_is_person_identity": False,
            "same_name_records": "NOT_USED_FOR_PROFILE_V2",
            "automatic_merge": False,
            "player_uid_policy": "PRESENT_FOR_PROFILE_V2",
            "same_person_assertion_without_independent_evidence": False,
        }
    else:
        lookup_contract = {
            "record_key": {
                "primary_source": _base_file(
                    "player_profile.json",
                    routes,
                ),
                "selector": "record_key",
                "copy_values_exactly": True,
                "preserve_null": True,
            },
            "documents": documents,
        }
        identity_policy = {
            "identity_selector": "record_key",
            "record_key_is_person_identity": False,
            "same_name_records": "REVIEW_REQUIRED",
            "automatic_merge": False,
            "player_uid_policy": "ABSENT",
            "same_person_assertion_without_independent_evidence": False,
        }
    return {
        "schema_version": NAVIGATION_SCHEMA_VERSION,
        "package_generation": PACKAGE_GENERATION,
        "target": target,
        "canonical_consumer_payload_sha256": payload[
            "consumer_payload_sha256"
        ],
        "lookup_contract": lookup_contract,
        "identity_policy": identity_policy,
        "provenance_policy": {
            "source_ref": "COPY_EXACT_STORED_LOCATOR",
            "source_url": "COPY_EXACT_STORED_LOCATOR",
            "source_file_id": "COPY_EXACT_STORED_VALUE",
            "authorized_evidence_sha256": "COPY_EXACT_CONTENT_SHA256",
        },
        "null_policy": {
            "preserve_canonical_null": True,
            "never_replace_null_with_guess": True,
        },
    }


def _assemble_package_files(
    payload,
    target,
    packaged,
    authorized,
    coverage,
    routes,
    sharded,
):
    base = _apply_base_file_routes(
        _base_files(payload, target, coverage),
        routes,
    )
    files = _with_document_files(base, packaged, sharded, routes)
    files = _with_authorized_evidence(files, authorized, routes)
    canonical_paths = {
        projection["doc_id"]: _canonical_projection_path(
            projection["doc_id"],
            routes,
            sharded,
        )
        for projection in packaged
    }
    authorized_paths = {
        item["doc_id"]: _authorized_evidence_path(item["doc_id"], routes)
        for item in authorized
    }
    navigation = _navigation_contract(
        payload,
        target,
        canonical_paths,
        authorized_paths,
        authorized,
        routes,
    )
    navigation_content = _json_text(navigation)
    navigation_path = _base_file("consumer_navigation_contract.json", routes)
    _put_package_file(files, navigation_path, navigation_content)
    return (
        files,
        canonical_paths,
        authorized_paths,
        navigation_path,
        digest(navigation_content.encode("utf-8")),
    )


def _validate_transport_route_scope(packaged, authorized, routes, sharded):
    packaged_docs = {item["doc_id"] for item in packaged}
    authorized_docs = {item["doc_id"] for item in authorized}
    if set(routes["canonical_projection_files"]) - packaged_docs:
        raise ConsumerPackageError("TRANSPORT_ROUTES_DOCUMENT_OUT_OF_SCOPE")
    if set(routes["authorized_evidence_files"]) - authorized_docs:
        raise ConsumerPackageError("TRANSPORT_ROUTES_EVIDENCE_OUT_OF_SCOPE")
    allowed_base = {
        "README.md",
        "canonical_consumer_payload.json",
        "player_profile.json",
        "source_index.json",
        "event_coverage.json",
        "coverage_report.json",
        "consumer_navigation_contract.json",
    }
    if set(routes["base_files"]) - allowed_base:
        raise ConsumerPackageError("TRANSPORT_ROUTES_BASE_OUT_OF_SCOPE")
    if (
        sharded
        and routes["canonical_projection_files"]
    ):
        raise ConsumerPackageError(
            "TRANSPORT_ROUTES_CONFLICT_WITH_PROJECTION_SHARDING",
        )


def build_target_package(
    payload,
    target,
    *,
    authorizations=None,
    authorized_evidence=None,
    transport_routes=None,
    limits=None,
):
    if target not in CONSUMER_TARGETS:
        raise ConsumerPackageError("CONSUMER_TARGET_INVALID")
    normalized = normalize_authorizations(authorizations)
    evidence = normalize_authorized_evidence(authorized_evidence)
    routes = normalize_transport_routes(transport_routes)
    packaged, blocked, authorized = _document_decisions(
        payload,
        target,
        normalized,
        evidence,
    )
    authorization_count = sum(
        1 for authorization_target, _ in normalized
        if authorization_target == target
    )
    coverage = _coverage_report(
        payload,
        target,
        packaged,
        blocked,
        authorization_count,
        len(authorized),
    )
    if coverage["status"] != "PASS":
        raise ConsumerPackageError("PACKAGE_COVERAGE_MISMATCH")
    _validate_transport_route_scope(packaged, authorized, routes, False)
    (
        files,
        canonical_paths,
        authorized_paths,
        navigation_path,
        navigation_sha,
    ) = _assemble_package_files(
        payload,
        target,
        packaged,
        authorized,
        coverage,
        routes,
        False,
    )
    limits = limits or {}
    if not isinstance(limits, dict):
        raise ConsumerPackageError("PACKAGE_LIMITS_OBJECT_REQUIRED")
    max_files = limits.get("max_files")
    max_bytes = limits.get("max_bytes")
    if max_files is not None and (
        not isinstance(max_files, int) or max_files <= 0
    ):
        raise ConsumerPackageError("PACKAGE_MAX_FILES_INVALID")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or max_bytes <= 0
    ):
        raise ConsumerPackageError("PACKAGE_MAX_BYTES_INVALID")
    if max_files is not None and len(files) + 1 > max_files:
        _validate_transport_route_scope(packaged, authorized, routes, True)
        (
            files,
            canonical_paths,
            authorized_paths,
            navigation_path,
            navigation_sha,
        ) = _assemble_package_files(
            payload,
            target,
            packaged,
            authorized,
            coverage,
            routes,
            True,
        )
    if max_files is not None and len(files) + 1 > max_files:
        raise ConsumerPackageError("PACKAGE_FILE_LIMIT_EXCEEDED")
    provisional = _manifest(
        payload,
        target,
        files,
        blocked,
        coverage,
        authorization_count,
        authorized,
        canonical_paths,
        authorized_paths,
        navigation_sha,
        navigation_path,
        _base_file("canonical_consumer_payload.json", routes),
    )
    if max_bytes is not None and (
        _package_size(files) + len(_json_text(provisional).encode("utf-8"))
        > max_bytes
    ):
        _validate_transport_route_scope(packaged, authorized, routes, True)
        (
            files,
            canonical_paths,
            authorized_paths,
            navigation_path,
            navigation_sha,
        ) = _assemble_package_files(
            payload,
            target,
            packaged,
            authorized,
            coverage,
            routes,
            True,
        )
        provisional = _manifest(
            payload,
            target,
            files,
            blocked,
            coverage,
            authorization_count,
            authorized,
            canonical_paths,
            authorized_paths,
            navigation_sha,
            navigation_path,
            _base_file("canonical_consumer_payload.json", routes),
        )
    if max_bytes is not None and (
        _package_size(files) + len(_json_text(provisional).encode("utf-8"))
        > max_bytes
    ):
        raise ConsumerPackageError("PACKAGE_SIZE_LIMIT_EXCEEDED")
    manifest = _manifest(
        payload,
        target,
        files,
        blocked,
        coverage,
        authorization_count,
        authorized,
        canonical_paths,
        authorized_paths,
        navigation_sha,
        navigation_path,
        _base_file("canonical_consumer_payload.json", routes),
    )
    files["package_manifest.json"] = _json_text(manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "consumer_payload_sha256": payload["consumer_payload_sha256"],
        "package_sha256": manifest["package_sha256"],
        "navigation_contract_path": navigation_path,
        "canonical_payload_file": _base_file(
            "canonical_consumer_payload.json",
            routes,
        ),
        "files": files,
        "manifest": manifest,
    }


def build_target_packages(
    payload,
    *,
    authorizations=None,
    authorized_evidence=None,
    transport_routes=None,
    limits=None,
):
    auth = normalize_authorizations(authorizations)
    evidence = normalize_authorized_evidence(authorized_evidence)
    route_map = normalize_target_transport_routes(transport_routes)
    limits = limits or {}
    if not isinstance(limits, dict):
        raise ConsumerPackageError("PACKAGE_LIMITS_OBJECT_REQUIRED")
    packages = {}
    for target in CONSUMER_TARGETS:
        target_evidence = {
            doc_id: item
            for doc_id, item in evidence.items()
            if item["target"] == target
            or (
                item["target"] is None
                and (target, doc_id) in auth
            )
        }
        packages[target] = build_target_package(
            payload,
            target,
            authorizations=list(auth.values()),
            authorized_evidence=target_evidence,
            transport_routes=route_map[target],
            limits=limits.get(target, {}),
        )
    hashes = {
        package["consumer_payload_sha256"]
        for package in packages.values()
    }
    if hashes != {payload["consumer_payload_sha256"]}:
        raise ConsumerPackageError("TARGET_PAYLOAD_HASH_MISMATCH")
    return packages


def validate_package(package):
    if not isinstance(package, dict):
        raise ConsumerPackageError("PACKAGE_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "target",
        "consumer_payload_sha256",
        "package_sha256",
        "navigation_contract_path",
        "canonical_payload_file",
        "files",
        "manifest",
    }
    if not required <= set(package):
        raise ConsumerPackageError("PACKAGE_INCOMPLETE")
    if package["target"] not in CONSUMER_TARGETS:
        raise ConsumerPackageError("CONSUMER_TARGET_INVALID")
    files = package["files"]
    if not isinstance(files, dict) or "package_manifest.json" not in files:
        raise ConsumerPackageError("PACKAGE_FILES_REQUIRED")
    coverage = package["manifest"].get("coverage")
    if not isinstance(coverage, dict):
        raise ConsumerPackageError("PACKAGE_COVERAGE_REQUIRED")
    declared = coverage.get("declared_items")
    packaged = coverage.get("packaged_items")
    blocked = coverage.get("explicitly_blocked_items")
    if (
        not all(isinstance(value, int) and value >= 0
                for value in (declared, packaged, blocked))
        or declared != packaged + blocked
        or coverage.get("missing") != 0
        or coverage.get("unexpected") != 0
        or coverage.get("duplicate") != 0
        or coverage.get("silent_truncation") != 0
    ):
        raise ConsumerPackageError("PACKAGE_COVERAGE_MISMATCH")
    evidence_entries = package["manifest"].get("authorized_evidence", [])
    if not isinstance(evidence_entries, list):
        raise ConsumerPackageError("PACKAGE_EVIDENCE_MANIFEST_INVALID")
    if coverage.get("authorized_evidence_items") != len(evidence_entries):
        raise ConsumerPackageError("PACKAGE_EVIDENCE_COVERAGE_MISMATCH")
    canonical_payload = files.get(package["canonical_payload_file"])
    if not isinstance(canonical_payload, str):
        raise ConsumerPackageError("CANONICAL_PAYLOAD_FILE_REQUIRED")
    try:
        canonical_value = json.loads(canonical_payload)
    except ValueError as exc:
        raise ConsumerPackageError("CANONICAL_PAYLOAD_FILE_INVALID") from exc
    evidence_ids = set()
    for entry in evidence_entries:
        if not isinstance(entry, dict):
            raise ConsumerPackageError("PACKAGE_EVIDENCE_MANIFEST_INVALID")
        doc_id = entry.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id or doc_id in evidence_ids:
            raise ConsumerPackageError("PACKAGE_EVIDENCE_ID_INVALID")
        evidence_ids.add(doc_id)
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ConsumerPackageError("PACKAGE_EVIDENCE_PATH_MISMATCH")
        content = files.get(path)
        if not isinstance(content, str):
            raise ConsumerPackageError("PACKAGE_EVIDENCE_FILE_MISSING")
        try:
            evidence = json.loads(content)
        except ValueError as exc:
            raise ConsumerPackageError("PACKAGE_EVIDENCE_FILE_INVALID") from exc
        if (
            evidence.get("doc_id") != doc_id
            or evidence.get("kind") != "TARGET_AUTHORIZED_PRIVATE_EVIDENCE"
            or evidence.get("rights") not in {
                "copyrighted", "private", "unknown",
            }
            or evidence.get("public_export_allowed") is not False
            or evidence.get("authorization", {}).get("target")
            != package["target"]
            or digest(str(evidence.get("body", "")).encode("utf-8"))
            != evidence.get("sha256")
        ):
            raise ConsumerPackageError("PACKAGE_EVIDENCE_INVALID")
        if evidence["body"] in canonical_payload:
            raise ConsumerPackageError(
                "AUTHORIZED_EVIDENCE_EMBEDDED_IN_CANONICAL_PAYLOAD",
            )
    navigation_path = package["navigation_contract_path"]
    navigation_content = files.get(navigation_path)
    if not isinstance(navigation_content, str):
        raise ConsumerPackageError("PACKAGE_NAVIGATION_CONTRACT_MISSING")
    if digest(navigation_content.encode("utf-8")) != package["manifest"].get(
        "navigation_contract_sha256",
    ):
        raise ConsumerPackageError("PACKAGE_NAVIGATION_CONTRACT_HASH_MISMATCH")
    try:
        navigation = json.loads(navigation_content)
    except ValueError as exc:
        raise ConsumerPackageError("PACKAGE_NAVIGATION_CONTRACT_INVALID") from exc
    if (
        navigation.get("schema_version") != NAVIGATION_SCHEMA_VERSION
        or navigation.get("package_generation") != PACKAGE_GENERATION
        or navigation.get("target") != package["target"]
        or navigation.get("canonical_consumer_payload_sha256")
        != package["consumer_payload_sha256"]
    ):
        raise ConsumerPackageError("PACKAGE_NAVIGATION_CONTRACT_INVALID")
    identity = navigation.get("identity_policy", {})
    profile_version = canonical_value.get(
        "player_profile", {},
    ).get("profile_version")
    expected_identity = (
        {
            "identity_selector": "player_uid",
            "record_key_is_person_identity": False,
            "same_name_records": "NOT_USED_FOR_PROFILE_V2",
            "automatic_merge": False,
            "player_uid_policy": "PRESENT_FOR_PROFILE_V2",
            "same_person_assertion_without_independent_evidence": False,
        }
        if profile_version == "v2.0"
        else {
            "identity_selector": "record_key",
            "record_key_is_person_identity": False,
            "same_name_records": "REVIEW_REQUIRED",
            "automatic_merge": False,
            "player_uid_policy": "ABSENT",
            "same_person_assertion_without_independent_evidence": False,
        }
    )
    if identity != expected_identity:
        raise ConsumerPackageError("PACKAGE_IDENTITY_POLICY_INVALID")
    if profile_version == "v2.0":
        selector = navigation.get("lookup_contract", {}).get(
            "player_uid", {},
        )
        if selector != {
            "primary_source": navigation["lookup_contract"].get(
                "player_uid", {},
            ).get("primary_source"),
            "selector": "player_uid",
            "copy_values_exactly": True,
            "preserve_null": True,
        }:
            raise ConsumerPackageError("PACKAGE_IDENTITY_LOOKUP_INVALID")
    if (
        navigation.get("null_policy") != {
            "preserve_canonical_null": True,
            "never_replace_null_with_guess": True,
        }
        or navigation.get("provenance_policy") != {
            "source_ref": "COPY_EXACT_STORED_LOCATOR",
            "source_url": "COPY_EXACT_STORED_LOCATOR",
            "source_file_id": "COPY_EXACT_STORED_VALUE",
            "authorized_evidence_sha256": "COPY_EXACT_CONTENT_SHA256",
        }
    ):
        raise ConsumerPackageError("PACKAGE_NAVIGATION_POLICY_INVALID")
    documents = navigation.get("lookup_contract", {}).get("documents")
    if not isinstance(documents, dict):
        raise ConsumerPackageError("PACKAGE_DOCUMENT_NAVIGATION_REQUIRED")
    payload_doc_ids = {
        item["doc_id"]
        for item in canonical_value["selected_document_projections"]
    }
    if set(documents) != payload_doc_ids:
        raise ConsumerPackageError("PACKAGE_DOCUMENT_NAVIGATION_INCOMPLETE")
    for doc_id, entry in documents.items():
        canonical_path = entry.get("canonical_projection_file")
        if canonical_path is not None and not isinstance(
            files.get(canonical_path), str,
        ):
            raise ConsumerPackageError("PACKAGE_DOCUMENT_ROUTE_MISSING")
        evidence_path = entry.get("authorized_evidence_file")
        if evidence_path is not None:
            content = files.get(evidence_path)
            if not isinstance(content, str):
                raise ConsumerPackageError("PACKAGE_EVIDENCE_ROUTE_MISSING")
            evidence = json.loads(content)
            if (
                evidence.get("doc_id") != doc_id
                or evidence.get("sha256")
                != entry.get("authorized_evidence_sha256")
                or evidence.get("source_ref") != entry.get("source_ref")
                or evidence.get("rights") != entry.get("rights")
                or evidence.get("public_export_allowed")
                != entry.get("public_export_allowed")
                or evidence.get("authorization", {}).get("allowed_scope")
                != entry.get("allowed_scope")
            ):
                raise ConsumerPackageError("PACKAGE_EVIDENCE_ROUTE_MISMATCH")
    navigation_text = json.dumps(navigation, ensure_ascii=False)
    if any(
        token in navigation_text
        for token in (
            "expected_answer",
            "correct_answer",
            "oracle_answer",
            "QG01",
            "QG10",
        )
    ):
        raise ConsumerPackageError("PACKAGE_NAVIGATION_ORACLE_LEAKAGE")
    return package


def write_packages(packages, output_root):
    output_root = Path(output_root)
    if output_root.exists():
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise ConsumerPackageError("PACKAGE_OUTPUT_NOT_EMPTY")
    for target, package in packages.items():
        validate_package(package)
        root = output_root / TARGET_SLUGS[target]
        for relative, content in sorted(package["files"].items()):
            atomic(root / relative, content.encode("utf-8"))
    return {
        "targets": sorted(packages),
        "files": sum(len(package["files"]) for package in packages.values()),
        "consumer_payload_sha256": next(
            iter(packages.values()),
        )["consumer_payload_sha256"],
    }


build_payload = build_consumer_payload
build_canonical_payload = build_consumer_payload
