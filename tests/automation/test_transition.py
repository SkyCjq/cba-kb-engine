import pytest

from automation.drive_io import MemoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, sha256_bytes


def _setup(tmp_path, task_bytes):
    predecessor = b"predecessor"
    store = MemoryDriveStore()
    store.seed("stable", "current", "next_task.md", predecessor, revision=7)
    ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    intent = TransitionIntent(sha256_bytes(predecessor), 7, task_bytes, "history", "gen3.md", "stable")
    return store, ledger, intent


def test_transition_commits_history_pointer_readback_and_ledger(tmp_path, task_bytes):
    store, ledger, intent = _setup(tmp_path, task_bytes)
    outcome = transition_commit(store, ledger, intent)
    assert outcome["classification"] == "TRANSITION_COMMITTED"
    assert outcome["stable_file_id"] == "stable"
    assert outcome["stable_revision"] == 8
    assert store.find("history", "gen3.md").content == store.read("stable").content == task_bytes
    assert dispatch_allowed(ledger, task_id=outcome["task_id"], generation=3)
    assert [item["event"] for item in ledger.events()] == [
        "SUCCESSOR_MATERIALIZING", "HISTORY_WRITTEN", "POINTER_UPDATED", "READBACK_VERIFIED", "TRANSITION_COMMITTED"
    ]


def test_history_before_pointer_interruption_recovers_same_successor(tmp_path, task_bytes):
    store, ledger, intent = _setup(tmp_path, task_bytes)
    with pytest.raises(P2AError, match="history write"):
        transition_commit(store, ledger, intent, fault_after="history")
    history_id = store.find("history", "gen3.md").file_id
    outcome = transition_commit(store, ledger, intent)
    assert store.find("history", "gen3.md").file_id == history_id
    assert outcome["task_id"] == "de26fcbb-ffd2-49d5-a689-74a294e3818e"


def test_pointer_before_ledger_interruption_recovers_without_revision_bump(tmp_path, task_bytes):
    store, ledger, intent = _setup(tmp_path, task_bytes)
    with pytest.raises(P2AError, match="pointer update"):
        transition_commit(store, ledger, intent, fault_after="pointer")
    revision = store.read("stable").revision
    outcome = transition_commit(store, ledger, intent)
    assert store.read("stable").revision == revision
    assert outcome["classification"] == "TRANSITION_COMMITTED"


def test_idempotent_committed_replay(tmp_path, task_bytes):
    store, ledger, intent = _setup(tmp_path, task_bytes)
    first = transition_commit(store, ledger, intent)
    second = transition_commit(store, ledger, intent)
    assert first["history_file_id"] == second["history_file_id"]
    assert store.read("stable").revision == 8
