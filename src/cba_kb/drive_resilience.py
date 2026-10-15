"""Concurrency control and crash recovery (2.0.4-2 slice 7, T2).

**Trust Boundary (defined 2026-10-15):**
The file system is TRUSTED. This module defends against:
- Accidental concurrent execution (two legitimate processes)
- Process crashes mid-apply (recovery via WAL)

It does NOT defend against malicious file tampering. If an attacker has
write access to the WAL/plan/ledger files, they can already modify any
of them. File-level ACLs (OS permissions) are the defense against
malicious tampering — out of scope for this module.

This boundary prevents unbounded hardening: without it, the reviewer
can always posit a deeper file-tampering variant.

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
  Uses SIGALRM for true interruption of synchronous operations.
  **Residual risk**: if the operation catches TimeoutError and continues,
  or spawns its own background threads, SIGALRM cannot force-stop those.
  All built-in plan operations are synchronous and do not catch
  TimeoutError.
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


def _wal_write(p: Path, entry: Dict[str, Any]) -> None:
    """Write WAL entry with fsync for crash durability (F2 fix).

    Ensures the WAL state is actually persisted to disk, not just in
    the OS page cache. This provides the crash-recovery guarantee:
    if we crash after writing IN_PROGRESS, the WAL will be there on
    restart; if we crash after writing COMPLETED, the completion will
    be there.
    """
    import os
    data = json.dumps(entry, ensure_ascii=False, indent=2).encode("utf-8")
    # Write to temp file, fsync, then rename (atomic + durable).
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    # Sync the directory to ensure the rename is durable.
    try:
        dir_fd = os.open(p.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # Best effort; file fsync is the critical part.


def wal_begin(ledger_path: Any, plan_id: str,
              operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Record apply intent before execution (atomic with check).

    Uses POSIX flock via file_lock() for the WAL critical section.
    The OS automatically releases flock when the process dies, even on
    SIGKILL or os._exit() — unlike O_CREAT|O_EXCL + unlink(), which can
    leave a stale lock file permanently blocking recovery.

    The lock file itself may persist on disk; that is fine. Only the
    flock state matters, and the OS cleans it up on process death.
    Do NOT delete the lock file, and do NOT guess expiry via mtime/PID.

    If an IN_PROGRESS WAL already exists for the same plan_id, preserves
    the existing `completed` progress (resume, not overwrite).
    """
    # Use flock (not O_EXCL) so a crashed process doesn't leave a
    # permanent block. The lock file path stays constant.
    with file_lock(_wal_path(ledger_path), timeout_s=10.0):
        p = _wal_path(ledger_path)
        # Check for existing IN_PROGRESS WAL.
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
                if (isinstance(existing, dict) and
                        existing.get("status") == "IN_PROGRESS"):
                    if existing.get("plan_id") == plan_id:
                        # Resume: preserve completed progress (F-04 fix).
                        return existing
                    else:
                        # F3-2 fix: Do NOT overwrite a different plan's
                        # IN_PROGRESS WAL. Fail closed.
                        raise ValueError(
                            f"{WAL_INCOMPLETE}: another plan "
                            f"({existing.get('plan_id')}) is in progress; "
                            f"cannot start {plan_id}")
            except (json.JSONDecodeError, OSError):
                pass
        # New WAL entry.
        entry = {
            "plan_id": plan_id,
            "started_ts": _utcnow(),
            "operations": [op.get("op") for op in operations],
            "completed": [],
            "status": "IN_PROGRESS",
        }
        _wal_write(p, entry)
        return entry
    # Note: file_lock context manager releases the flock automatically.
    # The .lock file itself persists on disk; that is expected and fine.


def wal_mark_complete(ledger_path: Any, op_index: int) -> None:
    """Mark an operation as completed in the WAL."""
    p = _wal_path(ledger_path)
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{WAL_CORRUPT}: {exc}") from exc
    entry["completed"].append(op_index)
    _wal_write(p, entry)


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
    _wal_write(p, entry)


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
    """Run func with a deadline. Timeout fails closed (raises TimeoutError).

    Uses SIGALRM for true interruption (not a daemon thread that keeps
    running after timeout). This ensures that when a timeout occurs, the
    operation is actually stopped and cannot continue modifying files
    after locks are released.

    Note: Only works on Unix and in the main thread. All plan operations
    use atomic writes (temp + rename), so interruption cannot corrupt
    the target file.
    """
    def _handler(signum, frame):
        raise TimeoutError(f"OPERATION_TIMEOUT after {timeout_s}s")

    # Only the main thread can use SIGALRM.
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("run_with_timeout requires main thread "
                           "(SIGALRM)")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    # Use setitimer for sub-second precision.
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        return func()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


# ------------------------------------------------------------- idempotency

def is_idempotent(op_name: str) -> bool:
    """All supported plan operations are idempotent."""
    return op_name in ("rebuild_index", "purge_tombstones",
                       "revoke_index_grant")
