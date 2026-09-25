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


def descendant_case(monkeypatch, task_dict, result_dict):
    base, reviewed, actual, historical, merge_one, final = (letter * 40 for letter in "abcdef")
    heads = ("1" * 40, "2" * 40)
    paths = ("automation/verify.py", "tests/automation/test_verify.py")
    store = MemoryDriveStore()
    task = copy.deepcopy(task_dict)
    task.update(task_type="CODEX_READ_ONLY_POST_MERGE_RECONCILIATION", expected_base_sha=final,
                feature_branch="main", allowed_paths=["NO_REPOSITORY_FILE_CHANGES_RECONCILIATION_ONLY"],
                allowed_actions=["verify descendant-aware post-merge reconciliation",
                                 f"require historical product PR 35 base {base} reviewed head {reviewed} merge commit {historical}",
                                 "require historical reviewed CI run 80"])
    entries = []
    for index, (previous, merge, head, path) in enumerate(zip((historical, merge_one), (merge_one, final), heads, paths)):
        number = 37 + index
        process_task = copy.deepcopy(task_dict)
        process_task.update(task_type="CODEX_PROCESS_REPAIR", canonical_generation=12 + index * 2,
                            task_id=f"00000000-0000-4000-8000-{number:012d}", expected_base_sha=previous,
                            feature_branch=f"codex/process-{number}", allowed_paths=[path])
        process_bytes = yaml.safe_dump(process_task, sort_keys=False).encode()
        process_result = copy.deepcopy(result_dict)
        process_result.update(canonical_generation=process_task["canonical_generation"], task_id=process_task["task_id"],
                              base_sha=previous, head_sha=head, feature_branch=process_task["feature_branch"],
                              changed_files=[path], source_task_sha256=sha256_bytes(process_bytes),
                              pr={"number": number, "url": f"https://example.test/pr/{number}", "head_sha": head, "base_sha": previous},
                              ci={"workflow_name": "Offline tests", "head_sha": head, "conclusion": "success", "runs": [{"id": 100 + index}]})
        human_task = copy.deepcopy(task_dict)
        human_task.update(task_type="HUMAN_MERGE_EXECUTION", canonical_generation=process_task["canonical_generation"] + 1,
                          task_id=f"00000000-0000-4000-9000-{number:012d}", expected_base_sha=previous,
                          feature_branch=process_task["feature_branch"], allowed_paths=["NO_REPOSITORY_FILE_CHANGES_MERGE_ONLY"],
                          allowed_actions=[f"require PR {number} head SHA exactly {head}",
                                           f"require PR {number} base SHA {previous}",
                                           f"require Offline tests run {100 + index} event pull_request status completed conclusion success and head SHA {head}"])
        human_task["authority_binding"]["predecessor_terminal_task_id"] = process_task["task_id"]
        human_task["authority_binding"]["predecessor_terminal_task_sha256"] = sha256_bytes(process_bytes)
        human_bytes = yaml.safe_dump(human_task, sort_keys=False).encode()
        human_result = copy.deepcopy(result_dict)
        human_result.update(canonical_generation=human_task["canonical_generation"], task_id=human_task["task_id"],
                            base_sha=previous, head_sha=merge, feature_branch=human_task["feature_branch"],
                            changed_files=[], source_task_sha256=sha256_bytes(human_bytes),
                            focused_tests={"status": "NOT_RERUN_DURING_HUMAN_MERGE"},
                            full_regression={"status": "NOT_RERUN_DURING_HUMAN_MERGE"},
                            pr={"number": number, "url": f"https://example.test/pr/{number}", "head_sha": head,
                                "base_sha": previous, "merge_commit_sha": merge},
                            ci={"workflow_name": "Offline tests", "head_sha": merge,
                                "conclusion": "success", "runs": [{"id": 91 + index}]},
                            machine_facts={"evidence_mode": "VERIFIED_PREDECESSOR_REUSE",
                                           "predecessor_task_file_id": f"process-task-{number}",
                                           "predecessor_result_file_id": f"process-result-{number}",
                                           "predecessor_review_package_file_id": f"process-review_package-{number}",
                                           "predecessor_result_sha256": sha256_bytes(canonical_json_bytes(process_result))})
        entry = {"merge_commit_sha": merge, "reviewed_head_sha": head, "pr_number": number}
        for prefix, source_task, source_bytes, source_result in (
            ("process", process_task, process_bytes, process_result),
            ("human", human_task, human_bytes, human_result),
        ):
            result_bytes = canonical_json_bytes(source_result)
            package = build_review_package(source_task, source_result, task_bytes=source_bytes, result_bytes=result_bytes)
            for kind, data in (("task", source_bytes), ("result", result_bytes), ("review_package", canonical_json_bytes(package))):
                file_id = f"{prefix}-{kind}-{number}"
                store.seed(file_id, "history", file_id, data)
                entry[f"{prefix}_{kind}_file_id"] = file_id
            entry[f"{prefix}_task_sha256"] = sha256_bytes(source_bytes)
            entry[f"{prefix}_result_sha256"] = sha256_bytes(result_bytes)
        entries.append(entry)
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result = copy.deepcopy(result_dict)
    result.update(canonical_generation=task["canonical_generation"], task_id=task["task_id"],
                  base_sha=final, head_sha=final, feature_branch="main", changed_files=[],
                  source_task_sha256=sha256_bytes(task_bytes),
                  pr={"number": 35, "url": "https://example.test/pr/35", "state": "MERGED",
                      "base_sha": base, "head_sha": reviewed, "actual_head_sha": actual,
                      "reviewed_head_sha": reviewed, "reviewed_ci_run_id": 80, "merge_commit_sha": historical},
                  ci={"workflow_name": "Offline tests", "head_sha": final, "conclusion": "success", "runs": [{"id": 92}]},
                  machine_facts={"descendant_mode": "EXACT_P2A_PROCESS_CHAIN",
                                 "requirement_sha256": task["authority_binding"]["requirement_sha256"],
                                 "descendants": entries})
    observed = {"number": 35, "url": result["pr"]["url"], "state": "MERGED",
                "baseRefOid": base, "headRefOid": actual}
    historical_pr = {"mergedAt": "2026-09-25T00:00:00Z", "mergeCommit": {"oid": historical}}
    descendant_prs = {37: {"number": 37, "state": "MERGED", "baseRefOid": historical,
                           "headRefOid": heads[0], "mergedAt": "2026-09-25T01:00:00Z", "mergeCommit": {"oid": merge_one}},
                      38: {"number": 38, "state": "MERGED", "baseRefOid": merge_one,
                           "headRefOid": heads[1], "mergedAt": "2026-09-25T02:00:00Z", "mergeCommit": {"oid": final}}}
    main_ref = {"object": {"sha": final}}
    runs = {reviewed: [{"id": 80, "name": "Offline tests", "event": "pull_request", "head_sha": reviewed,
                        "status": "completed", "conclusion": "success"}],
            heads[0]: [{"id": 100, "name": "Offline tests", "event": "pull_request", "head_sha": heads[0],
                        "status": "completed", "conclusion": "success"}],
            heads[1]: [{"id": 101, "name": "Offline tests", "event": "pull_request", "head_sha": heads[1],
                        "status": "completed", "conclusion": "success"}],
            merge_one: [{"id": 91, "name": "Offline tests", "event": "push", "head_sha": merge_one,
                         "status": "completed", "conclusion": "success"}],
            final: [{"id": 92, "name": "Offline tests", "event": "push", "head_sha": final,
                     "status": "completed", "conclusion": "success"}]}

    class GitHub:
        def collect(self, number, workflow, head):
            return {"pr": observed, "runs": runs[final]}

        def _json(self, *args):
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return historical_pr if number == 35 else descendant_prs[number]
            if "git/ref/heads/main" in args[1]:
                return main_ref
            return {"workflow_runs": runs[args[1].split("head_sha=")[1].split("&")[0]]}

    class Git:
        def __init__(self, root):
            pass

        def head_sha(self):
            return final

        def is_ancestor(self, ancestor, descendant):
            return True

        def changed_files(self, base_sha):
            return ()

        def _run(self, *args):
            if args[:3] == ("rev-list", "--parents", "-n"):
                return {historical: f"{historical} {base} {actual}",
                        merge_one: f"{merge_one} {historical} {heads[0]}",
                        final: f"{final} {merge_one} {heads[1]}"}[args[-1]]
            if args[:3] == ("rev-list", "--first-parent", "--reverse"):
                return f"{merge_one}\n{final}"
            if args[:2] == ("rev-list", "--reverse"):
                return args[-1].split("..")[-1]
            if args[0] == "diff-tree":
                return paths[heads.index(args[-1])]
            if args[:2] == ("diff", "--name-only"):
                return {historical: paths[0], merge_one: paths[1]}[args[2]]
            raise AssertionError(args)

    monkeypatch.setattr("automation.verify.GitInspector", Git)
    return task, task_bytes, result, store, GitHub(), observed, historical_pr, descendant_prs, main_ref, runs, Git


def test_descendant_mode_accepts_exact_p2a_process_chain(monkeypatch, task_dict, result_dict):
    _, task_bytes, result, store, github, *_ = descendant_case(monkeypatch, task_dict, result_dict)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"
