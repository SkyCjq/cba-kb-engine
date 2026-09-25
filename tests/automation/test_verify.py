import copy
from pathlib import Path
import yaml

from automation.models import canonical_json_bytes, sha256_bytes
from automation.drive_io import MemoryDriveStore
from automation.review_package import build_review_package
from automation.verify import verify_historical_source_result, verify_result_bytes, verify_task_bytes


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
                            pr={"number": number, "url": f"https://example.test/pr/{number}", "state": "MERGED", "head_sha": head,
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
            return {"pr": observed, "runs": runs[result["head_sha"]]}

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
            return result["head_sha"]

        def is_ancestor(self, ancestor, descendant):
            return True

        def changed_files(self, base_sha):
            return ()

        def _run(self, *args):
            if args[:3] == ("rev-list", "--parents", "-n"):
                return {historical: f"{historical} {base} {actual}",
                        merge_one: f"{merge_one} {historical} {heads[0]}",
                        final: f"{final} {merge_one} {heads[1]}",
                        "4" * 40: f"{'4' * 40} {final} {'3' * 40}"}[args[-1]]
            if args[:3] == ("rev-list", "--first-parent", "--reverse"):
                return f"{merge_one}\n{final}" + (f"\n{'4' * 40}" if result["head_sha"] == "4" * 40 else "")
            if args[:2] == ("rev-list", "--reverse"):
                return args[-1].split("..")[-1]
            if args[0] == "diff-tree":
                return "automation/verify.py" if args[-1] == "3" * 40 else paths[heads.index(args[-1])]
            if args[:2] == ("diff", "--name-only"):
                return {historical: paths[0], merge_one: paths[1], final: "automation/verify.py"}[args[2]]
            raise AssertionError(args)

    monkeypatch.setattr("automation.verify.GitInspector", Git)
    return task, task_bytes, result, store, GitHub(), observed, historical_pr, descendant_prs, main_ref, runs, Git


def test_descendant_mode_accepts_exact_p2a_process_chain(monkeypatch, task_dict, result_dict):
    _, task_bytes, result, store, github, *_ = descendant_case(monkeypatch, task_dict, result_dict)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"


def retrospective_case(monkeypatch, task_dict, result_dict):
    task, _, result, store, github, *rest = descendant_case(monkeypatch, task_dict, result_dict)
    entry = result["machine_facts"]["descendants"][0]
    historical_bytes = store.read(entry["human_result_file_id"]).content
    historical = __import__("json").loads(historical_bytes)
    historical.update(status="BLOCKED", classification="HUMAN_MERGE_RESULT_CONTRACT_UNSUPPORTED",
                      generated_at_utc="2026-09-25T01:05:00Z",
                      errors=[{"classification": "PROCESS", "code": code, "re_freeze": "NO"} for code in (
                          "HUMAN_MERGE_TEST_EVIDENCE_CONTRACT", "HUMAN_MERGE_PR_STATE_CONTRACT")])
    historical["machine_facts"] = {
        "product_failure": False, "identity_semantic_delta": "ZERO", "req_product_code_delta": 0,
        **{key: 0 for key in (
            "production_mutation", "publish", "restore", "identity_authority_change",
            "identity_registry_mutation", "master_mutation", "player_uid_mutation",
            "new_identity_decisions", "new_same_decisions", "new_not_same_decisions",
            "machine_final_uid_decisions",
        )},
    }
    for field in ("focused_tests", "full_regression"):
        historical[field] = {"status": "NOT_RERUN_DURING_HUMAN_MERGE",
                             "evidence_mode": "VERIFIED_PROCESS_PREDECESSOR_REUSE",
                             "process_result_sha256": entry["process_result_sha256"]}
    historical["pr"].update(merged_at="2026-09-25T01:00:00Z")
    historical["ci"].update(event="push", head_branch="main", status="completed")
    historical_bytes = canonical_json_bytes(historical)
    store.records[entry["human_result_file_id"]].content = historical_bytes
    entry["human_result_sha256"] = sha256_bytes(historical_bytes)
    historical_task_bytes = store.read(entry["human_task_file_id"]).content
    historical_task = yaml.safe_load(historical_task_bytes)
    package = build_review_package(historical_task, historical, task_bytes=historical_task_bytes,
                                   result_bytes=historical_bytes)
    package_bytes = canonical_json_bytes(package)
    store.records[entry["human_review_package_file_id"]].content = package_bytes
    authority = {"schema_version": "cba-kb.p2a-stabilization-task.v1", "repository": task["repository"],
                 "task_id": "00000000-0000-4000-a000-000000000001", "workstream_id": "INDEPENDENT-PROOF"}
    authority_bytes = yaml.safe_dump(authority).encode()
    store.seed("proof-authority", "stabilization", "frozen-task.yaml", authority_bytes)
    proof = {
        "schema_version": "cba-kb.p2a-retrospective-proof.v1", "status": "PASS",
        "capability": "VERIFIED_PREDECESSOR_REUSE_MERGED_PR", "verifier_version": "0.1.0",
        "verified_at_utc": "2026-09-25T03:00:00Z", "repository": task["repository"],
        "requirement_sha256": task["authority_binding"]["requirement_sha256"],
        "policy_bundle_sha256": task["policy_bundle_sha256"], "production_authority": False,
        "authority": {"task_file_id": "proof-authority", "task_sha256": sha256_bytes(authority_bytes),
                      "task_id": authority["task_id"], "workstream_id": authority["workstream_id"]},
        "historical": {"task_file_id": entry["human_task_file_id"], "task_sha256": entry["human_task_sha256"],
                       "result_file_id": entry["human_result_file_id"], "result_sha256": entry["human_result_sha256"],
                       "review_package_file_id": entry["human_review_package_file_id"],
                       "review_package_sha256": sha256_bytes(package_bytes), "status": "BLOCKED",
                       "classification": historical["classification"],
                       "error_codes": [error["code"] for error in historical["errors"]]},
        "predecessor": {"task_file_id": entry["process_task_file_id"], "task_sha256": entry["process_task_sha256"],
                        "result_file_id": entry["process_result_file_id"],
                        "result_sha256": entry["process_result_sha256"],
                        "review_package_file_id": entry["process_review_package_file_id"]},
        "event": {"pr_number": entry["pr_number"], "reviewed_head_sha": entry["reviewed_head_sha"],
                  "actual_pr_head_sha": entry["reviewed_head_sha"], "base_sha": "d" * 40,
                  "merge_commit_sha": entry["merge_commit_sha"], "merged_at": "2026-09-25T01:00:00Z",
                  "reviewed_ci_run_id": 100, "exact_main_ci_run_id": 91},
    }
    entry["retrospective_proof_file_id"] = "later-proof"
    proof_bytes = canonical_json_bytes(proof)
    entry["retrospective_proof_sha256"] = sha256_bytes(proof_bytes)
    store.seed("later-proof", "stabilization", "proof.json", proof_bytes)
    task["allowed_actions"].append(
        f"require retrospective proof SHA256 {entry['retrospective_proof_sha256']} authority SHA256 {proof['authority']['task_sha256']}")
    github_runs = rest[-2]
    github_runs[entry["merge_commit_sha"]][0]["head_branch"] = "main"
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    return task, task_bytes, result, store, github, proof, entry, historical_bytes


def test_retrospective_proof_preserves_blocked_bytes_and_reconciles_event(monkeypatch, task_dict, result_dict):
    _, task_bytes, result, store, github, _, entry, historical_bytes = retrospective_case(
        monkeypatch, task_dict, result_dict,
    )
    historical_sha = sha256_bytes(historical_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"
    assert store.read(entry["human_result_file_id"]).content == historical_bytes
    assert sha256_bytes(store.read(entry["human_result_file_id"]).content) == historical_sha


def test_historical_capability_gap_fixture_remains_blocked_and_immutable():
    root = Path(__file__).parent / "fixtures" / "p2a_stabilization"
    historical = (root / "historical_result.json").read_bytes()
    task = (root / "historical_task.yaml").read_bytes()
    package = (root / "historical_review_package.json").read_bytes()
    assert sha256_bytes(historical) == "95134cbad6b5428dfd076193ec693c8c1c6896c14f042e3bc6bad334ff10141a"
    assert sha256_bytes(task) == "463bff0c6254005e16ac545f9d8a728757890010b7e978dd73ab7d8a4ba2e87b"
    assert sha256_bytes(package) == "bd4e55a69078669aae7fe66ef19bea0cf7d89eb24c271986fab61a4ab8dee18a"
    document = __import__("json").loads(historical)
    assert document["status"] == "BLOCKED"
    assert {item["code"] for item in document["errors"]} == {
        "HUMAN_MERGE_TEST_EVIDENCE_CONTRACT", "HUMAN_MERGE_PR_STATE_CONTRACT",
    }
    assert all(item["classification"] == "PROCESS" and item["re_freeze"] == "NO"
               for item in document["errors"])


def test_retrospective_core_contains_no_request_or_generation_special_case():
    import inspect
    from automation.verify import _verify_retrospective_proof

    source = inspect.getsource(_verify_retrospective_proof)
    assert all(marker not in source for marker in ("Gen13", "PR37", "REQ-181", "generation == 13"))


def external_control_plane_case(monkeypatch, task_dict, result_dict):
    task, _, result, store, github, _, _, descendant_prs, main_ref, runs, _ = descendant_case(
        monkeypatch, task_dict, result_dict,
    )
    previous, reviewed, merged = "f" * 40, "3" * 40, "4" * 40
    paths = ["automation/verify.py"]
    zero = {field: 0 for field in (
        "PRODUCT_CODE_DELTA", "PRODUCTION_MUTATION", "PUBLISH", "RESTORE",
        "REQ181_POINTER_MUTATION", "REQ181_HISTORY_MUTATION",
    )}
    facts = {**zero, "IDENTITY_SEMANTIC_DELTA": "ZERO", "production_authority": False}
    external_task = {
        "schema_version": "cba-kb.p2a-stabilization-task.v1", "workstream_id": "INDEPENDENT-CONTROL",
        "stabilization_generation": 1,
        "task_id": "00000000-0000-4000-a000-000000000077", "repository": task["repository"],
        "expected_base_branch": "main", "expected_base_sha": previous,
        "feature_branch": "codex/independent-control", "allowed_paths": paths,
        "must_not_change": ["Production artifacts"], "forbidden_actions": ["do not publish"],
    }
    task_bytes_external = yaml.safe_dump(external_task, sort_keys=False).encode()
    source = {
        "status": "PASS", "classification": "P2A_STABILIZATION_SYNTHETIC_PASS",
        "workstream_id": external_task["workstream_id"],
        "stabilization_generation": 1,
        "task_id": external_task["task_id"], "source_task_sha256": sha256_bytes(task_bytes_external),
        "repository": task["repository"], "base_sha": previous, "head_sha": reviewed,
        "feature_branch": external_task["feature_branch"],
        "focused_tests": {"status": "PASS"}, "full_regression": {"status": "PASS"},
        "secret_guard": "PASS", "changed_files": paths,
        "pr": {"number": 77, "state": "OPEN", "base_sha": previous, "head_sha": reviewed},
        "ci": {"workflow_name": "Offline tests", "event": "pull_request", "head_sha": reviewed,
               "status": "completed", "conclusion": "success", "runs": [{"id": 200}]},
        "machine_facts": facts,
    }
    source_bytes = canonical_json_bytes(source)
    source_package = {
        "review_package_version": "cba-kb.p2a-stabilization-review-package.v1",
        "CONTROL": {"workstream_id": external_task["workstream_id"],
                    "stabilization_generation": 1,
                    "task_id": external_task["task_id"],
                    "task_sha256": sha256_bytes(task_bytes_external),
                    "result_sha256": sha256_bytes(source_bytes)},
        "VERIFIED_MACHINE_FACTS": {"pr": source["pr"], "ci": source["ci"], "changed_files": paths},
    }
    package_bytes = canonical_json_bytes(source_package)
    post = {
        "status": "PASS", "classification": "P2A_STABILIZATION_HUMAN_MERGE_VERIFIED",
        "workstream_id": external_task["workstream_id"],
        "stabilization_generation": 1,
        "task_id": external_task["task_id"], "repository": task["repository"],
        "source_task_file_id": "external-task", "source_task_sha256": sha256_bytes(task_bytes_external),
        "source_result_file_id": "external-result", "source_result_sha256": sha256_bytes(source_bytes),
        "source_review_package_file_id": "external-review_package",
        "source_review_package_sha256": sha256_bytes(package_bytes),
        "base_sha": previous, "reviewed_head_sha": reviewed, "head_sha": merged,
        "changed_files": paths,
        "pr": {"number": 77, "state": "MERGED", "base_sha": previous,
               "reviewed_head_sha": reviewed, "merge_commit_sha": merged,
               "merged_at": "2026-09-25T03:00:00Z"},
        "ci": {"workflow_name": "Offline tests", "event": "push", "head_branch": "main",
               "head_sha": merged, "status": "completed", "conclusion": "success", "run_id": 201},
        "machine_facts": facts,
    }
    post_bytes = canonical_json_bytes(post)
    post_package = {
        "review_package_version": "cba-kb.p2a-stabilization-post-merge-review-package.v1",
        "CONTROL": {"workstream_id": external_task["workstream_id"],
                    "task_id": external_task["task_id"],
                    "task_sha256": sha256_bytes(task_bytes_external),
                    "source_result_sha256": sha256_bytes(source_bytes),
                    "post_merge_result_sha256": sha256_bytes(post_bytes)},
        "VERIFIED_MACHINE_FACTS": {"pr": post["pr"], "ci": post["ci"],
                                   "changed_files": paths, "machine_facts": facts},
    }
    entry = {"type": "external_control_plane_descendant", "merge_commit_sha": merged,
             "reviewed_head_sha": reviewed, "actual_pr_head_sha": reviewed,
             "pr_number": 77, "production_authority": False}
    for kind, data in (
        ("task", task_bytes_external), ("result", source_bytes),
        ("review_package", package_bytes), ("post_merge_result", post_bytes),
        ("post_merge_review_package", canonical_json_bytes(post_package)),
    ):
        entry[f"{kind}_file_id"] = f"external-{kind}"
        entry[f"{kind}_sha256"] = sha256_bytes(data)
        store.seed(entry[f"{kind}_file_id"], "external-history", kind, data)
    frozen = ("external control-plane descendant task SHA256 {task_sha256} result SHA256 {result_sha256} "
              "review SHA256 {review_package_sha256} post-merge result SHA256 {post_merge_result_sha256} "
              "post-merge review SHA256 {post_merge_review_package_sha256}").format(**entry)
    task["allowed_actions"].append(frozen)
    task["expected_base_sha"] = merged
    result.update(base_sha=merged, head_sha=merged)
    result["ci"] = {"workflow_name": "Offline tests", "head_sha": merged,
                    "conclusion": "success", "runs": [{"id": 201}]}
    result["machine_facts"]["descendants"].append(entry)
    main_ref["object"]["sha"] = merged
    descendant_prs[77] = {"number": 77, "state": "MERGED", "baseRefOid": previous,
                          "headRefOid": reviewed, "mergedAt": "2026-09-25T03:00:00Z",
                          "mergeCommit": {"oid": merged}}
    runs[reviewed] = [{"id": 200, "name": "Offline tests", "event": "pull_request",
                       "head_sha": reviewed, "status": "completed", "conclusion": "success"}]
    runs[merged] = [{"id": 201, "name": "Offline tests", "event": "push", "head_branch": "main",
                     "head_sha": merged, "status": "completed", "conclusion": "success"}]
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    return task, task_bytes, result, store, github, entry, descendant_prs, main_ref, runs


def test_external_control_plane_merge_follows_req_scoped_descendants(monkeypatch, task_dict, result_dict):
    _, task_bytes, result, store, github, *_ = external_control_plane_case(
        monkeypatch, task_dict, result_dict,
    )
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == "EXECUTION_RESULT_VERIFIED"


def historical_source_case(monkeypatch, task_dict, result_dict):
    task, task_bytes, result, store, _, observed, merge, main_ref, reviewed_runs, main_runs, git_type = human_merge_case(
        monkeypatch, task_dict, result_dict,
    )
    current = "e" * 40
    result["ci"].update(event="push", head_branch="main", status="completed")
    result["machine_facts"].update(identity_semantic_delta="ZERO", req181_product_code_delta=0)
    for name in (
        "identity_authority_change", "identity_registry_mutation", "master_mutation",
        "player_uid_mutation", "new_identity_decisions", "new_same_decisions",
        "new_not_same_decisions", "machine_final_uid_decisions", "production_mutation", "publish", "restore",
    ):
        result["machine_facts"][name] = 0
    result_bytes = canonical_json_bytes(result)
    package = build_review_package(task, result, task_bytes=task_bytes, result_bytes=result_bytes)
    package_bytes = canonical_json_bytes(package)
    store.seed("historical-task", "history", "human-task.yaml", task_bytes)
    store.seed("historical-result", "history", "human-result.json", result_bytes)
    store.seed("historical-package", "history", "human-review.json", package_bytes)
    observed.update(mergedAt=result["pr"]["merged_at"], mergeCommit={"oid": result["head_sha"]})
    main_ref["object"]["sha"] = current
    main_runs[0]["head_branch"] = "main"

    class GitHub:
        def collect(self, number, workflow, head):
            return {"pr": observed, "runs": main_runs}

        def _json(self, *args):
            if args[:2] == ("pr", "view"):
                return observed
            if "git/ref/heads/main" in args[1]:
                return main_ref
            if result["pr"]["head_sha"] in args[1]:
                return reviewed_runs
            return {"workflow_runs": main_runs}

    class Git(git_type):
        def head_sha(self):
            return current

        def _run(self, *args):
            if args[:2] == ("diff", "--name-only"):
                return "automation/verify.py"
            return super()._run(*args)

    monkeypatch.setattr("automation.verify.GitInspector", Git)
    kwargs = {
        "task_file_id": "historical-task", "result_file_id": "historical-result",
        "review_package_file_id": "historical-package",
        "expected_task_sha256": sha256_bytes(task_bytes),
        "expected_result_sha256": sha256_bytes(result_bytes),
        "expected_review_package_sha256": sha256_bytes(package_bytes),
        "expected_requirement_sha256": task["authority_binding"]["requirement_sha256"],
        "expected_policy_sha256": task["policy_bundle_sha256"],
        "expected_repository": task["repository"],
        "git_root": "current-main", "github_inspector": GitHub(),
    }
    return task, result, store, kwargs, main_ref, main_runs, reviewed_runs, observed, Git


def test_historical_human_merge_source_accepts_authorized_descendant_main(monkeypatch, task_dict, result_dict):
    _, _, store, kwargs, main_ref, *_ = historical_source_case(monkeypatch, task_dict, result_dict)
    verified = verify_historical_source_result(store, **kwargs)
    assert verified.classification == "HISTORICAL_SOURCE_RESULT_VERIFIED"
    assert verified.facts["historical_head_sha"] != main_ref["object"]["sha"]


def test_current_human_merge_verification_still_requires_exact_live_main(monkeypatch, task_dict, result_dict):
    task, result, store, kwargs, *_ = historical_source_case(monkeypatch, task_dict, result_dict)
    current = verify_result_bytes(
        canonical_json_bytes(result), store.read("historical-task").content,
        git_root="current-main", github_inspector=kwargs["github_inspector"], predecessor_store=store,
    )
    assert current.classification == "FINAL_MAIN_MISMATCH"


def test_historical_source_accepts_gen17_then_two_authorized_main_descendants(monkeypatch, task_dict, result_dict):
    task, result, store, kwargs, main_ref, main_runs, _, observed, git_type = historical_source_case(
        monkeypatch, task_dict, result_dict,
    )
    historical = "f54518c14a0559f8ef5c406ecc3fd4d77ad5081b"
    first_descendant = "1a57053efabdbf60f891c59bc51652f99b8d22f1"
    current_main = "2bb7cba71736dcdeda3dbd85aca73b39bdd79d98"
    result["head_sha"] = historical
    result["pr"]["merge_commit_sha"] = historical
    result["ci"]["head_sha"] = historical
    result["ci"]["runs"] = [{"id": 36108497705}]
    observed["mergeCommit"]["oid"] = historical
    main_runs[0].update(id=36108497705, head_sha=historical)
    main_ref["object"]["sha"] = current_main
    task_bytes = store.read("historical-task").content
    result_bytes = canonical_json_bytes(result)
    store.records["historical-result"].content = result_bytes
    package = build_review_package(task, result, task_bytes=task_bytes, result_bytes=result_bytes)
    package_bytes = canonical_json_bytes(package)
    store.records["historical-package"].content = package_bytes
    kwargs["expected_result_sha256"] = sha256_bytes(result_bytes)
    kwargs["expected_review_package_sha256"] = sha256_bytes(package_bytes)

    class DescendantChainGit(git_type):
        def head_sha(self):
            return current_main

        def is_ancestor(self, ancestor, descendant):
            chain = (historical, first_descendant, current_main)
            return ancestor in chain and descendant in chain and chain.index(ancestor) <= chain.index(descendant)

        def _run(self, *args):
            if args[:2] == ("diff", "--name-only"):
                return "automation/verify.py"
            return f"{historical} {task['expected_base_sha']} {result['pr']['head_sha']}"

    monkeypatch.setattr("automation.verify.GitInspector", DescendantChainGit)
    verified = verify_historical_source_result(store, **kwargs)
    assert verified.classification == "HISTORICAL_SOURCE_RESULT_VERIFIED"
    assert verified.facts["historical_head_sha"] == historical
    assert verified.facts["current_main_sha"] == current_main
    assert verified.facts["historical_exact_main_ci_run_ids"] == [36108497705]
