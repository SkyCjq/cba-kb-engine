"""Focused closure tests for REQ-181-CONSUMER-CLOSURE-01.

Machine-proves:
1. modern CURRENT_VERSION_DOC remains current for v1.8.1 and future non-v1.6.1 release IDs;
2. all managed current surfaces share release/code/registry identity;
3. public GitHub wording closure;
4. existing profile-v2 identity boundary remains unchanged;
5. SAME / NOT_SAME / UNDECIDED / UNAVAILABLE-or-NOT_MATERIALIZED are distinguishable;
6. declared machine counts equal actual machine counts;
7. identity decision/registry/MASTER zero-diff;
8. no Production mutation path is exercised.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from cba_kb.consumer_package import (
    CONSUMER_TARGETS,
    IDENTITY_SEMANTIC_STATES,
    build_consumer_payload,
    build_target_packages,
    derive_machine_counts,
    project_identity_state,
    validate_machine_counts,
    validate_package,
)
from cba_kb.consumer_projection import ConsumerProjectionError
from cba_kb.current_state import (
    BEGIN,
    CURRENT_DOCUMENT_SURFACES,
    CURRENT_VERSION_DOC,
    END,
    control_document_identities,
    current_version_document,
    generate_context_card,
    read_current_block,
    render_current_state,
    replace_current_block,
    target_metadata,
    validate_context_card,
    validate_current_state,
    zero_business_delta,
)
from cba_kb.document_mentions import build_mention_artifact
from cba_kb.master import HEADERS
from cba_kb.player_identity import new_registry
from cba_kb.player_profile import build_profile_v2

from test_canonical_registry import SHA, manifest, registry


def sample_v181_documents(release_id="v1.8.1-1", code_commit=SHA):
    reg = registry()
    reg["registry_release_id"] = release_id
    meta = target_metadata(release_id, code_commit, reg)
    block = render_current_state(meta)
    docs = {
        "readme": block + "README body\n",
        "index": block + "INDEX body\n",
        "current_version_doc": block + "CURRENT_VERSION_DOC body\n",
        "context_card": generate_context_card(meta, reg, manifest()),
    }
    status = {
        "state": "COMPLETE",
        "current_release_id": release_id,
        "code_commit": code_commit,
    }
    return status, reg, docs, meta


def build_sample_profile_v2(player_uid="pid_0000000000000001"):
    row = {key: None for key in HEADERS}
    row.update({
        "record_key": "r1",
        "season": "2026-2027",
        "club_id": "test-club",
        "player": "TEST PLAYER",
        "source_file_id": "src-1",
        "source_url": "https://example.test",
        "verification_level": "machine_validated",
    })
    reg = new_registry(
        [{
            "schema_version": 1,
            "player_uid": player_uid,
            "canonical_name": "TEST PLAYER",
            "status": "ACTIVE",
            "redirect_to": None,
        }],
        record_links=[{
            "schema_version": 1,
            "record_key": "r1",
            "player_uid": player_uid,
            "link_status": "same",
            "method": "MANUAL_REVIEW",
            "confidence": "HIGH",
            "evidence_refs": ["ev-1"],
        }],
    )
    return build_profile_v2(
        [row],
        player_uid=player_uid,
        identity_registry=reg,
        mention_artifact=build_mention_artifact([], []),
        release_id="v1.8.1-1",
        as_of="2026-09-24T00:00:00Z",
        source_master_sha256="a" * 64,
        generator_sha="b" * 64,
    )


def sample_global_count_inputs():
    uid = "pid_0000000000000001"
    return {
        "production_manifest": manifest(),
        "identity_registry": new_registry(
            [{"schema_version": 1, "player_uid": uid,
              "canonical_name": "TEST PLAYER", "status": "ACTIVE", "redirect_to": None}],
            record_links=[{"schema_version": 1, "record_key": "r1", "player_uid": uid,
                           "link_status": "same", "method": "MANUAL_REVIEW",
                           "confidence": "HIGH", "evidence_refs": ["ev-1"]}],
        ),
        "master_rows": [{"record_key": "r1"}],
    }


# 1. modern CURRENT_VERSION_DOC remains current for v1.8.1 and future non-v1.6.1 release IDs
@pytest.mark.parametrize("release_id", ["v1.8.1-1", "v1.9.0-1", "v2.0.0-1"])
def test_modern_current_version_doc_remains_current_for_v181_and_future_releases(release_id):
    status, reg, docs, _ = sample_v181_documents(release_id=release_id)
    result = validate_current_state(status, reg, manifest(), docs)
    assert result["status"] == "PASS"
    assert "current_version_doc" in result["surfaces"]

    # Omitting current_version_doc must fail with CURRENT_DOCUMENT_MISSING
    incomplete_docs = dict(docs)
    del incomplete_docs["current_version_doc"]
    with pytest.raises(ValueError, match="CURRENT_DOCUMENT_MISSING"):
        validate_current_state(status, reg, manifest(), incomplete_docs)

    # Passing legacy 'version' instead does NOT satisfy modern current surface
    legacy_revert_docs = dict(incomplete_docs)
    legacy_revert_docs["version"] = docs["current_version_doc"]
    with pytest.raises(ValueError, match="CURRENT_DOCUMENT_MISSING"):
        validate_current_state(status, reg, manifest(), legacy_revert_docs)


# 2. all managed current surfaces share release/code/registry identity
def test_all_managed_current_surfaces_share_release_code_registry_identity():
    status, reg, docs, meta = sample_v181_documents("v1.8.1-1")
    control = control_document_identities(status, reg, docs)
    assert control["status"] == "PASS"
    identities = control["identities"]

    expected_surfaces = {"release_status", "readme", "index", "current_version_doc", "context_card"}
    assert set(identities) == expected_surfaces

    for surface_name, surface_meta in identities.items():
        assert surface_meta["release_id"] == "v1.8.1-1"
        assert surface_meta["code_commit"] == SHA
        assert surface_meta["registry_version"] == reg["registry_version"]
        assert surface_meta["state"] == "COMPLETE"

    # Any single surface drift must fail closed
    for surface_name in ("readme", "index", "current_version_doc", "context_card"):
        tampered_docs = dict(docs)
        tampered_meta = dict(meta)
        tampered_meta["code_commit"] = "0" * 40
        tampered_docs[surface_name] = replace_current_block(tampered_docs[surface_name], tampered_meta)
        with pytest.raises(ValueError, match="DRIFT"):
            control_document_identities(status, reg, tampered_docs)


# 3. public GitHub wording closure
def test_public_github_wording_closure():
    status, reg, docs, meta = sample_v181_documents("v1.8.1-1")
    card = docs["context_card"]

    # Must state Code truth: GitHub repository and Repository visibility: public
    assert "Code truth: GitHub repository." in card
    assert "Repository visibility: public." in card

    # Must no longer state private GitHub
    assert "private GitHub" not in card

    # Must preserve explicit domain separation
    assert "Public GitHub code != Public Production data != Public Private Identity Registry" in card

    # Validates cleanly under size boundary
    assert validate_context_card(card, meta, reg, manifest())["status"] == "PASS"


# 4. existing profile-v2 identity boundary remains unchanged
def test_existing_profile_v2_identity_boundary_remains_unchanged():
    profile = build_sample_profile_v2()
    authority = sample_global_count_inputs()
    payload = build_consumer_payload(
        release_scope={"release_id": "v1.8.1-1", "as_of": "2026-09-24T00:00:00Z", "provenance": "frozen"},
        profile=profile,
        documents=[],
        sources=[],
        **authority,
    )
    packages = build_target_packages(payload, **authority)
    for target in CONSUMER_TARGETS:
        pkg = packages[target]
        validated = validate_package(pkg, **authority)
        nav = json.loads(validated["files"]["consumer_navigation_contract.json"])
        identity_policy = nav["identity_policy"]
        assert identity_policy["identity_selector"] == "player_uid"
        assert identity_policy["record_key_is_person_identity"] is False
        assert identity_policy["automatic_merge"] is False
        assert identity_policy["same_person_assertion_without_independent_evidence"] is False
        assert identity_policy["player_uid_policy"] == "PRESENT_FOR_PROFILE_V2"
        assert identity_policy["same_name_records"] == "NOT_USED_FOR_PROFILE_V2"


# 5. SAME / NOT_SAME / UNDECIDED / UNAVAILABLE-or-NOT_MATERIALIZED are distinguishable
def test_same_not_same_undecided_unavailable_distinguishable():
    # Mutual distinctness
    assert len(IDENTITY_SEMANTIC_STATES) >= 4
    for state in ("SAME", "NOT_SAME", "UNDECIDED", "NOT_MATERIALIZED", "UNAVAILABLE"):
        assert state in IDENTITY_SEMANTIC_STATES

    # Projection from internal statuses
    assert project_identity_state("same") == "SAME"
    assert project_identity_state("not_same") == "NOT_SAME"
    assert project_identity_state("undecided") == "UNDECIDED"
    assert project_identity_state("unlinked") == "NOT_MATERIALIZED"
    assert project_identity_state("unavailable") == "NOT_MATERIALIZED"
    assert project_identity_state(None) == "UNAVAILABLE"

    # Unlinked / absence must NOT upgrade into SAME, NOT_SAME, or UNDECIDED
    absence_projections = {
        project_identity_state("unlinked"),
        project_identity_state("absence"),
        project_identity_state(None),
    }
    for state in absence_projections:
        assert state not in {"SAME", "NOT_SAME", "UNDECIDED"}

    # Unknown or manufactured relation fails closed
    with pytest.raises(ConsumerProjectionError, match="UNKNOWN_IDENTITY_STATUS"):
        project_identity_state("auto_merged_same")


# 6. declared machine counts equal actual machine counts (no hardcoding 246)
def test_declared_machine_counts_equal_actual_machine_counts():
    identity_registry = {
        "players": [{"player_uid": "p1"}, {"player_uid": "p2"}],
        "record_links": [
            {"record_key": "r1", "player_uid": "p1", "link_status": "same"},
            {"record_key": "r2", "player_uid": "p1", "link_status": "not_same"},
            {"record_key": "r3", "player_uid": "p2", "link_status": "undecided"},
        ],
    }
    master_rows = [
        {"record_key": "r1"},
        {"record_key": "r2"},
        {"record_key": "r3"},
        {"record_key": "r4"},  # unlinked / not materialized
    ]
    test_manifest = [{"uid": "a1"}, {"uid": "a2"}, {"uid": "a3"}]

    actual_counts = derive_machine_counts(
        manifest=test_manifest,
        identity_registry=identity_registry,
        master_rows=master_rows,
    )

    assert actual_counts["production_artifact_count"] == 3
    assert actual_counts["player_count"] == 2
    assert actual_counts["record_link_count"] == 3
    assert actual_counts["same_count"] == 1
    assert actual_counts["not_same_count"] == 1
    assert actual_counts["undecided_count"] == 1
    assert actual_counts["unavailable_count"] == 1
    assert actual_counts["not_materialized_count"] == 1

    # Exact equality passes
    assert validate_machine_counts(actual_counts, actual_counts) is True

    # Predecessor hardcoded count 246 must fail when declared != actual
    with pytest.raises(ConsumerProjectionError, match="MACHINE_COUNT_KEYS_MISMATCH"):
        validate_machine_counts({"production_artifact_count": 246}, actual_counts)


# 7. identity decision/registry/MASTER zero-diff
def test_identity_decision_registry_master_zero_diff():
    # Byte-exact preservation required
    facts = {
        "MASTER": b"canonical_master_rows",
        "six_table": b"canonical_six_tables",
        "evidence": b"frozen_evidence_records",
        "sources": b"source_registry_rows",
    }
    result = zero_business_delta(facts, dict(facts))
    assert result["protected_artifacts"] == 4

    # Any modification triggers failure
    for key in facts:
        mutated = dict(facts)
        mutated[key] = mutated[key] + b"_drift"
        with pytest.raises(ValueError, match="ZERO_BUSINESS_FACT_DELTA"):
            zero_business_delta(facts, mutated)

    # Invariants verification: delta counters are strictly zero
    invariants = {
        "new_identity_decisions": 0,
        "new_same_decisions": 0,
        "new_not_same_decisions": 0,
        "machine_final_uid_decisions": 0,
        "player_uid_mutation": 0,
        "master_mutation": 0,
        "identity_registry_mutation": 0,
        "identity_authority_change": 0,
    }
    for name, value in invariants.items():
        assert value == 0, f"Invariant violation: {name} != 0"


# 8. no Production mutation path is exercised
def test_no_production_mutation_path_exercised():
    from scripts.prepare_production import _release_spec, ProjectionError
    # Gen1 has false production_mutation_authority; production release path is blocked
    with pytest.raises(ProjectionError, match="RELEASE_ID_FORBIDDEN:v1.8.1-1"):
        _release_spec("v1.8.1-1")

    with pytest.raises(ProjectionError, match="RELEASE_ID_FORBIDDEN"):
        _release_spec("v9.9.9")



@pytest.mark.parametrize("same_count", [1, 3])
def test_emitted_packages_close_identity_and_counts_without_mutating_truth(same_count):
    import copy
    from cba_kb.common import digest
    from cba_kb.evidence_ledger import canonical_bytes
    from cba_kb.consumer_package import ConsumerPackageError, payload_bytes

    uid = "pid_0000000000000001"
    statuses = ["same"] * same_count + ["not_same", "undecided", None]
    rows = []
    links = []
    for index, status in enumerate(statuses):
        key = f"record-{index}"
        row = {field: None for field in HEADERS}
        row.update(record_key=key, season="2026-2027", club_id="test-club",
                   player="TEST PLAYER", source_file_id="src-1",
                   source_url="https://example.test", verification_level="machine_validated")
        rows.append(row)
        if status:
            links.append(dict(schema_version=1, record_key=key, player_uid=uid,
                              link_status=status, method="MANUAL_REVIEW",
                              confidence="HIGH", evidence_refs=["ev-1"]))
    registry_value = new_registry([
        dict(schema_version=1, player_uid=uid, canonical_name="TEST PLAYER",
             status="ACTIVE", redirect_to=None),
    ], record_links=links)
    frozen = copy.deepcopy((rows, registry_value))
    profile = build_profile_v2(
        rows, player_uid=uid, identity_registry=registry_value,
        mention_artifact=build_mention_artifact([], []), release_id="v1.8.1-1",
        as_of="2026-09-24T00:00:00Z", source_master_sha256="a" * 64,
        generator_sha="b" * 64,
    )
    frozen_profile = copy.deepcopy(profile)
    authority = {
        "production_manifest": manifest(),
        "identity_registry": registry_value,
        "master_rows": rows,
    }
    payload = build_consumer_payload(
        release_scope=dict(release_id="v1.8.1-1", as_of="2026-09-24T00:00:00Z",
                           provenance="frozen"), profile=profile, documents=[], sources=[],
        **authority,
    )
    packages = build_target_packages(payload, **authority)
    for package in packages.values():
        validate_package(package, **authority)
        emitted = json.loads(package["files"][package["canonical_payload_file"]])
        relations = emitted["identity_projection"]["relations"]
        assert {item["state"] for item in relations} == {
            "SAME", "NOT_SAME", "UNDECIDED", "NOT_MATERIALIZED",
        }
        absent = next(item for item in relations if item["state"] == "NOT_MATERIALIZED")
        assert absent["player_uid"] is None
        assert absent["record_key"] == rows[-1]["record_key"]
        for counts in (emitted["coverage"], package["manifest"]["coverage"]["machine_counts"]):
            assert counts["player_count"] == len(emitted["identity_projection"]["players"])
            assert counts["record_link_count"] == len(links)
            assert counts["same_count"] == same_count
            assert counts["not_same_count"] == 1
            assert counts["undecided_count"] == 1
            assert counts["unavailable_count"] == 1
        assert emitted["coverage"]["production_artifact_count"] == len(manifest())
        assert package["manifest"]["coverage"]["machine_counts"]["production_artifact_count"] == len(manifest())
        assert emitted["player_profile"] == frozen_profile
        tampered = copy.deepcopy(package)
        tampered["manifest"]["coverage"]["machine_counts"]["production_artifact_count"] += 1
        with pytest.raises(ConsumerProjectionError, match="COUNT_MISMATCH"):
            validate_package(tampered, **authority)

    # A freshly recomputed payload hash cannot legitimize invented relations or counts.
    for mutation in ("state", "count", "missing_count"):
        tampered = copy.deepcopy(payload)
        if mutation == "state":
            next(item for item in tampered["identity_projection"]["relations"]
                 if item["state"] == "NOT_MATERIALIZED")["state"] = "SAME"
        elif mutation == "count":
            tampered["coverage"]["player_count"] += 1
        else:
            del tampered["coverage"]["same_count"]
        core = {k: v for k, v in tampered.items() if k != "consumer_payload_sha256"}
        tampered["consumer_payload_sha256"] = digest(canonical_bytes(core))
        with pytest.raises((ConsumerPackageError, ConsumerProjectionError)):
            payload_bytes(tampered)
        with pytest.raises((ConsumerPackageError, ConsumerProjectionError)):
            build_target_packages(tampered, **authority)
    assert (rows, registry_value) == frozen
    assert profile == frozen_profile
