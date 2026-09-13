import json
from pathlib import Path

import pytest

from cba_kb.evidence_ledger import validate_attestation
from cba_kb.release import (
    PERFORMANCE_METRIC_FIELDS,
    PartialMutationError,
    RELEASE_STATE_SEQUENCE,
    ReleaseContractError,
    append_archive_checkpoint,
    bounded_read_retry,
    classify_remote_error,
    manifest_coverage,
    provenance_dag,
    read_archive_checkpoints,
    reconcile_write,
    release_critical_tree_attestation,
    validate_performance_metrics,
    validate_state_transition,
)


ROOT = Path(__file__).resolve().parents[1]


def role_values():
    return {
        "baseline_development_sha": "1" * 40,
        "product_candidate_sha": "2" * 40,
        "release_execution_sha": "3" * 40,
        "reviewed_release_pr_head_sha": "4" * 40,
        "reviewed_ci_head_sha": "4" * 40,
        "release_merge_sha": "5" * 40,
        "production_execution_sha": "3" * 40,
        "release_critical_tree_attestation": "6" * 64,
    }


def test_typed_sha_provenance_and_equalities():
    value = provenance_dag(role_values())
    assert value["roles"]["production_execution_sha"] == "3" * 40
    assert [item["status"] for item in value["equalities"]] == ["PASS", "PASS"]

    drift = role_values()
    drift["reviewed_ci_head_sha"] = "7" * 40
    with pytest.raises(ReleaseContractError, match="PROVENANCE_EQUALITY"):
        provenance_dag(drift)

    production_drift = role_values()
    production_drift["production_execution_sha"] = "7" * 40
    with pytest.raises(ReleaseContractError, match="PROVENANCE_EQUALITY"):
        provenance_dag(production_drift)

    independent = role_values()
    assert provenance_dag(independent)["roles"]["release_execution_sha"] != (
        independent["release_merge_sha"]
    )

    equal_merge = role_values()
    equal_merge["release_merge_sha"] = equal_merge["release_execution_sha"]
    value = provenance_dag(equal_merge)
    assert value["roles"]["release_execution_sha"] == value["roles"]["release_merge_sha"]
    assert value["nodes"].index("release_execution_sha") != value["nodes"].index(
        "release_merge_sha"
    )


def test_release_state_machine_is_explicit_and_monotonic():
    assert RELEASE_STATE_SEQUENCE[-1] == "COMPLETE"
    for current, target in zip(
        RELEASE_STATE_SEQUENCE, RELEASE_STATE_SEQUENCE[1:],
    ):
        assert validate_state_transition(current, target)
    with pytest.raises(ReleaseContractError, match="SKIP"):
        validate_state_transition("PROJECTED", "FROZEN_PLAN")
    with pytest.raises(ReleaseContractError, match="REGRESSION"):
        validate_state_transition("PUBLISHING", "PREPARED")


def test_manifest_coverage_requires_exact_sets():
    result = manifest_coverage(
        ["a", "b"], ["b", "a"], ["a", "b"], ["b", "a"],
    )
    assert result["missing"] == result["unexpected"] == 0
    assert result["duplicate"] == result["unresolved"] == 0
    with pytest.raises(ReleaseContractError, match="MISMATCH"):
        manifest_coverage(["a"], ["a"], ["a", "b"], ["a"])
    with pytest.raises(ReleaseContractError, match="DUPLICATE"):
        manifest_coverage(["a", "a"], ["a"], ["a"], ["a"])


def test_release_critical_tree_attestation_fails_closed(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    critical = repo / "critical.txt"
    critical.write_text("one\n")
    subprocess.run(["git", "add", "critical.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, check=True)
    first = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    critical.write_text("two\n")
    subprocess.run(["git", "add", "critical.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "two"], cwd=repo, check=True)
    second = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    result = release_critical_tree_attestation(
        repo, second, second, ["critical.txt"],
    )
    assert result["status"] == "PASS"
    with pytest.raises(ReleaseContractError, match="TREE_MISMATCH"):
        release_critical_tree_attestation(
            repo, first, second, ["critical.txt"],
        )


def test_remote_io_classification_and_bounded_retry():
    class Error(Exception):
        def __init__(self, status=None):
            super().__init__(str(status))
            self.status = status

    assert classify_remote_error(Error(429)) == "RETRYABLE"
    assert classify_remote_error(Error(503)) == "RETRYABLE"
    assert classify_remote_error(Error(403)) == "NON_RETRYABLE"
    assert classify_remote_error(RuntimeError("hash mismatch")) == "NON_RETRYABLE"
    events = []
    sleeps = []
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise Error(429)
        return "ok"

    assert bounded_read_retry(
        flaky, sleeper=sleeps.append, events=events, reset=lambda: None,
    ) == "ok"
    assert sleeps == [0.5]
    assert events[0]["classification"] == "RETRYABLE"


def test_write_reconciliation_never_retries_unknown_state():
    assert reconcile_write(
        journal_state="PUBLISHING",
        observed_sha256="after",
        before_sha256="before",
        after_sha256="after",
    )["decision"] == "ALREADY_COMMITTED"
    assert reconcile_write(
        journal_state="PUBLISHING",
        observed_sha256="before",
        before_sha256="before",
        after_sha256="after",
    )["decision"] == "RETRY_ALLOWED"
    with pytest.raises(PartialMutationError):
        reconcile_write(
            journal_state="PUBLISHING",
            observed_sha256="external",
            before_sha256="before",
            after_sha256="after",
        )


def test_archive_checkpoint_is_append_only_and_typed(tmp_path):
    first = append_archive_checkpoint(
        tmp_path,
        release_execution_sha="a" * 40,
        release_id="v1.6.1-1",
        state="ARCHIVING",
        progress={"snapshots": 1},
        recorded_at="2026-09-13T00:00:00Z",
    )
    second = append_archive_checkpoint(
        tmp_path,
        release_execution_sha="a" * 40,
        release_id="v1.6.1-1",
        state="ARCHIVE_COMPLETE",
        progress={"snapshots": 2},
        recorded_at="2026-09-13T00:01:00Z",
    )
    assert first["state"] == "ARCHIVING"
    assert read_archive_checkpoints(tmp_path) == [first, second]

def test_credential_and_offsite_attestations_are_secret_free():
    credential = {
        "schema_version": 1,
        "recorded_at": "2026-09-13T00:00:00Z",
        "credentials": [{
            "credential_id": "old-oauth-client",
            "status": "REVOKED",
            "verification_attempted": True,
            "verification_result": "REJECTED",
        }],
        "secret_material_included": False,
        "verification_result": "REJECTED_OR_NOT_FOUND",
    }
    assert validate_attestation("credential_revocation", credential)["status"] == "PASS"
    offsite = {
        "schema_version": 1,
        "recorded_at": "2026-09-13T00:00:00Z",
        "backup_id": "backup-1",
        "backup_failure_domain": "offsite-bucket",
        "production_failure_domain": "google-drive",
        "restore_target_empty": True,
        "core_bundle_hashes": {"core.tar": "a" * 64},
        "readback": "PASS",
        "secret_material_included": False,
    }
    assert validate_attestation("offsite_backup_restore", offsite)["status"] == "PASS"


def test_performance_metrics_require_explicit_not_available_values():
    metrics = {name: "NOT_AVAILABLE" for name in PERFORMANCE_METRIC_FIELDS}
    assert validate_performance_metrics(metrics)["status"] == "PASS"
    metrics.pop(PERFORMANCE_METRIC_FIELDS[0])
    with pytest.raises(ReleaseContractError, match="PERFORMANCE_METRICS_MISSING"):
        validate_performance_metrics(metrics)
