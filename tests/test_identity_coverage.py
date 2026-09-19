import csv
import hashlib
import io
import json

import pytest

from cba_kb.identity_coverage import (
    IdentityCoverageError,
    REVIEW_FIELDS,
    apply_reviewed_decisions,
    build_candidate_registry_manifest,
    build_coverage_certificate,
    build_coverage_inventory,
    build_coverage_ledger,
    coverage_semantic_hash,
    certify_coverage,
    generate_candidate_proposals,
    normalize_semantic_field,
    prepare_review_packet,
    reconcile_coverage,
    review_packet_to_csv,
    validate_candidate_uid,
    validate_machine_proposal,
    validate_coverage_ledger,
    verify_r2_certificate_bindings,
    validate_reviewed_csv,
)
from cba_kb.evidence_ledger import canonical_bytes
from cba_kb.master import HEADERS
from cba_kb.player_identity import new_registry, validate_registry


UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"
UID_NEW = "pid_0000000000000099"


def row(record_key, name, *, season="2026-2027", club="club-a"):
    value = {key: None for key in HEADERS}
    value.update({
        "record_key": record_key,
        "season": season,
        "club_id": club,
        "club_official": club,
        "player": name,
        "source_file_id": "synthetic-source",
        "source_url": "https://example.test/source",
        "verification_level": "machine_validated",
    })
    return value


def player(uid, name):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "canonical_name": name,
        "status": "ACTIVE",
        "redirect_to": None,
    }


def link(record_key, uid, status):
    return {
        "schema_version": 1,
        "record_key": record_key,
        "player_uid": uid,
        "link_status": status,
        "method": "MANUAL_REVIEW" if status != "undecided" else "REVIEW_PENDING",
        "confidence": "HIGH" if status != "undecided" else "UNKNOWN",
        "evidence_refs": ["synthetic-evidence"] if status != "undecided" else [],
    }


def base_registry(links=None):
    return new_registry(
        [
            player(UID_A, "Synthetic Alpha"),
            player(UID_B, "Synthetic Beta"),
        ],
        record_links=links or [],
    )


def base_rows():
    return [
        row("r1", "Synthetic Alpha"),
        row("r2", "Synthetic Alpha"),
        row("r3", "Synthetic Alpha"),
        row("r4", "Synthetic Gamma"),
    ]


def complete_review_csv(packet, overrides=None):
    overrides = overrides or {}
    rows = list(csv.DictReader(
        io.StringIO(review_packet_to_csv(packet).decode("utf-8"))
    ))
    for item in rows:
        review_id = item["review_id"]
        item.update({
            "human_decision": "UNDECIDED",
            "human_note": None,
            "approved_player_uid": None,
            "approved_canonical_name": None,
            "source_exception_reason": None,
            "reviewed_at": "2026-09-17T00:00:00Z",
        })
        if item["proposal_type"] == "NO_SAFE_CANDIDATE":
            item["human_decision"] = "APPROVE"
        if item["proposal_type"] == "SOURCE_EXCEPTION_CANDIDATE":
            item["human_decision"] = "APPROVE"
            item["source_exception_reason"] = "source-defect"
        if review_id in overrides:
            item.update(overrides[review_id])
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(rows[0]) if rows else list(REVIEW_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def reviewed_for(packet, overrides=None):
    return validate_reviewed_csv(
        packet,
        complete_review_csv(packet, overrides),
    )


def test_inventory_partitions_all_record_states():
    registry = base_registry([
        link("r1", UID_A, "same"),
        link("r2", UID_A, "undecided"),
        link("r3", UID_A, "not_same"),
    ])
    inventory = build_coverage_inventory(base_rows(), registry)
    by_key = {item["record_key"]: item for item in inventory["entries"]}
    assert by_key["r1"]["coverage_disposition"] == "RESOLVED_SAME"
    assert by_key["r2"]["coverage_disposition"] == "UNRESOLVED_CANDIDATES"
    assert by_key["r3"]["coverage_disposition"] == "NO_SAFE_CANDIDATE"
    assert by_key["r4"]["coverage_disposition"] == "NO_SAFE_CANDIDATE"
    assert inventory["disposition_counts"]["RESOLVED_SAME"] == 1
    assert inventory["disposition_counts"]["UNRESOLVED_CANDIDATES"] == 1
    assert inventory["disposition_counts"]["NO_SAFE_CANDIDATE"] == 2


def test_exact_name_candidate_is_candidate_only_and_no_safe_is_explicit():
    proposals = generate_candidate_proposals(
        base_rows(),
        base_registry(),
    )
    by_key = {}
    for proposal in proposals:
        by_key.setdefault(proposal["record_key"], []).append(proposal)
    assert by_key["r2"][0]["proposal_type"] == "EXISTING_IDENTITY_CANDIDATE"
    assert by_key["r2"][0]["proposed_relation"] == "KEEP_UNDECIDED"
    assert by_key["r4"][0]["proposal_type"] == "NO_SAFE_CANDIDATE"


def test_review_packet_and_reviewed_csv_are_deterministic_and_tamper_evident():
    packet = prepare_review_packet(
        generate_candidate_proposals(base_rows(), base_registry())
    )
    csv_bytes = complete_review_csv(packet)
    first = validate_reviewed_csv(packet, csv_bytes)
    second = validate_reviewed_csv(packet, csv_bytes)
    assert first == second
    tampered = csv_bytes.replace(
        packet["reviews"][0]["record_key"].encode(),
        b"tampered",
        1,
    )
    with pytest.raises(IdentityCoverageError, match="MACHINE_FIELD_TAMPER"):
        validate_reviewed_csv(packet, tampered)


def test_source_exception_requires_reason_and_creates_no_registry_relation():
    hints = {
        "r4": {
            "proposal_type": "SOURCE_EXCEPTION_CANDIDATE",
            "evidence_refs": ["source-defect-evidence"],
        }
    }
    packet = prepare_review_packet(
        generate_candidate_proposals(
            [row("r4", "Synthetic Gamma")],
            base_registry(),
            candidate_hints=hints,
        )
    )
    review_id = packet["reviews"][0]["review_id"]
    with pytest.raises(IdentityCoverageError, match="SOURCE_EXCEPTION_REASON"):
        reviewed_for(packet, {
            review_id: {"source_exception_reason": None},
        })
    reviewed = reviewed_for(packet, {
        review_id: {"source_exception_reason": "explicit-source-defect"},
    })
    candidate, _ = apply_reviewed_decisions(
        [row("r4", "Synthetic Gamma")],
        base_registry(),
        packet,
        reviewed,
    )
    assert candidate["record_links"] == []
    ledger = build_coverage_ledger(
        [row("r4", "Synthetic Gamma")],
        candidate,
        packet,
        reviewed,
    )
    assert ledger["entries"][0]["coverage_disposition"] == "SOURCE_EXCEPTION"
    assert ledger["entries"][0]["source_exception_reason"] == (
        "explicit-source-defect"
    )


def test_reject_does_not_infer_opposite_and_incomplete_record_blocks_completion():
    packet = prepare_review_packet(
        generate_candidate_proposals([row("r4", "Synthetic Gamma")], base_registry())
    )
    review_id = packet["reviews"][0]["review_id"]
    reviewed = reviewed_for(packet, {
        review_id: {"human_decision": "REJECT"},
    })
    candidate, _ = apply_reviewed_decisions(
        [row("r4", "Synthetic Gamma")],
        base_registry(),
        packet,
        reviewed,
    )
    assert candidate["record_links"] == []
    ledger = build_coverage_ledger(
        [row("r4", "Synthetic Gamma")],
        candidate,
        packet,
        reviewed,
    )
    report = reconcile_coverage(
        [row("r4", "Synthetic Gamma")],
        ledger,
        candidate,
        base_registry=base_registry(),
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    assert report["full_record_coverage_complete"] is False


def test_unsupported_relation_and_incompatible_type_fail_closed():
    base = {
        "record_key": "r4",
        "proposal_type": "NO_SAFE_CANDIDATE",
        "candidate_group_id": None,
        "candidate_player_uid": None,
        "candidate_name_display_only": None,
        "proposed_relation": "PROPOSED_SAME",
        "machine_suggestion": "PROPOSED_SAME",
        "machine_reason": "synthetic",
        "evidence_refs": [],
    }
    with pytest.raises(IdentityCoverageError, match="RELATION_INCOMPATIBLE"):
        validate_machine_proposal(base)
    invalid = {**base, "proposed_relation": "UNSUPPORTED"}
    with pytest.raises(IdentityCoverageError, match="PROPOSED_RELATION_INVALID"):
        validate_machine_proposal(invalid)


def test_keep_undecided_creates_no_registry_relation():
    packet = prepare_review_packet(
        generate_candidate_proposals([row("r2", "Synthetic Alpha")], base_registry())
    )
    reviewed = reviewed_for(packet)
    candidate, _ = apply_reviewed_decisions(
        [row("r2", "Synthetic Alpha")],
        base_registry(),
        packet,
        reviewed,
    )
    assert candidate["record_links"] == []
    ledger = build_coverage_ledger(
        [row("r2", "Synthetic Alpha")],
        candidate,
        packet,
        reviewed,
    )
    assert ledger["entries"][0]["coverage_disposition"] == (
        "UNRESOLVED_CANDIDATES"
    )


def test_new_identity_group_creates_one_player_and_links_only_reviewed_rows():
    rows = [
        row("r4", "Synthetic Gamma"),
        row("r5", "Synthetic Gamma"),
        row("r6", "Synthetic Gamma"),
    ]
    hints = {
        key: {
            "proposal_type": "NEW_IDENTITY_CANDIDATE",
            "candidate_group_id": "group-gamma",
            "proposed_relation": "PROPOSED_SAME",
            "evidence_refs": ["identity-evidence"],
        }
        for key in ("r4", "r5", "r6")
    }
    packet = prepare_review_packet(
        generate_candidate_proposals(
            rows,
            base_registry(),
            candidate_hints=hints,
        )
    )
    remaining = [
        {
            key: item[key]
            for key in (
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
        }
        for item in packet["reviews"]
        if item["record_key"] != "r6"
    ]
    packet = prepare_review_packet(remaining)
    overrides = {
        item["review_id"]: {
            "human_decision": "APPROVE",
            "human_note": "allocated independently",
            "approved_player_uid": UID_NEW,
            "approved_canonical_name": "Synthetic Gamma",
        }
        for item in packet["reviews"]
    }
    reviewed = reviewed_for(packet, overrides)
    candidate, groups = apply_reviewed_decisions(
        rows,
        base_registry(),
        packet,
        reviewed,
    )
    assert len(groups) == 1
    assert len(candidate["players"]) == 3
    assert validate_registry(candidate) == candidate
    assert set(candidate) == {
        "schema_version",
        "identity_version",
        "players",
        "aliases",
        "record_links",
        "registry_sha256",
    }
    linked = {
        item["record_key"] for item in candidate["record_links"]
        if item["player_uid"] == UID_NEW and item["link_status"] == "same"
    }
    assert linked == {"r4", "r5"}
    assert "r6" not in linked


def test_new_identity_group_conflict_fails_closed():
    rows = [row("r4", "Synthetic Gamma"), row("r5", "Synthetic Gamma")]
    hints = {
        key: {
            "proposal_type": "NEW_IDENTITY_CANDIDATE",
            "candidate_group_id": "group-gamma",
            "proposed_relation": "PROPOSED_SAME",
            "evidence_refs": ["identity-evidence"],
        }
        for key in ("r4", "r5")
    }
    packet = prepare_review_packet(
        generate_candidate_proposals(rows, base_registry(), candidate_hints=hints)
    )
    overrides = {}
    for index, item in enumerate(packet["reviews"]):
        overrides[item["review_id"]] = {
            "human_decision": "APPROVE",
            "human_note": "allocated independently",
            "approved_player_uid": UID_NEW if index == 0 else UID_B,
            "approved_canonical_name": "Synthetic Gamma",
        }
    with pytest.raises(
        IdentityCoverageError,
        match="NEW_IDENTITY_GROUP_AUTHORITY_CONFLICT",
    ):
        reviewed_for(packet, overrides)


def test_uid_policy_rejects_semantic_equality_and_hash_but_accepts_opaque():
    semantic = " Synthetic  Gamma "
    normalized = normalize_semantic_field(semantic)
    assert normalized == "Synthetic Gamma"
    with pytest.raises(IdentityCoverageError, match="SEMANTIC_EQUALITY"):
        validate_candidate_uid(
            normalized,
            [semantic],
            "allocated independently",
        )
    with pytest.raises(IdentityCoverageError, match="SEMANTIC_HASH"):
        validate_candidate_uid(
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            [semantic],
            "allocated independently",
        )
    assert validate_candidate_uid(
        UID_NEW,
        [semantic],
        "allocated independently",
    ) == UID_NEW


def test_candidate_manifest_and_certificate_provenance_and_full_resolution():
    rows = [row("r4", "Synthetic Gamma")]
    packet = prepare_review_packet(
        generate_candidate_proposals(rows, base_registry())
    )
    reviewed = reviewed_for(packet)
    candidate, _ = apply_reviewed_decisions(
        rows,
        base_registry(),
        packet,
        reviewed,
    )
    assert candidate["record_links"] == []
    ledger = build_coverage_ledger(
        rows,
        candidate,
        packet,
        reviewed,
    )
    manifest = build_candidate_registry_manifest(
        base_registry_sha256=base_registry()["registry_sha256"],
        candidate_registry_sha256=candidate["registry_sha256"],
        master_sha256=None,
        master_authority_mode=(
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ),
        master_rows=1,
        master_unique_record_keys=1,
        review_packet_sha256=packet["review_packet_sha256"],
        reviewed_decisions_sha256=reviewed["reviewed_decisions_sha256"],
        created_at="2026-09-17T00:00:00Z",
    )
    assert manifest["MASTER_sha256"] is None
    assert manifest["master_authority_mode"] == (
        "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
    )
    reconciliation = reconcile_coverage(
        rows,
        ledger,
        candidate,
        base_registry=base_registry(),
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    certificate = build_coverage_certificate(
        master_sha256=None,
        master_authority_mode=(
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ),
        master_rows=1,
        master_unique_record_keys=1,
        base_registry_sha256=base_registry()["registry_sha256"],
        final_registry_sha256=candidate["registry_sha256"],
        review_packet_sha256=packet["review_packet_sha256"],
        reviewed_decisions_sha256=reviewed["reviewed_decisions_sha256"],
        candidate_registry_manifest_sha256=manifest[
            "candidate_registry_manifest_sha256"
        ],
        coverage_ledger_sha256=ledger["coverage_ledger_sha256"],
        reconciliation=reconciliation,
    )
    assert certificate["review_packet_sha256"] == packet[
        "review_packet_sha256"
    ]
    assert certificate["reviewed_decisions_sha256"] == reviewed[
        "reviewed_decisions_sha256"
    ]
    assert certificate["candidate_registry_manifest_sha256"] == manifest[
        "candidate_registry_manifest_sha256"
    ]
    assert certificate["full_record_coverage_complete"] is True
    assert certificate["full_identity_resolution_complete"] is False
    assert "review_manifest_sha256" not in certificate


def rehash(value, field):
    core = {
        key: item for key, item in value.items()
        if key != field
    }
    value[field] = hashlib.sha256(
        canonical_bytes(core)
    ).hexdigest()
    return value


def rehash_ledger(value):
    return rehash(value, "coverage_ledger_sha256")


def test_reconciliation_reports_missing_unknown_and_duplicate_fail_closed():
    rows = [row("r1", "Synthetic Alpha")]
    registry = base_registry([link("r1", UID_A, "same")])
    packet = prepare_review_packet([])
    reviewed = reviewed_for(packet)
    ledger = build_coverage_ledger(rows, registry, packet, reviewed)

    missing = rehash_ledger({
        **ledger,
        "entries": [],
    })
    report = reconcile_coverage(
        rows,
        missing,
        registry,
        base_registry=registry,
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    assert report["missing_record_count"] == 1
    assert report["full_record_coverage_complete"] is False

    unknown_entry = {
        **ledger["entries"][0],
        "record_key": "r-unknown",
    }
    unknown = rehash_ledger({**ledger, "entries": [unknown_entry]})
    report = reconcile_coverage(
        rows,
        unknown,
        registry,
        base_registry=registry,
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    assert report["unknown_record_count"] == 1
    assert report["full_record_coverage_complete"] is False

    duplicate = {
        **ledger,
        "entries": [ledger["entries"][0], ledger["entries"][0]],
    }
    duplicate = rehash_ledger(duplicate)
    with pytest.raises(IdentityCoverageError, match="DUPLICATE_RECORD"):
        reconcile_coverage(
            rows,
            duplicate,
            registry,
            base_registry=registry,
            review_packet=packet,
            reviewed_decisions=reviewed,
        )


def test_full_identity_resolution_true_only_when_all_records_are_same():
    rows = [row("r1", "Synthetic Alpha")]
    registry = base_registry([link("r1", UID_A, "same")])
    packet = prepare_review_packet([])
    reviewed = reviewed_for(packet)
    ledger = build_coverage_ledger(rows, registry, packet, reviewed)
    report = reconcile_coverage(
        rows,
        ledger,
        registry,
        base_registry=registry,
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    assert report["full_record_coverage_complete"] is True
    assert report["full_identity_resolution_complete"] is True


def test_master_sha_sentinel_and_mode_mismatch_fail_closed():
    with pytest.raises(IdentityCoverageError, match="MASTER_SHA256_INVALID"):
        build_candidate_registry_manifest(
            base_registry_sha256="a" * 64,
            candidate_registry_sha256="b" * 64,
            master_sha256="NOT_AVAILABLE",
            master_authority_mode="FILE_SHA256_VERIFIED",
            master_rows=1,
            master_unique_record_keys=1,
            review_packet_sha256="c" * 64,
            reviewed_decisions_sha256="d" * 64,
            created_at="2026-09-17T00:00:00Z",
        )


def test_non_master_hashes_are_required():
    with pytest.raises(IdentityCoverageError, match="BASE_REGISTRY_SHA256_REQUIRED"):
        build_candidate_registry_manifest(
            base_registry_sha256=None,
            candidate_registry_sha256="b" * 64,
            master_sha256=None,
            master_authority_mode=(
                "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
            ),
            master_rows=1,
            master_unique_record_keys=1,
            review_packet_sha256="c" * 64,
            reviewed_decisions_sha256="d" * 64,
            created_at="2026-09-17T00:00:00Z",
        )


def _completed_authority():
    rows = [row("r4", "Synthetic Gamma")]
    base = base_registry()
    packet = prepare_review_packet(
        generate_candidate_proposals(rows, base)
    )
    reviewed = reviewed_for(packet)
    candidate, _ = apply_reviewed_decisions(
        rows,
        base,
        packet,
        reviewed,
    )
    ledger = build_coverage_ledger(
        rows,
        candidate,
        packet,
        reviewed,
    )
    manifest = build_candidate_registry_manifest(
        base_registry_sha256=base["registry_sha256"],
        candidate_registry_sha256=candidate["registry_sha256"],
        master_sha256=None,
        master_authority_mode=(
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ),
        master_rows=1,
        master_unique_record_keys=1,
        review_packet_sha256=packet["review_packet_sha256"],
        reviewed_decisions_sha256=reviewed["reviewed_decisions_sha256"],
        created_at="2026-09-17T00:00:00Z",
    )
    return rows, base, packet, reviewed, candidate, manifest, ledger


def test_certification_rejects_rehashed_provenance_mismatches():
    rows, base, packet, reviewed, candidate, manifest, ledger = (
        _completed_authority()
    )
    tampered_manifest = {
        **manifest,
        "candidate_registry_sha256": "f" * 64,
    }
    tampered_manifest = rehash(
        tampered_manifest,
        "candidate_registry_manifest_sha256",
    )
    with pytest.raises(
        IdentityCoverageError,
        match="MANIFEST_CANDIDATE_REGISTRY_MISMATCH",
    ):
        certify_coverage(
            master_rows=rows,
            master_sha256=None,
            master_authority_mode=(
                "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
            ),
            base_registry=base,
            final_registry=candidate,
            review_packet=packet,
            reviewed_decisions=reviewed,
            candidate_registry_manifest=tampered_manifest,
            coverage_ledger=ledger,
        )

    packet_mismatch = {**manifest, "review_packet_sha256": "e" * 64}
    packet_mismatch = rehash(
        packet_mismatch,
        "candidate_registry_manifest_sha256",
    )
    with pytest.raises(
        IdentityCoverageError,
        match="MANIFEST_REVIEW_PACKET_MISMATCH",
    ):
        certify_coverage(
            master_rows=rows,
            master_sha256=None,
            master_authority_mode=(
                "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
            ),
            base_registry=base,
            final_registry=candidate,
            review_packet=packet,
            reviewed_decisions=reviewed,
            candidate_registry_manifest=packet_mismatch,
            coverage_ledger=ledger,
        )

    decisions_mismatch = {**manifest, "reviewed_decisions_sha256": "d" * 64}
    decisions_mismatch = rehash(
        decisions_mismatch,
        "candidate_registry_manifest_sha256",
    )
    with pytest.raises(
        IdentityCoverageError,
        match="MANIFEST_REVIEWED_DECISIONS_MISMATCH",
    ):
        certify_coverage(
            master_rows=rows,
            master_sha256=None,
            master_authority_mode=(
                "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
            ),
            base_registry=base,
            final_registry=candidate,
            review_packet=packet,
            reviewed_decisions=reviewed,
            candidate_registry_manifest=decisions_mismatch,
            coverage_ledger=ledger,
        )

    changed_final = new_registry(
        [player(UID_A, "Synthetic Alpha")],
    )
    with pytest.raises(
        IdentityCoverageError,
        match="MANIFEST_CANDIDATE_REGISTRY_MISMATCH",
    ):
        certify_coverage(
            master_rows=rows,
            master_sha256=None,
            master_authority_mode=(
                "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
            ),
            base_registry=base,
            final_registry=changed_final,
            review_packet=packet,
            reviewed_decisions=reviewed,
            candidate_registry_manifest=manifest,
            coverage_ledger=ledger,
        )


def test_unauthorized_final_same_relation_derives_false_merge_and_fails_complete():
    rows = [row("r4", "Synthetic Gamma")]
    base = base_registry()
    packet = prepare_review_packet([])
    reviewed = reviewed_for(packet)
    unauthorized = new_registry(
        [player(UID_A, "Synthetic Alpha")],
        record_links=[link("r4", UID_A, "same")],
    )
    ledger = build_coverage_ledger(
        rows,
        unauthorized,
        packet,
        reviewed,
    )
    report = reconcile_coverage(
        rows,
        ledger,
        unauthorized,
        base_registry=base,
        review_packet=packet,
        reviewed_decisions=reviewed,
    )
    assert report["false_merge_count"] == 1
    assert report["full_record_coverage_complete"] is False


def test_new_identity_and_source_exception_evidence_are_required():
    rows = [row("r4", "Synthetic Gamma")]
    packet = prepare_review_packet(
        generate_candidate_proposals(
            rows,
            base_registry(),
            candidate_hints={
                "r4": {
                    "proposal_type": "NEW_IDENTITY_CANDIDATE",
                    "candidate_group_id": "group-gamma",
                    "proposed_relation": "PROPOSED_SAME",
                    "evidence_refs": [],
                }
            },
        )
    )
    review_id = packet["reviews"][0]["review_id"]
    with pytest.raises(
        IdentityCoverageError,
        match="NEW_IDENTITY_EVIDENCE_REQUIRED",
    ):
        reviewed_for(packet, {
            review_id: {
                "human_decision": "APPROVE",
                "human_note": "allocated independently",
                "approved_player_uid": UID_NEW,
                "approved_canonical_name": "Synthetic Gamma",
            },
        })

    source_packet = prepare_review_packet(
        generate_candidate_proposals(
            rows,
            base_registry(),
            candidate_hints={
                "r4": {
                    "proposal_type": "SOURCE_EXCEPTION_CANDIDATE",
                    "evidence_refs": [],
                }
            },
        )
    )
    with pytest.raises(
        IdentityCoverageError,
        match="SOURCE_EXCEPTION_EVIDENCE_REQUIRED",
    ):
        reviewed_for(source_packet)


def test_new_identity_cannot_reuse_base_uid_or_share_uid_across_groups():
    rows = [row("r4", "Synthetic Gamma")]
    packet = prepare_review_packet(
        generate_candidate_proposals(
            rows,
            base_registry(),
            candidate_hints={
                "r4": {
                    "proposal_type": "NEW_IDENTITY_CANDIDATE",
                    "candidate_group_id": "group-gamma",
                    "proposed_relation": "PROPOSED_SAME",
                    "evidence_refs": ["identity-evidence"],
                }
            },
        )
    )
    review_id = packet["reviews"][0]["review_id"]
    reviewed = reviewed_for(packet, {
        review_id: {
            "human_decision": "APPROVE",
            "human_note": "allocated independently",
            "approved_player_uid": UID_A,
            "approved_canonical_name": "Synthetic Gamma",
        },
    })
    with pytest.raises(
        IdentityCoverageError,
        match="NEW_IDENTITY_UID_ALREADY_EXISTS",
    ):
        apply_reviewed_decisions(
            rows,
            base_registry(),
            packet,
            reviewed,
        )

    two_rows = [row("r4", "Synthetic Gamma"), row("r5", "Synthetic Gamma")]
    hints = {
        key: {
            "proposal_type": "NEW_IDENTITY_CANDIDATE",
            "candidate_group_id": f"group-{key}",
            "proposed_relation": "PROPOSED_SAME",
            "evidence_refs": ["identity-evidence"],
        }
        for key in ("r4", "r5")
    }
    two_packet = prepare_review_packet(
        generate_candidate_proposals(
            two_rows,
            base_registry(),
            candidate_hints=hints,
        )
    )
    overrides = {
        item["review_id"]: {
            "human_decision": "APPROVE",
            "human_note": "allocated independently",
            "approved_player_uid": UID_NEW,
            "approved_canonical_name": "Synthetic Gamma",
        }
        for item in two_packet["reviews"]
    }
    two_reviewed = reviewed_for(two_packet, overrides)
    with pytest.raises(
        IdentityCoverageError,
        match="NEW_IDENTITY_UID_GROUP_CONFLICT",
    ):
        apply_reviewed_decisions(
            two_rows,
            base_registry(),
            two_packet,
            two_reviewed,
        )


def test_not_same_pair_is_not_reproposed_and_new_group_counts_once():
    registry = base_registry([
        link("r2", UID_A, "not_same"),
    ])
    proposals = generate_candidate_proposals(
        [row("r2", "Synthetic Alpha")],
        registry,
    )
    assert proposals[0]["proposal_type"] == "NO_SAFE_CANDIDATE"

    rows = [row("r4", "Synthetic Gamma"), row("r5", "Synthetic Gamma")]
    hints = {
        key: {
            "proposal_type": "NEW_IDENTITY_CANDIDATE",
            "candidate_group_id": "group-gamma",
            "proposed_relation": "KEEP_UNDECIDED",
            "evidence_refs": ["identity-evidence"],
        }
        for key in ("r4", "r5")
    }
    packet = prepare_review_packet(
        generate_candidate_proposals(
            rows,
            base_registry(),
            candidate_hints=hints,
        )
    )
    reviewed = reviewed_for(packet)
    candidate, _ = apply_reviewed_decisions(
        rows,
        base_registry(),
        packet,
        reviewed,
    )
    ledger = build_coverage_ledger(
        rows,
        candidate,
        packet,
        reviewed,
    )
    assert all(entry["candidate_count"] == 1 for entry in ledger["entries"])


def test_rehashed_reviewed_decision_semantic_tamper_still_fails():
    packet = prepare_review_packet(
        generate_candidate_proposals(
            [row("r4", "Synthetic Gamma")],
            base_registry(),
            candidate_hints={
                "r4": {
                    "proposal_type": "SOURCE_EXCEPTION_CANDIDATE",
                    "evidence_refs": ["source-evidence"],
                }
            },
        )
    )
    reviewed = reviewed_for(packet)
    tampered = {
        **reviewed,
        "decisions": [
            {
                **reviewed["decisions"][0],
                "source_exception_reason": None,
            },
        ],
    }
    tampered = rehash(tampered, "reviewed_decisions_sha256")
    with pytest.raises(
        IdentityCoverageError,
        match="SOURCE_EXCEPTION_REASON_REQUIRED",
    ):
        apply_reviewed_decisions(
            [row("r4", "Synthetic Gamma")],
            base_registry(),
            packet,
            tampered,
        )


def test_manifest_created_at_is_deterministic_when_explicit():
    kwargs = {
        "base_registry_sha256": "a" * 64,
        "candidate_registry_sha256": "b" * 64,
        "master_sha256": None,
        "master_authority_mode": (
            "ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA"
        ),
        "master_rows": 1,
        "master_unique_record_keys": 1,
        "review_packet_sha256": "c" * 64,
        "reviewed_decisions_sha256": "d" * 64,
        "created_at": "2026-09-17T00:00:00Z",
    }
    first = build_candidate_registry_manifest(**kwargs)
    second = build_candidate_registry_manifest(**kwargs)
    assert first == second
    assert first["candidate_registry_manifest_sha256"] == second[
        "candidate_registry_manifest_sha256"
    ]
    with pytest.raises(IdentityCoverageError, match="MODE_MISMATCH"):
        build_candidate_registry_manifest(
            base_registry_sha256="a" * 64,
            candidate_registry_sha256="b" * 64,
            master_sha256=None,
            master_authority_mode="FILE_SHA256_VERIFIED",
            master_rows=1,
            master_unique_record_keys=1,
            review_packet_sha256="c" * 64,
            reviewed_decisions_sha256="d" * 64,
            created_at="2026-09-17T00:00:00Z",
        )


def test_r2_ledger_certificate_and_semantic_replay():
    rows, base, packet, reviewed, candidate, manifest, _ = (
        _completed_authority()
    )
    ledger = build_coverage_ledger(
        rows, candidate, packet, reviewed, r2=True,
    )
    entry = ledger["entries"][0]
    assert entry["coverage_disposition"] == "NO_SAFE_CANDIDATE"
    assert entry["evidence_tier"] == "BEST_EFFORT_NEGATIVE"
    assert entry["provenance_status"] == "PARTIAL"
    assert entry["recheck_allowed"] is True
    assert entry["identity_authority_effect"] == "NONE"
    validate_coverage_ledger(ledger)
    report = reconcile_coverage(
        rows, ledger, candidate, base_registry=base,
        review_packet=packet, reviewed_decisions=reviewed,
    )
    assert report["full_record_coverage_complete"] is True
    assert report["full_identity_resolution_complete"] is False
    r2 = {
        "frozen_requirement_file_id": "req-file",
        "frozen_requirement_sha256": "a" * 64,
        "freeze_decision_file_id": "decision-file",
        "freeze_decision_sha256": "b" * 64,
        "created_at": "2026-09-19T00:00:00Z",
        "search_enrichment_complete": False,
        "evidence_tier_counts": {"BEST_EFFORT_NEGATIVE": 1},
        "provenance_status_counts": {"PARTIAL": 1},
    }
    certificate = certify_coverage(
        master_rows=rows, master_sha256=None,
        master_authority_mode="ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA",
        base_registry=base, final_registry=candidate, review_packet=packet,
        reviewed_decisions=reviewed,
        candidate_registry_manifest=manifest, coverage_ledger=ledger, r2=r2,
    )
    assert certificate["coverage_certificate_version"] == "v2"
    assert certificate["search_enrichment_complete"] is False
    assert certificate["best_effort_negative_count"] == 1
    changed_timestamp = {**certificate, "created_at": "2026-09-20T00:00:00Z"}
    assert coverage_semantic_hash(certificate) == coverage_semantic_hash(
        changed_timestamp,
    )
    changed_semantic = {**certificate, "search_enrichment_complete": True}
    assert coverage_semantic_hash(certificate) != coverage_semantic_hash(
        changed_semantic,
    )


def test_r2_ledger_rejects_invalid_no_safe_semantics():
    rows, base, packet, reviewed, candidate, _, _ = _completed_authority()
    ledger = build_coverage_ledger(
        rows, candidate, packet, reviewed, r2=True,
    )
    ledger["entries"][0]["recheck_allowed"] = False
    ledger = rehash_ledger(ledger)
    with pytest.raises(IdentityCoverageError, match="R2_NO_SAFE_INVALID"):
        validate_coverage_ledger(ledger)


def test_r2_partial_negative_and_binding_verifier_are_fail_closed():
    rows, base, packet, reviewed, candidate, manifest, _ = (
        _completed_authority()
    )
    ledger = build_coverage_ledger(
        rows, candidate, packet, reviewed, r2=True,
    )
    r2 = {
        "frozen_requirement_file_id": "requirement-file",
        "frozen_requirement_sha256": "a" * 64,
        "freeze_decision_file_id": "decision-file",
        "freeze_decision_sha256": "b" * 64,
        "created_at": "2026-09-19T00:00:00Z",
        "search_enrichment_complete": False,
        "evidence_tier_counts": {"BEST_EFFORT_NEGATIVE": 1},
        "provenance_status_counts": {"PARTIAL": 1},
    }
    certificate = certify_coverage(
        master_rows=rows, master_sha256=None,
        master_authority_mode="ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA",
        base_registry=base, final_registry=candidate, review_packet=packet,
        reviewed_decisions=reviewed,
        candidate_registry_manifest=manifest, coverage_ledger=ledger, r2=r2,
    )
    assert certificate["partial_negative_provenance_count"] == 1
    expected = {
        "frozen_r2_requirement_file_id": "requirement-file",
        "frozen_r2_requirement_sha256": "a" * 64,
        "r2_freeze_decision_file_id": "decision-file",
        "r2_freeze_decision_sha256": "b" * 64,
    }
    verify_r2_certificate_bindings(certificate, expected)
    for field in expected:
        mismatch = {**expected, field: "mismatch"}
        with pytest.raises(IdentityCoverageError, match="R2_BINDING_MISMATCH"):
            verify_r2_certificate_bindings(certificate, mismatch)

    partial_verified = {
        **ledger,
        "entries": [{
            **ledger["entries"][0],
            "coverage_disposition": "UNRESOLVED_CANDIDATES",
            "evidence_tier": "VERIFIED_SOURCE_EVIDENCE",
            "provenance_status": "PARTIAL",
            "identity_authority_effect": "NONE_UNTIL_HUMAN_DECISION",
            "review_required": True,
        }],
    }
    partial_verified = rehash_ledger(partial_verified)
    r2_verified = {
        **r2,
        "evidence_tier_counts": {"VERIFIED_SOURCE_EVIDENCE": 1},
        "provenance_status_counts": {"PARTIAL": 1},
    }
    verified_certificate = certify_coverage(
        master_rows=rows, master_sha256=None,
        master_authority_mode="ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA",
        base_registry=base, final_registry=candidate, review_packet=packet,
        reviewed_decisions=reviewed,
        candidate_registry_manifest=manifest,
        coverage_ledger=partial_verified, r2=r2_verified,
    )
    assert verified_certificate["partial_negative_provenance_count"] == 0


def test_r2_unresolved_without_verified_evidence_fails_closed():
    rows = [row("r2", "Synthetic Alpha")]
    packet = prepare_review_packet(
        generate_candidate_proposals(rows, base_registry())
    )
    reviewed = reviewed_for(packet)
    candidate, _ = apply_reviewed_decisions(
        rows, base_registry(), packet, reviewed,
    )
    with pytest.raises(
        IdentityCoverageError, match="R2_UNRESOLVED_EVIDENCE_REQUIRED",
    ):
        build_coverage_ledger(rows, candidate, packet, reviewed, r2=True)
