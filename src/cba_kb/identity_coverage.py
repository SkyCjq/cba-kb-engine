"""Deterministic private identity-coverage orchestration.

This module never writes the canonical identity registry. It consumes MASTER
rows and the existing Identity v1.8 relation authority to build review-only
candidates, candidate registries, coverage ledgers, and certificates.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict

from .evidence_ledger import canonical_bytes
from .master import HEADERS
from .player_identity import (
    new_registry,
    normalize_name,
    validate_player_uid,
    validate_registry,
)


SCHEMA_VERSION = 1
COVERAGE_LEDGER_VERSION = "v1"
COVERAGE_LEDGER_R2_VERSION = "v2"
REVIEW_PACKET_VERSION = "v1"
REVIEWED_DECISIONS_VERSION = "v1"
COVERAGE_CERTIFICATE_VERSION = "v1"
COVERAGE_CERTIFICATE_R2_VERSION = "v2"

DISPOSITIONS = frozenset({
    "RESOLVED_SAME",
    "UNRESOLVED_CANDIDATES",
    "NO_SAFE_CANDIDATE",
    "SOURCE_EXCEPTION",
})
PROPOSAL_TYPES = frozenset({
    "EXISTING_IDENTITY_CANDIDATE",
    "NEW_IDENTITY_CANDIDATE",
    "NO_SAFE_CANDIDATE",
    "SOURCE_EXCEPTION_CANDIDATE",
})
PROPOSED_RELATIONS = frozenset({
    "PROPOSED_SAME",
    "PROPOSED_SEPARATE",
    "KEEP_UNDECIDED",
    "NO_SAFE_CANDIDATE",
    "SOURCE_EXCEPTION",
})
DECISIONS = frozenset({"APPROVE", "REJECT", "UNDECIDED"})
MASTER_AUTHORITY_MODES = frozenset({
    "FILE_SHA256_VERIFIED",
    "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA",
})
RELATION_COMPATIBILITY = {
    "EXISTING_IDENTITY_CANDIDATE": frozenset({
        "PROPOSED_SAME",
        "PROPOSED_SEPARATE",
        "KEEP_UNDECIDED",
    }),
    "NEW_IDENTITY_CANDIDATE": frozenset({
        "PROPOSED_SAME",
        "KEEP_UNDECIDED",
    }),
    "NO_SAFE_CANDIDATE": frozenset({"NO_SAFE_CANDIDATE"}),
    "SOURCE_EXCEPTION_CANDIDATE": frozenset({"SOURCE_EXCEPTION"}),
}
MACHINE_REVIEW_FIELDS = (
    "review_id",
    "record_key",
    "proposal_type",
    "candidate_group_id",
    "candidate_player_uid",
    "candidate_name_display_only",
    "proposed_relation",
    "machine_suggestion",
    "machine_reason",
    "evidence_refs",
)
HUMAN_REVIEW_FIELDS = (
    "human_decision",
    "human_note",
    "approved_player_uid",
    "approved_canonical_name",
    "source_exception_reason",
    "reviewed_at",
)
REVIEW_FIELDS = MACHINE_REVIEW_FIELDS + HUMAN_REVIEW_FIELDS
CANDIDATE_REGISTRY_MANIFEST_FIELDS = (
    "schema_version",
    "base_registry_sha256",
    "candidate_registry_sha256",
    "MASTER_sha256",
    "master_authority_mode",
    "MASTER_rows",
    "MASTER_unique_record_keys",
    "review_packet_sha256",
    "reviewed_decisions_sha256",
    "created_at",
    "candidate_registry_manifest_sha256",
)
COVERAGE_LEDGER_ENTRY_FIELDS = frozenset({
    "record_key",
    "coverage_disposition",
    "same_count",
    "undecided_count",
    "not_same_count",
    "candidate_count",
    "review_required",
    "source_exception_reason",
    "evidence_refs",
})
R2_COVERAGE_LEDGER_ENTRY_FIELDS = (
    COVERAGE_LEDGER_ENTRY_FIELDS
    | {
        "evidence_tier",
        "provenance_status",
        "recheck_allowed",
        "identity_authority_effect",
    }
)
EVIDENCE_TIERS = frozenset({
    "AUDITED_AUTHORITY",
    "VERIFIED_SOURCE_EVIDENCE",
    "BEST_EFFORT_NEGATIVE",
})
PROVENANCE_STATUSES = frozenset({"COMPLETE", "PARTIAL"})
IDENTITY_AUTHORITY_EFFECTS = frozenset({
    "EXISTING_AUTHORITY",
    "NONE_UNTIL_HUMAN_DECISION",
    "NONE",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class IdentityCoverageError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise IdentityCoverageError(f"{label}_REQUIRED")
    return value.strip()


def _optional_text(value, label):
    if value is None:
        return None
    return _required_text(value, label)


def _required_sha256_or_null(value, label):
    if value is None:
        return None
    value = _required_text(value, label)
    if not _SHA256.fullmatch(value):
        raise IdentityCoverageError(f"{label}_INVALID")
    return value


def _required_sha256(value, label):
    value = _required_sha256_or_null(value, label)
    if value is None:
        raise IdentityCoverageError(f"{label}_REQUIRED")
    return value


def _evidence_refs(value, label):
    if not isinstance(value, list):
        raise IdentityCoverageError(f"{label}_LIST_REQUIRED")
    result = []
    for item in value:
        result.append(_required_text(item, f"{label}_ITEM"))
    if result != sorted(set(result)):
        raise IdentityCoverageError(f"{label}_ORDER_OR_DUPLICATE_INVALID")
    return result


def normalize_semantic_field(value):
    text = unicodedata.normalize("NFKC", _required_text(value, "SEMANTIC_FIELD"))
    return " ".join(text.strip().split())


def _validated_rows(rows):
    if not isinstance(rows, list):
        raise IdentityCoverageError("MASTER_ROWS_REQUIRED")
    result = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise IdentityCoverageError(f"MASTER_ROW_OBJECT_REQUIRED:{index}")
        missing = [key for key in HEADERS if key not in row]
        if missing:
            raise IdentityCoverageError(f"MASTER_ROW_SCHEMA_INVALID:{index}")
        record_key = row.get("record_key")
        if not isinstance(record_key, str) or not record_key:
            raise IdentityCoverageError(f"MASTER_RECORD_KEY_REQUIRED:{index}")
        if record_key in seen:
            raise IdentityCoverageError("MASTER_RECORD_KEY_DUPLICATE")
        seen.add(record_key)
        result.append({key: row[key] for key in HEADERS})
    return result


def _record_key(row):
    return row["record_key"]


def _link_status(links, status):
    return [item for item in links if item["link_status"] == status]


def _disposition_from_links(links):
    if len(_link_status(links, "same")) == 1:
        return "RESOLVED_SAME"
    if _link_status(links, "undecided"):
        return "UNRESOLVED_CANDIDATES"
    return "NO_SAFE_CANDIDATE"


def _add_hash(core, field):
    return {
        **core,
        field: hashlib.sha256(canonical_bytes(core)).hexdigest(),
    }


def build_coverage_inventory(master_rows, identity_registry):
    rows = _validated_rows(master_rows)
    registry = validate_registry(identity_registry)
    links_by_record = defaultdict(list)
    for link in registry["record_links"]:
        links_by_record[link["record_key"]].append(link)

    unknown = sorted(set(links_by_record) - {row["record_key"] for row in rows})
    multiple_same = sum(
        len(_link_status(links, "same")) > 1
        for links in links_by_record.values()
    )
    entries = []
    counts = {disposition: 0 for disposition in sorted(DISPOSITIONS)}
    for row in sorted(rows, key=_record_key):
        record_key = row["record_key"]
        links = links_by_record.get(record_key, [])
        disposition = _disposition_from_links(links)
        counts[disposition] += 1
        entries.append({
            "record_key": record_key,
            "same_count": len(_link_status(links, "same")),
            "undecided_count": len(_link_status(links, "undecided")),
            "not_same_count": len(_link_status(links, "not_same")),
            "coverage_disposition": disposition,
            "candidate_count": 0,
            "review_required": disposition != "RESOLVED_SAME",
            "source_exception_reason": None,
            "evidence_refs": [],
        })
    core = {
        "schema_version": SCHEMA_VERSION,
        "coverage_ledger_version": COVERAGE_LEDGER_VERSION,
        "identity_registry_sha256": registry["registry_sha256"],
        "MASTER_rows": len(rows),
        "MASTER_unique_record_keys": len({row["record_key"] for row in rows}),
        "registry_linked_unique_record_keys": len(set(links_by_record)),
        "unknown_registry_record_keys": unknown,
        "multiple_same_count": multiple_same,
        "disposition_counts": counts,
        "entries": entries,
    }
    return _add_hash(core, "coverage_inventory_sha256")


def _candidate_hint(record_key, hints):
    if hints is None:
        return None
    hint = hints.get(record_key)
    if hint is None:
        return None
    if not isinstance(hint, dict):
        raise IdentityCoverageError("CANDIDATE_HINT_OBJECT_REQUIRED")
    return hint


def _machine_proposal(
    *,
    record_key,
    proposal_type,
    proposed_relation,
    candidate_group_id=None,
    candidate_player_uid=None,
    candidate_name_display_only=None,
    machine_suggestion,
    machine_reason,
    evidence_refs,
):
    proposal = {
        "record_key": record_key,
        "proposal_type": proposal_type,
        "candidate_group_id": candidate_group_id,
        "candidate_player_uid": candidate_player_uid,
        "candidate_name_display_only": candidate_name_display_only,
        "proposed_relation": proposed_relation,
        "machine_suggestion": machine_suggestion,
        "machine_reason": machine_reason,
        "evidence_refs": sorted(set(evidence_refs)),
    }
    validate_machine_proposal(proposal)
    return proposal


def validate_machine_proposal(proposal):
    if not isinstance(proposal, dict):
        raise IdentityCoverageError("PROPOSAL_OBJECT_REQUIRED")
    allowed = set(MACHINE_REVIEW_FIELDS) - {"review_id"}
    if set(proposal) != allowed:
        raise IdentityCoverageError("PROPOSAL_SCHEMA_INVALID")
    if proposal["proposal_type"] not in PROPOSAL_TYPES:
        raise IdentityCoverageError("PROPOSAL_TYPE_INVALID")
    relation = proposal["proposed_relation"]
    if relation not in PROPOSED_RELATIONS:
        raise IdentityCoverageError("PROPOSED_RELATION_INVALID")
    if relation not in RELATION_COMPATIBILITY[proposal["proposal_type"]]:
        raise IdentityCoverageError("PROPOSAL_RELATION_INCOMPATIBLE")
    _required_text(proposal["record_key"], "RECORD_KEY")
    if proposal["proposal_type"] == "EXISTING_IDENTITY_CANDIDATE":
        validate_player_uid(proposal["candidate_player_uid"])
    else:
        if proposal["candidate_player_uid"] is not None:
            raise IdentityCoverageError("CANDIDATE_PLAYER_UID_FORBIDDEN")
    if proposal["candidate_group_id"] is not None:
        _required_text(proposal["candidate_group_id"], "CANDIDATE_GROUP_ID")
        if proposal["candidate_group_id"] == proposal["candidate_player_uid"]:
            raise IdentityCoverageError("CANDIDATE_GROUP_UID_FORBIDDEN")
    if proposal["candidate_name_display_only"] is not None:
        _required_text(
            proposal["candidate_name_display_only"],
            "CANDIDATE_NAME_DISPLAY_ONLY",
        )
    _required_text(proposal["machine_suggestion"], "MACHINE_SUGGESTION")
    _required_text(proposal["machine_reason"], "MACHINE_REASON")
    if not isinstance(proposal["evidence_refs"], list):
        raise IdentityCoverageError("EVIDENCE_REFS_REQUIRED")
    if proposal["evidence_refs"] != sorted(set(proposal["evidence_refs"])):
        raise IdentityCoverageError("EVIDENCE_REFS_ORDER_INVALID")
    return proposal


def generate_candidate_proposals(
    master_rows,
    identity_registry,
    *,
    candidate_hints=None,
):
    rows = _validated_rows(master_rows)
    registry = validate_registry(identity_registry)
    active = {
        item["player_uid"]: item
        for item in registry["players"]
        if item["status"] == "ACTIVE"
    }
    matches_by_name = defaultdict(set)
    for player in active.values():
        matches_by_name[normalize_name(player["canonical_name"])].add(
            player["player_uid"],
        )
    for alias in registry["aliases"]:
        if alias["player_uid"] in active:
            matches_by_name[normalize_name(alias["alias"])].add(
                alias["player_uid"],
            )
    existing_status = defaultdict(set)
    existing_pair_status = {}
    for link in registry["record_links"]:
        existing_status[link["record_key"]].add(link["link_status"])
        existing_pair_status[
            (link["record_key"], link["player_uid"])
        ] = link["link_status"]

    proposals = []
    for row in sorted(rows, key=_record_key):
        record_key = row["record_key"]
        statuses = existing_status.get(record_key, set())
        if "same" in statuses:
            continue
        hint = _candidate_hint(record_key, candidate_hints)
        if hint is not None:
            proposal_type = hint.get("proposal_type")
            if proposal_type not in {
                "NEW_IDENTITY_CANDIDATE",
                "NO_SAFE_CANDIDATE",
                "SOURCE_EXCEPTION_CANDIDATE",
            }:
                raise IdentityCoverageError("CANDIDATE_HINT_TYPE_INVALID")
            evidence_refs = hint.get("evidence_refs", [])
            if not isinstance(evidence_refs, list):
                raise IdentityCoverageError("CANDIDATE_HINT_EVIDENCE_REQUIRED")
            relation = {
                "NEW_IDENTITY_CANDIDATE": hint.get(
                    "proposed_relation",
                    "KEEP_UNDECIDED",
                ),
                "NO_SAFE_CANDIDATE": "NO_SAFE_CANDIDATE",
                "SOURCE_EXCEPTION_CANDIDATE": "SOURCE_EXCEPTION",
            }[proposal_type]
            proposals.append(_machine_proposal(
                record_key=record_key,
                proposal_type=proposal_type,
                proposed_relation=relation,
                candidate_group_id=hint.get("candidate_group_id"),
                candidate_name_display_only=hint.get(
                    "candidate_name_display_only",
                    row.get("player"),
                ),
                machine_suggestion=relation,
                machine_reason=hint.get(
                    "machine_reason",
                    f"hint:{proposal_type}",
                ),
                evidence_refs=evidence_refs,
            ))
            continue

        matches = set(matches_by_name.get(
            normalize_name(row.get("player") or ""),
            set(),
        ))
        matches = {
            player_uid for player_uid in matches
            if existing_pair_status.get(
                (record_key, player_uid),
            ) != "not_same"
        }
        if matches:
            for player_uid in sorted(matches):
                proposals.append(_machine_proposal(
                    record_key=record_key,
                    proposal_type="EXISTING_IDENTITY_CANDIDATE",
                    proposed_relation="KEEP_UNDECIDED",
                    candidate_player_uid=player_uid,
                    candidate_name_display_only=active[player_uid][
                        "canonical_name"
                    ],
                    machine_suggestion="KEEP_UNDECIDED",
                    machine_reason="exact_name_or_alias_requires_review",
                    evidence_refs=[],
                ))
        else:
            proposals.append(_machine_proposal(
                record_key=record_key,
                proposal_type="NO_SAFE_CANDIDATE",
                proposed_relation="NO_SAFE_CANDIDATE",
                machine_suggestion="NO_SAFE_CANDIDATE",
                machine_reason="no_safe_candidate",
                evidence_refs=[],
            ))
    return proposals


def prepare_review_packet(proposals):
    if not isinstance(proposals, list):
        raise IdentityCoverageError("PROPOSALS_REQUIRED")
    rows = []
    for proposal in proposals:
        validate_machine_proposal(proposal)
        review_id = "review_" + hashlib.sha256(
            canonical_bytes(proposal),
        ).hexdigest()[:24]
        rows.append({
            "review_id": review_id,
            **proposal,
            **{field: None for field in HUMAN_REVIEW_FIELDS},
        })
    rows.sort(key=lambda item: (
        item["record_key"],
        item["proposal_type"],
        item["candidate_group_id"] or "",
        item["candidate_player_uid"] or "",
        item["review_id"],
    ))
    review_ids = [item["review_id"] for item in rows]
    if len(review_ids) != len(set(review_ids)):
        raise IdentityCoverageError("REVIEW_ID_DUPLICATE")
    core = {
        "schema_version": SCHEMA_VERSION,
        "review_packet_version": REVIEW_PACKET_VERSION,
        "reviews": rows,
    }
    return _add_hash(core, "review_packet_sha256")


def review_packet_to_csv(packet):
    validate_review_packet(packet)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(REVIEW_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    for row in packet["reviews"]:
        output = {}
        for field in REVIEW_FIELDS:
            value = row.get(field)
            if field == "evidence_refs":
                value = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            output[field] = "" if value is None else value
        writer.writerow(output)
    return buffer.getvalue().encode("utf-8")


def _csv_field(value):
    return None if value == "" else value


def validate_review_packet(packet):
    if not isinstance(packet, dict):
        raise IdentityCoverageError("REVIEW_PACKET_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "review_packet_version",
        "reviews",
        "review_packet_sha256",
    }
    if set(packet) != required:
        raise IdentityCoverageError("REVIEW_PACKET_SCHEMA_INVALID")
    if packet["schema_version"] != SCHEMA_VERSION:
        raise IdentityCoverageError("REVIEW_PACKET_VERSION_INVALID")
    if packet["review_packet_version"] != REVIEW_PACKET_VERSION:
        raise IdentityCoverageError("REVIEW_PACKET_VERSION_INVALID")
    if not isinstance(packet["reviews"], list):
        raise IdentityCoverageError("REVIEW_PACKET_REVIEWS_REQUIRED")
    for row in packet["reviews"]:
        if not isinstance(row, dict) or set(row) != set(REVIEW_FIELDS):
            raise IdentityCoverageError("REVIEW_ROW_SCHEMA_INVALID")
        _required_text(row["review_id"], "REVIEW_ID")
        machine = {
            key: row[key]
            for key in MACHINE_REVIEW_FIELDS
            if key != "review_id"
        }
        validate_machine_proposal(machine)
    core = {
        key: packet[key]
        for key in packet
        if key != "review_packet_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != packet[
        "review_packet_sha256"
    ]:
        raise IdentityCoverageError("REVIEW_PACKET_HASH_MISMATCH")
    return packet


def _validate_group_consistency(rows):
    groups = defaultdict(dict)
    for row in rows:
        if (
            row["proposal_type"] == "NEW_IDENTITY_CANDIDATE"
            and row["human_decision"] == "APPROVE"
            and row["proposed_relation"] == "PROPOSED_SAME"
        ):
            group_id = _required_text(
                row["candidate_group_id"],
                "CANDIDATE_GROUP_ID",
            )
            values = {
                "approved_player_uid": _required_text(
                    row["approved_player_uid"],
                    "APPROVED_PLAYER_UID",
                ),
                "approved_canonical_name": _required_text(
                    row["approved_canonical_name"],
                    "APPROVED_CANONICAL_NAME",
                ),
            }
            current = groups.get(group_id)
            if current is not None and current != values:
                raise IdentityCoverageError(
                    "NEW_IDENTITY_GROUP_AUTHORITY_CONFLICT",
                )
            groups[group_id] = values


def _validate_decision_semantics(row):
    if not isinstance(row, dict) or set(row) != set(REVIEW_FIELDS):
        raise IdentityCoverageError("REVIEW_DECISION_ROW_SCHEMA_INVALID")
    _required_text(row["review_id"], "REVIEW_ID")
    machine = {
        key: row[key]
        for key in MACHINE_REVIEW_FIELDS
        if key != "review_id"
    }
    validate_machine_proposal(machine)
    decision = row["human_decision"]
    if decision not in DECISIONS:
        raise IdentityCoverageError("HUMAN_DECISION_REQUIRED")
    _required_text(row["reviewed_at"], "REVIEWED_AT")
    proposal_type = row["proposal_type"]
    relation = row["proposed_relation"]
    reason = row["source_exception_reason"]
    if proposal_type == "SOURCE_EXCEPTION_CANDIDATE":
        if decision == "APPROVE":
            _required_text(reason, "SOURCE_EXCEPTION_REASON")
            if not row["evidence_refs"]:
                raise IdentityCoverageError(
                    "SOURCE_EXCEPTION_EVIDENCE_REQUIRED",
                )
        elif reason is not None:
            raise IdentityCoverageError(
                "SOURCE_EXCEPTION_REASON_FORBIDDEN",
            )
    elif reason is not None:
        raise IdentityCoverageError("SOURCE_EXCEPTION_REASON_FORBIDDEN")

    is_new_same = (
        proposal_type == "NEW_IDENTITY_CANDIDATE"
        and decision == "APPROVE"
        and relation == "PROPOSED_SAME"
    )
    if is_new_same:
        validate_player_uid(row["approved_player_uid"])
        _required_text(
            row["approved_canonical_name"],
            "APPROVED_CANONICAL_NAME",
        )
        _required_text(row["human_note"], "UID_ALLOCATION_ATTESTATION")
        if not row["evidence_refs"]:
            raise IdentityCoverageError(
                "NEW_IDENTITY_EVIDENCE_REQUIRED",
            )
    elif (
        row["approved_player_uid"] is not None
        or row["approved_canonical_name"] is not None
    ):
        raise IdentityCoverageError("APPROVED_IDENTITY_FIELDS_FORBIDDEN")
    return row


def validate_reviewed_csv(packet, csv_bytes):
    validate_review_packet(packet)
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityCoverageError("REVIEW_CSV_UTF8_REQUIRED") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != list(REVIEW_FIELDS):
        raise IdentityCoverageError("REVIEW_CSV_COLUMNS_INVALID")
    by_id = {row["review_id"]: row for row in packet["reviews"]}
    decisions = []
    seen = set()
    for raw in reader:
        review_id = _csv_field(raw["review_id"])
        if review_id is None or review_id not in by_id:
            raise IdentityCoverageError("REVIEW_CSV_UNKNOWN_REVIEW_ID")
        if review_id in seen:
            raise IdentityCoverageError("REVIEW_CSV_DUPLICATE_REVIEW_ID")
        seen.add(review_id)
        authority = by_id[review_id]
        for field in MACHINE_REVIEW_FIELDS:
            expected = authority[field]
            if field == "evidence_refs":
                try:
                    actual = json.loads(_csv_field(raw[field]) or "[]")
                except ValueError as exc:
                    raise IdentityCoverageError(
                        "REVIEW_CSV_EVIDENCE_REFS_INVALID",
                    ) from exc
            else:
                actual = _csv_field(raw[field])
            if actual != expected:
                raise IdentityCoverageError(
                    f"REVIEW_CSV_MACHINE_FIELD_TAMPER:{field}",
                )
        row = {
            field: _csv_field(raw[field])
            for field in REVIEW_FIELDS
        }
        row["evidence_refs"] = json.loads(row["evidence_refs"] or "[]")
        _validate_decision_semantics(row)
        decisions.append(row)
    if seen != set(by_id):
        raise IdentityCoverageError("REVIEW_CSV_MISSING_DECISIONS")
    _validate_group_consistency(decisions)
    core = {
        "schema_version": SCHEMA_VERSION,
        "reviewed_decisions_version": REVIEWED_DECISIONS_VERSION,
        "review_packet_sha256": packet["review_packet_sha256"],
        "decisions": decisions,
    }
    return _add_hash(core, "reviewed_decisions_sha256")


def validate_reviewed_decisions(value):
    if not isinstance(value, dict):
        raise IdentityCoverageError("REVIEWED_DECISIONS_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "reviewed_decisions_version",
        "review_packet_sha256",
        "decisions",
        "reviewed_decisions_sha256",
    }
    if set(value) != required:
        raise IdentityCoverageError("REVIEWED_DECISIONS_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise IdentityCoverageError("REVIEWED_DECISIONS_VERSION_INVALID")
    if value["reviewed_decisions_version"] != REVIEWED_DECISIONS_VERSION:
        raise IdentityCoverageError("REVIEWED_DECISIONS_VERSION_INVALID")
    _required_sha256(
        value["review_packet_sha256"],
        "REVIEW_PACKET_SHA256",
    )
    if not isinstance(value["decisions"], list):
        raise IdentityCoverageError("REVIEWED_DECISIONS_LIST_REQUIRED")
    for row in value["decisions"]:
        _validate_decision_semantics(row)
    core = {
        key: value[key]
        for key in value
        if key != "reviewed_decisions_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "reviewed_decisions_sha256"
    ]:
        raise IdentityCoverageError("REVIEWED_DECISIONS_HASH_MISMATCH")
    _validate_group_consistency(value["decisions"])
    return value


def _semantic_fields(row):
    values = [
        row.get("player"),
        row.get("record_key"),
        row.get("club_official"),
        row.get("club_id"),
        row.get("season"),
    ]
    return [
        normalize_semantic_field(value)
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def validate_candidate_uid(player_uid, semantic_fields, attestation):
    if not attestation:
        raise IdentityCoverageError("UID_ALLOCATION_ATTESTATION_REQUIRED")
    normalized = [normalize_semantic_field(value) for value in semantic_fields]
    candidate = _required_text(player_uid, "PLAYER_UID")
    if candidate in normalized:
        raise IdentityCoverageError("UID_SEMANTIC_EQUALITY_FORBIDDEN")
    for value in normalized:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if candidate == digest:
            raise IdentityCoverageError("UID_SEMANTIC_HASH_FORBIDDEN")
    return validate_player_uid(candidate)


def _proposal_index(packet):
    return {row["review_id"]: row for row in packet["reviews"]}


def _replace_link(links, record_key, player_uid, status, evidence_refs):
    result = [
        link for link in links
        if (link["record_key"], link["player_uid"]) != (record_key, player_uid)
    ]
    result.append({
        "schema_version": SCHEMA_VERSION,
        "record_key": record_key,
        "player_uid": player_uid,
        "link_status": status,
        "method": "MANUAL_REVIEW",
        "confidence": "HIGH",
        "evidence_refs": sorted(set(evidence_refs)) or [
            "reviewed-decision",
        ],
    })
    return result


def apply_reviewed_decisions(
    master_rows,
    base_registry,
    review_packet,
    reviewed_decisions,
):
    rows = _validated_rows(master_rows)
    registry = validate_registry(base_registry)
    validate_review_packet(review_packet)
    reviewed = validate_reviewed_decisions(reviewed_decisions)
    if reviewed["review_packet_sha256"] != review_packet[
        "review_packet_sha256"
    ]:
        raise IdentityCoverageError("REVIEW_AUTHORITY_MISMATCH")

    proposals = _proposal_index(review_packet)
    players = list(registry["players"])
    aliases = list(registry["aliases"])
    links = list(registry["record_links"])
    by_uid = {item["player_uid"]: item for item in players}
    base_uids = set(by_uid)
    row_by_key = {row["record_key"]: row for row in rows}
    created_groups = {}
    allocated_uid_groups = {}

    for decision in reviewed["decisions"]:
        review_id = decision["review_id"]
        proposal = proposals.get(review_id)
        if proposal is None:
            raise IdentityCoverageError("REVIEW_DECISION_UNKNOWN")
        for field in MACHINE_REVIEW_FIELDS:
            if decision[field] != proposal[field]:
                raise IdentityCoverageError("REVIEW_DECISION_PROPOSAL_MISMATCH")
        if decision["human_decision"] in {"REJECT", "UNDECIDED"}:
            continue
        if decision["human_decision"] != "APPROVE":
            raise IdentityCoverageError("HUMAN_DECISION_INVALID")
        relation = proposal["proposed_relation"]
        record_key = proposal["record_key"]
        if proposal["proposal_type"] == "SOURCE_EXCEPTION_CANDIDATE":
            if relation != "SOURCE_EXCEPTION" or not decision[
                "source_exception_reason"
            ]:
                raise IdentityCoverageError("SOURCE_EXCEPTION_REASON_REQUIRED")
            continue
        if proposal["proposal_type"] == "NO_SAFE_CANDIDATE":
            if relation != "NO_SAFE_CANDIDATE":
                raise IdentityCoverageError("PROPOSAL_RELATION_INCOMPATIBLE")
            continue
        if relation == "KEEP_UNDECIDED":
            continue
        if relation not in {"PROPOSED_SAME", "PROPOSED_SEPARATE"}:
            raise IdentityCoverageError("PROPOSED_RELATION_INVALID")

        if proposal["proposal_type"] == "EXISTING_IDENTITY_CANDIDATE":
            player_uid = validate_player_uid(proposal["candidate_player_uid"])
        else:
            group_id = _required_text(
                proposal["candidate_group_id"],
                "CANDIDATE_GROUP_ID",
            )
            approved_uid = validate_candidate_uid(
                decision["approved_player_uid"],
                _semantic_fields(row_by_key[record_key]),
                decision["human_note"],
            )
            approved_name = _required_text(
                decision["approved_canonical_name"],
                "APPROVED_CANONICAL_NAME",
            )
            current = created_groups.get(group_id)
            values = {
                "approved_player_uid": approved_uid,
                "approved_canonical_name": approved_name,
            }
            if current is not None and current != values:
                raise IdentityCoverageError(
                    "NEW_IDENTITY_GROUP_AUTHORITY_CONFLICT",
                )
            if current is None and approved_uid in base_uids:
                raise IdentityCoverageError(
                    "NEW_IDENTITY_UID_ALREADY_EXISTS",
                )
            owner = allocated_uid_groups.get(approved_uid)
            if owner is not None and owner != group_id:
                raise IdentityCoverageError(
                    "NEW_IDENTITY_UID_GROUP_CONFLICT",
                )
            allocated_uid_groups[approved_uid] = group_id
            created_groups[group_id] = values
            if approved_uid not in by_uid:
                players.append({
                    "schema_version": SCHEMA_VERSION,
                    "player_uid": approved_uid,
                    "canonical_name": approved_name,
                    "status": "ACTIVE",
                    "redirect_to": None,
                })
                by_uid[approved_uid] = players[-1]
            player_uid = approved_uid

        status = "same" if relation == "PROPOSED_SAME" else "not_same"
        if status == "same":
            existing_same = [
                link for link in links
                if link["record_key"] == record_key
                and link["link_status"] == "same"
                and link["player_uid"] != player_uid
            ]
            if existing_same:
                raise IdentityCoverageError(
                    "EXISTING_IDENTITY_AUTHORITY_CONFLICT",
                )
        links = _replace_link(
            links,
            record_key,
            player_uid,
            status,
            decision["evidence_refs"],
        )

    candidate_registry = new_registry(
        players,
        aliases=aliases,
        record_links=links,
    )
    return candidate_registry, created_groups


def build_candidate_registry_manifest(
    *,
    base_registry_sha256,
    candidate_registry_sha256,
    master_sha256,
    master_authority_mode,
    master_rows,
    master_unique_record_keys,
    review_packet_sha256,
    reviewed_decisions_sha256,
    created_at,
):
    base_registry_sha256 = _required_sha256(
        base_registry_sha256,
        "BASE_REGISTRY_SHA256",
    )
    candidate_registry_sha256 = _required_sha256(
        candidate_registry_sha256,
        "CANDIDATE_REGISTRY_SHA256",
    )
    master_sha256 = _required_sha256_or_null(
        master_sha256,
        "MASTER_SHA256",
    )
    if master_authority_mode not in MASTER_AUTHORITY_MODES:
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_INVALID")
    if master_sha256 is None:
        if master_authority_mode != (
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ):
            raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    elif master_authority_mode != "FILE_SHA256_VERIFIED":
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    if not isinstance(master_rows, int) or master_rows < 0:
        raise IdentityCoverageError("MASTER_ROWS_INVALID")
    if not isinstance(master_unique_record_keys, int) or (
        master_unique_record_keys < 0
    ):
        raise IdentityCoverageError("MASTER_UNIQUE_RECORD_KEYS_INVALID")
    core = {
        "schema_version": SCHEMA_VERSION,
        "base_registry_sha256": base_registry_sha256,
        "candidate_registry_sha256": candidate_registry_sha256,
        "MASTER_sha256": master_sha256,
        "master_authority_mode": master_authority_mode,
        "MASTER_rows": master_rows,
        "MASTER_unique_record_keys": master_unique_record_keys,
        "review_packet_sha256": _required_sha256(
            review_packet_sha256,
            "REVIEW_PACKET_SHA256",
        ),
        "reviewed_decisions_sha256": _required_sha256(
            reviewed_decisions_sha256,
            "REVIEWED_DECISIONS_SHA256",
        ),
        "created_at": _required_text(created_at, "CREATED_AT"),
    }
    return _add_hash(core, "candidate_registry_manifest_sha256")


def validate_candidate_registry_manifest(value):
    if not isinstance(value, dict):
        raise IdentityCoverageError("CANDIDATE_MANIFEST_OBJECT_REQUIRED")
    if set(value) != set(CANDIDATE_REGISTRY_MANIFEST_FIELDS):
        raise IdentityCoverageError("CANDIDATE_MANIFEST_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise IdentityCoverageError("CANDIDATE_MANIFEST_VERSION_INVALID")
    for field in (
        "base_registry_sha256",
        "candidate_registry_sha256",
        "review_packet_sha256",
        "reviewed_decisions_sha256",
        "candidate_registry_manifest_sha256",
    ):
        _required_sha256(value[field], field.upper())
    master_sha = _required_sha256_or_null(
        value["MASTER_sha256"],
        "MASTER_SHA256",
    )
    mode = value["master_authority_mode"]
    if mode not in MASTER_AUTHORITY_MODES:
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_INVALID")
    if master_sha is None:
        if mode != "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA":
            raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    elif mode != "FILE_SHA256_VERIFIED":
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    for field in ("MASTER_rows", "MASTER_unique_record_keys"):
        if not isinstance(value[field], int) or value[field] < 0:
            raise IdentityCoverageError(f"{field}_INVALID")
    _required_text(value["created_at"], "CREATED_AT")
    core = {
        key: value[key]
        for key in value
        if key != "candidate_registry_manifest_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "candidate_registry_manifest_sha256"
    ]:
        raise IdentityCoverageError("CANDIDATE_MANIFEST_HASH_MISMATCH")
    return value


def _coverage_disposition(
    same_count,
    undecided_count,
    record_decisions,
):
    """Derive coverage disposition without R2 or provenance inputs."""
    approved = [
        item for item in record_decisions
        if item["human_decision"] == "APPROVE"
    ]
    if same_count == 1:
        disposition = "RESOLVED_SAME"
        review_required = False
    elif any(
        item["proposal_type"] == "SOURCE_EXCEPTION_CANDIDATE"
        for item in approved
    ):
        disposition = "SOURCE_EXCEPTION"
        review_required = False
    elif undecided_count or any(
        item["human_decision"] in {"APPROVE", "UNDECIDED"}
        and item["proposed_relation"] == "KEEP_UNDECIDED"
        for item in record_decisions
    ):
        disposition = "UNRESOLVED_CANDIDATES"
        review_required = not record_decisions
    elif any(
        item["proposal_type"] == "NO_SAFE_CANDIDATE"
        for item in approved
    ) or (
        same_count == 0 and undecided_count == 0 and record_decisions
    ):
        disposition = "NO_SAFE_CANDIDATE"
        review_required = not record_decisions
    else:
        disposition = "NO_SAFE_CANDIDATE"
        review_required = True
    if record_decisions and all(
        item["human_decision"] == "REJECT"
        for item in record_decisions
    ):
        review_required = True
    return disposition, review_required


def build_coverage_ledger(
    master_rows,
    registry,
    review_packet,
    reviewed_decisions,
    *,
    r2=False,
    provenance_overlay=None,
):
    rows = _validated_rows(master_rows)
    registry = validate_registry(registry)
    reviewed = validate_reviewed_decisions(reviewed_decisions)
    if provenance_overlay is not None and not r2:
        raise IdentityCoverageError("R2_OVERLAY_REQUIRES_R2")
    proposals = _proposal_index(review_packet)
    decisions_by_record = defaultdict(list)
    proposals_by_record = defaultdict(list)
    for decision in reviewed["decisions"]:
        decisions_by_record[decision["record_key"]].append(decision)
    for proposal in review_packet["reviews"]:
        proposals_by_record[proposal["record_key"]].append(proposal)
    links_by_record = defaultdict(list)
    for link in registry["record_links"]:
        links_by_record[link["record_key"]].append(link)
    overlay_by_record = _validate_r2_provenance_overlay(
        provenance_overlay,
        proposals_by_record,
    ) if provenance_overlay is not None else {}

    entries = []
    for row in sorted(rows, key=_record_key):
        record_key = row["record_key"]
        links = links_by_record.get(record_key, [])
        same_count = len(_link_status(links, "same"))
        undecided_count = len(_link_status(links, "undecided"))
        not_same_count = len(_link_status(links, "not_same"))
        record_decisions = decisions_by_record.get(record_key, [])
        proposals_for_record = proposals_by_record.get(record_key, [])
        approved = [
            item for item in record_decisions
            if item["human_decision"] == "APPROVE"
        ]
        source_reason = next((
            item["source_exception_reason"]
            for item in approved
            if item["proposal_type"] == "SOURCE_EXCEPTION_CANDIDATE"
        ), None)
        disposition, review_required = _coverage_disposition(
            same_count,
            undecided_count,
            record_decisions,
        )
        candidate_uids = {
            item["candidate_player_uid"]
            for item in proposals_for_record
            if item["candidate_player_uid"] is not None
        }
        candidate_groups = {
            item["candidate_group_id"]
            for item in proposals_for_record
            if (
                item["proposal_type"] == "NEW_IDENTITY_CANDIDATE"
                and item["candidate_group_id"] is not None
            )
        }
        evidence_refs = sorted({
            ref
            for item in record_decisions
            for ref in item["evidence_refs"]
        })
        if r2 and disposition == "UNRESOLVED_CANDIDATES":
            evidence_refs = sorted(
                set(evidence_refs)
                | {
                    ref
                    for item in proposals_for_record
                    for ref in item["evidence_refs"]
                }
            )
        if record_key in overlay_by_record:
            if disposition != "UNRESOLVED_CANDIDATES":
                raise IdentityCoverageError(
                    "R2_OVERLAY_FINAL_DISPOSITION_FORBIDDEN",
                )
            evidence_refs = sorted(
                set(evidence_refs)
                | set(overlay_by_record[record_key]["existing_evidence_refs"])
            )
        entry = {
            "record_key": record_key,
            "coverage_disposition": disposition,
            "same_count": same_count,
            "undecided_count": undecided_count,
            "not_same_count": not_same_count,
            "candidate_count": len(candidate_uids) + len(candidate_groups),
            "review_required": review_required,
            "source_exception_reason": source_reason,
            "evidence_refs": evidence_refs,
        }
        if r2:
            if disposition == "RESOLVED_SAME":
                entry.update({
                    "evidence_tier": "AUDITED_AUTHORITY",
                    "provenance_status": "COMPLETE",
                    "recheck_allowed": False,
                    "identity_authority_effect": "EXISTING_AUTHORITY",
                })
            elif disposition == "UNRESOLVED_CANDIDATES":
                if not evidence_refs:
                    raise IdentityCoverageError(
                        "R2_UNRESOLVED_EVIDENCE_REQUIRED",
                    )
                entry.update({
                    "evidence_tier": "VERIFIED_SOURCE_EVIDENCE",
                    "provenance_status": (
                        overlay_by_record[record_key]["provenance_status"]
                        if record_key in overlay_by_record else "COMPLETE"
                    ),
                    "recheck_allowed": True,
                    "identity_authority_effect": (
                        "NONE_UNTIL_HUMAN_DECISION"
                    ),
                })
            else:
                entry.update({
                    "evidence_tier": "BEST_EFFORT_NEGATIVE",
                    "provenance_status": (
                        "COMPLETE" if evidence_refs else "PARTIAL"
                    ),
                    "recheck_allowed": True,
                    "identity_authority_effect": "NONE",
                })
            if disposition == "NO_SAFE_CANDIDATE":
                entry["review_required"] = False
        entries.append(entry)
    core = {
        "schema_version": SCHEMA_VERSION,
        "coverage_ledger_version": (
            COVERAGE_LEDGER_R2_VERSION if r2 else COVERAGE_LEDGER_VERSION
        ),
        "entries": entries,
    }
    return _add_hash(core, "coverage_ledger_sha256")


def _validate_r2_provenance_overlay(
    overlay,
    proposals_by_record,
):
    if not isinstance(overlay, dict):
        raise IdentityCoverageError("R2_OVERLAY_OBJECT_REQUIRED")
    if set(overlay) != {"schema_version", "task_id", "entries"}:
        raise IdentityCoverageError("R2_OVERLAY_SCHEMA_INVALID")
    if overlay["schema_version"] != (
        "cba-kb.r2-unresolved-candidate-evidence-overlay.v1"
    ):
        raise IdentityCoverageError("R2_OVERLAY_VERSION_INVALID")
    _required_text(overlay["task_id"], "R2_OVERLAY_TASK_ID")
    if not isinstance(overlay["entries"], list):
        raise IdentityCoverageError("R2_OVERLAY_ENTRIES_REQUIRED")
    required = {
        "record_key",
        "candidate_player_uid",
        "existing_evidence_refs",
        "evidence_tier",
        "provenance_status",
        "source_artifact_path",
        "source_artifact_sha256",
        "source_locator",
        "source_type",
        "why_non_negative_candidate_evidence",
    }
    seen = set()
    result = {}
    for item in overlay["entries"]:
        if not isinstance(item, dict) or set(item) != required:
            raise IdentityCoverageError("R2_OVERLAY_ENTRY_SCHEMA_INVALID")
        record_key = _required_text(item["record_key"], "RECORD_KEY")
        if record_key in seen:
            raise IdentityCoverageError("R2_OVERLAY_DUPLICATE_RECORD")
        seen.add(record_key)
        proposals = proposals_by_record.get(record_key, [])
        candidate_uids = {
            proposal["candidate_player_uid"]
            for proposal in proposals
            if proposal["proposal_type"] == "EXISTING_IDENTITY_CANDIDATE"
        }
        if not candidate_uids:
            raise IdentityCoverageError("R2_OVERLAY_NOT_UNRESOLVED_CANDIDATE")
        if item["candidate_player_uid"] not in candidate_uids:
            raise IdentityCoverageError("R2_OVERLAY_CANDIDATE_TARGET_MISMATCH")
        _evidence_refs(
            item["existing_evidence_refs"], "R2_OVERLAY_EVIDENCE_REFS",
        )
        if not item["existing_evidence_refs"]:
            raise IdentityCoverageError("R2_OVERLAY_EVIDENCE_REFS_REQUIRED")
        if item["evidence_tier"] != "VERIFIED_SOURCE_EVIDENCE":
            raise IdentityCoverageError("R2_OVERLAY_EVIDENCE_TIER_INVALID")
        if item["provenance_status"] not in PROVENANCE_STATUSES:
            raise IdentityCoverageError("R2_OVERLAY_PROVENANCE_STATUS_INVALID")
        for field in (
            "source_artifact_path",
            "source_locator",
            "source_type",
            "why_non_negative_candidate_evidence",
        ):
            _required_text(item[field], field.upper())
        _required_sha256(
            item["source_artifact_sha256"],
            "R2_OVERLAY_SOURCE_ARTIFACT_SHA256",
        )
        result[record_key] = item
    return result


def validate_coverage_ledger(value):
    if not isinstance(value, dict):
        raise IdentityCoverageError("COVERAGE_LEDGER_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "coverage_ledger_version",
        "entries",
        "coverage_ledger_sha256",
    }
    if set(value) != required:
        raise IdentityCoverageError("COVERAGE_LEDGER_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "coverage_ledger_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "coverage_ledger_sha256"
    ]:
        raise IdentityCoverageError("COVERAGE_LEDGER_HASH_MISMATCH")
    seen = set()
    r2 = value["coverage_ledger_version"] == COVERAGE_LEDGER_R2_VERSION
    if value["coverage_ledger_version"] not in {
        COVERAGE_LEDGER_VERSION, COVERAGE_LEDGER_R2_VERSION,
    }:
        raise IdentityCoverageError("COVERAGE_LEDGER_VERSION_INVALID")
    for entry in value["entries"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != (
                R2_COVERAGE_LEDGER_ENTRY_FIELDS
                if r2 else COVERAGE_LEDGER_ENTRY_FIELDS
            )
        ):
            raise IdentityCoverageError("COVERAGE_ENTRY_OBJECT_REQUIRED")
        record_key = _required_text(entry.get("record_key"), "RECORD_KEY")
        if record_key in seen:
            raise IdentityCoverageError("COVERAGE_LEDGER_DUPLICATE_RECORD")
        seen.add(record_key)
        if entry.get("coverage_disposition") not in DISPOSITIONS:
            raise IdentityCoverageError("COVERAGE_DISPOSITION_INVALID")
        for field in (
            "same_count",
            "undecided_count",
            "not_same_count",
            "candidate_count",
        ):
            if not isinstance(entry[field], int) or entry[field] < 0:
                raise IdentityCoverageError("COVERAGE_COUNT_INVALID")
        if not isinstance(entry["review_required"], bool):
            raise IdentityCoverageError("REVIEW_REQUIRED_BOOLEAN_INVALID")
        if entry["source_exception_reason"] is not None:
            _required_text(
                entry["source_exception_reason"],
                "SOURCE_EXCEPTION_REASON",
            )
        if entry["coverage_disposition"] == "SOURCE_EXCEPTION":
            _required_text(
                entry["source_exception_reason"],
                "SOURCE_EXCEPTION_REASON",
            )
        elif entry["source_exception_reason"] is not None:
            raise IdentityCoverageError(
                "SOURCE_EXCEPTION_REASON_FORBIDDEN",
            )
        _evidence_refs(entry["evidence_refs"], "COVERAGE_EVIDENCE_REFS")
        if r2:
            if entry["evidence_tier"] not in EVIDENCE_TIERS:
                raise IdentityCoverageError("EVIDENCE_TIER_INVALID")
            if entry["provenance_status"] not in PROVENANCE_STATUSES:
                raise IdentityCoverageError("PROVENANCE_STATUS_INVALID")
            if not isinstance(entry["recheck_allowed"], bool):
                raise IdentityCoverageError("RECHECK_ALLOWED_BOOLEAN_INVALID")
            if entry["identity_authority_effect"] not in (
                IDENTITY_AUTHORITY_EFFECTS
            ):
                raise IdentityCoverageError(
                    "IDENTITY_AUTHORITY_EFFECT_INVALID",
                )
            disposition = entry["coverage_disposition"]
            if disposition == "RESOLVED_SAME" and (
                entry["evidence_tier"] != "AUDITED_AUTHORITY"
                or entry["identity_authority_effect"] != "EXISTING_AUTHORITY"
            ):
                raise IdentityCoverageError("R2_RESOLVED_SAME_INVALID")
            if disposition == "UNRESOLVED_CANDIDATES" and (
                not entry["review_required"]
                or entry["identity_authority_effect"]
                != "NONE_UNTIL_HUMAN_DECISION"
            ):
                raise IdentityCoverageError("R2_UNRESOLVED_INVALID")
            if disposition == "NO_SAFE_CANDIDATE" and (
                entry["evidence_tier"] != "BEST_EFFORT_NEGATIVE"
                or entry["recheck_allowed"] is not True
                or entry["identity_authority_effect"] != "NONE"
            ):
                raise IdentityCoverageError("R2_NO_SAFE_INVALID")
    return value


def _same_relation_set(registry):
    return {
        (link["record_key"], link["player_uid"])
        for link in registry["record_links"]
        if link["link_status"] == "same"
    }


def authorized_same_relations(
    base_registry,
    review_packet,
    reviewed_decisions,
):
    base = validate_registry(base_registry)
    validate_review_packet(review_packet)
    reviewed = validate_reviewed_decisions(reviewed_decisions)
    if reviewed["review_packet_sha256"] != review_packet[
        "review_packet_sha256"
    ]:
        raise IdentityCoverageError("REVIEW_AUTHORITY_MISMATCH")
    authorized = _same_relation_set(base)
    for decision in reviewed["decisions"]:
        if (
            decision["human_decision"] == "APPROVE"
            and decision["proposed_relation"] == "PROPOSED_SAME"
        ):
            if decision["proposal_type"] == "EXISTING_IDENTITY_CANDIDATE":
                player_uid = validate_player_uid(
                    decision["candidate_player_uid"],
                )
            elif decision["proposal_type"] == "NEW_IDENTITY_CANDIDATE":
                player_uid = validate_player_uid(
                    decision["approved_player_uid"],
                )
            else:
                raise IdentityCoverageError(
                    "PROPOSAL_RELATION_INCOMPATIBLE",
                )
            authorized.add((decision["record_key"], player_uid))
    return authorized


def reconcile_coverage(
    master_rows,
    coverage_ledger,
    final_registry,
    *,
    base_registry=None,
    review_packet=None,
    reviewed_decisions=None,
):
    rows = _validated_rows(master_rows)
    ledger = validate_coverage_ledger(coverage_ledger)
    registry = validate_registry(final_registry)
    if (
        base_registry is None
        or review_packet is None
        or reviewed_decisions is None
    ):
        raise IdentityCoverageError("COVERAGE_AUTHORITY_CONTEXT_REQUIRED")
    authorized_same = authorized_same_relations(
        base_registry,
        review_packet,
        reviewed_decisions,
    )
    final_same = _same_relation_set(registry)
    false_merge_count = len(final_same - authorized_same)
    master_keys = {row["record_key"] for row in rows}
    ledger_keys = {entry["record_key"] for entry in ledger["entries"]}
    missing = sorted(master_keys - ledger_keys)
    unknown = sorted(ledger_keys - master_keys)
    duplicate_count = len(ledger["entries"]) - len(ledger_keys)
    silent_drop_count = max(0, len(master_keys) - len(ledger_keys))
    review_required_count = sum(
        bool(entry["review_required"]) for entry in ledger["entries"]
    )
    counts = {disposition: 0 for disposition in sorted(DISPOSITIONS)}
    for entry in ledger["entries"]:
        counts[entry["coverage_disposition"]] += 1
    multiple_same_count = 0
    seen_same = set()
    for link in registry["record_links"]:
        if link["link_status"] != "same":
            continue
        if link["record_key"] in seen_same:
            multiple_same_count += 1
        seen_same.add(link["record_key"])
    r2 = ledger["coverage_ledger_version"] == COVERAGE_LEDGER_R2_VERSION
    full_record_coverage_complete = (
        not missing
        and not unknown
        and duplicate_count == 0
        and silent_drop_count == 0
        and (r2 or review_required_count == 0)
        and false_merge_count == 0
    )
    full_identity_resolution_complete = (
        len(master_keys) == len(ledger_keys)
        and all(
            entry["coverage_disposition"] == "RESOLVED_SAME"
            for entry in ledger["entries"]
        )
    )
    core = {
        "schema_version": SCHEMA_VERSION,
        "missing_record_keys": missing,
        "missing_record_count": len(missing),
        "unknown_record_keys": unknown,
        "unknown_record_count": len(unknown),
        "duplicate_disposition_count": duplicate_count,
        "silent_drop_count": silent_drop_count,
        "false_merge_count": false_merge_count,
        "review_required_count": review_required_count,
        "resolved_same_count": counts["RESOLVED_SAME"],
        "unresolved_candidates_count": counts["UNRESOLVED_CANDIDATES"],
        "no_safe_candidate_count": counts["NO_SAFE_CANDIDATE"],
        "source_exception_count": counts["SOURCE_EXCEPTION"],
        "multiple_same_count": multiple_same_count,
        "full_record_coverage_complete": full_record_coverage_complete,
        "full_identity_resolution_complete": (
            full_identity_resolution_complete
        ),
        "coverage_ledger_version": ledger["coverage_ledger_version"],
    }
    return _add_hash(core, "coverage_reconciliation_sha256")


def build_coverage_certificate(
    *,
    master_sha256,
    master_authority_mode,
    master_rows,
    master_unique_record_keys,
    base_registry_sha256,
    final_registry_sha256,
    review_packet_sha256,
    reviewed_decisions_sha256,
    candidate_registry_manifest_sha256,
    coverage_ledger_sha256,
    reconciliation,
    r2=None,
):
    master_sha256 = _required_sha256_or_null(
        master_sha256,
        "MASTER_SHA256",
    )
    if master_authority_mode not in MASTER_AUTHORITY_MODES:
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_INVALID")
    if master_sha256 is None:
        if master_authority_mode != (
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ):
            raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    elif master_authority_mode != "FILE_SHA256_VERIFIED":
        raise IdentityCoverageError("MASTER_AUTHORITY_MODE_MISMATCH")
    core = {
        "schema_version": SCHEMA_VERSION,
        "coverage_certificate_version": COVERAGE_CERTIFICATE_VERSION,
        "MASTER_sha256": master_sha256,
        "master_authority_mode": master_authority_mode,
        "MASTER_rows": master_rows,
        "MASTER_unique_record_keys": master_unique_record_keys,
        "base_registry_sha256": _required_sha256(
            base_registry_sha256,
            "BASE_REGISTRY_SHA256",
        ),
        "final_registry_sha256": _required_sha256(
            final_registry_sha256,
            "FINAL_REGISTRY_SHA256",
        ),
        "review_packet_sha256": _required_sha256(
            review_packet_sha256,
            "REVIEW_PACKET_SHA256",
        ),
        "reviewed_decisions_sha256": _required_sha256(
            reviewed_decisions_sha256,
            "REVIEWED_DECISIONS_SHA256",
        ),
        "candidate_registry_manifest_sha256": _required_sha256(
            candidate_registry_manifest_sha256,
            "CANDIDATE_REGISTRY_MANIFEST_SHA256",
        ),
        "coverage_ledger_sha256": _required_sha256(
            coverage_ledger_sha256,
            "COVERAGE_LEDGER_SHA256",
        ),
    }
    for field in (
        "missing_record_count",
        "unknown_record_count",
        "duplicate_disposition_count",
        "silent_drop_count",
        "false_merge_count",
        "multiple_same_count",
        "resolved_same_count",
        "unresolved_candidates_count",
        "no_safe_candidate_count",
        "source_exception_count",
    ):
        core[field] = reconciliation[field]
    core["full_record_coverage_complete"] = reconciliation[
        "full_record_coverage_complete"
    ]
    core["full_identity_resolution_complete"] = reconciliation[
        "full_identity_resolution_complete"
    ]
    if r2 is not None:
        required = {
            "frozen_requirement_file_id",
            "frozen_requirement_sha256",
            "freeze_decision_file_id",
            "freeze_decision_sha256",
            "created_at",
            "search_enrichment_complete",
            "evidence_tier_counts",
            "provenance_status_counts",
            "partial_negative_provenance_count",
        }
        if set(r2) != required:
            raise IdentityCoverageError("R2_CERTIFICATE_INPUT_INVALID")
        if not isinstance(r2["search_enrichment_complete"], bool):
            raise IdentityCoverageError(
                "SEARCH_ENRICHMENT_COMPLETE_BOOLEAN_INVALID",
            )
        for field in (
            "frozen_requirement_file_id",
            "freeze_decision_file_id",
            "created_at",
        ):
            _required_text(r2[field], field.upper())
        for field in (
            "frozen_requirement_sha256",
            "freeze_decision_sha256",
        ):
            _required_sha256(r2[field], field.upper())
        tier_counts = dict(r2["evidence_tier_counts"])
        provenance_counts = dict(r2["provenance_status_counts"])
        if set(tier_counts) - EVIDENCE_TIERS:
            raise IdentityCoverageError("EVIDENCE_TIER_COUNTS_INVALID")
        if set(provenance_counts) - PROVENANCE_STATUSES:
            raise IdentityCoverageError("PROVENANCE_STATUS_COUNTS_INVALID")
        if any(
            not isinstance(value, int) or value < 0
            for value in (*tier_counts.values(), *provenance_counts.values())
        ):
            raise IdentityCoverageError("R2_CERTIFICATE_COUNT_INVALID")
        core.update({
            "coverage_certificate_version": COVERAGE_CERTIFICATE_R2_VERSION,
            "evidence_tier_counts": tier_counts,
            "provenance_status_counts": provenance_counts,
            "best_effort_negative_count": tier_counts.get(
                "BEST_EFFORT_NEGATIVE", 0,
            ),
            "partial_negative_provenance_count": r2[
                "partial_negative_provenance_count"
            ],
            "search_enrichment_complete": r2[
                "search_enrichment_complete"
            ],
            "frozen_r2_requirement_file_id": r2[
                "frozen_requirement_file_id"
            ],
            "frozen_r2_requirement_sha256": r2[
                "frozen_requirement_sha256"
            ],
            "r2_freeze_decision_file_id": r2["freeze_decision_file_id"],
            "r2_freeze_decision_sha256": r2[
                "freeze_decision_sha256"
            ],
            "created_at": r2["created_at"],
        })
    return _add_hash(core, "coverage_certificate_sha256")


def coverage_semantic_hash(value):
    """Hash a coverage artifact excluding only its authorized timestamp."""
    excluded = frozenset({"created_at"})
    def clean(item):
        if isinstance(item, dict):
            return {
                key: clean(child)
                for key, child in item.items()
                if key not in excluded
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item
    return hashlib.sha256(canonical_bytes(clean(value))).hexdigest()


def verify_r2_certificate_bindings(certificate, expected):
    required = {
        "frozen_r2_requirement_file_id",
        "frozen_r2_requirement_sha256",
        "r2_freeze_decision_file_id",
        "r2_freeze_decision_sha256",
    }
    if set(expected) != required:
        raise IdentityCoverageError("R2_BINDING_EXPECTATION_INVALID")
    for field in required:
        if certificate.get(field) != expected[field]:
            raise IdentityCoverageError(f"R2_BINDING_MISMATCH_{field.upper()}")

def certify_coverage(
    *,
    master_rows,
    master_sha256,
    master_authority_mode,
    base_registry,
    final_registry,
    review_packet,
    reviewed_decisions,
    candidate_registry_manifest,
    coverage_ledger,
    r2=None,
):
    rows = _validated_rows(master_rows)
    base = validate_registry(base_registry)
    final = validate_registry(final_registry)
    packet = validate_review_packet(review_packet)
    reviewed = validate_reviewed_decisions(reviewed_decisions)
    manifest = validate_candidate_registry_manifest(
        candidate_registry_manifest,
    )
    ledger = validate_coverage_ledger(coverage_ledger)
    if reviewed["review_packet_sha256"] != packet["review_packet_sha256"]:
        raise IdentityCoverageError("REVIEW_AUTHORITY_MISMATCH")
    if manifest["base_registry_sha256"] != base["registry_sha256"]:
        raise IdentityCoverageError("MANIFEST_BASE_REGISTRY_MISMATCH")
    if manifest["candidate_registry_sha256"] != final["registry_sha256"]:
        raise IdentityCoverageError("MANIFEST_CANDIDATE_REGISTRY_MISMATCH")
    if manifest["review_packet_sha256"] != packet["review_packet_sha256"]:
        raise IdentityCoverageError("MANIFEST_REVIEW_PACKET_MISMATCH")
    if manifest["reviewed_decisions_sha256"] != reviewed[
        "reviewed_decisions_sha256"
    ]:
        raise IdentityCoverageError("MANIFEST_REVIEWED_DECISIONS_MISMATCH")
    if manifest["MASTER_sha256"] != master_sha256:
        raise IdentityCoverageError("MANIFEST_MASTER_SHA_MISMATCH")
    if manifest["master_authority_mode"] != master_authority_mode:
        raise IdentityCoverageError("MANIFEST_MASTER_MODE_MISMATCH")
    if manifest["MASTER_rows"] != len(rows):
        raise IdentityCoverageError("MANIFEST_MASTER_ROWS_MISMATCH")
    unique_keys = len({row["record_key"] for row in rows})
    if manifest["MASTER_unique_record_keys"] != unique_keys:
        raise IdentityCoverageError(
            "MANIFEST_MASTER_UNIQUE_RECORD_KEYS_MISMATCH",
        )
    reconciliation = reconcile_coverage(
        rows,
        ledger,
        final,
        base_registry=base,
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    if r2 is not None:
        if ledger["coverage_ledger_version"] != COVERAGE_LEDGER_R2_VERSION:
            raise IdentityCoverageError("R2_LEDGER_REQUIRED")
        tier_counts = defaultdict(int)
        provenance_counts = defaultdict(int)
        for entry in ledger["entries"]:
            tier_counts[entry["evidence_tier"]] += 1
            provenance_counts[entry["provenance_status"]] += 1
        if dict(r2.get("evidence_tier_counts", {})) != dict(tier_counts):
            raise IdentityCoverageError("R2_EVIDENCE_TIER_COUNTS_MISMATCH")
        if dict(r2.get("provenance_status_counts", {})) != dict(
            provenance_counts
        ):
            raise IdentityCoverageError(
                "R2_PROVENANCE_STATUS_COUNTS_MISMATCH",
            )
        r2 = {
            **r2,
            "partial_negative_provenance_count": sum(
                entry["evidence_tier"] == "BEST_EFFORT_NEGATIVE"
                and entry["provenance_status"] == "PARTIAL"
                for entry in ledger["entries"]
            ),
        }
    return build_coverage_certificate(
        master_sha256=master_sha256,
        master_authority_mode=master_authority_mode,
        master_rows=len(rows),
        master_unique_record_keys=unique_keys,
        base_registry_sha256=base["registry_sha256"],
        final_registry_sha256=final["registry_sha256"],
        review_packet_sha256=packet["review_packet_sha256"],
        reviewed_decisions_sha256=reviewed["reviewed_decisions_sha256"],
        candidate_registry_manifest_sha256=manifest[
            "candidate_registry_manifest_sha256"
        ],
        coverage_ledger_sha256=ledger["coverage_ledger_sha256"],
        reconciliation=reconciliation,
        r2=r2,
    )


def coverage_inventory(master_rows, identity_registry):
    return build_coverage_inventory(master_rows, identity_registry)


def coverage_candidates(
    master_rows,
    identity_registry,
    *,
    candidate_hints=None,
):
    return generate_candidate_proposals(
        master_rows,
        identity_registry,
        candidate_hints=candidate_hints,
    )


def review_prepare(proposals):
    return prepare_review_packet(proposals)


def review_validate(packet, csv_bytes):
    return validate_reviewed_csv(packet, csv_bytes)


def review_apply(master_rows, base_registry, packet, reviewed_decisions):
    return apply_reviewed_decisions(
        master_rows,
        base_registry,
        packet,
        reviewed_decisions,
    )


def coverage_reconcile(
    master_rows,
    ledger,
    final_registry,
    *,
    base_registry,
    review_packet,
    reviewed_decisions,
):
    return reconcile_coverage(
        master_rows,
        ledger,
        final_registry,
        base_registry=base_registry,
        review_packet=review_packet,
        reviewed_decisions=reviewed_decisions,
    )


def coverage_certify(**kwargs):
    return certify_coverage(**kwargs)
