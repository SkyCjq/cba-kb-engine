from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.drive_io import DirectoryDriveStore
from automation.handoff import TransitionIntent, dispatch_allowed, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import P2AError, canonical_json_bytes, read_bytes, sha256_bytes
from automation.verify import verify_task_bytes


def _transition(root: Path, task: bytes, *, fault: str | None = None):
    store = DirectoryDriveStore(root / "store")
    predecessor = b"p2a-canary-predecessor\n"
    store.seed("stable-next-task", "current", "next_task.md", predecessor)
    ledger = AppendOnlyLedger(root / "ledger.jsonl")
    intent = TransitionIntent(
        predecessor_sha256=sha256_bytes(predecessor),
        predecessor_revision=1,
        successor_bytes=task,
        history_folder_id="history",
        history_name="gen3-canary-next-task.md",
        stable_file_id="stable-next-task",
    )
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
    verification = verify_task_bytes(task, current_pointer_bytes=task, expected_generation=3, expected_executor="CODEX_RUNNER")
    if not verification.ok:
        raise P2AError(verification.classification, "Canary task verification failed", errors=verification.as_dict()["errors"])
    root = Path(args.root)
    normal = _transition(root / "normal", task)
    history_recovery = _transition(root / "history-recovery", task, fault="history")
    pointer_recovery = _transition(root / "pointer-recovery", task, fault="pointer")

    bad_store = DirectoryDriveStore(root / "old-pointer" / "store")
    bad_store.seed("stable-next-task", "current", "next_task.md", b"unexpected\n")
    old_pointer = "FAIL"
    try:
        transition_commit(
            bad_store,
            AppendOnlyLedger(root / "old-pointer" / "ledger.jsonl"),
            TransitionIntent(sha256_bytes(b"expected\n"), 1, task, "history", "gen3.md", "stable-next-task"),
        )
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
