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

# Drive move authorization error codes.
DRIVE_MOVE_AUTH_REQUIRED = "DRIVE_MOVE_AUTH_REQUIRED"
DRIVE_MOVE_AUTH_AMBIGUOUS = "DRIVE_MOVE_AUTH_AMBIGUOUS"
DRIVE_MOVE_ALLOWLIST_REQUIRED = "DRIVE_MOVE_ALLOWLIST_REQUIRED"
DRIVE_MOVE_FOLDER_NOT_ALLOWED = "DRIVE_MOVE_FOLDER_NOT_ALLOWED"
DRIVE_MOVE_PRODUCTION_FOLDER_BLOCKED = "DRIVE_MOVE_PRODUCTION_FOLDER_BLOCKED"


def _resolve_drive_authorization(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve TEST vs PRODUCTION authorization for a Drive write.

    Authorization model (fail-closed):
    - TEST: ctx["test_go"] non-empty. For isolated testing only.
      Requires ctx["allowed_folders"] (non-empty list). Touched folders
      must all be in allowed_folders AND must not be in
      ctx["production_folders"] (if provided).
    - PRODUCTION: ctx["production_go"] non-empty. For real production.
      Requires ctx["allowed_folders"] (non-empty list) bound to the Human
      authorization scope. A bare non-empty string is NOT sufficient.
    - Exactly one of test_go / production_go must be present.
      Both or neither -> fail-closed.

    Returns {"env": "test"|"production", "go_ref": str,
             "allowed_folders": [str]}.

    Raises ValueError with DRIVE_MOVE_* codes on any violation.
    """
    test_go = ctx.get("test_go")
    production_go = ctx.get("production_go")
    test_go = str(test_go).strip() if test_go else ""
    production_go = str(production_go).strip() if production_go else ""

    if test_go and production_go:
        raise ValueError(
            f"{DRIVE_MOVE_AUTH_AMBIGUOUS}: ctx must not contain both "
            f"'test_go' and 'production_go'. Ambiguous authorization "
            f"rejected.")
    if not test_go and not production_go:
        raise ValueError(
            f"{DRIVE_MOVE_AUTH_REQUIRED}: explicit authorization required "
            f"in ctx['test_go'] (isolated testing) or "
            f"ctx['production_go'] (real production) before any Drive "
            f"write. Plan approval alone is insufficient.")

    allowed = ctx.get("allowed_folders")
    if not isinstance(allowed, (list, tuple)) or not allowed:
        raise ValueError(
            f"{DRIVE_MOVE_ALLOWLIST_REQUIRED}: ctx['allowed_folders'] must "
            f"be a non-empty list of permitted folder IDs. The authorization "
            f"reference alone is not sufficient.")
    allowed_folders = []
    for f in allowed:
        if not isinstance(f, str) or not f.strip():
            raise ValueError(
                f"{DRIVE_MOVE_ALLOWLIST_REQUIRED}: allowed_folders must "
                f"contain only non-empty strings, got {f!r}")
        allowed_folders.append(f.strip())

    env = "test" if test_go else "production"
    return {"env": env, "go_ref": test_go or production_go,
            "allowed_folders": allowed_folders}


def _check_folder_allowlist(move_plan: Dict[str, Any],
                            auth: Dict[str, Any],
                            ctx: Dict[str, Any]) -> None:
    """Verify all folders touched by the move are authorized.

    Touched = source_parent + destination + expected_parent_set.
    All must be in auth["allowed_folders"]. In TEST mode, none may be in
    ctx["production_folders"] (production isolation guard).

    Raises ValueError with DRIVE_MOVE_* codes on violation.
    Fail-closed: any violation -> error, no write.
    """
    touched = {move_plan["source_parent"], move_plan["destination"]}
    touched.update(move_plan.get("expected_parent_set", []))
    allowed = set(auth["allowed_folders"])
    for folder in sorted(touched):
        if folder not in allowed:
            raise ValueError(
                f"{DRIVE_MOVE_FOLDER_NOT_ALLOWED}: folder '{folder}' "
                f"touched by this move is not in allowed_folders "
                f"{sorted(allowed)} (env={auth['env']}). Failing closed.")
    if auth["env"] == "test":
        prod_folders = ctx.get("production_folders") or []
        prod_set = {str(f).strip() for f in prod_folders
                    if isinstance(f, str) and f.strip()}
        for folder in sorted(touched):
            if folder in prod_set:
                raise ValueError(
                    f"{DRIVE_MOVE_PRODUCTION_FOLDER_BLOCKED}: folder "
                    f"'{folder}' is a known production folder; TEST "
                    f"authorization must never touch production. "
                    f"Failing closed.")


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
                            "revoke_index_grant", "drive_move"):
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


def _apply_drive_move(params: Dict[str, Any],
                      ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a validated Drive move plan.

    This is the guarded Drive move operation (F6). It:
    1. Validates the move plan via drive_move_plan.validate_drive_move_plan().
    2. Requires explicit Human Production GO in ctx (fail-closed).
    3. Reads current metadata via the adapter (from ctx).
    4. Verifies source_parent matches actual current parent.
    5. Verifies revision evidence (sha256/revision_id/mime_type) matches.
    6. Executes the move via adapter.reparent().
    7. Reads back and verifies expected_parent_set matches.

    The adapter is provided via ctx["drive_adapter"]. In production, this
    is the real Drive adapter; in tests, a fake.

    Authorization (fail-closed):
    - TEST: ctx["test_go"] (isolated testing) + ctx["allowed_folders"].
    - PRODUCTION: ctx["production_go"] + ctx["allowed_folders"] bound to
      the Human authorization scope. A bare non-empty string is NOT
      sufficient.
    Exactly one of test_go / production_go must be present.
    All folders touched (source_parent, destination, expected_parent_set)
    must be in allowed_folders. In TEST mode, touching a folder listed in
    ctx["production_folders"] is rejected (production isolation guard).

    F-01 fix: Revision evidence (sha256, revision_id, mime_type) from the
    plan is verified against actual current metadata BEFORE the reparent.
    Stale evidence → fail closed, no write.

    Fail-closed: any mismatch → error, no move.
    """
    from .drive_move_plan import validate_drive_move_plan

    # Validate the plan (fail-closed).
    move_plan = validate_drive_move_plan(params.get("move_plan", {}))

    # Authorization: TEST vs PRODUCTION (mutually exclusive, fail-closed).
    # - TEST (ctx["test_go"]): isolated testing only, with allowlist.
    # - PRODUCTION (ctx["production_go"]): real production, with Human-bound
    #   allowlist. A bare non-empty string is NOT sufficient.
    # Plan approval (approval_ref) authorizes the plan; the go reference
    # authorizes the actual Drive write. These are separate.
    auth = _resolve_drive_authorization(ctx)
    _check_folder_allowlist(move_plan, auth, ctx)

    adapter = ctx.get("drive_adapter")
    if adapter is None:
        raise ValueError("DRIVE_ADAPTER_REQUIRED: ctx must provide "
                         "'drive_adapter'")

    file_id = move_plan["file_id"]
    # 1. Read current metadata.
    meta = adapter.meta(file_id)
    current_parents = meta.get("parents", [])

    # F-01 (R3 fix): Verify revision evidence against actual metadata
    # BEFORE write. Strict rule: if the plan specifies an expected value,
    # the actual metadata MUST provide it and it MUST match exactly.
    # Missing/empty/whitespace actual with non-empty expected → FAIL.
    # This prevents bypassing validation via absent adapter fields.
    evidence = move_plan["identity_and_revision_evidence"]
    expected_sha = evidence.get("sha256", "")
    expected_rev = evidence.get("revision_id", "")
    expected_mime = evidence.get("mime_type", "")
    # Use .get() without default to distinguish missing vs empty.
    actual_sha = meta.get("sha256")
    actual_rev = meta.get("revision_id")
    actual_mime = meta.get("mime_type")

    def _require_exact(field_name: str, expected, actual):
        """Strict evidence check: non-empty expected requires exact actual."""
        exp = str(expected).strip() if expected is not None else ""
        if not exp:
            return  # Caller explicitly doesn't care about this field.
        act = str(actual).strip() if actual is not None else ""
        if not act:
            raise ValueError(
                f"DRIVE_MOVE_EVIDENCE_MISMATCH: {field_name} required "
                f"(expected '{exp}') but missing/empty in actual metadata. "
                f"Cannot verify identity; failing closed.")
        if act != exp:
            raise ValueError(
                f"DRIVE_MOVE_EVIDENCE_MISMATCH: {field_name} mismatch. "
                f"Expected '{exp}', got '{act}'.")

    # sha256 is mandatory (validator ensures non-empty) and must match.
    _require_exact("sha256", expected_sha, actual_sha)
    # revision_id and mime_type: if plan specifies, actual must match.
    _require_exact("revision_id", expected_rev, actual_rev)
    _require_exact("mime_type", expected_mime, actual_mime)

    # 2. Verify source_parent matches.
    if move_plan["source_parent"] not in current_parents:
        raise ValueError(
            f"DRIVE_MOVE_PARENT_MISMATCH: expected source_parent "
            f"{move_plan['source_parent']} not in current parents "
            f"{current_parents}")
    # 3. Execute the move (skip if RETAIN_IN_PLACE and already there).
    if move_plan["disposition"] == "RETAIN_IN_PLACE":
        # No move needed; verify already in place.
        result = {"moved": False, "reason": "RETAIN_IN_PLACE"}
    else:
        adapter.reparent(file_id, move_plan["destination"],
                         old_parent=move_plan["source_parent"])
        result = {"moved": True,
                  "auth_env": auth["env"],
                  "go_ref": auth["go_ref"]}
    # 4. Read back and verify.
    new_meta = adapter.meta(file_id)
    new_parents = set(new_meta.get("parents", []))
    expected = set(move_plan["expected_parent_set"])
    if new_parents != expected:
        raise ValueError(
            f"DRIVE_MOVE_READBACK_MISMATCH: expected parents {expected}, "
            f"got {new_parents}")
    result["verified_parents"] = sorted(new_parents)
    return result


_OP_HANDLERS = {
    "rebuild_index": _apply_rebuild_index,
    "purge_tombstones": _apply_purge_tombstones,
    "revoke_index_grant": _apply_revoke_index_grant,
    "drive_move": _apply_drive_move,
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

    # Resilient mode: delegate to _apply_plan_resilient which holds the
    # execution lock for the entire duration (F3-1 fix).
    if resilient:
        return _apply_plan_resilient(plan_path, ledger_path, plan, ctx,
                                     actor, timeout_s)

    # Non-resilient mode: original logic.
    for i, op in enumerate(plan["operations"]):
        op_name = op["op"]
        handler = _OP_HANDLERS.get(op_name)
        if handler is None:
            raise ValueError(f"PLAN_UNKNOWN_OP: {op_name}")
        ts = _utcnow()
        try:
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
    return {"plan_id": plan["plan_id"], "completed": completed,
            "failed": None}


def _apply_plan_resilient(plan_path: Any, ledger_path: Any,
                          plan: Dict[str, Any], ctx: Dict[str, Any],
                          actor: str, timeout_s: float) -> Dict[str, Any]:
    """Resilient apply with execution lock held for entire duration.

    F3-1 fix: The execution lock ensures only one process can execute
    a plan at a time. The WAL lock in wal_begin only protects check+begin;
    without this execution lock, two processes could both pass wal_begin
    and run concurrently.

    F4 fix: Skips operations already marked complete in the WAL (resume).
    """
    from .drive_resilience import (file_lock, run_with_timeout,
                                   wal_begin, wal_check_incomplete,
                                   wal_finish, wal_mark_complete)
    from contextlib import ExitStack

    completed = []

    # Execution lock: held for the ENTIRE apply duration.
    with file_lock(str(ledger_path) + ".exec", timeout_s=10.0):
        # Check for incomplete previous apply from a DIFFERENT plan.
        incomplete = wal_check_incomplete(ledger_path)
        if incomplete and incomplete.get("plan_id") != plan["plan_id"]:
            raise ValueError(f"{WAL_INCOMPLETE}: previous apply of "
                             f"{incomplete.get('plan_id')} did not finish")
        wal_entry = wal_begin(ledger_path, plan["plan_id"],
                              plan["operations"])
        # F4: Get already-completed indices from WAL (resume).
        already_done = set(wal_entry.get("completed", []))

        for i, op in enumerate(plan["operations"]):
            # F4 fix: Skip already-completed ops.
            if i in already_done:
                completed.append({"op": op["op"], "index": i,
                                  "result": {"resumed": True}})
                continue

            op_name = op["op"]
            handler = _OP_HANDLERS.get(op_name)
            if handler is None:
                raise ValueError(f"PLAN_UNKNOWN_OP: {op_name}")
            ts = _utcnow()
            try:
                # Per-op file locks + timeout.
                lock_targets = []
                params = op.get("params", {})
                for key in ("registry", "index"):
                    if params.get(key):
                        lock_targets.append(params[key])
                with ExitStack() as stack:
                    for target in sorted(set(lock_targets)):
                        stack.enter_context(file_lock(target))
                    result = run_with_timeout(
                        lambda p=params: handler(p, ctx), timeout_s)
                wal_mark_complete(ledger_path, i)
                completed.append({"op": op_name, "index": i,
                                  "result": result})
                append_ledger(ledger_path, {
                    "ts": ts, "actor": actor,
                    "action": "drive_plan_op_applied",
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

        # All ops completed. Final persistence BEFORE wal_finish (F2).
        append_ledger(ledger_path, {
            "ts": _utcnow(), "actor": actor, "action": "drive_plan_applied",
            "prev_state": "APPLYING", "new_state": "APPLIED",
            "evidence_ref": str(plan_path),
            "policy_version": "2.0.4-2-slice6",
            "operator": plan["approval"]["approver"],
            "approval_ref": plan["plan_id"], "retry_count": 0,
            "note": f"plan {plan['plan_id'][:12]} fully applied "
                    f"({len(completed)} ops)",
            "plan_id": plan["plan_id"],
        })
        plan["apply_log"] = completed
        save_plan(plan_path, plan)
        # WAL finish LAST, after all persistence is durable.
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
        elif op_name == "drive_move":
            # F-03 fix: Independent verify for drive_move.
            # Re-reads Drive metadata via adapter and verifies the parent
            # set matches expected_parent_set. This catches cases where
            # the file was moved again after apply.
            from .drive_move_plan import validate_drive_move_plan
            try:
                move_plan = validate_drive_move_plan(
                    params.get("move_plan", {}))
            except ValueError as exc:
                errors.append(f"op {entry['index']}: invalid move_plan: "
                              f"{exc}")
                continue
            adapter = (ctx or {}).get("drive_adapter")
            if adapter is None:
                errors.append(f"op {entry['index']}: drive_move verify "
                              f"requires ctx['drive_adapter']")
                continue
            try:
                meta = adapter.meta(move_plan["file_id"])
                current = set(meta.get("parents", []))
                expected = set(move_plan["expected_parent_set"])
                if current != expected:
                    errors.append(
                        f"op {entry['index']}: drive_move verify failed: "
                        f"current parents {sorted(current)} != expected "
                        f"{sorted(expected)}")
            except Exception as exc:
                errors.append(f"op {entry['index']}: drive_move verify "
                              f"error: {exc}")
    return (len(errors) == 0, errors)
