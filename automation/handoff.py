from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .drive_io import DriveStore
from .ledger import AppendOnlyLedger
from .models import P2AError, load_yaml_bytes, sha256_bytes
from .verify import validate_task_document


@dataclass(frozen=True)
class TransitionIntent:
    predecessor_sha256: str
    predecessor_revision: int
    successor_bytes: bytes
    history_folder_id: str
    history_name: str
    stable_file_id: str


def transition_commit(
    store: DriveStore,
    ledger: AppendOnlyLedger,
    intent: TransitionIntent,
    *,
    fault_after: str | None = None,
) -> dict[str, Any]:
    successor = load_yaml_bytes(intent.successor_bytes)
    validate_task_document(successor)
    successor_sha = sha256_bytes(intent.successor_bytes)
    req_id = successor["req_id"]
    task_id = successor["task_id"]
    generation = successor["canonical_generation"]
    policy_sha = successor["policy_bundle_sha256"]
    stable_before = store.read(intent.stable_file_id)

    ledger.append(req_id=req_id, task_id=task_id, canonical_generation=generation, event="SUCCESSOR_MATERIALIZING", result="PASS", policy_bundle_sha256=policy_sha, input_binding_sha256=intent.predecessor_sha256, output_binding_sha256=successor_sha)

    stable_is_predecessor = sha256_bytes(stable_before.content) == intent.predecessor_sha256
    stable_is_successor = stable_before.content == intent.successor_bytes
    if not stable_is_predecessor and not stable_is_successor:
        raise P2AError("OLD_STABLE_POINTER", "Stable pointer is neither predecessor nor authorized successor")
    if stable_is_predecessor and stable_before.revision != intent.predecessor_revision:
        raise P2AError("STABLE_REVISION_MISMATCH", "Stable predecessor revision drifted", expected=intent.predecessor_revision, observed=stable_before.revision)

    history = store.find(intent.history_folder_id, intent.history_name)
    if history is None:
        history = store.create(intent.history_folder_id, intent.history_name, intent.successor_bytes)
    elif history.content != intent.successor_bytes:
        raise P2AError("DUPLICATE_TASK_ID", "History name/task identity already exists with different bytes", history_file_id=history.file_id)
    ledger.append(req_id=req_id, task_id=task_id, canonical_generation=generation, event="HISTORY_WRITTEN", result="PASS", policy_bundle_sha256=policy_sha, input_binding_sha256=intent.predecessor_sha256, output_binding_sha256=successor_sha)
    if fault_after == "history":
        raise P2AError("TRANSITION_INCOMPLETE", "Injected interruption after history write")

    if stable_is_predecessor:
        stable_after = store.update(intent.stable_file_id, intent.successor_bytes)
        if stable_after.file_id != intent.stable_file_id:
            raise P2AError("TRANSITION_POINTER_UPDATE_FAILED", "Stable pointer file ID changed")
        if stable_after.revision <= stable_before.revision:
            raise P2AError("STABLE_REVISION_NOT_ADVANCED", "Stable pointer revision did not advance")
    else:
        stable_after = stable_before
        if stable_after.revision <= intent.predecessor_revision:
            raise P2AError("STABLE_REVISION_NOT_ADVANCED", "Recovered pointer has no revision advancement")
    ledger.append(req_id=req_id, task_id=task_id, canonical_generation=generation, event="POINTER_UPDATED", result="PASS", policy_bundle_sha256=policy_sha, input_binding_sha256=intent.predecessor_sha256, output_binding_sha256=successor_sha)
    if fault_after == "pointer":
        raise P2AError("TRANSITION_INCOMPLETE", "Injected interruption after pointer update")

    history_readback = store.read(history.file_id)
    stable_readback = store.read(intent.stable_file_id)
    if history_readback.content != intent.successor_bytes or stable_readback.content != intent.successor_bytes:
        raise P2AError("TRANSITION_READBACK_MISMATCH", "History/stable exact-byte readback failed")
    if history_readback.content != stable_readback.content:
        raise P2AError("TRANSITION_READBACK_MISMATCH", "History and stable bytes differ")
    ledger.append(req_id=req_id, task_id=task_id, canonical_generation=generation, event="READBACK_VERIFIED", result="PASS", policy_bundle_sha256=policy_sha, input_binding_sha256=intent.predecessor_sha256, output_binding_sha256=successor_sha)
    ledger.append(req_id=req_id, task_id=task_id, canonical_generation=generation, event="TRANSITION_COMMITTED", result="PASS", policy_bundle_sha256=policy_sha, input_binding_sha256=intent.predecessor_sha256, output_binding_sha256=successor_sha)
    return {
        "status": "PASS",
        "classification": "TRANSITION_COMMITTED",
        "dispatch_status": "DISPATCH_ALLOWED",
        "task_id": task_id,
        "canonical_generation": generation,
        "task_sha256": successor_sha,
        "history_file_id": history.file_id,
        "stable_file_id": stable_readback.file_id,
        "stable_revision": stable_readback.revision,
        "exact_readback": True,
    }


def dispatch_allowed(ledger: AppendOnlyLedger, *, task_id: str, generation: int) -> bool:
    return ledger.contains("TRANSITION_COMMITTED", task_id=task_id, generation=generation)
