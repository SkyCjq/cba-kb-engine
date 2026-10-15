"""Concurrency control and crash recovery (2.0.4-2 slice 7, T2).

- **FileLock**: POSIX advisory exclusive locks for registry/index/ledger.
  Prevents concurrent modification corruption.
- **Idempotency**: all plan operations are safe to retry.
  - rebuild_index: full rebuild from registry (idempotent by construction).
  - revoke_index_grant: revoking an already-revoked doc is a no-op.
  - purge_tombstones: deleting already-purged entries is a no-op.
- **WAL**: write-ahead log for plan applies. Before apply, record intent;
  after each op, mark complete. On startup, incomplete WAL entries are
  detected and reported (resume or manual intervention).
- **Timeout**: operations run with a deadline; timeout fails closed.
- **Partial success**: apply tracks exactly which ops completed.

All LOCAL. No Drive writes.
"""
from __future__ import annotations

import fcntl
import json
import signal
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

WAL_CORRUPT = "WAL_CORRUPT"
WAL_INCOMPLETE = "WAL_INCOMPLETE"
LOCK_TIMEOUT = "LOCK_TIMEOUT"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- locking

@contextmanager
def file_lock(path: Any, timeout_s: float = 10.0) -> Iterator[None]:
    """Exclusive advisory lock on a file (POSIX flock).

    Creates the lock file if missing. Fails closed on timeout.
    """
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        # Non-blocking attempt with timeout.
        import time
        deadline = time.time() + timeout_s
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, IOError):
                if time.time() >= deadline:
                    raise TimeoutError(f"{LOCK_TIMEOUT}: {lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# ---------------------------------------------------------------------- WAL

def _wal_path(ledger_path: Any) -> Path:
    return Path(str(ledger_path) + ".wal")


def wal_begin(ledger_path: Any, plan_id: str,
              operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Record apply intent before execution."""
    entry = {
        "plan_id": plan_id,
        "started_ts": _utcnow(),
        "operations": [op.get("op") for op in operations],
        "completed": [],
        "status": "IN_PROGRESS",
    }
    p = _wal_path(ledger_path)
    p.write_text(json.dumps(entry, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return entry


def wal_mark_complete(ledger_path: Any, op_index: int) -> None:
    """Mark an operation as completed in the WAL."""
    p = _wal_path(ledger_path)
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{WAL_CORRUPT}: {exc}") from exc
    entry["completed"].append(op_index)
    p.write_text(json.dumps(entry, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def wal_finish(ledger_path: Any) -> None:
    """Mark the WAL entry as complete and remove it."""
    p = _wal_path(ledger_path)
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    entry["status"] = "COMPLETED"
    entry["finished_ts"] = _utcnow()
    # Keep the completed entry for audit, but mark it done.
    p.write_text(json.dumps(entry, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def wal_check_incomplete(ledger_path: Any) -> Optional[Dict[str, Any]]:
    """Detect an incomplete (crashed) apply. Returns the WAL entry or None."""
    p = _wal_path(ledger_path)
    if not p.exists():
        return None
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{WAL_CORRUPT}: {exc}") from exc
    if entry.get("status") == "IN_PROGRESS":
        return entry
    return None


# ------------------------------------------------------------------ timeout

def run_with_timeout(func: Callable[[], Any],
                     timeout_s: float = 30.0) -> Any:
    """Run func with a deadline. Timeout fails closed (raises TimeoutError)."""
    result: List[Any] = []
    error: List[BaseException] = []

    def _target():
        try:
            result.append(func())
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError(f"OPERATION_TIMEOUT after {timeout_s}s")
    if error:
        raise error[0]
    return result[0] if result else None


# ------------------------------------------------------------- idempotency

def is_idempotent(op_name: str) -> bool:
    """All supported plan operations are idempotent."""
    return op_name in ("rebuild_index", "purge_tombstones",
                       "revoke_index_grant")
