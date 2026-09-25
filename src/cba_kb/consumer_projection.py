"""Rights-aware consumer projections and versioned consumer result schemas."""
from __future__ import annotations

from pathlib import Path

import yaml

from .common import digest
from .evidence_ledger import canonical_bytes


SCHEMA_VERSION = 1
RESULT_STATUSES = frozenset({"PASS", "FAIL", "NOT_TESTED"})
FAILURE_LAYERS = frozenset({
    "projection",
    "retrieval",
    "aggregation",
    "counting",
    "provenance",
    "source_gap",
    "identity",
    "consumer_tool",
})
GOLDEN_CATEGORY_COUNTS = {
    "deterministic_exact": 4,
    "provenance": 3,
    "cross_source_synthesis": 2,
    "unknown_gap": 1,
}
RIGHTS = frozenset({"public", "copyrighted", "private", "unknown"})
BODY_STATUSES = frozenset({
    "AVAILABLE",
    "OCR_REQUIRED",
    "REVIEW_REQUIRED",
    "BODY_UNAVAILABLE",
})
IDENTITY_SEMANTIC_STATES = frozenset({
    "SAME",
    "NOT_SAME",
    "UNDECIDED",
    "UNAVAILABLE",
    "NOT_MATERIALIZED",
})
CONSUMER_IDENTITY_STATE_MAP = {
    "same": "SAME",
    "not_same": "NOT_SAME",
    "undecided": "UNDECIDED",
    "unlinked": "NOT_MATERIALIZED",
    "unavailable": "UNAVAILABLE",
    "not_materialized": "NOT_MATERIALIZED",
}


class ConsumerProjectionError(RuntimeError):
    pass


def _require_scope(scope):
    if not isinstance(scope, dict):
        raise ConsumerProjectionError("CONSUMER_SCOPE_REQUIRED")
    required = {"release_id", "as_of", "provenance"}
    if not required <= set(scope):
        raise ConsumerProjectionError("CONSUMER_SCOPE_INCOMPLETE")
    if any(not isinstance(scope[key], str) or not scope[key] for key in required):
        raise ConsumerProjectionError("CONSUMER_SCOPE_INVALID")
    return {key: scope[key] for key in sorted(required)}


def _body_status(document):
    value = document.get("body_status") or "AVAILABLE"
    value = str(value).upper()
    if value not in BODY_STATUSES:
        raise ConsumerProjectionError("DOCUMENT_BODY_STATUS_INVALID")
    return value


def project_document(document, scope):
    if not isinstance(document, dict):
        raise ConsumerProjectionError("DOCUMENT_PROJECTION_OBJECT_REQUIRED")
    scope = _require_scope(scope)
    doc_id = document.get("doc_id")
    source_ref = document.get("source_ref")
    rights = str(document.get("rights") or "unknown").lower()
    if not isinstance(doc_id, str) or not doc_id:
        raise ConsumerProjectionError("DOCUMENT_ID_REQUIRED")
    if not isinstance(source_ref, str) or not source_ref:
        raise ConsumerProjectionError("DOCUMENT_SOURCE_REQUIRED")
    if rights not in RIGHTS:
        raise ConsumerProjectionError("DOCUMENT_RIGHTS_INVALID")
    status = _body_status(document)
    available = status == "AVAILABLE"
    metadata_scope = set(document.get("metadata_scope") or [
        "title", "published_at", "canonical_url",
    ])
    metadata = {
        key: document[key]
        for key in sorted(metadata_scope)
        if key in document and document[key] is not None
    }
    title = document.get("title")
    body = document.get("body")
    availability = "BLOCKED"
    body_output = None
    title_output = None
    if rights == "public":
        availability = "AVAILABLE" if available else status
        title_output = title
        body_output = body if available else None
    elif rights == "copyrighted":
        allowed = document.get("public_export_allowed") is True
        availability = (
            "AVAILABLE" if allowed and available
            else "METADATA_ONLY"
        )
        title_output = title if allowed else None
        body_output = body if allowed and available else None
    elif rights == "private":
        availability = "PRIVATE"
        title_output = None
        body_output = None
        metadata = {}
    else:
        availability = "BLOCKED"
        title_output = None
        body_output = None
        metadata = {}
        body_status = "BODY_UNAVAILABLE"
    if rights in {"private", "unknown"}:
        body_status = "BODY_UNAVAILABLE"
    elif rights == "copyrighted" and document.get("public_export_allowed") is not True:
        body_status = "BODY_UNAVAILABLE"
    else:
        body_status = status
    projection = {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "availability": availability,
        "rights": rights,
        "body_status": body_status,
        "source_ref": source_ref,
        "metadata": metadata,
        "title": title_output,
        "body": body_output,
        "release_id": scope["release_id"],
        "as_of": scope["as_of"],
        "provenance": scope["provenance"],
    }
    projection["projection_sha256"] = digest(canonical_bytes(projection))
    return projection


def project_documents(documents, scope):
    if not isinstance(documents, list):
        raise ConsumerProjectionError("DOCUMENT_COLLECTION_REQUIRED")
    return [
        project_document(document, scope)
        for document in sorted(documents, key=lambda item: item.get("doc_id", ""))
    ]


def event_coverage(*, season, team, events, sources, as_of):
    if any(not isinstance(value, str) or not value for value in (
        season, team, as_of,
    )):
        raise ConsumerProjectionError("EVENT_COVERAGE_SCOPE_REQUIRED")
    if not isinstance(events, list) or not isinstance(sources, list):
        raise ConsumerProjectionError("EVENT_COVERAGE_INPUT_REQUIRED")
    covered = []
    unresolved = []
    for event in sorted(events, key=lambda item: item.get("event_id", "")):
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ConsumerProjectionError("EVENT_ID_REQUIRED")
        if (
            event.get("season", season) == season
            and event.get("team", team) == team
            and event.get("resolved") is not False
        ):
            covered.append(event_id)
        else:
            unresolved.append(event_id)
    source_status = {}
    for source in sources:
        source_id = source.get("source_id")
        status = source.get("status")
        if not isinstance(source_id, str) or not source_id:
            raise ConsumerProjectionError("EVENT_SOURCE_ID_REQUIRED")
        if status not in {"PASS", "FAIL", "PENDING", "NOT_TESTED"}:
            raise ConsumerProjectionError("EVENT_SOURCE_STATUS_INVALID")
        source_status[source_id] = status
    return {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "team": team,
        "covered": sorted(covered),
        "unresolved": sorted(unresolved),
        "critical_gap": bool(unresolved),
        "source_status": source_status,
        "as_of": as_of,
    }


def validate_consumer_result(result):
    if not isinstance(result, dict):
        raise ConsumerProjectionError("CONSUMER_RESULT_OBJECT_REQUIRED")
    status = result.get("status")
    if status not in RESULT_STATUSES:
        raise ConsumerProjectionError("CONSUMER_RESULT_STATUS_INVALID")
    failure_layer = result.get("failure_layer")
    if status == "FAIL" and failure_layer not in FAILURE_LAYERS:
        raise ConsumerProjectionError("CONSUMER_FAILURE_LAYER_REQUIRED")
    if failure_layer is not None and failure_layer not in FAILURE_LAYERS:
        raise ConsumerProjectionError("CONSUMER_FAILURE_LAYER_INVALID")
    return {
        "status": status,
        "failure_layer": failure_layer,
        "evidence": result.get("evidence"),
    }


def consumer_baseline_template():
    return {
        "schema_version": SCHEMA_VERSION,
        "questions_version": "v1",
        "consumers": {
            name: {"status": "NOT_TESTED", "failure_layer": None}
            for name in ("ChatGPT", "Gemini", "WorkBuddy")
        },
    }


def validate_consumer_baseline(baseline):
    if (
        not isinstance(baseline, dict)
        or baseline.get("questions_version") != "v1"
        or not isinstance(baseline.get("consumers"), dict)
        or set(baseline["consumers"]) != {"ChatGPT", "Gemini", "WorkBuddy"}
    ):
        raise ConsumerProjectionError("CONSUMER_BASELINE_INVALID")
    consumers = {
        name: validate_consumer_result(result)
        for name, result in baseline["consumers"].items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "questions_version": "v1",
        "consumers": consumers,
    }


def usage_failure_layer_schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "required": [
            "consumer",
            "question_id",
            "status",
            "failure_layer",
            "evidence_ref",
        ],
        "statuses": sorted(RESULT_STATUSES),
        "failure_layers": sorted(FAILURE_LAYERS),
    }


def validate_golden_questions(value):
    if not isinstance(value, dict):
        raise ConsumerProjectionError("GOLDEN_QUESTIONS_OBJECT_REQUIRED")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("version") != "v1":
        raise ConsumerProjectionError("GOLDEN_QUESTIONS_VERSION_INVALID")
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) != 10:
        raise ConsumerProjectionError("GOLDEN_QUESTIONS_COUNT_INVALID")
    counts = {}
    ids = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ConsumerProjectionError("GOLDEN_QUESTION_INVALID")
        question_id = question.get("id")
        category = question.get("category")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in ids
            or category not in GOLDEN_CATEGORY_COUNTS
            or not question.get("question")
            or "expected_answer" not in question
            or not isinstance(question.get("expected_schema"), dict)
            or question.get("frozen") is not True
        ):
            raise ConsumerProjectionError("GOLDEN_QUESTION_INVALID")
        ids.add(question_id)
        counts[category] = counts.get(category, 0) + 1
    if counts != GOLDEN_CATEGORY_COUNTS:
        raise ConsumerProjectionError("GOLDEN_QUESTION_COVERAGE_INVALID")
    return {
        "status": "PASS",
        "version": "v1",
        "questions": len(questions),
        "coverage": counts,
        "sha256": digest(canonical_bytes(value)),
    }


def load_golden_questions(path):
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConsumerProjectionError("GOLDEN_QUESTIONS_INVALID") from exc
    validate_golden_questions(value)
    return value


def golden_questions_sha256(path):
    return digest(Path(path).read_bytes())


def project_identity_state(internal_status: str | None) -> str:
    """Project internal link status to a consumer-safe semantic state without manufacturing relations.

    Machine-distinguishes:
    - SAME
    - NOT_SAME
    - UNDECIDED
    - UNAVAILABLE / NOT_MATERIALIZED

    Existing internal unlinked/absence semantics are projected to the frozen consumer-safe
    unavailable/not-materialized state and never upgraded to SAME, NOT_SAME, or UNDECIDED.
    """
    if internal_status is None:
        return "UNAVAILABLE"
    normalized = str(internal_status).strip().lower()
    if normalized == "same":
        return "SAME"
    if normalized == "not_same":
        return "NOT_SAME"
    if normalized == "undecided":
        return "UNDECIDED"
    if normalized in {"unlinked", "unavailable", "not_materialized", "absence"}:
        return "NOT_MATERIALIZED"
    raise ConsumerProjectionError(f"UNKNOWN_IDENTITY_STATUS:{internal_status}")


def derive_machine_counts(manifest, identity_registry, master_rows=None):
    """Deterministically calculate machine counts from actual frozen inputs.

    Never hard-code expected counts.
    Every declared count must be machine-derived and satisfy declared == actual.
    """
    if isinstance(manifest, list):
        artifacts_count = len(manifest)
    elif isinstance(manifest, dict):
        artifacts_count = len(manifest.get("artifacts", manifest.get("rows", [])))
    else:
        raise ConsumerProjectionError("MANIFEST_COLLECTION_REQUIRED")

    if not isinstance(identity_registry, dict):
        raise ConsumerProjectionError("IDENTITY_REGISTRY_REQUIRED")

    players = identity_registry.get("players", [])
    record_links = identity_registry.get("record_links", [])

    player_count = len(players)
    record_link_count = len(record_links)
    same_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "same")
    not_same_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "not_same")
    undecided_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "undecided")

    linked_keys = {link.get("record_key") for link in record_links if link.get("record_key")}
    if master_rows is not None:
        if not isinstance(master_rows, list):
            raise ConsumerProjectionError("MASTER_ROWS_COLLECTION_REQUIRED")
        all_record_keys = {row.get("record_key") for row in master_rows if row.get("record_key")}
        unavailable_count = len(all_record_keys - linked_keys)
    else:
        unavailable_count = 0

    return {
        "production_artifact_count": artifacts_count,
        "player_count": player_count,
        "record_link_count": record_link_count,
        "same_count": same_count,
        "not_same_count": not_same_count,
        "undecided_count": undecided_count,
        "unavailable_count": unavailable_count,
        "not_materialized_count": unavailable_count,
    }


def validate_machine_counts(declared_counts, actual_counts):
    """Verify that every declared count equals the actual machine-derived count."""
    if not isinstance(declared_counts, dict) or not isinstance(actual_counts, dict):
        raise ConsumerProjectionError("MACHINE_COUNTS_OBJECT_REQUIRED")
    for key, expected_val in declared_counts.items():
        if key not in actual_counts:
            raise ConsumerProjectionError(f"UNDECLARED_COUNT_KEY:{key}")
        if actual_counts[key] != expected_val:
            raise ConsumerProjectionError(
                f"COUNT_MISMATCH:{key}:declared={expected_val},actual={actual_counts[key]}"
            )
    return True



def project_profile_identity(profile):
    """Project only relations already represented by a validated profile v2.

    Counts are scoped to the selected player and the supplied profile's
    unlinked records, never presented as counts of the private registry.
    """
    if profile.get("profile_version") != "v2.0":
        raise ConsumerProjectionError("PROFILE_V2_REQUIRED")
    coverage = profile["identity_coverage"]
    relations = []
    for status, keys in (
        ("same", profile["record_keys"]),
        ("not_same", coverage["selected_not_same_record_keys"]),
        ("undecided", coverage["undecided_record_keys"]),
        ("unlinked", coverage["unlinked_record_keys"]),
    ):
        for key in keys:
            relations.append({
                "record_key": key,
                "player_uid": None if status == "unlinked" else profile["player_uid"],
                "state": project_identity_state(status),
            })
    return {
        "scope": "SELECTED_PROFILE_RELATIONS_AND_SUPPLIED_UNLINKED_RECORDS",
        "source_profile_sha256": profile["profile_sha256"],
        "identity_selector": "player_uid",
        "record_key_is_person_identity": False,
        "automatic_merge": False,
        "same_person_assertion_without_independent_evidence": False,
        "players": [{"player_uid": profile["player_uid"]}],
        "relations": sorted(relations, key=lambda item: (item["record_key"], item["state"])),
    }


def derive_consumer_counts(artifacts, projection):
    """Count emitted projection data; this is not an identity registry."""
    links = [
        {"record_key": item["record_key"], "link_status": item["state"].lower()}
        for item in projection["relations"]
        if item["state"] in {"SAME", "NOT_SAME", "UNDECIDED"}
    ]
    rows = [{"record_key": item["record_key"]} for item in projection["relations"]]
    return derive_machine_counts(
        artifacts, {"players": projection["players"], "record_links": links}, rows,
    )
