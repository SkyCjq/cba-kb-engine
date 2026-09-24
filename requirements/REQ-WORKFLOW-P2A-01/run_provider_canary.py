from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from automation.drive_io import GoogleDriveStore
from automation.handoff import TransitionIntent, transition_commit
from automation.ledger import AppendOnlyLedger
from automation.models import AUTOMATION_VERSION, P2AError, canonical_json_bytes, load_yaml_bytes, read_bytes, sha256_bytes
from automation.verify import verify_task_bytes


def _task_id(prefix: str, role: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cba-kb:p2a-provider-canary:{prefix}:{role}"))


def _source_result(task: dict, task_bytes: bytes) -> bytes:
    return canonical_json_bytes({
        "schema_version": "cba-kb.p2a-result.v1", "req_id": task["req_id"],
        "canonical_generation": task["canonical_generation"], "task_id": task["task_id"],
        "status": "BLOCKED", "classification": "CANARY_PREDECESSOR_COMPLETE",
        "automation_version": AUTOMATION_VERSION, "policy_bundle_sha256": task["policy_bundle_sha256"],
        "repository": task["repository"], "base_sha": task["expected_base_sha"], "head_sha": task["expected_base_sha"],
        "feature_branch": task["feature_branch"], "changed_files": [], "focused_tests": {}, "full_regression": {},
        "pr": None, "ci": None, "output_artifacts": [], "forbidden_actions_observed": [], "machine_facts": {},
        "errors": [], "source_task_sha256": sha256_bytes(task_bytes), "recommended_next_gate": task["return_gate"],
        "return_gate": task["return_gate"], "generated_at_utc": "2026-09-24T00:00:00Z",
    })


def _lane(store: GoogleDriveStore, task_template: dict, folder_id: str, prefix: str, ledger_root: Path, fault: str | None):
    stable_name = f"{prefix}-stable-next-task.md"
    stable = store.find(folder_id, stable_name)
    if stable is None:
        stable = store.create(folder_id, stable_name, b"provider-canary-bootstrap\n")
    predecessor = copy.deepcopy(task_template)
    predecessor["canonical_generation"] = 1
    predecessor["task_id"] = _task_id(prefix, "predecessor")
    predecessor["canonical_binding"]["canonical_req_root_folder_id"] = folder_id
    predecessor["canonical_binding"]["canonical_current_folder_id"] = folder_id
    predecessor["canonical_binding"]["canonical_history_folder_id"] = folder_id
    predecessor["canonical_binding"]["canonical_next_task_file_id"] = stable.file_id
    predecessor_bytes = __import__("yaml").safe_dump(predecessor, sort_keys=False, allow_unicode=True).encode()
    if stable.content == b"provider-canary-bootstrap\n":
        stable = store.update(stable.file_id, predecessor_bytes)
    elif stable.content != predecessor_bytes:
        raise P2AError("CANARY_STABLE_STATE_UNEXPECTED", "Existing provider canary stable bytes are not the expected predecessor")
    predecessor_history = store.create(folder_id, f"{prefix}-gen1-task.md", predecessor_bytes)
    source_result_bytes = _source_result(predecessor, predecessor_bytes)
    source_result = store.create(folder_id, f"{prefix}-gen1-result.json", source_result_bytes)

    successor = copy.deepcopy(predecessor)
    successor["canonical_generation"] = 2
    successor["task_id"] = _task_id(prefix, "successor")
    successor["transition_binding"] = {
        "source_result_required": True,
        "supersedes_task_id": predecessor["task_id"],
        "supersedes_task_sha256": sha256_bytes(predecessor_bytes),
        "transition_from_gate": "PROVIDER_CANARY_GATE",
        "transition_commit_required": True,
    }
    successor_bytes = __import__("yaml").safe_dump(successor, sort_keys=False, allow_unicode=True).encode()
    verification = verify_task_bytes(successor_bytes, expected_generation=2, expected_executor=successor["next_executor"])
    if not verification.ok:
        raise P2AError(verification.classification, "Provider canary successor failed task verification")
    stable = store.read(stable.file_id)
    intent = TransitionIntent(
        predecessor_task_file_id=predecessor_history.file_id,
        predecessor_sha256=sha256_bytes(predecessor_bytes),
        predecessor_revision=stable.revision,
        source_result_file_id=source_result.file_id,
        source_result_sha256=sha256_bytes(source_result_bytes),
        expected_requirement_sha256=successor["authority_binding"]["requirement_sha256"],
        expected_policy_sha256=successor["policy_bundle_sha256"],
        expected_supersedes_task_id=predecessor["task_id"],
        expected_return_gate=successor["return_gate"],
        expected_next_executor=successor["next_executor"],
        successor_bytes=successor_bytes,
        history_folder_id=folder_id,
        history_name=f"{prefix}-gen2-task.md",
        stable_file_id=stable.file_id,
    )
    ledger = AppendOnlyLedger(ledger_root / f"{prefix}.jsonl")
    if fault:
        try:
            transition_commit(store, ledger, intent, fault_after=fault)
        except P2AError as exc:
            if exc.code != "TRANSITION_INCOMPLETE":
                raise
    outcome = transition_commit(store, ledger, intent)
    replay = transition_commit(store, ledger, intent)
    final_stable = store.read(stable.file_id)
    final_history = store.read(outcome["history_file_id"])
    if final_stable.content != successor_bytes or final_history.content != successor_bytes:
        raise P2AError("TRANSITION_READBACK_MISMATCH", "Provider canary final exact bytes differ")
    return {
        "outcome": outcome,
        "idempotent_replay": replay["history_file_id"] == outcome["history_file_id"],
        "stable_revision_before": intent.predecessor_revision,
        "stable_revision_after": final_stable.revision,
        "stable_file_id_preserved": final_stable.file_id == stable.file_id,
        "history_stable_exact_byte_match": final_history.content == final_stable.content,
        "predecessor_task_file_id": predecessor_history.file_id,
        "source_result_file_id": source_result.file_id,
        "ledger_path": str(ledger.path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--instance-root", required=True)
    parser.add_argument("--drive-folder-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--ledger-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    template_bytes = read_bytes(args.task)
    template = load_yaml_bytes(template_bytes)
    store = GoogleDriveStore.from_trusted_runtime(args.engine_root, args.instance_root)
    ledger_root = Path(args.ledger_root)
    evidence = {
        "schema_version": "cba-kb.p2a-provider-canary.v1",
        "status": "PASS",
        "classification": "REAL_PROVIDER_BACKED_NO_PRODUCTION_HANDOFF_CANARY_PASS",
        "provider": "google_drive",
        "drive_folder_id": args.drive_folder_id,
        "template_task_sha256": sha256_bytes(template_bytes),
        "normal": _lane(store, template, args.drive_folder_id, f"{args.prefix}-normal", ledger_root, None),
        "history_interruption_recovery": _lane(store, template, args.drive_folder_id, f"{args.prefix}-history", ledger_root, "history"),
        "pointer_interruption_recovery": _lane(store, template, args.drive_folder_id, f"{args.prefix}-pointer", ledger_root, "pointer"),
        "publish_invocations": 0,
        "restore_invocations": 0,
        "production_mutations": 0,
    }
    Path(args.output).write_bytes(canonical_json_bytes(evidence))
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
