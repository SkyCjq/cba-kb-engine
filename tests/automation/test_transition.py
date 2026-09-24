import pytest
import copy
import yaml

from automation.drive_io import MemoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, sha256_bytes


def _setup(tmp_path, task_dict):
    predecessor_doc = copy.deepcopy(task_dict)
    predecessor_doc["canonical_generation"] = 2
    predecessor_doc["task_id"] = task_dict["transition_binding"]["supersedes_task_id"]
    predecessor = yaml.safe_dump(predecessor_doc, sort_keys=False).encode()
    successor_doc = copy.deepcopy(task_dict)
    successor_doc["transition_binding"]["supersedes_task_sha256"] = sha256_bytes(predecessor)
    successor = yaml.safe_dump(successor_doc, sort_keys=False).encode()
    source_result = canonical_json_bytes({
        "schema_version": "cba-kb.p2a-result.v1", "req_id": predecessor_doc["req_id"],
        "canonical_generation": 2, "task_id": predecessor_doc["task_id"], "status": "BLOCKED",
        "classification": "TEST_SOURCE", "automation_version": "0.1.0",
        "policy_bundle_sha256": predecessor_doc["policy_bundle_sha256"], "repository": predecessor_doc["repository"],
        "base_sha": predecessor_doc["expected_base_sha"], "head_sha": predecessor_doc["expected_base_sha"],
        "feature_branch": predecessor_doc["feature_branch"], "changed_files": [], "focused_tests": {},
        "full_regression": {}, "pr": None, "ci": None, "output_artifacts": [], "forbidden_actions_observed": [],
        "machine_facts": {}, "errors": [], "source_task_sha256": sha256_bytes(predecessor),
        "recommended_next_gate": predecessor_doc["return_gate"], "return_gate": predecessor_doc["return_gate"],
        "generated_at_utc": "2026-09-24T00:00:00Z",
    })
    store = MemoryDriveStore()
    store.seed("stable", "current", "next_task.md", predecessor, revision=7)
    store.seed("predecessor-task", "history", "gen2.md", predecessor, revision=1)
    store.seed("source-result", "current", "codex_result.json", source_result, revision=1)
    ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    intent = TransitionIntent(
        "predecessor-task", sha256_bytes(predecessor), 7, "source-result", sha256_bytes(source_result),
        successor_doc["authority_binding"]["requirement_sha256"], successor_doc["policy_bundle_sha256"],
        predecessor_doc["task_id"], successor_doc["return_gate"], successor_doc["next_executor"],
        successor, "history", "gen3.md", "stable",
    )
    return store, ledger, intent, successor


def test_transition_commits_history_pointer_readback_and_ledger(tmp_path, task_dict):
    store, ledger, intent, successor = _setup(tmp_path, task_dict)
    outcome = transition_commit(store, ledger, intent)
    assert outcome["classification"] == "TRANSITION_COMMITTED"
    assert outcome["stable_file_id"] == "stable"
    assert outcome["stable_revision"] == 8
    assert store.find("history", "gen3.md").content == store.read("stable").content == successor
    assert dispatch_allowed(ledger, task_id=outcome["task_id"], generation=3)
    assert [item["event"] for item in ledger.events()] == [
        "SUCCESSOR_MATERIALIZING", "HISTORY_WRITTEN", "POINTER_UPDATED", "READBACK_VERIFIED", "TRANSITION_COMMITTED"
    ]


def test_history_before_pointer_interruption_recovers_same_successor(tmp_path, task_dict):
    store, ledger, intent, _ = _setup(tmp_path, task_dict)
    with pytest.raises(P2AError, match="history write"):
        transition_commit(store, ledger, intent, fault_after="history")
    history_id = store.find("history", "gen3.md").file_id
    outcome = transition_commit(store, ledger, intent)
    assert store.find("history", "gen3.md").file_id == history_id
    assert outcome["task_id"] == "de26fcbb-ffd2-49d5-a689-74a294e3818e"


def test_pointer_before_ledger_interruption_recovers_without_revision_bump(tmp_path, task_dict):
    store, ledger, intent, _ = _setup(tmp_path, task_dict)
    with pytest.raises(P2AError, match="pointer update"):
        transition_commit(store, ledger, intent, fault_after="pointer")
    revision = store.read("stable").revision
    outcome = transition_commit(store, ledger, intent)
    assert store.read("stable").revision == revision
    assert outcome["classification"] == "TRANSITION_COMMITTED"


def test_idempotent_committed_replay(tmp_path, task_dict):
    store, ledger, intent, _ = _setup(tmp_path, task_dict)
    first = transition_commit(store, ledger, intent)
    second = transition_commit(store, ledger, intent)
    assert first["history_file_id"] == second["history_file_id"]
    assert store.read("stable").revision == 8
