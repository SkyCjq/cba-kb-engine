import pytest
import yaml

from automation.drive_io import MemoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, sha256_bytes
from automation.verify import guarded_verify, verify_result_bytes, verify_task_bytes


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


def test_result_wrong_hash_and_ci_mismatch_fail_closed(task_bytes, result_dict):
    result_dict["source_task_sha256"] = "0" * 64
    assert verify_result_bytes(canonical_json_bytes(result_dict), task_bytes).classification == "WRONG_RESULT_HASH"
    result_dict["source_task_sha256"] = sha256_bytes(task_bytes)
    result_dict["pr"]["head_sha"] = "c" * 40
    assert verify_result_bytes(canonical_json_bytes(result_dict), task_bytes).classification == "PR_CI_HEAD_MISMATCH"


def test_history_current_mismatch_fails_closed(tmp_path, task_bytes):
    store = MemoryDriveStore()
    store.seed("stable", "current", "next_task.md", b"unknown", revision=1)
    intent = TransitionIntent(sha256_bytes(b"old"), 1, task_bytes, "history", "gen3.md", "stable")
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "OLD_STABLE_POINTER"


def test_duplicate_task_id_with_different_bytes_fails_closed(tmp_path, task_bytes):
    store = MemoryDriveStore()
    predecessor = b"old"
    store.seed("stable", "current", "next_task.md", predecessor)
    store.seed("history-existing", "history", "gen3.md", b"different")
    intent = TransitionIntent(sha256_bytes(predecessor), 1, task_bytes, "history", "gen3.md", "stable")
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


def test_git_ancestry_mismatch_fails_closed(monkeypatch, task_bytes, result_dict):
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
    result = verify_result_bytes(canonical_json_bytes(result_dict), task_bytes, git_root="unused")
    assert result.classification == "GIT_ANCESTRY_MISMATCH"


def test_stable_revision_not_advanced_fails_closed(tmp_path, task_bytes):
    class NoRevisionStore(MemoryDriveStore):
        def update(self, file_id, content):
            current = self.read(file_id)
            self.records[file_id].content = content
            return self.read(file_id)

    store = NoRevisionStore()
    predecessor = b"old"
    store.seed("stable", "current", "next_task.md", predecessor, revision=2)
    intent = TransitionIntent(sha256_bytes(predecessor), 2, task_bytes, "history", "gen3.md", "stable")
    with pytest.raises(P2AError) as caught:
        transition_commit(store, AppendOnlyLedger(tmp_path / "ledger"), intent)
    assert caught.value.code == "STABLE_REVISION_NOT_ADVANCED"
