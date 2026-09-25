import copy
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


def post_merge_case(monkeypatch, task_dict, result_dict):
    """A merge commit whose reviewed PR head and exact-main runs are independent facts."""
    final, base, pr_head, reviewed = (letter * 40 for letter in "fedc")
    task = copy.deepcopy(task_dict)
    task.update(task_type="CODEX_READ_ONLY_POST_MERGE_RECONCILIATION",
                expected_base_sha=final, feature_branch="main", allowed_paths=["NO_REPOSITORY_FILE_CHANGES_RECONCILIATION_ONLY"],
                allowed_actions=[f"compare reviewed product head {reviewed} to the merged PR head"])
    result = copy.deepcopy(result_dict)
    result.update(base_sha=final, head_sha=final, feature_branch="main", changed_files=[],
                  machine_facts={"requirement_sha256": task["authority_binding"]["requirement_sha256"]},
                  pr={"number": 35, "url": "https://example.test/pr/35", "state": "MERGED",
                      "head_sha": pr_head, "base_sha": base, "merge_commit_sha": final,
                      "reviewed_head_sha": reviewed, "reviewed_ci_run_id": 101},
                  ci={"workflow_name": "Offline tests", "head_sha": final,
                      "conclusion": "success", "runs": [{"id": 102}]})
    observed = {"number": 35, "url": result["pr"]["url"], "state": "MERGED",
                "headRefOid": pr_head, "baseRefOid": base}
    main_ref = {"object": {"sha": final}}
    merge = {"mergedAt": "2026-09-25T02:29:03Z", "mergeCommit": {"oid": final}}
    reviewed_runs = {"workflow_runs": [{"id": 101, "name": "Offline tests", "head_sha": reviewed,
                                          "status": "completed", "conclusion": "success", "event": "pull_request"}]}

    class GitHub:
        def collect(self, number, workflow, head):
            return {"pr": observed, "runs": [{"id": 102, "name": "Offline tests", "head_sha": final,
                                                "status": "completed", "conclusion": "success", "event": "push"}]}

        def _json(self, *args):
            if args[:2] == ("pr", "view"):
                return merge
            if "git/ref/heads/main" in args[1]:
                return main_ref
            return reviewed_runs

    class Git:
        def __init__(self, root):
            pass

        def head_sha(self):
            return final

        def is_ancestor(self, ancestor, descendant):
            return True

        def _run(self, *args):
            return f"{final} {base} {pr_head}"

        def changed_files(self, base_sha):
            return ()

    monkeypatch.setattr("automation.verify.GitInspector", Git)
    return task, result, GitHub(), observed, main_ref, merge, reviewed_runs, Git


def test_post_merge_reconciliation_accepts_verified_merge_and_exact_main(monkeypatch, task_dict, result_dict):
    task, result, github, *_ = post_merge_case(monkeypatch, task_dict, result_dict)
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main", github_inspector=github)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"
