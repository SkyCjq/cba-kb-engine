"""Rights-aware consumer projections and versioned consumer result schemas."""
from __future__ import annotations

import json
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
    from .canonical_registry import manifest_index

    if manifest is None:
        raise ConsumerProjectionError("MANIFEST_COLLECTION_REQUIRED")
    try:
        artifacts_count = len(manifest_index(manifest))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConsumerProjectionError("MANIFEST_INVALID") from exc
    if artifacts_count == 0:
        raise ConsumerProjectionError("MANIFEST_EMPTY")

    if not isinstance(identity_registry, dict):
        raise ConsumerProjectionError("IDENTITY_REGISTRY_REQUIRED")

    players = identity_registry.get("players")
    record_links = identity_registry.get("record_links")
    if not isinstance(players, list) or not isinstance(record_links, list):
        raise ConsumerProjectionError("IDENTITY_REGISTRY_LISTS_REQUIRED")
    if any(not isinstance(item, dict) for item in players + record_links):
        raise ConsumerProjectionError("IDENTITY_REGISTRY_ROWS_INVALID")
    if any(link.get("link_status") not in {"same", "not_same", "undecided"}
           for link in record_links):
        raise ConsumerProjectionError("IDENTITY_LINK_STATUS_INVALID")

    player_count = len(players)
    record_link_count = len(record_links)
    same_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "same")
    not_same_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "not_same")
    undecided_count = sum(1 for link in record_links if str(link.get("link_status")).lower() == "undecided")

    linked_keys = {link.get("record_key") for link in record_links if link.get("record_key")}
    if not isinstance(master_rows, list):
        raise ConsumerProjectionError("MASTER_ROWS_COLLECTION_REQUIRED")
    if any(not isinstance(row, dict) or not isinstance(row.get("record_key"), str)
           or not row["record_key"] for row in master_rows):
        raise ConsumerProjectionError("MASTER_ROWS_INVALID")
    all_record_keys = {row["record_key"] for row in master_rows}
    if len(all_record_keys) != len(master_rows) or not linked_keys <= all_record_keys:
        raise ConsumerProjectionError("MASTER_LINK_COVERAGE_INVALID")
    unavailable_count = len(all_record_keys - linked_keys)

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
    if set(declared_counts) != set(actual_counts):
        raise ConsumerProjectionError("MACHINE_COUNT_KEYS_MISMATCH")
    for key, expected_val in declared_counts.items():
        if type(expected_val) is not int or expected_val < 0:
            raise ConsumerProjectionError(f"MACHINE_COUNT_INVALID:{key}")
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
    if not isinstance(artifacts, list):
        raise ConsumerProjectionError("ARTIFACT_COLLECTION_REQUIRED")
    links = [
        {"record_key": item["record_key"], "link_status": item["state"].lower()}
        for item in projection["relations"]
        if item["state"] in {"SAME", "NOT_SAME", "UNDECIDED"}
    ]
    rows = [{"record_key": item["record_key"]} for item in projection["relations"]]
    return derive_machine_counts(
        [{"uid": str(index)} for index, _ in enumerate(artifacts)],
        {"players": projection["players"], "record_links": links}, rows,
    )


CONSUMER_IDENTITY_SCHEMA = "player_identity_consumer_v1"
CONSUMER_IDENTITY_SCHEMA_VERSION = 1


def build_player_identity_consumer_projection(
    identity_registry,
    *,
    release_id,
    product_version="v1.8.1",
    code_commit=None,
    as_of=None,
):
    """Derive an immutable, release-scoped, rights-aware Consumer projection.

    Only Consumer-safe fields are projected.
    Private registry internals, administrative notes, and raw file paths are strictly omitted.
    """
    from .player_identity import normalize_name, validate_player_uid, validate_registry
    if not isinstance(identity_registry, dict):
        raise ConsumerProjectionError("IDENTITY_REGISTRY_OBJECT_REQUIRED")
    validated_registry = validate_registry(identity_registry)

    # Active players only
    projected_players = []
    aliases_by_uid = {}
    for alias_entry in validated_registry.get("aliases", []):
        uid = alias_entry.get("player_uid")
        alias_name = alias_entry.get("alias")
        if uid and alias_name:
            aliases_by_uid.setdefault(uid, set()).add(alias_name)

    for player in validated_registry.get("players", []):
        if player.get("status") != "ACTIVE":
            continue
        uid = validate_player_uid(player["player_uid"])
        cname = normalize_name(player["canonical_name"])
        approved_aliases = sorted(aliases_by_uid.get(uid, set()))
        projected_players.append({
            "player_uid": uid,
            "canonical_name": cname,
            "approved_aliases": approved_aliases,
        })

    # Record links
    projected_links = []
    for link in validated_registry.get("record_links", []):
        rkey = link.get("record_key")
        puid = link.get("player_uid")
        status = link.get("link_status")
        rel = project_identity_state(status)
        sanitized_refs = []
        for ref in link.get("evidence_refs") or []:
            if isinstance(ref, str) and not ref.startswith(("/", "file:", "private:")):
                sanitized_refs.append(ref)
        projected_links.append({
            "record_key": rkey,
            "player_uid": puid,
            "relation": rel,
            "confidence": str(link.get("confidence") or "UNKNOWN").upper(),
            "evidence_refs": sorted(sanitized_refs),
        })

    same_count = sum(1 for l in projected_links if l["relation"] == "SAME")
    not_same_count = sum(1 for l in projected_links if l["relation"] == "NOT_SAME")
    undecided_count = sum(1 for l in projected_links if l["relation"] == "UNDECIDED")

    projection = {
        "schema": CONSUMER_IDENTITY_SCHEMA,
        "schema_version": CONSUMER_IDENTITY_SCHEMA_VERSION,
        "release_id": release_id,
        "product_version": product_version,
        "code_commit": code_commit,
        "as_of": as_of or "2026-09-27T00:00:00Z",
        "private_registry_exposed": False,
        "private_registry_leakage": 0,
        "summary": {
            "total_players": len(projected_players),
            "total_record_links": len(projected_links),
            "same_count": same_count,
            "not_same_count": not_same_count,
            "undecided_count": undecided_count,
        },
        "players": sorted(projected_players, key=lambda p: p["player_uid"]),
        "record_links": sorted(projected_links, key=lambda l: (l["record_key"], l.get("player_uid") or "")),
    }
    projection["projection_sha256"] = digest(canonical_bytes(projection))
    return projection


def validate_player_identity_consumer_projection(
    projection,
    *,
    expected_release_id=None,
    expected_product_version="v1.8.1",
    expected_code_commit=None,
):
    if not isinstance(projection, dict):
        raise ConsumerProjectionError("IDENTITY_PROJECTION_OBJECT_REQUIRED")
    if projection.get("schema") != CONSUMER_IDENTITY_SCHEMA:
        raise ConsumerProjectionError("IDENTITY_PROJECTION_SCHEMA_INVALID")
    if projection.get("schema_version") != CONSUMER_IDENTITY_SCHEMA_VERSION:
        raise ConsumerProjectionError("IDENTITY_PROJECTION_SCHEMA_VERSION_INVALID")
    if projection.get("private_registry_exposed") is not False:
        raise ConsumerProjectionError("PRIVATE_REGISTRY_EXPOSED")
    if projection.get("private_registry_leakage") != 0:
        raise ConsumerProjectionError("PRIVATE_REGISTRY_LEAKAGE")

    release_id = projection.get("release_id")
    if not release_id or not isinstance(release_id, str):
        raise ConsumerProjectionError("IDENTITY_PROJECTION_RELEASE_ID_REQUIRED")
    if expected_release_id is not None and release_id != expected_release_id:
        raise ConsumerProjectionError(
            f"IDENTITY_PROJECTION_RELEASE_MISMATCH:{release_id}!={expected_release_id}"
        )
    product_version = projection.get("product_version")
    if expected_product_version is not None and product_version != expected_product_version:
        raise ConsumerProjectionError(
            f"IDENTITY_PROJECTION_PRODUCT_VERSION_MISMATCH:{product_version}!={expected_product_version}"
        )
    if expected_code_commit is not None and projection.get("code_commit") != expected_code_commit:
        raise ConsumerProjectionError("IDENTITY_PROJECTION_CODE_COMMIT_MISMATCH")

    # Private registry leakage checks
    forbidden_tokens = {"notes", "admin", "redirect_to", "internal_id", "/private/", "/Users/"}
    projection_str = json.dumps(projection)
    for token in forbidden_tokens:
        if f'"{token}"' in projection_str or token in projection_str:
            raise ConsumerProjectionError(f"PRIVATE_REGISTRY_LEAKAGE:{token}")

    stored_sha = projection.get("projection_sha256")
    without_hash = {k: v for k, v in projection.items() if k != "projection_sha256"}
    if stored_sha not in {digest(canonical_bytes(projection)), digest(canonical_bytes(without_hash))}:
        raise ConsumerProjectionError("IDENTITY_PROJECTION_HASH_MISMATCH")

    return {
        "status": "PASS",
        "release_id": release_id,
        "product_version": product_version,
        "players": len(projection.get("players", [])),
        "record_links": len(projection.get("record_links", [])),
        "projection_sha256": stored_sha,
    }


def lookup_player_identity(projection, query):
    from .player_identity import normalize_name
    if projection is None or not isinstance(projection, dict):
        return {
            "status": "CONSUMER_AUTHORITY_UNAVAILABLE",
            "query": query,
            "candidate_count": 0,
            "candidates": [],
            "automatic_link_allowed": False,
        }
    if projection.get("schema") != CONSUMER_IDENTITY_SCHEMA:
        return {
            "status": "CONSUMER_AUTHORITY_UNAVAILABLE",
            "query": query,
            "candidate_count": 0,
            "candidates": [],
            "automatic_link_allowed": False,
        }
    query_norm = normalize_name(query)
    candidates = []
    for player in projection.get("players", []):
        matches = []
        if normalize_name(player["canonical_name"]) == query_norm:
            matches.append({"match_type": "CANONICAL_NAME", "value": player["canonical_name"]})
        for alias in player.get("approved_aliases", []):
            if normalize_name(alias) == query_norm:
                matches.append({"match_type": "ALIAS", "value": alias})
        if matches:
            candidates.append({
                "player_uid": player["player_uid"],
                "canonical_name": player["canonical_name"],
                "matches": matches,
            })
    if not candidates:
        return {
            "status": "NOT_FOUND",
            "query": query,
            "candidate_count": 0,
            "candidates": [],
            "automatic_link_allowed": False,
        }
    if len(candidates) == 1:
        player_uid = candidates[0]["player_uid"]
        links = [
            link for link in projection.get("record_links", [])
            if link.get("player_uid") == player_uid
        ]
        return {
            "status": "SUCCESS",
            "query": query,
            "candidate_count": 1,
            "player": candidates[0],
            "record_links": links,
            "automatic_link_allowed": False,
        }
    return {
        "status": "REVIEW_REQUIRED",
        "query": query,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "automatic_link_allowed": False,
    }


def cross_season_identity_query(projection, query):
    lookup = lookup_player_identity(projection, query)
    if lookup["status"] != "SUCCESS":
        return lookup
    player = lookup["player"]
    links = lookup["record_links"]
    seasons = sorted({
        link["record_key"].split("|")[0]
        for link in links
        if "|" in link.get("record_key", "")
    })
    return {
        "status": "SUCCESS",
        "query": query,
        "player_uid": player["player_uid"],
        "canonical_name": player["canonical_name"],
        "season_count": len(seasons),
        "seasons": seasons,
        "record_links": links,
    }
