import pytest
import yaml
import copy
from dataclasses import replace

from automation.drive_io import GoogleDriveStore, MemoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, sha256_bytes
from automation.review_package import build_review_package
from automation.verify import guarded_verify, verify_historical_source_result, verify_result_bytes, verify_task_bytes
from test_verify import descendant_case, external_control_plane_case, historical_source_case, human_merge_case, post_merge_case, retrospective_case


def _strict_case(tmp_path, task_dict, store=None):
    predecessor_doc = copy.deepcopy(task_dict)
    predecessor_doc["canonical_generation"] = 2
    predecessor_doc["task_id"] = task_dict["transition_binding"]["supersedes_task_id"]
    predecessor = yaml.safe_dump(predecessor_doc, sort_keys=False).encode()
    successor_doc = copy.deepcopy(task_dict)
    successor_doc["transition_binding"]["supersedes_task_sha256"] = sha256_bytes(predecessor)
    successor = yaml.safe_dump(successor_doc, sort_keys=False).encode()
    source = canonical_json_bytes({
        "schema_version": "cba-kb.p2a-result.v1", "req_id": predecessor_doc["req_id"], "canonical_generation": 2,
        "task_id": predecessor_doc["task_id"], "status": "BLOCKED", "classification": "TEST_SOURCE",
        "automation_version": "0.1.0", "policy_bundle_sha256": predecessor_doc["policy_bundle_sha256"],
        "repository": predecessor_doc["repository"], "base_sha": predecessor_doc["expected_base_sha"],
        "head_sha": predecessor_doc["expected_base_sha"], "feature_branch": predecessor_doc["feature_branch"],
        "changed_files": [], "focused_tests": {}, "full_regression": {}, "pr": None, "ci": None,
        "output_artifacts": [], "forbidden_actions_observed": [], "machine_facts": {}, "errors": [],
        "source_task_sha256": sha256_bytes(predecessor), "recommended_next_gate": predecessor_doc["return_gate"],
        "return_gate": predecessor_doc["return_gate"], "generated_at_utc": "2026-09-24T00:00:00Z",
    })
    store = store or MemoryDriveStore()
    store.seed("stable", "current", "next_task.md", predecessor, revision=2)
    store.seed("predecessor-task", "history", "gen2.md", predecessor)
    store.seed("source-result", "current", "codex_result.json", source)
    intent = TransitionIntent(
        "predecessor-task", sha256_bytes(predecessor), 2, "source-result", sha256_bytes(source),
        successor_doc["authority_binding"]["requirement_sha256"], successor_doc["policy_bundle_sha256"],
        predecessor_doc["task_id"], successor_doc["return_gate"], successor_doc["next_executor"],
        successor, "history", "gen3.md", "stable",
    )
    return store, intent


@pytest.mark.parametrize(
    ("kwargs", "classification"),
    [
        ({"expected_generation": 4}, "STALE_TASK"),
        ({"expected_task_sha256": "0" * 64}, "STALE_TASK"),
        ({"expected_base_sha": "0" * 40}, "BASELINE_DRIFT"),
        ({"expected_requirement_sha256": "0" * 64}, "REQUIREMENT_BINDING_MISMATCH"),
        ({"expected_policy_sha256": "0" * 64}, "POLICY_BINDING_MISMATCH"),
        ({"expected_executor": "WEB_AI"}, "EXECUTOR_MISMATCH"),
        ({"current_pointer_bytes": b"old"}, "OLD_STABLE_POINTER"),
    ],
)
def test_task_negative_cases_fail_closed(task_bytes, kwargs, classification):
    result = verify_task_bytes(task_bytes, **kwargs)
    assert not result.ok
    assert result.classification == classification


def test_malformed_schema_fails_closed(task_dict):
    task_dict.pop("canonical_binding")
    result = verify_task_bytes(yaml.safe_dump(task_dict).encode())
    assert result.classification == "TASK_SCHEMA_INVALID"


def test_result_wrong_hash_and_ci_mismatch_fail_closed(task_bytes, result_dict, github_inspector):
    result_dict["source_task_sha256"] = "0" * 64
    assert verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector).classification == "WRONG_RESULT_HASH"
    result_dict["source_task_sha256"] = sha256_bytes(task_bytes)
    result_dict["pr"]["head_sha"] = "c" * 40
    assert verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, github_inspector=github_inspector).classification == "PR_CI_HEAD_MISMATCH"


def test_history_current_mismatch_fails_closed(tmp_path, task_dict):
    store, intent = _strict_case(tmp_path, task_dict)
    store.records["stable"].content = b"unknown"
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "OLD_STABLE_POINTER"


def test_duplicate_task_id_with_different_bytes_fails_closed(tmp_path, task_dict):
    store, intent = _strict_case(tmp_path, task_dict)
    store.seed("history-existing", "history", "gen3.md", b"different")
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "DUPLICATE_TASK_ID"


def test_dispatch_is_denied_before_commit(tmp_path, task_dict):
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    assert not dispatch_allowed(ledger, task_id=task_dict["task_id"], generation=3)


def test_unclassified_exception_is_fail_closed():
    result = guarded_verify(lambda: 1 / 0)
    assert not result.ok
    assert result.classification == "UNCLASSIFIED_EXCEPTION"


def test_git_ancestry_mismatch_fails_closed(monkeypatch, task_bytes, result_dict, github_inspector):
    class FakeGit:
        def __init__(self, root):
            pass

        def head_sha(self):
            return result_dict["head_sha"]

        def is_ancestor(self, ancestor, descendant):
            return False

        def changed_files(self, base):
            return tuple(sorted(result_dict["changed_files"]))

    monkeypatch.setattr("automation.verify.GitInspector", FakeGit)
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, git_root="unused", github_inspector=github_inspector)
    assert result.classification == "GIT_ANCESTRY_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "classification"),
    [
        ("pr_head", "PR_FACTS_MISMATCH"),
        ("ci_head", "PR_CI_HEAD_MISMATCH"),
        ("scope", "SCOPE_VIOLATION"),
        ("ancestry", "GIT_ANCESTRY_MISMATCH"),
    ],
)
def test_bounded_repair_keeps_other_result_guards(
    monkeypatch, task_dict, result_dict, github_inspector, mutation, classification,
):
    task_dict["task_type"] = "CODEX_BOUNDED_REPAIR"
    task_bytes = yaml.safe_dump(task_dict, sort_keys=False).encode()
    result_dict["source_task_sha256"] = sha256_bytes(task_bytes)
    observed_pr = github_inspector.collect(33, "Offline tests", result_dict["head_sha"])["pr"]
    observed_pr["baseRefOid"] = "c" * 40
    if mutation == "pr_head":
        observed_pr["headRefOid"] = "d" * 40
    elif mutation == "ci_head":
        result_dict["ci"]["head_sha"] = "d" * 40
    elif mutation == "scope":
        result_dict["changed_files"].append("src/cba_kb/release.py")
    else:
        class NonAncestorGit:
            def __init__(self, root):
                pass

            def head_sha(self):
                return result_dict["head_sha"]

            def is_ancestor(self, ancestor, descendant):
                return False

        monkeypatch.setattr("automation.verify.GitInspector", NonAncestorGit)
    result = verify_result_bytes(
        canonical_json_bytes(result_dict), task_bytes,
        git_root="unused" if mutation == "ancestry" else None,
        github_inspector=github_inspector,
    )
    assert result.classification == classification


def test_stable_revision_not_advanced_fails_closed(tmp_path, task_dict):
    class NoRevisionStore(MemoryDriveStore):
        def update(self, file_id, content):
            current = self.read(file_id)
            self.records[file_id].content = content
            return self.read(file_id)

    store, intent = _strict_case(tmp_path, task_dict, NoRevisionStore())
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "STABLE_REVISION_NOT_ADVANCED"


@pytest.mark.parametrize(("mutation", "classification"), [
    ("unbound_commit", "UNBOUND_DESCENDANT_COMMIT"),
    ("unverified_result", "DESCENDANT_EVIDENCE_MISMATCH"),
    ("missing_package", "DRIVE_FILE_NOT_FOUND"),
    ("product_file", "DESCENDANT_SCOPE_VIOLATION"),
    ("reverted_branch_product", "DESCENDANT_SCOPE_VIOLATION"),
    ("historical_merge", "HISTORICAL_PR_MISMATCH"),
    ("reviewed_head", "HISTORICAL_PR_MISMATCH"),
    ("reviewed_ci", "HISTORICAL_CI_MISMATCH"),
    ("final_main", "FINAL_MAIN_MISMATCH"),
    ("missing_main_ci", "CI_FACTS_MISMATCH"),
    ("stale_main_ci", "CI_FACTS_MISMATCH"),
    ("wrong_main_event", "CI_FACTS_MISMATCH"),
    ("policy", "DESCENDANT_EVIDENCE_MISMATCH"),
    ("requirement", "DESCENDANT_EVIDENCE_MISMATCH"),
    ("task_binding", "DESCENDANT_EVIDENCE_MISMATCH"),
    ("ancestry", "GIT_ANCESTRY_MISMATCH"),
    ("descendant_pr", "DESCENDANT_PR_MISMATCH"),
    ("descendant_pr_ci", "DESCENDANT_CI_MISMATCH"),
    ("descendant_push_ci", "DESCENDANT_CI_MISMATCH"),
])
def test_descendant_mode_rejects_unbound_or_drifted_evidence(
    monkeypatch, task_dict, result_dict, mutation, classification,
):
    _, task_bytes, result, store, github, _, historical_pr, descendants, main_ref, runs, git_type = descendant_case(
        monkeypatch, task_dict, result_dict,
    )
    entry = result["machine_facts"]["descendants"][0]
    if mutation == "unbound_commit":
        result["machine_facts"]["descendants"].pop()
    elif mutation == "unverified_result":
        import json
        file = store.records[entry["process_result_file_id"]]
        doc = json.loads(file.content)
        doc["status"] = "FAIL"
        file.content = canonical_json_bytes(doc)
        entry["process_result_sha256"] = sha256_bytes(file.content)
    elif mutation == "missing_package":
        del store.records[entry["process_review_package_file_id"]]
    elif mutation == "product_file":
        class ProductGit(git_type):
            def _run(self, *args):
                if args[:2] == ("diff", "--name-only") and args[2] == "d" * 40:
                    return "src/cba_kb/cli.py"
                return super()._run(*args)
        monkeypatch.setattr("automation.verify.GitInspector", ProductGit)
    elif mutation == "reverted_branch_product":
        class RevertedProductGit(git_type):
            def _run(self, *args):
                if args[0] == "diff-tree" and args[-1] == "1" * 40:
                    return "src/cba_kb/cli.py"
                return super()._run(*args)
        monkeypatch.setattr("automation.verify.GitInspector", RevertedProductGit)
    elif mutation == "historical_merge":
        historical_pr["mergeCommit"]["oid"] = "0" * 40
    elif mutation == "reviewed_head":
        result["pr"]["reviewed_head_sha"] = "0" * 40
    elif mutation == "reviewed_ci":
        runs["b" * 40][0]["conclusion"] = "failure"
    elif mutation == "final_main":
        main_ref["object"]["sha"] = "0" * 40
    elif mutation == "missing_main_ci":
        runs["f" * 40].clear()
    elif mutation == "stale_main_ci":
        result["ci"]["runs"] = [{"id": 1}]
    elif mutation == "wrong_main_event":
        runs["f" * 40][0]["event"] = "pull_request"
    elif mutation in {"policy", "requirement", "task_binding"}:
        import json
        file_id = entry["process_task_file_id"] if mutation != "task_binding" else entry["process_result_file_id"]
        file = store.records[file_id]
        if mutation == "task_binding":
            doc = json.loads(file.content)
            doc["task_id"] = "00000000-0000-4000-8000-000000000000"
            file.content = canonical_json_bytes(doc)
            entry["process_result_sha256"] = sha256_bytes(file.content)
        else:
            doc = yaml.safe_load(file.content)
            if mutation == "policy":
                doc["policy_bundle_sha256"] = "0" * 64
            else:
                doc["authority_binding"]["requirement_sha256"] = "0" * 64
            file.content = yaml.safe_dump(doc, sort_keys=False).encode()
            entry["process_task_sha256"] = sha256_bytes(file.content)
    elif mutation == "ancestry":
        class NonAncestorGit(git_type):
            def is_ancestor(self, ancestor, descendant):
                return False
        monkeypatch.setattr("automation.verify.GitInspector", NonAncestorGit)
    elif mutation == "descendant_pr":
        descendants[37]["mergeCommit"]["oid"] = "0" * 40
    elif mutation == "descendant_pr_ci":
        runs["1" * 40][0]["event"] = "push"
    elif mutation == "descendant_push_ci":
        runs["e" * 40][0]["conclusion"] = "failure"
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == classification


@pytest.mark.parametrize(("mutation", "classification"), [
    ("product_failure", "RETROSPECTIVE_INELIGIBLE"),
    ("test_failure", "RETROSPECTIVE_INELIGIBLE"),
    ("wrong_task_sha", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("wrong_result_sha", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("wrong_package", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("wrong_pr", "RETROSPECTIVE_EVENT_MISMATCH"),
    ("wrong_reviewed_head", "RETROSPECTIVE_EVENT_MISMATCH"),
    ("wrong_merge", "RETROSPECTIVE_EVENT_MISMATCH"),
    ("wrong_main_ci", "RETROSPECTIVE_EVENT_MISMATCH"),
    ("wrong_requirement", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("wrong_policy", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("missing_history", "DRIVE_FILE_NOT_FOUND"),
    ("unbound_proof", "RETROSPECTIVE_PROOF_UNBOUND"),
    ("replaced_result", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("changed_classification", "RETROSPECTIVE_PROOF_BINDING_MISMATCH"),
    ("production_claim", "RETROSPECTIVE_PROOF_INVALID"),
    ("arbitrary_blocked", "DESCENDANT_EVIDENCE_MISMATCH"),
    ("wrong_ancestry", "GIT_ANCESTRY_MISMATCH"),
    ("wrong_capability", "RETROSPECTIVE_PROOF_INVALID"),
    ("wrong_verifier_version", "RETROSPECTIVE_PROOF_INVALID"),
    ("premature_proof", "RETROSPECTIVE_PROOF_INVALID"),
    ("identity_mutation", "RETROSPECTIVE_INELIGIBLE"),
    ("failed_reviewed_ci", "RETROSPECTIVE_CI_MISMATCH"),
    ("failed_main_ci", "RETROSPECTIVE_CI_MISMATCH"),
    ("proof_hash_drift", "RETROSPECTIVE_PROOF_HASH_MISMATCH"),
    ("authority_task_drift", "RETROSPECTIVE_PROOF_UNBOUND"),
])
def test_retrospective_proof_rejects_invalid_history_or_authority(
    monkeypatch, task_dict, result_dict, mutation, classification,
):
    import json
    import automation.verify as verify_module

    task, _, result, store, github, proof, entry, _ = retrospective_case(monkeypatch, task_dict, result_dict)
    if mutation in {"product_failure", "test_failure", "identity_mutation"}:
        file = store.records[entry["human_result_file_id"]]
        historical = json.loads(file.content)
        if mutation == "identity_mutation":
            historical["machine_facts"]["identity_registry_mutation"] = 1
        else:
            historical["errors"][0]["code"] = "PRODUCT_FAILURE" if mutation == "product_failure" else "TEST_FAILURE"
        file.content = canonical_json_bytes(historical)
        entry["human_result_sha256"] = sha256_bytes(file.content)
        historical_task_bytes = store.read(entry["human_task_file_id"]).content
        package = build_review_package(yaml.safe_load(historical_task_bytes), historical,
                                       task_bytes=historical_task_bytes, result_bytes=file.content)
        package_bytes = canonical_json_bytes(package)
        store.records[entry["human_review_package_file_id"]].content = package_bytes
        proof["historical"].update(result_sha256=entry["human_result_sha256"],
                                   review_package_sha256=sha256_bytes(package_bytes),
                                   error_codes=[error["code"] for error in historical["errors"]])
    elif mutation == "wrong_task_sha":
        proof["historical"]["task_sha256"] = "0" * 64
    elif mutation == "wrong_result_sha":
        proof["historical"]["result_sha256"] = "0" * 64
    elif mutation == "wrong_package":
        proof["historical"]["review_package_sha256"] = "0" * 64
    elif mutation == "wrong_pr":
        proof["event"]["pr_number"] = 999
    elif mutation == "wrong_reviewed_head":
        proof["event"]["reviewed_head_sha"] = "0" * 40
    elif mutation == "wrong_merge":
        proof["event"]["merge_commit_sha"] = "0" * 40
    elif mutation == "wrong_main_ci":
        proof["event"]["exact_main_ci_run_id"] = 999
    elif mutation == "wrong_requirement":
        proof["requirement_sha256"] = "0" * 64
    elif mutation == "wrong_policy":
        proof["policy_bundle_sha256"] = "0" * 64
    elif mutation == "missing_history":
        del store.records[entry["human_review_package_file_id"]]
    elif mutation == "unbound_proof":
        task["allowed_actions"].pop()
    elif mutation == "replaced_result":
        proof["historical"]["status"] = "PASS"
    elif mutation == "changed_classification":
        proof["historical"]["classification"] = "SUCCESS"
    elif mutation == "production_claim":
        proof["production_authority"] = True
    elif mutation == "arbitrary_blocked":
        entry.pop("retrospective_proof_file_id")
        entry.pop("retrospective_proof_sha256")
    elif mutation == "wrong_ancestry":
        base_git = verify_module.GitInspector
        class WrongAncestryGit(base_git):
            def is_ancestor(self, ancestor, descendant):
                return False
        monkeypatch.setattr("automation.verify.GitInspector", WrongAncestryGit)
    elif mutation == "wrong_capability":
        proof["capability"] = "ARBITRARY_BLOCKED_OVERRIDE"
    elif mutation == "wrong_verifier_version":
        proof["verifier_version"] = "0.0.0"
    elif mutation == "premature_proof":
        proof["verified_at_utc"] = "2026-09-25T00:00:00Z"
    elif mutation == "failed_reviewed_ci":
        github._json("api", f"repos/{task['repository']}/actions/runs?head_sha={'1'*40}&per_page=100")["workflow_runs"][0]["conclusion"] = "failure"
    elif mutation == "failed_main_ci":
        github._json("api", f"repos/{task['repository']}/actions/runs?head_sha={'e'*40}&per_page=100")["workflow_runs"][0]["conclusion"] = "failure"
    elif mutation == "authority_task_drift":
        store.records["proof-authority"].content += b"\n# drift\n"
    if mutation not in {"unbound_proof", "arbitrary_blocked", "wrong_ancestry", "proof_hash_drift", "authority_task_drift", "failed_reviewed_ci", "failed_main_ci"}:
        proof_bytes = canonical_json_bytes(proof)
        store.records["later-proof"].content = proof_bytes
        entry["retrospective_proof_sha256"] = sha256_bytes(proof_bytes)
        task["allowed_actions"][-1] = (
            f"require retrospective proof SHA256 {entry['retrospective_proof_sha256']} "
            f"authority SHA256 {proof['authority']['task_sha256']}")
    if mutation == "proof_hash_drift":
        store.records["later-proof"].content += b"\n"
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == classification


@pytest.mark.parametrize(("mutation", "classification"), [
    ("missing_task", "DRIVE_FILE_NOT_FOUND"),
    ("missing_result", "DRIVE_FILE_NOT_FOUND"),
    ("missing_review_package", "DRIVE_FILE_NOT_FOUND"),
    ("missing_post_merge_result", "DRIVE_FILE_NOT_FOUND"),
    ("missing_post_merge_review_package", "DRIVE_FILE_NOT_FOUND"),
    ("task_sha", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("result_sha", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("review_package_sha", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("post_merge_result_sha", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("post_merge_review_package_sha", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("unfrozen_evidence", "EXTERNAL_DESCENDANT_UNBOUND"),
    ("wrong_pr_number", "EXTERNAL_DESCENDANT_PR_MISMATCH"),
    ("wrong_reviewed_head", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("wrong_merge", "UNBOUND_DESCENDANT_COMMIT"),
    ("wrong_merge_time", "EXTERNAL_DESCENDANT_PR_MISMATCH"),
    ("failed_reviewed_ci", "EXTERNAL_DESCENDANT_CI_MISMATCH"),
    ("stale_exact_main_ci", "CI_FACTS_MISMATCH"),
    ("wrong_main_event", "CI_FACTS_MISMATCH"),
    ("wrong_repository", "EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH"),
    ("product_code_delta", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("identity_delta", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("production_mutation", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("publish", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("restore", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("production_authority", "EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION"),
    ("product_file", "EXTERNAL_DESCENDANT_SCOPE_VIOLATION"),
    ("unbound_first_parent", "UNBOUND_DESCENDANT_COMMIT"),
    ("wrong_ancestry", "GIT_ANCESTRY_MISMATCH"),
])
def test_external_control_plane_descendant_fails_closed(
    monkeypatch, task_dict, result_dict, mutation, classification,
):
    import json
    import automation.verify as verifier

    task, _, result, store, github, entry, observed_prs, _, runs = external_control_plane_case(
        monkeypatch, task_dict, result_dict,
    )
    kinds = ("task", "result", "review_package", "post_merge_result", "post_merge_review_package")
    if mutation.startswith("missing_") and mutation.removeprefix("missing_") in kinds:
        del store.records[entry[mutation.removeprefix("missing_") + "_file_id"]]
    elif mutation.endswith("_sha") and mutation.removesuffix("_sha") in kinds:
        entry[mutation.removesuffix("_sha") + "_sha256"] = "0" * 64
    elif mutation == "unfrozen_evidence":
        task["allowed_actions"].pop()
    elif mutation == "wrong_pr_number":
        entry["pr_number"] = 999
    elif mutation == "wrong_reviewed_head":
        entry["reviewed_head_sha"] = "0" * 40
    elif mutation == "wrong_merge":
        entry["merge_commit_sha"] = "0" * 40
    elif mutation == "wrong_merge_time":
        observed_prs[77]["mergedAt"] = "2026-09-25T04:00:00Z"
    elif mutation == "failed_reviewed_ci":
        runs["3" * 40][0]["conclusion"] = "failure"
    elif mutation == "stale_exact_main_ci":
        runs["4" * 40].clear()
    elif mutation == "wrong_main_event":
        runs["4" * 40][0]["event"] = "pull_request"
    elif mutation in {"wrong_repository", "product_code_delta", "identity_delta", "production_mutation", "publish", "restore"}:
        file = store.records[entry["post_merge_result_file_id"]]
        doc = json.loads(file.content)
        if mutation == "wrong_repository":
            doc["repository"] = "unrelated/repository"
        else:
            field, value = {
                "product_code_delta": ("PRODUCT_CODE_DELTA", 1),
                "identity_delta": ("IDENTITY_SEMANTIC_DELTA", "NONZERO"),
                "production_mutation": ("PRODUCTION_MUTATION", 1),
                "publish": ("PUBLISH", 1), "restore": ("RESTORE", 1),
            }[mutation]
            doc["machine_facts"][field] = value
        file.content = canonical_json_bytes(doc)
        entry["post_merge_result_sha256"] = sha256_bytes(file.content)
        package_file = store.records[entry["post_merge_review_package_file_id"]]
        package = json.loads(package_file.content)
        package["CONTROL"]["post_merge_result_sha256"] = entry["post_merge_result_sha256"]
        package["VERIFIED_MACHINE_FACTS"]["machine_facts"] = doc["machine_facts"]
        package_file.content = canonical_json_bytes(package)
        entry["post_merge_review_package_sha256"] = sha256_bytes(package_file.content)
    elif mutation == "production_authority":
        entry["production_authority"] = True
    elif mutation == "product_file":
        base_git = verifier.GitInspector
        class ProductGit(base_git):
            def _run(self, *args):
                if args[:2] == ("diff", "--name-only") and args[2] == "f" * 40:
                    return "src/cba_kb/cli.py"
                return super()._run(*args)
        monkeypatch.setattr("automation.verify.GitInspector", ProductGit)
    elif mutation == "unbound_first_parent":
        result["machine_facts"]["descendants"].pop()
    elif mutation == "wrong_ancestry":
        base_git = verifier.GitInspector
        class WrongGit(base_git):
            def is_ancestor(self, ancestor, descendant):
                return False
        monkeypatch.setattr("automation.verify.GitInspector", WrongGit)
    if mutation not in {"unfrozen_evidence", "wrong_merge", "unbound_first_parent", "wrong_ancestry"}:
        task["allowed_actions"][-1] = (
            "external control-plane descendant task SHA256 {task_sha256} result SHA256 {result_sha256} "
            "review SHA256 {review_package_sha256} post-merge result SHA256 {post_merge_result_sha256} "
            "post-merge review SHA256 {post_merge_review_package_sha256}").format(**entry)
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="exact-main",
                                   github_inspector=github, predecessor_store=store)
    assert verified.classification == classification


def test_source_result_hash_mismatch_fails_closed(tmp_path, task_dict):
    store, intent = _strict_case(tmp_path, task_dict)
    store.records["source-result"].content += b"drift"
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "SOURCE_RESULT_HASH_MISMATCH"


def test_source_result_identity_mismatch_fails_closed(tmp_path, task_dict):
    store, intent = _strict_case(tmp_path, task_dict)
    source = __import__("json").loads(store.read("source-result").content)
    source["task_id"] = "00000000-0000-4000-8000-000000000000"
    changed = canonical_json_bytes(source)
    store.records["source-result"].content = changed
    intent = replace(intent, source_result_sha256=sha256_bytes(changed))
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "SOURCE_RESULT_IDENTITY_MISMATCH"


def test_provider_history_readback_mismatch_fails_closed(tmp_path, task_dict):
    class CorruptHistoryStore(MemoryDriveStore):
        def create(self, folder_id, name, content):
            result = super().create(folder_id, name, content)
            self.records[result.file_id].content = b"corrupt"
            return self.read(result.file_id)

    store, intent = _strict_case(tmp_path, task_dict, CorruptHistoryStore())
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "TRANSITION_READBACK_MISMATCH"


class FakeProviderDrive:
    def __init__(self, *, advance=True, corrupt=False):
        self.advance = advance
        self.corrupt = corrupt
        self.records = {
            "stable": {"id": "stable", "name": "next_task.md", "mimeType": "application/octet-stream", "parents": ["current"], "version": "1", "content": b"old"}
        }

    def meta(self, file_id):
        return {key: value for key, value in self.records[file_id].items() if key != "content"}

    def get(self, file_id):
        return self.records[file_id]["content"]

    def list(self, parent):
        return [self.meta(file_id) for file_id, item in self.records.items() if parent in item["parents"]]

    def ensure(self, parent, key, name, mime, content):
        self.records["history"] = {"id": "history", "name": name, "mimeType": mime, "parents": [parent], "version": "1", "content": b"corrupt" if self.corrupt else content}
        return "history"

    def put(self, file_id, content, mime):
        self.records[file_id]["content"] = b"corrupt" if self.corrupt else content
        if self.advance:
            self.records[file_id]["version"] = str(int(self.records[file_id]["version"]) + 1)
        return self.meta(file_id)


def test_provider_revision_not_advanced_is_typed():
    store = GoogleDriveStore(FakeProviderDrive(advance=False))
    with pytest.raises(P2AError) as caught:
        store.update("stable", b"new")
    assert caught.value.code == "STABLE_REVISION_NOT_ADVANCED"


def test_provider_history_mismatch_is_typed():
    store = GoogleDriveStore(FakeProviderDrive(corrupt=True))
    with pytest.raises(P2AError) as caught:
        store.create("history-folder", "gen4.md", b"expected")
    assert caught.value.code == "TRANSITION_READBACK_MISMATCH"


def test_provider_stable_mismatch_is_typed():
    store = GoogleDriveStore(FakeProviderDrive(corrupt=True))
    with pytest.raises(P2AError) as caught:
        store.update("stable", b"expected")
    assert caught.value.code == "TRANSITION_READBACK_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "classification"),
    [
        ("wrong_merge_commit", "MERGE_COMMIT_MISMATCH"),
        ("wrong_github_merge_commit", "MERGE_COMMIT_MISMATCH"),
        ("wrong_actual_main", "FINAL_MAIN_MISMATCH"),
        ("main_advanced", "FINAL_MAIN_MISMATCH"),
        ("stale_ci_head", "PR_CI_HEAD_MISMATCH"),
        ("missing_reviewed_head", "POST_MERGE_PROVENANCE_MISSING"),
        ("missing_frozen_reviewed_head", "POST_MERGE_PROVENANCE_MISSING"),
        ("missing_reviewed_run", "POST_MERGE_PROVENANCE_MISSING"),
        ("closed_not_merged", "PR_FACTS_MISMATCH"),
        ("unmerged_pr", "PR_FACTS_MISMATCH"),
        ("ordinary_task", "PR_CI_HEAD_MISMATCH"),
        ("bounded_repair", "PR_CI_HEAD_MISMATCH"),
        ("arbitrary_task", "PR_CI_HEAD_MISMATCH"),
        ("scope", "SCOPE_VIOLATION"),
        ("task_binding", "RESULT_BINDING_MISMATCH"),
        ("policy_binding", "RESULT_BINDING_MISMATCH"),
        ("requirement_binding", "REQUIREMENT_BINDING_MISMATCH"),
        ("ancestry", "GIT_ANCESTRY_MISMATCH"),
    ],
)
def test_post_merge_contract_fails_closed(monkeypatch, task_dict, result_dict, mutation, classification):
    task, result, github, observed, main_ref, merge, reviewed_runs, git_type = post_merge_case(
        monkeypatch, task_dict, result_dict,
    )
    if mutation == "wrong_merge_commit":
        result["pr"]["merge_commit_sha"] = "a" * 40
    elif mutation == "wrong_github_merge_commit":
        merge["mergeCommit"]["oid"] = "a" * 40
    elif mutation in {"wrong_actual_main", "main_advanced"}:
        main_ref["object"]["sha"] = "a" * 40
    elif mutation == "stale_ci_head":
        result["ci"]["head_sha"] = result["pr"]["head_sha"]
    elif mutation == "missing_reviewed_head":
        result["pr"].pop("reviewed_head_sha")
    elif mutation == "missing_frozen_reviewed_head":
        task["allowed_actions"] = ["inspect merged PR provenance"]
    elif mutation == "missing_reviewed_run":
        reviewed_runs["workflow_runs"] = []
    elif mutation == "closed_not_merged":
        observed["state"] = "CLOSED"
    elif mutation == "unmerged_pr":
        observed["state"] = "OPEN"
    elif mutation in {"ordinary_task", "bounded_repair", "arbitrary_task"}:
        task["task_type"] = {"ordinary_task": "CODEX_READ_ONLY_SUPERVISORY_REVIEW",
                             "bounded_repair": "CODEX_BOUNDED_REPAIR",
                             "arbitrary_task": "ARBITRARY"}[mutation]
    elif mutation == "scope":
        result["changed_files"] = ["src/cba_kb/cli.py"]
    elif mutation == "task_binding":
        result["task_id"] = "00000000-0000-4000-8000-000000000000"
    elif mutation == "policy_binding":
        result["policy_bundle_sha256"] = "a" * 64
    elif mutation == "requirement_binding":
        result["machine_facts"]["requirement_sha256"] = "a" * 64
    elif mutation == "ancestry":
        monkeypatch.setattr(git_type, "is_ancestor", lambda self, ancestor, descendant: False)
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes,
                                   git_root="exact-main", github_inspector=github)
    assert not verified.ok
    assert verified.classification == classification


@pytest.mark.parametrize(("mutation", "classification"), [
    ("diverged_main", "GIT_ANCESTRY_MISMATCH"),
    ("wrong_local_main", "HISTORICAL_SOURCE_MAIN_MISMATCH"),
    ("wrong_task_sha", "HISTORICAL_SOURCE_HASH_MISMATCH"),
    ("rewritten_task", "HISTORICAL_SOURCE_HASH_MISMATCH"),
    ("wrong_result_sha", "HISTORICAL_SOURCE_HASH_MISMATCH"),
    ("rewritten_result", "HISTORICAL_SOURCE_HASH_MISMATCH"),
    ("wrong_package_sha", "HISTORICAL_SOURCE_HASH_MISMATCH"),
    ("wrong_package_binding", "HISTORICAL_SOURCE_PACKAGE_MISMATCH"),
    ("wrong_merge", "HISTORICAL_SOURCE_PR_MISMATCH"),
    ("wrong_reviewed_ci", "HISTORICAL_SOURCE_CI_MISMATCH"),
    ("missing_historical_ci", "HISTORICAL_SOURCE_CI_MISMATCH"),
    ("failed_historical_ci", "HISTORICAL_SOURCE_CI_MISMATCH"),
    ("wrong_requirement", "HISTORICAL_SOURCE_BINDING_MISMATCH"),
    ("wrong_policy", "HISTORICAL_SOURCE_BINDING_MISMATCH"),
    ("wrong_repository", "HISTORICAL_SOURCE_BINDING_MISMATCH"),
    ("identity_mutation", "HISTORICAL_SOURCE_MUTATION"),
    ("production_mutation", "HISTORICAL_SOURCE_MUTATION"),
    ("blocked_source", "HISTORICAL_SOURCE_NOT_PASS"),
])
def test_historical_source_verification_fails_closed(
    monkeypatch, task_dict, result_dict, mutation, classification,
):
    import json

    task, result, store, kwargs, main_ref, main_runs, reviewed_runs, observed, git_type = historical_source_case(
        monkeypatch, task_dict, result_dict,
    )
    if mutation == "diverged_main":
        class DivergedGit(git_type):
            def is_ancestor(self, ancestor, descendant):
                return False
        monkeypatch.setattr("automation.verify.GitInspector", DivergedGit)
    elif mutation == "wrong_local_main":
        main_ref["object"]["sha"] = "9" * 40
    elif mutation == "wrong_task_sha":
        kwargs["expected_task_sha256"] = "0" * 64
    elif mutation == "rewritten_task":
        store.records["historical-task"].content += b"\n# rewritten\n"
    elif mutation == "wrong_result_sha":
        kwargs["expected_result_sha256"] = "0" * 64
    elif mutation == "rewritten_result":
        store.records["historical-result"].content += b"\n"
    elif mutation == "wrong_package_sha":
        kwargs["expected_review_package_sha256"] = "0" * 64
    elif mutation == "wrong_package_binding":
        item = store.records["historical-package"]
        package = json.loads(item.content)
        package["CONTROL"]["task_id"] = "00000000-0000-4000-8000-000000000000"
        item.content = canonical_json_bytes(package)
        kwargs["expected_review_package_sha256"] = sha256_bytes(item.content)
    elif mutation == "wrong_merge":
        observed["mergeCommit"]["oid"] = "0" * 40
    elif mutation == "wrong_reviewed_ci":
        reviewed_runs["workflow_runs"].clear()
    elif mutation == "missing_historical_ci":
        main_runs.clear()
    elif mutation == "failed_historical_ci":
        main_runs[0]["conclusion"] = "failure"
    elif mutation == "wrong_requirement":
        kwargs["expected_requirement_sha256"] = "0" * 64
    elif mutation == "wrong_policy":
        kwargs["expected_policy_sha256"] = "0" * 64
    elif mutation == "wrong_repository":
        kwargs["expected_repository"] = "unrelated/repository"
    elif mutation in {"identity_mutation", "production_mutation", "blocked_source"}:
        item = store.records["historical-result"]
        document = json.loads(item.content)
        if mutation == "identity_mutation":
            document["machine_facts"]["identity_registry_mutation"] = 1
        elif mutation == "production_mutation":
            document["machine_facts"]["production_mutation"] = 1
        else:
            document["status"] = "BLOCKED"
        item.content = canonical_json_bytes(document)
        kwargs["expected_result_sha256"] = sha256_bytes(item.content)
        package_item = store.records["historical-package"]
        package = json.loads(package_item.content)
        package["VERIFIED_MACHINE_FACTS"]["result_sha256"] = kwargs["expected_result_sha256"]
        package_item.content = canonical_json_bytes(package)
        kwargs["expected_review_package_sha256"] = sha256_bytes(package_item.content)
    verified = verify_historical_source_result(store, **kwargs)
    assert verified.classification == classification


@pytest.mark.parametrize(
    ("mutation", "classification"),
    [
        ("wrong_reviewed_head", "MERGE_COMMIT_MISMATCH"),
        ("wrong_predecessor_sha", "PREDECESSOR_RESULT_HASH_MISMATCH"),
        ("missing_predecessor", "DRIVE_FILE_NOT_FOUND"),
        ("predecessor_not_pass", "PREDECESSOR_NOT_VERIFIED_PASS"),
        ("predecessor_ci_not_green", "PREDECESSOR_CI_MISMATCH"),
        ("predecessor_ci_wrong_head", "PREDECESSOR_CI_MISMATCH"),
        ("wrong_pr_base", "MERGE_COMMIT_MISMATCH"),
        ("closed_not_merged", "PR_FACTS_MISMATCH"),
        ("wrong_merge_commit", "MERGE_COMMIT_MISMATCH"),
        ("main_advanced", "FINAL_MAIN_MISMATCH"),
        ("stale_exact_main_ci", "PR_CI_HEAD_MISMATCH"),
        ("wrong_ci_event", "CI_FACTS_MISMATCH"),
        ("missing_exact_main_ci", "CI_FACTS_MISMATCH"),
        ("ordinary_task", "TEST_EVIDENCE_INCOMPLETE"),
        ("bounded_repair_task", "TEST_EVIDENCE_INCOMPLETE"),
        ("post_merge_task", "TEST_EVIDENCE_INCOMPLETE"),
        ("scope_mismatch", "SCOPE_VIOLATION"),
        ("policy_mismatch", "RESULT_BINDING_MISMATCH"),
        ("requirement_mismatch", "REQUIREMENT_BINDING_MISMATCH"),
        ("task_binding_mismatch", "RESULT_BINDING_MISMATCH"),
        ("git_ancestry_mismatch", "GIT_ANCESTRY_MISMATCH"),
    ],
)
def test_human_merge_contract_fails_closed(monkeypatch, task_dict, result_dict, mutation, classification):
    task, task_bytes, result, store, github, observed, merge, main_ref, reviewed_runs, main_runs, git_type = human_merge_case(
        monkeypatch, task_dict, result_dict,
    )
    if mutation == "wrong_reviewed_head":
        result["pr"]["head_sha"] = "d" * 40
    elif mutation == "wrong_predecessor_sha":
        result["machine_facts"]["predecessor_result_sha256"] = "d" * 64
        for name in ("focused_tests", "full_regression"):
            result[name]["predecessor_result_sha256"] = "d" * 64
    elif mutation == "missing_predecessor":
        store.records.pop("previous-result")
    elif mutation in {"predecessor_not_pass", "predecessor_ci_not_green", "predecessor_ci_wrong_head"}:
        previous = __import__("json").loads(store.read("previous-result").content)
        if mutation == "predecessor_not_pass":
            previous["status"] = "FAIL"
        elif mutation == "predecessor_ci_not_green":
            previous["ci"]["conclusion"] = "failure"
        else:
            previous["ci"]["head_sha"] = "d" * 40
        changed = canonical_json_bytes(previous)
        store.records["previous-result"].content = changed
        result["machine_facts"]["predecessor_result_sha256"] = sha256_bytes(changed)
        for name in ("focused_tests", "full_regression"):
            result[name]["predecessor_result_sha256"] = sha256_bytes(changed)
        package = __import__("json").loads(store.read("previous-package").content)
        package["VERIFIED_MACHINE_FACTS"]["result_sha256"] = sha256_bytes(changed)
        package["VERIFIED_MACHINE_FACTS"]["ci"] = previous["ci"]
        store.records["previous-package"].content = canonical_json_bytes(package)
    elif mutation == "wrong_pr_base":
        result["pr"]["base_sha"] = "d" * 40
    elif mutation == "closed_not_merged":
        observed["state"] = "CLOSED"
    elif mutation == "wrong_merge_commit":
        result["pr"]["merge_commit_sha"] = "d" * 40
    elif mutation == "main_advanced":
        main_ref["object"]["sha"] = "d" * 40
    elif mutation == "stale_exact_main_ci":
        result["ci"]["head_sha"] = result["pr"]["head_sha"]
    elif mutation == "wrong_ci_event":
        main_runs[0]["event"] = "pull_request"
    elif mutation == "missing_exact_main_ci":
        main_runs.clear()
    elif mutation in {"ordinary_task", "bounded_repair_task", "post_merge_task"}:
        task["task_type"] = {"ordinary_task": "CODEX_READ_ONLY_SUPERVISORY_REVIEW",
                             "bounded_repair_task": "CODEX_BOUNDED_REPAIR",
                             "post_merge_task": "CODEX_READ_ONLY_POST_MERGE_RECONCILIATION"}[mutation]
    elif mutation == "scope_mismatch":
        result["changed_files"] = ["src/cba_kb/cli.py"]
    elif mutation == "policy_mismatch":
        result["policy_bundle_sha256"] = "d" * 64
    elif mutation == "requirement_mismatch":
        result["machine_facts"]["requirement_sha256"] = "d" * 64
    elif mutation == "task_binding_mismatch":
        result["task_id"] = "00000000-0000-4000-8000-000000000000"
    elif mutation == "git_ancestry_mismatch":
        monkeypatch.setattr(git_type, "_run", lambda self, *args: "wrong parents")
    task_bytes = yaml.safe_dump(task, sort_keys=False).encode()
    result["source_task_sha256"] = sha256_bytes(task_bytes)
    verified = verify_result_bytes(canonical_json_bytes(result), task_bytes, git_root="final-main",
                                   github_inspector=github, predecessor_store=store)
    assert not verified.ok
    assert verified.classification == classification
