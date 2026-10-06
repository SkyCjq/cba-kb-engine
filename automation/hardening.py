"""Deterministic, fail-closed workflow and release preflight composition.

This module deliberately contains no provider routing and performs no remote
writes.  Release-specific truth is delegated to ``prepare_production`` so P2A
does not grow a second release-spec or production-policy implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from scripts.prepare_production import (
    ProjectionError,
    _release_spec,
    validate_policy_reconciliation,
    validate_reservation,
)


TASK_CONTRACT_INVALID = "TASK_CONTRACT_INVALID"
Q2_RUNTIME_CONFORMANCE_FAILED = "Q2_RUNTIME_CONFORMANCE_FAILED"
NON_CONVERGENT_REPAIR = "NON_CONVERGENT_REPAIR"
RELEASE_IDENTITY_NOT_READY = "RELEASE_IDENTITY_NOT_READY"
CURRENT_WORLD_CONTROL_TRIAD_MISMATCH = "CURRENT_WORLD_CONTROL_TRIAD_MISMATCH"
PREPARE_RECOVERY_CONTRACT_INCOMPLETE = "PREPARE_RECOVERY_CONTRACT_INCOMPLETE"


@dataclass(frozen=True)
class HardeningFailure(RuntimeError):
    classification: str
    reason: str
    facts: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "BLOCKED",
            "classification": self.classification,
            "reason": self.reason,
            "facts": dict(self.facts),
        }


def _fail(classification: str, reason: str, **facts: Any) -> None:
    raise HardeningFailure(classification, reason, facts)


def _pass(check: str, **facts: Any) -> dict[str, Any]:
    return {"status": "PASS", "classification": f"{check}_PASS", "facts": facts}


_OUTPUT_ACTIONS = {
    "FROZEN_PLAN": frozenset({
        "RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN",
    }),
    "PREPARED": frozenset({
        "RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN",
    }),
    "GO_PACKET_COMPLETE": frozenset({
        "RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN",
    }),
}

_STATE_TRANSITION_ACTIONS = {
    "RESERVED": frozenset({"RESERVE_STAGING"}),
    "REPROJECTED": frozenset({"RESERVE_STAGING"}),
    "FROZEN_PLAN": frozenset({
        "RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN",
    }),
    "PREPARED": frozenset({
        "RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN",
    }),
}


def validate_task_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Reject authority/output combinations that cannot be executed."""
    mode = contract.get("authority_mode")
    outputs = set(contract.get("required_outputs") or [])
    transitions = set(contract.get("required_state_transitions") or [])
    mutations = set(contract.get("authorized_mutations") or [])
    if mode not in {"READ_ONLY", "STATEFUL_PREPARE"}:
        _fail(TASK_CONTRACT_INVALID, "authority_mode is missing or unsupported", authority_mode=mode)
    stateful_outputs = outputs & set(_OUTPUT_ACTIONS)
    stateful_transitions = transitions & set(_STATE_TRANSITION_ACTIONS)
    required = set().union(*(_OUTPUT_ACTIONS[value] for value in stateful_outputs)) if stateful_outputs else set()
    if stateful_transitions:
        required |= set().union(*(
            _STATE_TRANSITION_ACTIONS[value] for value in stateful_transitions
        ))
    missing = sorted(required - mutations)
    if (mode == "READ_ONLY" and (stateful_outputs or stateful_transitions)) or missing:
        _fail(
            TASK_CONTRACT_INVALID,
            "required output/state transition exceeds the mutation authority envelope",
            authority_mode=mode,
            stateful_outputs=sorted(stateful_outputs),
            stateful_transitions=sorted(stateful_transitions),
            missing_authorized_mutations=missing,
        )
    if mode == "STATEFUL_PREPARE" and not required:
        _fail(TASK_CONTRACT_INVALID, "stateful PREPARE authority has no stateful required output")
    return _pass("TASK_CONTRACT", authority_mode=mode, required_mutations=sorted(required))


Q2_FIELDS = (
    "profile_id",
    "surface",
    "vendor_backend",
    "observed_model_or_family",
    "runtime_environment",
    "review_mode",
    "reviewed_head_sha",
    "checkout_capability",
    "workspace_isolated",
    "workspace_clean_start",
    "reviewer_may_modify_reviewed_head",
)


def validate_q2_runtime(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    """Prove execution-contract conformance, never technical-Q2 success."""
    missing = [field for field in Q2_FIELDS if field not in expected or field not in observed]
    mismatches = {
        field: {"expected": expected.get(field), "observed": observed.get(field)}
        for field in Q2_FIELDS
        if field not in missing and expected[field] != observed[field]
    }
    invalid = []
    if observed.get("checkout_capability") != "LITERAL_EXACT_HEAD":
        invalid.append("checkout_capability")
    if observed.get("workspace_isolated") is not True:
        invalid.append("workspace_isolated")
    if observed.get("workspace_clean_start") is not True:
        invalid.append("workspace_clean_start")
    if observed.get("reviewer_may_modify_reviewed_head") is not False:
        invalid.append("reviewer_may_modify_reviewed_head")
    if not re.fullmatch(r"[0-9a-f]{40}", str(observed.get("reviewed_head_sha", ""))):
        invalid.append("reviewed_head_sha")
    if missing or mismatches or invalid:
        _fail(
            Q2_RUNTIME_CONFORMANCE_FAILED,
            "Q2 execution contract is not conformant",
            missing_fields=missing,
            mismatches=mismatches,
            invalid_fields=sorted(set(invalid)),
        )
    return _pass(
        "Q2_RUNTIME_CONFORMANCE",
        execution_contract_only=True,
        technical_q2_pass=False,
        merge_ready=False,
        reviewed_head_sha=observed["reviewed_head_sha"],
    )


_LOCAL_REPAIR_MODES = frozenset({"SPOT_PATCH", "BLACKLIST_PATCH", "LOCAL_PATCH"})


def validate_failure_convergence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    invariant = evidence.get("invariant_id")
    failure_class = evidence.get("failure_class")
    count = evidence.get("consecutive_same_class_count")
    repair_mode = evidence.get("proposed_repair_mode")
    if not invariant or not failure_class or not isinstance(count, int) or count < 1 or not repair_mode:
        _fail(NON_CONVERGENT_REPAIR, "convergence evidence is incomplete", evidence=dict(evidence))
    if count >= 2 and repair_mode in _LOCAL_REPAIR_MODES:
        _fail(
            NON_CONVERGENT_REPAIR,
            "repeated same-invariant failure still proposes a local repair",
            invariant_id=invariant,
            failure_class=failure_class,
            consecutive_same_class_count=count,
            proposed_repair_mode=repair_mode,
        )
    return _pass(
        "FAILURE_CONVERGENCE",
        invariant_id=invariant,
        failure_class=failure_class,
        consecutive_same_class_count=count,
        proposed_repair_mode=repair_mode,
        routing_authority="EXTERNAL_WEB_D1",
    )


def validate_release_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Delegate attempt support to the canonical release-spec lookup."""
    product_version = identity.get("target_product_version")
    attempt = identity.get("proposed_release_attempt")
    candidate = identity.get("product_candidate_sha")
    role = identity.get("release_execution_role")
    if not all((product_version, attempt, candidate, role)):
        _fail(RELEASE_IDENTITY_NOT_READY, "release identity binding is incomplete")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", str(product_version)):
        _fail(RELEASE_IDENTITY_NOT_READY, "target product version is invalid", target_product_version=product_version)
    if not re.fullmatch(r"[0-9a-f]{40}", str(candidate)):
        _fail(RELEASE_IDENTITY_NOT_READY, "product candidate SHA is invalid")
    if role != "RELEASE_EXECUTOR":
        _fail(RELEASE_IDENTITY_NOT_READY, "release execution role is not bound", release_execution_role=role)
    if not str(attempt).startswith(f"{product_version}-"):
        _fail(RELEASE_IDENTITY_NOT_READY, "product version and release attempt are not separately consistent")
    try:
        spec = _release_spec(str(attempt))
    except ProjectionError as exc:
        _fail(RELEASE_IDENTITY_NOT_READY, "canonical release tooling does not support the attempt", canonical_error=str(exc))
    return _pass(
        "RELEASE_IDENTITY",
        target_product_version=product_version,
        release_attempt=attempt,
        product_candidate_sha=candidate,
        release_execution_role=role,
        canonical_product_baseline_sha=spec["product_baseline_sha"],
    )


def validate_control_triad(
    projection: Mapping[str, Any],
    allocation: Mapping[str, Any],
    policy: Mapping[str, Any],
    manifest_target_ids: list[str],
) -> dict[str, Any]:
    """Bind manifest coverage to canonical production-policy reconciliation."""
    active = [item.get("id") for item in projection.get("existing_targets") or []]
    if any(not value for value in active) or len(active) != len(set(active)):
        _fail(CURRENT_WORLD_CONTROL_TRIAD_MISMATCH, "projected active target set is invalid")
    if len(manifest_target_ids) != len(set(manifest_target_ids)) or set(active) != set(manifest_target_ids):
        _fail(
            CURRENT_WORLD_CONTROL_TRIAD_MISMATCH,
            "release-status active targets and canonical manifest mapping differ",
            active_count=len(active),
            manifest_count=len(set(manifest_target_ids)),
            missing_manifest_ids=sorted(set(active) - set(manifest_target_ids)),
            unexpected_manifest_ids=sorted(set(manifest_target_ids) - set(active)),
        )
    try:
        reconciliation = validate_policy_reconciliation(projection, allocation, policy)
    except (ProjectionError, KeyError, TypeError) as exc:
        _fail(
            CURRENT_WORLD_CONTROL_TRIAD_MISMATCH,
            "canonical production-policy reconciliation failed",
            canonical_error=str(exc),
        )
    return _pass("CURRENT_WORLD_CONTROL_TRIAD", **reconciliation, manifest_targets=sorted(manifest_target_ids))


def _json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_prepare_recovery(
    projection: Mapping[str, Any],
    allocation: Mapping[str, Any],
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    """Require exact reservation reuse after the stateful PREPARE boundary."""
    required = {
        "transaction_id",
        "staging_folder_id",
        "reservations",
        "release_binding",
        "allocation_sha256",
        "last_completed_stage",
        "reentry_rule",
    }
    missing = sorted(required - set(journal))
    expected_binding = {
        "release_id": allocation.get("release_id"),
        "status_id": allocation.get("status_id"),
    }
    inconsistent = []
    if journal.get("staging_folder_id") != allocation.get("staging_id"):
        inconsistent.append("staging_folder_id")
    if journal.get("reservations") != allocation.get("reservations"):
        inconsistent.append("reservations")
    if journal.get("release_binding") != expected_binding:
        inconsistent.append("release_binding")
    if journal.get("allocation_sha256") != _json_sha256(allocation):
        inconsistent.append("allocation_sha256")
    if journal.get("last_completed_stage") != "RESERVED":
        inconsistent.append("last_completed_stage")
    if journal.get("reentry_rule") != "REUSE_EXACT_IDS":
        inconsistent.append("reentry_rule")
    try:
        validate_reservation(projection, allocation)
    except (ProjectionError, KeyError, TypeError) as exc:
        inconsistent.append(f"canonical_reservation:{exc}")
    if missing or inconsistent or not journal.get("transaction_id"):
        _fail(
            PREPARE_RECOVERY_CONTRACT_INCOMPLETE,
            "partial PREPARE recovery is not bound to exact existing IDs",
            missing_fields=missing,
            inconsistent_fields=inconsistent,
        )
    return _pass(
        "PREPARE_RECOVERY_CONTRACT",
        transaction_id=journal["transaction_id"],
        staging_folder_id=journal["staging_folder_id"],
        reservation_count=len(journal["reservations"]),
        reentry_rule=journal["reentry_rule"],
        duplicate_reservation_count=0,
    )


def run_preflight_document(document: Mapping[str, Any]) -> dict[str, Any]:
    validators: dict[str, Callable[..., dict[str, Any]]] = {
        "task_contract": validate_task_contract,
        "q2_runtime": validate_q2_runtime,
        "failure_convergence": validate_failure_convergence,
        "release_identity": validate_release_identity,
        "control_triad": validate_control_triad,
        "prepare_recovery": validate_prepare_recovery,
    }
    results = []
    for index, check in enumerate(document.get("checks") or []):
        kind = check.get("kind") if isinstance(check, Mapping) else None
        if kind not in validators:
            _fail(TASK_CONTRACT_INVALID, "unknown hardening preflight kind", index=index, kind=kind)
        arguments = check.get("arguments")
        if not isinstance(arguments, Mapping):
            _fail(TASK_CONTRACT_INVALID, "preflight arguments must be an object", index=index, kind=kind)
        results.append(validators[kind](**arguments) if kind in {"q2_runtime", "control_triad", "prepare_recovery"} else validators[kind](arguments))
    if not results:
        _fail(TASK_CONTRACT_INVALID, "at least one hardening preflight check is required")
    return {"status": "PASS", "classification": "HARDENING_PREFLIGHT_PASS", "checks": results}


def _triad_fixture(active_count: int, manifest_count: int, *, include_classes: bool = False):
    active_ids = [f"active-{index:03d}" for index in range(active_count)]
    existing = [{
        "id": value,
        "mime": "text/plain",
        "mode": "binary",
        "allowed_parents": ["current"],
    } for value in active_ids]
    projection: dict[str, Any] = {"release_id": "v2.0.0-1", "existing_targets": existing}
    targets = {
        item["id"]: {key: item[key] for key in ("mime", "mode", "allowed_parents")}
        for item in existing
    }
    reservations: dict[str, str] = {}
    if include_classes:
        projection["historical_retired_policy_target_ids"] = ["retired-001"]
        projection["historical_retired_policy_markers"] = {"retired-001": "v1.9.0-1"}
        targets["retired-001"] = {
            "mime": "text/plain", "mode": "binary", "allowed_parents": ["history"],
            "retire_in_release": "v1.9.0-1",
        }
        reservations = {"code/new.py": "reserved-001"}
        targets["reserved-001"] = {
            "mime": "text/plain", "mode": "binary", "allowed_parents": ["staging"],
        }
    allocation = {"reservations": reservations}
    return projection, allocation, {"targets": targets}, active_ids[:manifest_count]


def _recovery_fixture():
    projection = {
        "release_id": "v2.0.0-1",
        "status_id": "status-001",
        "semantic_delta_signature": "delta-001",
        "new_targets": [{"logical_key": "code/new.py"}],
        "existing_targets": [{"id": "active-001"}],
        "historical_retired_policy_target_ids": [],
    }
    allocation = {
        "release_id": "v2.0.0-1",
        "status_id": "status-001",
        "semantic_delta_signature": "delta-001",
        "staging_id": "staging-001",
        "reservations": {"code/new.py": "reserved-001"},
    }
    journal = {
        "transaction_id": "txn-001",
        "staging_folder_id": "staging-001",
        "reservations": {"code/new.py": "reserved-001"},
        "release_binding": {"release_id": "v2.0.0-1", "status_id": "status-001"},
        "allocation_sha256": _json_sha256(allocation),
        "last_completed_stage": "RESERVED",
        "reentry_rule": "REUSE_EXACT_IDS",
    }
    return projection, allocation, journal


def run_hardening_canary() -> dict[str, Any]:
    """Zero-Production synthetic proof of all frozen positive/negative cases."""
    head = "a" * 40
    q2 = {
        "profile_id": "Q2_INDEPENDENT_TECH_QA@1",
        "surface": "CODEX",
        "vendor_backend": "OPENAI/SOL",
        "observed_model_or_family": "SOL",
        "runtime_environment": "EXECUTABLE_SANDBOX",
        "review_mode": "FULL_Q2",
        "reviewed_head_sha": head,
        "checkout_capability": "LITERAL_EXACT_HEAD",
        "workspace_isolated": True,
        "workspace_clean_start": True,
        "reviewer_may_modify_reviewed_head": False,
    }
    cases: list[dict[str, Any]] = []

    def positive(case_id: str, call: Callable[[], dict[str, Any]]) -> None:
        result = call()
        cases.append({"case": case_id, "expected": "PASS", "observed": result["status"], "classification": result["classification"]})

    def negative(case_id: str, classification: str, call: Callable[[], Any]) -> None:
        try:
            call()
        except HardeningFailure as exc:
            observed = exc.classification
        else:
            observed = "UNEXPECTED_PASS"
        if observed != classification:
            raise AssertionError(f"{case_id}: expected {classification}, observed {observed}")
        cases.append({"case": case_id, "expected": classification, "observed": observed})

    negative("N1", TASK_CONTRACT_INVALID, lambda: validate_task_contract({
        "authority_mode": "READ_ONLY", "required_outputs": ["PREPARED", "GO_PACKET_COMPLETE"],
        "required_state_transitions": ["PREPARED"], "authorized_mutations": [],
    }))
    positive("P1", lambda: validate_task_contract({
        "authority_mode": "STATEFUL_PREPARE", "required_outputs": ["PREPARED"],
        "required_state_transitions": ["RESERVED", "PREPARED"],
        "authorized_mutations": ["RESERVE_STAGING", "WRITE_PREPARE_JOURNAL", "FREEZE_PLAN"],
    }))
    negative("N1_FREEZE_PLAN", TASK_CONTRACT_INVALID, lambda: validate_task_contract({
        "authority_mode": "STATEFUL_PREPARE", "required_outputs": ["PREPARED"],
        "required_state_transitions": ["FROZEN_PLAN", "PREPARED"],
        "authorized_mutations": ["RESERVE_STAGING", "WRITE_PREPARE_JOURNAL"],
    }))
    wrong_q2 = dict(q2, surface="WEB", checkout_capability="REMOTE_INSPECTION_ONLY")
    negative("N2", Q2_RUNTIME_CONFORMANCE_FAILED, lambda: validate_q2_runtime(q2, wrong_q2))
    positive("P2", lambda: validate_q2_runtime(q2, dict(q2)))
    negative("N3", NON_CONVERGENT_REPAIR, lambda: validate_failure_convergence({
        "invariant_id": "privacy.no-private-id", "failure_class": "PRIVACY_INVARIANT",
        "consecutive_same_class_count": 2, "proposed_repair_mode": "BLACKLIST_PATCH",
    }))
    positive("P3", lambda: validate_failure_convergence({
        "invariant_id": "privacy.no-private-id", "failure_class": "PRIVACY_INVARIANT",
        "consecutive_same_class_count": 2, "proposed_repair_mode": "STRUCTURAL_ROOT_CAUSE",
    }))
    negative("N4", RELEASE_IDENTITY_NOT_READY, lambda: validate_release_identity({
        "target_product_version": "v2.0.1", "proposed_release_attempt": None,
        "product_candidate_sha": head, "release_execution_role": "RELEASE_EXECUTOR",
    }))
    mismatch = _triad_fixture(299, 294)
    negative("N5", CURRENT_WORLD_CONTROL_TRIAD_MISMATCH, lambda: validate_control_triad(*mismatch))
    exact = _triad_fixture(2, 2, include_classes=True)
    positive("P4", lambda: validate_control_triad(*exact))
    recovery = _recovery_fixture()
    incomplete = dict(recovery[2])
    incomplete.pop("reentry_rule")
    negative("N6", PREPARE_RECOVERY_CONTRACT_INCOMPLETE, lambda: validate_prepare_recovery(recovery[0], recovery[1], incomplete))
    positive("P5", lambda: validate_prepare_recovery(*recovery))
    return {
        "status": "PASS",
        "classification": "SYNTHETIC_HARDENING_CANARY_PASS",
        "production_mutation_count": 0,
        "case_count": len(cases),
        "cases": cases,
        "bound_regressions": [
            "tests/test_release_orchestration.py (retirement + exact reservation reuse)",
            "tests/test_archive_read_retry.py (ARCHIVING checkpoint/resume)",
            "tests/test_release_topology_compatibility.py (CURRENT_ZONE/reverse-dependency semantics)",
        ],
    }
