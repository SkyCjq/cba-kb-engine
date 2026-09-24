from automation.models import canonical_json_bytes, sha256_bytes
from automation.verify import verify_result_bytes, verify_task_bytes


def test_task_verify_accepts_frozen_schema(task_bytes, task_dict):
    result = verify_task_bytes(
        task_bytes,
        expected_task_sha256=sha256_bytes(task_bytes),
        expected_generation=3,
        expected_executor="CODEX_RUNNER",
        expected_requirement_sha256=task_dict["authority_binding"]["requirement_sha256"],
        expected_policy_sha256=task_dict["policy_bundle_sha256"],
        expected_base_sha=task_dict["expected_base_sha"],
        current_pointer_bytes=task_bytes,
    )
    assert result.ok
    assert result.classification == "TASK_VERIFIED"


def test_result_verify_accepts_exact_binding(task_bytes, result_bytes):
    result = verify_result_bytes(result_bytes, task_bytes)
    assert result.ok
    assert result.classification == "EXECUTION_RESULT_VERIFIED"


def test_result_verify_rejects_wrong_source_task_hash(task_bytes, result_dict):
    result_dict["source_task_sha256"] = "0" * 64
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes)
    assert not result.ok
    assert result.classification == "WRONG_RESULT_HASH"


def test_result_verify_rejects_unexpected_changed_file(task_bytes, result_dict):
    result_dict["changed_files"].append("src/cba_kb/release.py")
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes)
    assert not result.ok
    assert result.classification == "SCOPE_VIOLATION"


def test_result_verify_rejects_pr_ci_head_mismatch(task_bytes, result_dict):
    result_dict["ci"]["head_sha"] = "c" * 40
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes)
    assert not result.ok
    assert result.classification == "PR_CI_HEAD_MISMATCH"
