import copy
import yaml

from automation.models import canonical_json_bytes, sha256_bytes
from automation.drive_io import MemoryDriveStore
from automation.review_package import build_review_package
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


def human_merge_case(monkeypatch, task_dict, result_dict):
    base, reviewed, final = (letter * 40 for letter in "abc")
    previous_task = copy.deepcopy(task_dict)
    previous_task.update(canonical_generation=2, expected_base_sha=base)
    previous_task_bytes = yaml.safe_dump(previous_task, sort_keys=False).encode()
    previous_result = copy.deepcopy(result_dict)
    previous_result.update(canonical_generation=2, base_sha=base, head_sha=reviewed,
                           source_task_sha256=sha256_bytes(previous_task_bytes),
                           pr={"number": 33, "url": "https://example.test/pr/33", "head_sha": reviewed, "base_sha": base},
                           ci={"workflow_name": "Offline tests", "head_sha": reviewed,
                               "conclusion": "success", "runs": [{"id": 33}]})
    previous_result_bytes = canonical_json_bytes(previous_result)
    package = build_review_package(previous_task, previous_result, task_bytes=previous_task_bytes,
                                   result_bytes=previous_result_bytes)
    task = copy.deepcopy(task_dict)
    task.update(task_id="00000000-0000-4000-8000-000000000013", task_type="HUMAN_MERGE_EXECUTION",
                expected_base_sha=base, feature_branch="codex/process", allowed_paths=["NO_REPOSITORY_FILE_CHANGES_MERGE_ONLY"],
                allowed_actions=[f"require PR 33 base SHA {base}", f"require PR 33 head SHA exactly {reviewed}",
                                 f"require Offline tests run 33 event pull_request status completed conclusion success and head SHA {reviewed}"])
    task["authority_binding"]["predecessor_terminal_task_id"] = previous_task["task_id"]
    task["authority_binding"]["predecessor_terminal_task_sha256"] = sha256_bytes(previous_task_bytes)
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    store = MemoryDriveStore()
    store.seed("previous-task", "history", "gen2.yaml", previous_task_bytes)
    store.seed("previous-result", "history", "gen2-result.json", previous_result_bytes)
    store.seed("previous-package", "history", "gen2-review.json", canonical_json_bytes(package))
    result = copy.deepcopy(result_dict)
    result.update(canonical_generation=3, task_id=task["task_id"], base_sha=base, head_sha=final,
                  feature_branch=task["feature_branch"], changed_files=[], source_task_sha256=sha256_bytes(task_bytes),
                  focused_tests={"status": "NOT_RERUN_DURING_HUMAN_MERGE", "evidence_mode": "VERIFIED_PREDECESSOR_REUSE",
                                 "predecessor_result_sha256": sha256_bytes(previous_result_bytes)},
                  full_regression={"status": "NOT_RERUN_DURING_HUMAN_MERGE", "evidence_mode": "VERIFIED_PREDECESSOR_REUSE",
                                   "predecessor_result_sha256": sha256_bytes(previous_result_bytes)},
                  pr={"number": 33, "url": "https://example.test/pr/33", "state": "MERGED", "head_sha": reviewed,
                      "base_sha": base, "merge_commit_sha": final, "merged_at": "2099-01-01T00:00:00Z"},
                  ci={"workflow_name": "Offline tests", "head_sha": final, "conclusion": "success", "runs": [{"id": 44}]},
                  machine_facts={"requirement_sha256": task["authority_binding"]["requirement_sha256"],
                                 "evidence_mode": "VERIFIED_PREDECESSOR_REUSE",
                                 "predecessor_task_file_id": "previous-task", "predecessor_result_file_id": "previous-result",
                                 "predecessor_review_package_file_id": "previous-package",
                                 "predecessor_result_sha256": sha256_bytes(previous_result_bytes),
                                 "pr_changed_files": ["automation/verify.py"]})
    observed = {"number": 33, "url": result["pr"]["url"], "state": "MERGED",
                "headRefOid": reviewed, "baseRefOid": base}
    merge = {"mergedAt": "2099-01-01T00:00:00Z", "mergeCommit": {"oid": final}}
    main_ref = {"object": {"sha": final}}
    reviewed_runs = {"workflow_runs": [{"id": 33, "name": "Offline tests", "head_sha": reviewed,
                                          "event": "pull_request", "status": "completed", "conclusion": "success"}]}
    main_runs = [{"id": 44, "name": "Offline tests", "head_sha": final,
                  "event": "push", "status": "completed", "conclusion": "success"}]

    class GitHub:
        def collect(self, number, workflow, head):
            return {"pr": observed, "runs": main_runs}

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
            return f"{final} {base} {reviewed}"

        def changed_files(self, base_sha):
            return ("automation/verify.py",)

    monkeypatch.setattr("automation.verify.GitInspector", Git)
    return task, task_bytes, result, store, GitHub(), observed, merge, main_ref, reviewed_runs, main_runs, Git


def test_human_merge_accepts_truthful_verified_predecessor_reuse(monkeypatch, task_dict, result_dict):
    _, task_bytes, result, store, github, *_ = human_merge_case(monkeypatch, task_dict, result_dict)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="final-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"
