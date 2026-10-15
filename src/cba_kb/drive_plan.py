"""Guarded plan-apply workflow (2.0.4-2 slice 6, T2).

Exact plan -> prepare -> human approval -> guarded apply -> independent
readback. All operations are LOCAL (registry/index files). No Drive writes.

Workflow:
1. ``create_plan(operations, creator)``: builds an immutable, SHA-addressed
   plan. The plan_id is the SHA-256 of the canonical operations list.
2. ``prepare_plan(plan, ctx)``: validates each operation's preconditions
   against current state. Returns (ok, errors).
3. ``approve_plan(plan_path, approver, ledger_path)``: records human
   approval in the plan file + ledger event. One-time; cannot be
   re-approved or altered after.
4. ``apply_plan(plan_path, ledger_path, ctx)``: executes ONLY approved
   plans, op by op, with per-op ledger events. Any failure aborts; the
   plan records which ops completed.
5. ``verify_plan(plan_path, ctx)``: independent readback — re-reads state
   from disk and verifies each operation's expected outcome.

Supported operations:
- ``rebuild_index``: rebuild the search index from the registry.
- ``purge_tombstones``: remove tombstoned registry entries older than N days.
- ``revoke_index_grant``: revoke a document's visibility in the persisted index.

Safety invariants:
- An unapproved plan is refused at apply time (fail-closed).
- The plan's operations list is immutable after creation (SHA-verified).
- Approval is one-time and ledger-recorded.
- Apply is all-or-nothing per operation; failures are ledger-recorded.
- Readback is independent (fresh disk reads, not cached state).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import atomic
from .drive_discovery import append_ledger

PLAN_VERSION = 1
PLAN_CORRUPT = "PLAN_CORRUPT"
PLAN_NOT_APPROVED = "PLAN_NOT_APPROVED"
PLAN_ALREADY_APPROVED = "PLAN_ALREADY_APPROVED"
PLAN_PRECONDITION_FAILED = "PLAN_PRECONDITION_FAILED"
WAL_INCOMPLETE = "WAL_INCOMPLETE"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _plan_id(operations: List[Dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(operations)).hexdigest()


def create_plan(operations: List[Dict[str, Any]],
                creator: str = "muse") -> Dict[str, Any]:
    """Create an immutable, SHA-addressed plan.

    Each operation: {"op": str, "params": dict}. Supported ops:
    rebuild_index, purge_tombstones, revoke_index_grant.
    """
    if not operations:
        raise ValueError("PLAN_OPERATIONS_REQUIRED")
    for op in operations:
        if not isinstance(op, dict) or not op.get("op"):
            raise ValueError("PLAN_OPERATION_MALFORMED")
        if op["op"] not in ("rebuild_index", "purge_tombstones",
                            "revoke_index_grant"):
            raise ValueError(f"PLAN_UNKNOWN_OP: {op['op']}")
    return {
        "version": PLAN_VERSION,
        "kind": "apply_plan",
        "plan_id": _plan_id(operations),
        "creator": creator,
        "created_ts": _utcnow(),
        "operations": operations,
        "approval": None,
        "apply_log": [],
    }


def save_plan(path: Any, plan: Dict[str, Any]) -> None:
    """Persist the plan atomically."""
    data = dict(plan, version=PLAN_VERSION, kind="apply_plan")
    # Verify immutability: plan_id must match operations.
    if data.get("plan_id") != _plan_id(data.get("operations", [])):
        raise ValueError(f"{PLAN_CORRUPT}: plan_id mismatch")
    atomic(Path(path), json.dumps(data, ensure_ascii=False, indent=2,
                                  sort_keys=True).encode("utf-8"))


def load_plan(path: Any) -> Dict[str, Any]:
    """Load and validate a plan (fail-closed)."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{PLAN_CORRUPT}: {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != PLAN_VERSION:
        raise ValueError(f"{PLAN_CORRUPT}: {p}: bad version/shape")
    if data.get("kind") != "apply_plan":
        raise ValueError(f"{PLAN_CORRUPT}: {p}: kind mismatch")
    if data.get("plan_id") != _plan_id(data.get("operations", [])):
        raise ValueError(f"{PLAN_CORRUPT}: {p}: plan_id mismatch "
                         f"(operations tampered)")
    # F-01 root cause: the approval is bound to the plan_id. If operations
    # are modified and plan_id recomputed, a stale approval (with the old
    # plan_id) must not be accepted.
    approval = data.get("approval")
    if approval is not None:
        if not isinstance(approval, dict):
            raise ValueError(f"{PLAN_CORRUPT}: {p}: approval not a dict")
        if approval.get("plan_id") != data.get("plan_id"):
            raise ValueError(f"{PLAN_CORRUPT}: {p}: approval plan_id "
                             f"mismatch (stale/forged approval)")
    return data


# ---------------------------------------------------------------- prepare

def prepare_plan(plan: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate each operation's preconditions. Returns (ok, errors)."""
    errors = []
    registry = ctx.get("registry", {})
    for i, op in enumerate(plan.get("operations", [])):
        op_name = op.get("op")
        params = op.get("params", {})
        if op_name == "rebuild_index":
            if not params.get("registry") or not params.get("index"):
                errors.append(f"op {i}: rebuild_index needs registry+index")
        elif op_name == "purge_tombstones":
            days = params.get("older_than_days")
            if not isinstance(days, (int, float)) or days < 0:
                errors.append(f"op {i}: purge_tombstones needs "
                              f"older_than_days >= 0")
        elif op_name == "revoke_index_grant":
            fid = params.get("file_id")
            if not fid or fid not in registry:
                errors.append(f"op {i}: revoke_index_grant needs valid "
                              f"file_id in registry")
            idx_docs = (ctx.get("index") or {}).get("docs", {})
            if fid and fid not in idx_docs:
                errors.append(f"op {i}: file_id not in index")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------- approve

def approve_plan(plan_path: Any, approver: str, ledger_path: Any,
                 actor: str = "muse") -> Dict[str, Any]:
    """Record human approval (one-time, ledger-recorded)."""
    if not approver or not approver.strip():
        raise ValueError("PLAN_APPROVER_REQUIRED")
    plan = load_plan(plan_path)
    if plan.get("approval") is not None:
        raise ValueError(PLAN_ALREADY_APPROVED)
    approval = {
        "approver": approver.strip(),
        "approved_ts": _utcnow(),
        "plan_id": plan["plan_id"],
    }
    plan["approval"] = approval
    save_plan(plan_path, plan)
    append_ledger(ledger_path, {
        "ts": approval["approved_ts"], "actor": actor,
        "action": "drive_plan_approved",
        "prev_state": "PLANNED", "new_state": "APPROVED",
        "evidence_ref": str(plan_path), "policy_version": "2.0.4-2-slice6",
        "operator": approver.strip(), "approval_ref": plan["plan_id"],
        "retry_count": 0,
        "note": f"plan {plan['plan_id'][:12]} approved by "
                f"{approver.strip()}",
        "plan_id": plan["plan_id"],
    })
    return approval


# ------------------------------------------------------------------ apply

def _apply_rebuild_index(params: Dict[str, Any],
                         ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .drive_search import load_registry
    from .drive_search_index import build_index, save_index
    registry = load_registry(params["registry"])
    index = build_index(registry)
    save_index(params["index"], index)
    return {"indexed_docs": len(index["docs"]), "terms": len(index["terms"])}


def _apply_purge_tombstones(params: Dict[str, Any],
                            ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .drive_search import load_registry
    from .common import atomic as _atomic
    import json as _json
    from datetime import datetime, timezone, timedelta
    reg_path = Path(params["registry"])
    registry = load_registry(reg_path)
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=params["older_than_days"])
    purged = []
    for fid, record in list(registry.items()):
        if record.get("tombstoned"):
            ts = record.get("tombstoned_ts")
            try:
                tomb_ts = datetime.fromisoformat(ts) if ts else None
            except (ValueError, TypeError):
                tomb_ts = None
            if tomb_ts and tomb_ts < cutoff:
                del registry[fid]
                purged.append(fid)
    _atomic(reg_path, _json.dumps(registry, ensure_ascii=False, indent=2,
                                  sort_keys=True).encode("utf-8"))
    return {"purged": purged}


def _apply_revoke_index_grant(params: Dict[str, Any],
                              ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .drive_search_index import revoke_grant_persistent
    changed = revoke_grant_persistent(params["index"], params["file_id"],
                                      params.get("principal", "operator"))
    return {"revoked": changed}


_OP_HANDLERS = {
    "rebuild_index": _apply_rebuild_index,
    "purge_tombstones": _apply_purge_tombstones,
    "revoke_index_grant": _apply_revoke_index_grant,
}


def _approval_verified(plan: Dict[str, Any], ledger_path: Any) -> bool:
    """Verify the plan's approval via the ledger (anti-forgery).

    A plan JSON with a hand-written ``approval`` dict (not via
    ``approve_plan``) is FORGED. The only trusted approval record is the
    ``drive_plan_approved`` ledger event with a matching plan_id.
    Returns True iff such an event exists.
    """
    try:
        lines = Path(ledger_path).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return False
    plan_id = plan.get("plan_id")
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("action") != "drive_plan_approved":
            continue
        if event.get("approval_ref") == plan_id or \
           event.get("plan_id") == plan_id:
            # Also verify the approver matches the plan's approval record.
            approver = (plan.get("approval") or {}).get("approver")
            if approver and event.get("operator") == approver:
                return True
    return False


def apply_plan(plan_path: Any, ledger_path: Any,
               ctx: Optional[Dict[str, Any]] = None,
               actor: str = "muse",
               resilient: bool = False,
               timeout_s: float = 30.0) -> Dict[str, Any]:
    """Execute an approved plan, op by op. Fail-closed if not approved.

    Approval is verified via the ledger: the plan must have a matching
    ``drive_plan_approved`` event. A hand-written ``approval`` dict in the
    plan JSON (forged, not via ``approve_plan``) is rejected.

    If ``resilient`` is True, uses WAL + file locking + timeout:
    - WAL records intent before execution; crash recovery via
      ``wal_check_incomplete``.
    - File locks prevent concurrent modification.
    - Each op runs with a timeout (fail-closed).

    Returns {"plan_id": ..., "completed": [...], "failed": None | {...}}.
    Each operation is ledger-recorded. On failure, aborts and records
    which ops completed (no silent partial apply).
    """
    plan = load_plan(plan_path)
    if plan.get("approval") is None:
        raise ValueError(PLAN_NOT_APPROVED)
    if not _approval_verified(plan, ledger_path):
        raise ValueError(f"{PLAN_NOT_APPROVED}: approval not found in "
                         f"ledger (forged approval rejected)")
    ctx = ctx or {}
    completed = []

    # Resilient mode: WAL + locking.
    wal_entry = None
    if resilient:
        from .drive_resilience import (file_lock, run_with_timeout,
                                       wal_begin, wal_finish,
                                       wal_mark_complete)
        # Check for incomplete previous apply (crash recovery).
        from .drive_resilience import wal_check_incomplete
        incomplete = wal_check_incomplete(ledger_path)
        if incomplete and incomplete.get("plan_id") != plan["plan_id"]:
            raise ValueError(f"{WAL_INCOMPLETE}: previous apply of "
                             f"{incomplete.get('plan_id')} did not finish")
        wal_entry = wal_begin(ledger_path, plan["plan_id"],
                              plan["operations"])

    for i, op in enumerate(plan["operations"]):
        op_name = op["op"]
        handler = _OP_HANDLERS.get(op_name)
        if handler is None:
            raise ValueError(f"PLAN_UNKNOWN_OP: {op_name}")
        ts = _utcnow()
        try:
            if resilient:
                # Lock + timeout for each operation.
                lock_targets = []
                params = op.get("params", {})
                for key in ("registry", "index"):
                    if params.get(key):
                        lock_targets.append(params[key])
                result = None
                # Acquire all locks (in sorted order to avoid deadlock).
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for target in sorted(set(lock_targets)):
                        stack.enter_context(file_lock(target))
                    result = run_with_timeout(
                        lambda: handler(params, ctx), timeout_s)
                wal_mark_complete(ledger_path, i)
            else:
                result = handler(op.get("params", {}), ctx)
            completed.append({"op": op_name, "index": i, "result": result})
            append_ledger(ledger_path, {
                "ts": ts, "actor": actor, "action": "drive_plan_op_applied",
                "prev_state": "APPROVED", "new_state": "APPLYING",
                "evidence_ref": str(plan_path),
                "policy_version": "2.0.4-2-slice6",
                "operator": plan["approval"]["approver"],
                "approval_ref": plan["plan_id"], "retry_count": 0,
                "note": f"op {i} ({op_name}) applied",
                "plan_id": plan["plan_id"], "op_index": i,
            })
        except Exception as exc:
            append_ledger(ledger_path, {
                "ts": _utcnow(), "actor": actor,
                "action": "drive_plan_op_failed",
                "prev_state": "APPLYING", "new_state": "APPLY_FAILED",
                "evidence_ref": str(plan_path),
                "policy_version": "2.0.4-2-slice6",
                "operator": plan["approval"]["approver"],
                "approval_ref": plan["plan_id"], "retry_count": 0,
                "note": f"op {i} ({op_name}) failed: {exc}",
                "plan_id": plan["plan_id"], "op_index": i,
            })
            return {"plan_id": plan["plan_id"], "completed": completed,
                    "failed": {"op": op_name, "index": i,
                               "error": str(exc)}}
    # All ops completed.
    # F-02 fix: WAL finish AFTER final persistence. If we crash before
    # wal_finish, the WAL still says IN_PROGRESS and recovery will detect
    # it. Since ops are idempotent, re-apply is safe.
    append_ledger(ledger_path, {
        "ts": _utcnow(), "actor": actor, "action": "drive_plan_applied",
        "prev_state": "APPLYING", "new_state": "APPLIED",
        "evidence_ref": str(plan_path), "policy_version": "2.0.4-2-slice6",
        "operator": plan["approval"]["approver"],
        "approval_ref": plan["plan_id"], "retry_count": 0,
        "note": f"plan {plan['plan_id'][:12]} fully applied "
                f"({len(completed)} ops)",
        "plan_id": plan["plan_id"],
    })
    plan["apply_log"] = completed
    save_plan(plan_path, plan)
    # WAL finish LAST, after all persistence is durable.
    if resilient and wal_entry is not None:
        from .drive_resilience import wal_finish
        wal_finish(ledger_path)
    return {"plan_id": plan["plan_id"], "completed": completed,
            "failed": None}


# --------------------------------------------------------------- readback

def verify_plan(plan_path: Any,
                ctx: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """Independent readback: re-read from disk, verify each op's outcome.

    Returns (ok, errors). Uses fresh disk reads, not cached state.
    A plan that was never applied (empty apply_log) returns
    (False, ["PLAN_NOT_APPLIED"]) — vacuous success is not allowed.
    """
    plan = load_plan(plan_path)
    apply_log = plan.get("apply_log", [])
    if not apply_log:
        return False, ["PLAN_NOT_APPLIED: plan has no apply_log"]
    operations = plan.get("operations", [])
    # Completeness: the log must cover EVERY operation, in order.
    # A partial log (e.g. only the first of two ops) must not verify.
    if len(apply_log) != len(operations):
        return False, [f"PLAN_INCOMPLETE: apply_log has {len(apply_log)} "
                       f"entries but plan has {len(operations)} operations"]
    for pos, entry in enumerate(apply_log):
        if entry.get("index") != pos:
            return False, [f"PLAN_INCOMPLETE: apply_log entry {pos} has "
                           f"wrong index {entry.get('index')}"]
        if entry.get("op") != operations[pos].get("op"):
            return False, [f"PLAN_INCOMPLETE: apply_log entry {pos} op "
                           f"mismatch"]
    errors = []
    for entry in apply_log:
        op_name = entry.get("op")
        params = plan["operations"][entry["index"]].get("params", {})
        if op_name == "rebuild_index":
            from .drive_search_index import load_index
            try:
                index = load_index(params["index"])
                if not index.get("docs"):
                    errors.append(f"op {entry['index']}: index empty "
                                  f"after rebuild")
            except ValueError as exc:
                errors.append(f"op {entry['index']}: index unreadable: "
                              f"{exc}")
        elif op_name == "purge_tombstones":
            from .drive_search import load_registry
            try:
                registry = load_registry(params["registry"])
                for fid in entry["result"].get("purged", []):
                    if fid in registry:
                        errors.append(f"op {entry['index']}: {fid} not "
                                      f"purged")
            except ValueError as exc:
                errors.append(f"op {entry['index']}: registry unreadable: "
                              f"{exc}")
        elif op_name == "revoke_index_grant":
            from .drive_search_index import load_index
            try:
                index = load_index(params["index"])
                meta = index.get("docs", {}).get(params["file_id"], {})
                if meta.get("visible") is True:
                    errors.append(f"op {entry['index']}: "
                                  f"{params['file_id']} still visible")
            except ValueError as exc:
                errors.append(f"op {entry['index']}: index unreadable: "
                              f"{exc}")
    return (len(errors) == 0, errors)
