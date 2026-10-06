import copy
import json

import pytest

from automation.hardening import (
    CURRENT_WORLD_CONTROL_TRIAD_MISMATCH,
    NON_CONVERGENT_REPAIR,
    PREPARE_RECOVERY_CONTRACT_INCOMPLETE,
    Q2_RUNTIME_CONFORMANCE_FAILED,
    RELEASE_IDENTITY_NOT_READY,
    TASK_CONTRACT_INVALID,
    HardeningFailure,
    _recovery_fixture,
    _triad_fixture,
    run_hardening_canary,
    run_preflight_document,
    validate_control_triad,
    validate_failure_convergence,
    validate_prepare_recovery,
    validate_q2_runtime,
    validate_release_identity,
    validate_task_contract,
)


def _classification(call):
    with pytest.raises(HardeningFailure) as caught:
        call()
    return caught.value.classification


def test_authority_output_contract_fails_closed_and_stateful_passes():
    assert _classification(lambda: validate_task_contract({
        "authority_mode": "READ_ONLY",
        "required_outputs": ["PREPARED", "GO_PACKET_COMPLETE"],
        "required_state_transitions": ["PREPARED"],
        "authorized_mutations": [],
    })) == TASK_CONTRACT_INVALID
    assert validate_task_contract({
        "authority_mode": "STATEFUL_PREPARE",
        "required_outputs": ["GO_PACKET_COMPLETE"],
        "required_state_transitions": ["PREPARED"],
        "authorized_mutations": ["RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN"],
    })["status"] == "PASS"


@pytest.mark.parametrize(
    ("required_outputs", "required_state_transitions"),
    [
        (["PREPARED"], []),
        (["FROZEN_PLAN"], []),
        ([], ["FROZEN_PLAN"]),
        ([], ["PREPARED"]),
    ],
)
def test_freeze_bound_output_or_transition_requires_freeze_plan(
    required_outputs, required_state_transitions,
):
    contract = {
        "authority_mode": "STATEFUL_PREPARE",
        "required_outputs": required_outputs,
        "required_state_transitions": required_state_transitions,
        "authorized_mutations": ["RESERVE_STAGING", "WRITE_PREPARE_JOURNAL"],
    }
    assert _classification(
        lambda: validate_task_contract(contract)
    ) == TASK_CONTRACT_INVALID


def test_q2_contract_binds_every_required_field_and_is_not_technical_pass():
    expected = {
        "profile_id": "Q2_INDEPENDENT_TECH_QA@1",
        "surface": "CODEX",
        "vendor_backend": "OPENAI/SOL",
        "observed_model_or_family": "SOL",
        "runtime_environment": "EXECUTABLE_SANDBOX",
        "review_mode": "FULL_Q2",
        "reviewed_head_sha": "a" * 40,
        "checkout_capability": "LITERAL_EXACT_HEAD",
        "workspace_isolated": True,
        "workspace_clean_start": True,
        "reviewer_may_modify_reviewed_head": False,
    }
    result = validate_q2_runtime(expected, copy.deepcopy(expected))
    assert result["facts"]["execution_contract_only"] is True
    assert result["facts"]["technical_q2_pass"] is False
    assert result["facts"]["merge_ready"] is False
    wrong = copy.deepcopy(expected)
    wrong.update(surface="WEB", checkout_capability="REMOTE_INSPECTION_ONLY")
    assert _classification(lambda: validate_q2_runtime(expected, wrong)) == Q2_RUNTIME_CONFORMANCE_FAILED
    missing = copy.deepcopy(expected)
    missing.pop("workspace_clean_start")
    assert _classification(lambda: validate_q2_runtime(expected, missing)) == Q2_RUNTIME_CONFORMANCE_FAILED


def test_same_class_failure_reports_machine_fact_without_provider_routing():
    evidence = {
        "invariant_id": "security.secret-guard",
        "failure_class": "SECURITY_INVARIANT",
        "consecutive_same_class_count": 2,
        "proposed_repair_mode": "SPOT_PATCH",
    }
    assert _classification(lambda: validate_failure_convergence(evidence)) == NON_CONVERGENT_REPAIR
    evidence["proposed_repair_mode"] = "STRUCTURAL_ROOT_CAUSE"
    result = validate_failure_convergence(evidence)
    assert result["facts"]["routing_authority"] == "EXTERNAL_WEB_D1"
    assert "vendor" not in json.dumps(result).lower()


def test_release_identity_uses_canonical_release_spec_support():
    supported = {
        "target_product_version": "v2.0.0",
        "proposed_release_attempt": "v2.0.0-1",
        "product_candidate_sha": "a" * 40,
        "release_execution_role": "RELEASE_EXECUTOR",
    }
    assert validate_release_identity(supported)["status"] == "PASS"
    missing = dict(supported, proposed_release_attempt=None)
    assert _classification(lambda: validate_release_identity(missing)) == RELEASE_IDENTITY_NOT_READY
    unsupported = dict(supported, target_product_version="v2.0.1", proposed_release_attempt="v2.0.1-99")
    assert _classification(lambda: validate_release_identity(unsupported)) == RELEASE_IDENTITY_NOT_READY


def test_control_triad_catches_299_294_and_accepts_canonical_classes():
    mismatch = _triad_fixture(299, 294)
    assert _classification(lambda: validate_control_triad(*mismatch)) == CURRENT_WORLD_CONTROL_TRIAD_MISMATCH
    exact = _triad_fixture(2, 2, include_classes=True)
    result = validate_control_triad(*exact)
    assert result["facts"]["active_production_targets"] == ["active-000", "active-001"]
    assert result["facts"]["historical_retired_policy_targets"] == ["retired-001"]
    assert result["facts"]["reserved_staging_targets"] == ["reserved-001"]


def test_prepare_recovery_requires_complete_exact_id_reuse_contract():
    projection, allocation, journal = _recovery_fixture()
    broken = copy.deepcopy(journal)
    broken["reservations"]["code/new.py"] = "reserved-duplicate"
    assert _classification(
        lambda: validate_prepare_recovery(projection, allocation, broken)
    ) == PREPARE_RECOVERY_CONTRACT_INCOMPLETE
    result = validate_prepare_recovery(projection, allocation, journal)
    assert result["facts"]["reentry_rule"] == "REUSE_EXACT_IDS"
    assert result["facts"]["duplicate_reservation_count"] == 0


def test_preflight_document_and_zero_production_canary():
    result = run_preflight_document({"checks": [{
        "kind": "failure_convergence",
        "arguments": {
            "invariant_id": "privacy.no-private-id",
            "failure_class": "PRIVACY_INVARIANT",
            "consecutive_same_class_count": 1,
            "proposed_repair_mode": "SPOT_PATCH",
        },
    }]})
    assert result["classification"] == "HARDENING_PREFLIGHT_PASS"
    canary = run_hardening_canary()
    assert canary["status"] == "PASS"
    assert canary["case_count"] == 12
    assert canary["production_mutation_count"] == 0
    assert [case["case"] for case in canary["cases"]] == [
        "N1", "P1", "N1_FREEZE_PLAN", "N2", "P2", "N3", "P3", "N4", "N5", "P4", "N6", "P5",
    ]
