import pytest
import yaml
import copy
from dataclasses import replace

from automation.drive_io import GoogleDriveStore, MemoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, sha256_bytes
from automation.verify import guarded_verify, verify_result_bytes, verify_task_bytes
from test_verify import post_merge_case


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
