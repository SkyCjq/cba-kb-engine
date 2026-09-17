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
REVIEW_PACKET_VERSION = "v1"
REVIEWED_DECISIONS_VERSION = "v1"
COVERAGE_CERTIFICATE_VERSION = "v1"

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
    for link in registry["record_links"]:
        existing_status[link["record_key"]].add(link["link_status"])

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

        matches = matches_by_name.get(normalize_name(row.get("player") or ""), set())
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
        decision = row["human_decision"]
        if decision not in DECISIONS:
            raise IdentityCoverageError("HUMAN_DECISION_REQUIRED")
        if not row["reviewed_at"]:
            raise IdentityCoverageError("REVIEWED_AT_REQUIRED")
        if (
            row["proposal_type"] == "SOURCE_EXCEPTION_CANDIDATE"
            and decision == "APPROVE"
            and not row["source_exception_reason"]
        ):
            raise IdentityCoverageError("SOURCE_EXCEPTION_REASON_REQUIRED")
        if (
            row["proposal_type"] != "SOURCE_EXCEPTION_CANDIDATE"
            and row["source_exception_reason"] is not None
        ):
            raise IdentityCoverageError("SOURCE_EXCEPTION_REASON_FORBIDDEN")
        if (
            row["proposal_type"] == "NEW_IDENTITY_CANDIDATE"
            and decision == "APPROVE"
            and row["proposed_relation"] == "PROPOSED_SAME"
        ):
            validate_player_uid(row["approved_player_uid"])
            _required_text(
                row["approved_canonical_name"],
                "APPROVED_CANONICAL_NAME",
            )
            if not row["human_note"]:
                raise IdentityCoverageError(
                    "UID_ALLOCATION_ATTESTATION_REQUIRED",
                )
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
    row_by_key = {row["record_key"]: row for row in rows}
    created_groups = {}

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
    base_registry_sha256 = _required_sha256_or_null(
        base_registry_sha256,
        "BASE_REGISTRY_SHA256",
    )
    candidate_registry_sha256 = _required_sha256_or_null(
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
        "review_packet_sha256": _required_sha256_or_null(
            review_packet_sha256,
            "REVIEW_PACKET_SHA256",
        ),
        "reviewed_decisions_sha256": _required_sha256_or_null(
            reviewed_decisions_sha256,
            "REVIEWED_DECISIONS_SHA256",
        ),
        "created_at": _required_text(created_at, "CREATED_AT"),
    }
    return _add_hash(core, "candidate_registry_manifest_sha256")


def build_coverage_ledger(
    master_rows,
    registry,
    review_packet,
    reviewed_decisions,
):
    rows = _validated_rows(master_rows)
    registry = validate_registry(registry)
    reviewed = validate_reviewed_decisions(reviewed_decisions)
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
        candidate_uids = {
            item["candidate_player_uid"]
            for item in proposals_for_record
            if item["candidate_player_uid"] is not None
        }
        evidence_refs = sorted({
            ref
            for item in record_decisions
            for ref in item["evidence_refs"]
        })
        entries.append({
            "record_key": record_key,
            "coverage_disposition": disposition,
            "same_count": same_count,
            "undecided_count": undecided_count,
            "not_same_count": not_same_count,
            "candidate_count": len(candidate_uids),
            "review_required": review_required,
            "source_exception_reason": source_reason,
            "evidence_refs": evidence_refs,
        })
    core = {
        "schema_version": SCHEMA_VERSION,
        "coverage_ledger_version": COVERAGE_LEDGER_VERSION,
        "entries": entries,
    }
    return _add_hash(core, "coverage_ledger_sha256")


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
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            raise IdentityCoverageError("COVERAGE_ENTRY_OBJECT_REQUIRED")
        record_key = _required_text(entry.get("record_key"), "RECORD_KEY")
        if record_key in seen:
            raise IdentityCoverageError("COVERAGE_LEDGER_DUPLICATE_RECORD")
        seen.add(record_key)
        if entry.get("coverage_disposition") not in DISPOSITIONS:
            raise IdentityCoverageError("COVERAGE_DISPOSITION_INVALID")
    return value


def reconcile_coverage(master_rows, coverage_ledger, final_registry):
    rows = _validated_rows(master_rows)
    ledger = validate_coverage_ledger(coverage_ledger)
    registry = validate_registry(final_registry)
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
    full_record_coverage_complete = (
        not missing
        and not unknown
        and duplicate_count == 0
        and silent_drop_count == 0
        and review_required_count == 0
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
        "false_merge_count": 0,
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
        "base_registry_sha256": _required_sha256_or_null(
            base_registry_sha256,
            "BASE_REGISTRY_SHA256",
        ),
        "final_registry_sha256": _required_sha256_or_null(
            final_registry_sha256,
            "FINAL_REGISTRY_SHA256",
        ),
        "review_packet_sha256": _required_sha256_or_null(
            review_packet_sha256,
            "REVIEW_PACKET_SHA256",
        ),
        "reviewed_decisions_sha256": _required_sha256_or_null(
            reviewed_decisions_sha256,
            "REVIEWED_DECISIONS_SHA256",
        ),
        "candidate_registry_manifest_sha256": _required_sha256_or_null(
            candidate_registry_manifest_sha256,
            "CANDIDATE_REGISTRY_MANIFEST_SHA256",
        ),
        "coverage_ledger_sha256": _required_sha256_or_null(
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
    return _add_hash(core, "coverage_certificate_sha256")


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


def coverage_reconcile(master_rows, ledger, final_registry):
    return reconcile_coverage(master_rows, ledger, final_registry)


def coverage_certify(**kwargs):
    return build_coverage_certificate(**kwargs)
