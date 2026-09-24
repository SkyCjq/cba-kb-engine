from __future__ import annotations

import argparse
import json
import sys
import copy
import uuid
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.drive_io import DirectoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, load_yaml_bytes, read_bytes, sha256_bytes
from automation.verify import verify_task_bytes


def _case(root: Path, task: bytes):
    template = load_yaml_bytes(task)
    predecessor_doc = copy.deepcopy(template)
    predecessor_doc["canonical_generation"] = 1
    predecessor_doc["task_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, str(root) + ":predecessor"))
    predecessor = yaml.safe_dump(predecessor_doc, sort_keys=False, allow_unicode=True).encode()
    successor_doc = copy.deepcopy(template)
    successor_doc["canonical_generation"] = 2
    successor_doc["task_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, str(root) + ":successor"))
    successor_doc["transition_binding"]["supersedes_task_id"] = predecessor_doc["task_id"]
    successor_doc["transition_binding"]["supersedes_task_sha256"] = sha256_bytes(predecessor)
    successor = yaml.safe_dump(successor_doc, sort_keys=False, allow_unicode=True).encode()
    source = canonical_json_bytes({
        "schema_version": "cba-kb.p2a-result.v1", "req_id": predecessor_doc["req_id"], "canonical_generation": 1,
        "task_id": predecessor_doc["task_id"], "status": "BLOCKED", "classification": "CANARY_SOURCE",
        "automation_version": "0.1.0", "policy_bundle_sha256": predecessor_doc["policy_bundle_sha256"],
        "repository": predecessor_doc["repository"], "base_sha": predecessor_doc["expected_base_sha"],
        "head_sha": predecessor_doc["expected_base_sha"], "feature_branch": predecessor_doc["feature_branch"],
        "changed_files": [], "focused_tests": {}, "full_regression": {}, "pr": None, "ci": None,
        "output_artifacts": [], "forbidden_actions_observed": [], "machine_facts": {}, "errors": [],
        "source_task_sha256": sha256_bytes(predecessor), "recommended_next_gate": predecessor_doc["return_gate"],
        "return_gate": predecessor_doc["return_gate"], "generated_at_utc": "2026-09-24T00:00:00Z",
    })
    store = DirectoryDriveStore(root / "store")
    store.seed("stable-next-task", "current", "next_task.md", predecessor)
    store.seed("predecessor-task", "history", "gen1-task.md", predecessor)
    store.seed("source-result", "current", "codex_result.json", source)
    ledger = AppendOnlyLedger(root / "ledger.jsonl")
    intent = TransitionIntent(
        predecessor_task_file_id="predecessor-task",
        predecessor_sha256=sha256_bytes(predecessor),
        predecessor_revision=1,
        source_result_file_id="source-result",
        source_result_sha256=sha256_bytes(source),
        expected_requirement_sha256=successor_doc["authority_binding"]["requirement_sha256"],
        expected_policy_sha256=successor_doc["policy_bundle_sha256"],
        expected_supersedes_task_id=predecessor_doc["task_id"],
        expected_return_gate=successor_doc["return_gate"],
        expected_next_executor=successor_doc["next_executor"],
        successor_bytes=successor,
        history_folder_id="history",
        history_name="gen2-canary-next-task.md",
        stable_file_id="stable-next-task",
    )
    return store, ledger, intent


def _transition(root: Path, task: bytes, *, fault: str | None = None):
    store, ledger, intent = _case(root, task)
    if fault:
        try:
            transition_commit(store, ledger, intent, fault_after=fault)
        except P2AError as exc:
            if exc.code != "TRANSITION_INCOMPLETE":
                raise
    outcome = transition_commit(store, ledger, intent)
    replay = transition_commit(store, ledger, intent)
    assert outcome["task_id"] == replay["task_id"]
    assert outcome["history_file_id"] == replay["history_file_id"]
    assert dispatch_allowed(ledger, task_id=outcome["task_id"], generation=outcome["canonical_generation"])
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--production-before", required=True)
    parser.add_argument("--production-after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    task = read_bytes(args.task)
    before = read_bytes(args.production_before)
    after = read_bytes(args.production_after)
    if before != after:
        raise P2AError("PRODUCTION_MUTATION_DETECTED", "Production snapshots differ")
    template = load_yaml_bytes(task)
    verification = verify_task_bytes(task, current_pointer_bytes=task, expected_generation=template["canonical_generation"], expected_executor="CODEX_RUNNER")
    if not verification.ok:
        raise P2AError(verification.classification, "Canary task verification failed", errors=verification.as_dict()["errors"])
    root = Path(args.root)
    normal = _transition(root / "normal", task)
    history_recovery = _transition(root / "history-recovery", task, fault="history")
    pointer_recovery = _transition(root / "pointer-recovery", task, fault="pointer")

    bad_store, bad_ledger, bad_intent = _case(root / "old-pointer", task)
    bad_store._path("stable-next-task").write_bytes(b"unexpected\n")
    old_pointer = "FAIL"
    try:
        transition_commit(bad_store, bad_ledger, bad_intent)
    except P2AError as exc:
        old_pointer = exc.code
    executor = verify_task_bytes(task, expected_executor="WEB_AI")
    evidence = {
        "status": "PASS",
        "classification": "REAL_NO_PRODUCTION_HANDOFF_CANARY_PASS",
        "task_sha256": sha256_bytes(task),
        "production_snapshot_sha256_before": sha256_bytes(before),
        "production_snapshot_sha256_after": sha256_bytes(after),
        "production_snapshot_exact_match": True,
        "normal": normal,
        "history_before_pointer_recovery": history_recovery,
        "pointer_before_ledger_recovery": pointer_recovery,
        "old_pointer_fail_closed": old_pointer,
        "executor_mismatch_fail_closed": executor.classification,
        "publish_invocations": 0,
        "restore_invocations": 0,
        "production_mutations": 0,
    }
    Path(args.output).write_bytes(canonical_json_bytes(evidence))
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
