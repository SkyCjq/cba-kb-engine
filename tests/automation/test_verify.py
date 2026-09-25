import yaml

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


def test_result_verify_accepts_exact_binding(task_bytes, result_dict, github_inspector):
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert result.ok
    assert result.classification == "EXECUTION_RESULT_VERIFIED"


def test_result_verify_rejects_wrong_source_task_hash(task_bytes, result_dict, github_inspector):
    result_dict["source_task_sha256"] = "0" * 64
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert not result.ok
    assert result.classification == "WRONG_RESULT_HASH"


def test_result_verify_rejects_unexpected_changed_file(task_bytes, result_dict, github_inspector):
    result_dict["changed_files"].append("src/cba_kb/release.py")
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert not result.ok
    assert result.classification == "SCOPE_VIOLATION"


def test_result_verify_rejects_pr_ci_head_mismatch(task_bytes, result_dict, github_inspector):
    result_dict["ci"]["head_sha"] = "c" * 40
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert not result.ok
    assert result.classification == "PR_CI_HEAD_MISMATCH"


def test_result_verify_rejects_forged_pr_facts(task_bytes, result_dict, github_inspector):
    result_dict["pr"]["url"] = "https://example.test/forged"
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert result.classification == "PR_FACTS_MISMATCH"


def test_result_verify_rejects_forged_ci_run(task_bytes, result_dict, github_inspector):
    result_dict["ci"]["runs"] = [{"id": 999}]
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert result.classification == "CI_FACTS_MISMATCH"
    assert not result.ok


def test_bounded_repair_accepts_incremental_base_on_existing_pr(task_dict, result_dict, github_inspector):
    task_dict["task_type"] = "CODEX_BOUNDED_REPAIR"
    task_bytes = yaml.safe_dump(task_dict, sort_keys=False).encode()
    result_dict["source_task_sha256"] = sha256_bytes(task_bytes)
    observed = github_inspector.collect(33, "Offline tests", result_dict["head_sha"])
    observed["pr"]["baseRefOid"] = "c" * 40
    assert verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector).classification == "EXECUTION_RESULT_VERIFIED"


def test_non_repair_still_rejects_wrong_pr_base(task_bytes, result_dict, github_inspector):
    observed = github_inspector.collect(33, "Offline tests", result_dict["head_sha"])
    observed["pr"]["baseRefOid"] = "c" * 40
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector)
    assert result.classification == "PR_FACTS_MISMATCH"
