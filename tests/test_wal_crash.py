"""Real subprocess crash injection tests for WAL recovery (slice 7).

These tests use actual OS subprocesses (not mocks) to verify that:
- A crashed process releases the flock automatically (OS guarantee).
- WAL state survives crashes and can be recovered.
- No stale lock files block recovery.

Each test spawns a real Python subprocess that acquires the WAL lock
then dies via os._exit() (simulating SIGKILL — no finally/unlink runs).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cba_kb import drive_resilience as dr


@pytest.fixture()
def tmp_ledger(tmp_path):
    return tmp_path / "ledger.jsonl"


def _run_crash_child(ledger_path: str, script: str, timeout: int = 10):
    """Run a Python snippet in a subprocess that will crash.

    Returns (returncode, stdout, stderr).
    """
    # Write the script to a temp file to avoid shell quoting issues.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                     delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
            # Pass ledger path via env to avoid quoting issues.
            env={**os.environ, "TEST_LEDGER": ledger_path},
        )
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        os.unlink(script_path)


def test_A_crash_releases_wal_lock(tmp_ledger):
    """Test A: Crashed process releases WAL lock automatically.

    1. Child acquires WAL lock via wal_begin().
    2. Child calls os._exit() while holding the lock (no cleanup).
    3. Parent verifies child is dead.
    4. Parent calls wal_begin() — must succeed (no LOCK_TIMEOUT).
    """
    ledger_str = str(tmp_ledger)
    child_script = """
import os, sys
sys.path.insert(0, "src")
# Also try installed path
try:
    from cba_kb import drive_resilience as dr
except ImportError:
    sys.path.insert(0, "/home/hatch/workspace/cba-kb-engine-2042/src")
    from cba_kb import drive_resilience as dr
ledger = os.environ["TEST_LEDGER"]
# Acquire the WAL lock and hold it.
from cba_kb.drive_resilience import file_lock, _wal_path
with file_lock(_wal_path(ledger), timeout_s=5.0):
    # Signal that we hold the lock, then die without cleanup.
    # os._exit() does NOT run finally blocks or atexit handlers,
    # simulating SIGKILL.
    os._exit(42)
"""
    # The child needs the src path. Use cwd.
    import subprocess as sp
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                     delete=False, dir="/tmp") as f:
        f.write(child_script)
        script_path = f.name
    try:
        proc = sp.Popen(
            [sys.executable, script_path],
            env={**os.environ, "TEST_LEDGER": ledger_str,
                 "PYTHONPATH": "/home/hatch/workspace/cba-kb-engine-2042/src"},
        )
        proc.wait(timeout=10)
        assert proc.returncode == 42, f"Child didn't crash as expected: {proc.returncode}"
        # Child is dead. The flock should be released by the OS.
        # Now try to acquire the WAL lock — must succeed.
        # Use a short timeout to fail fast if the bug exists.
        entry = dr.wal_begin(ledger_str, "recovery-test", [{"op": "x"}])
        assert entry["plan_id"] == "recovery-test"
        # If we get here, the lock was released. PASS.
    finally:
        os.unlink(script_path)


def test_B_crash_recovers_wal_progress(tmp_ledger):
    """Test B: Crashed process's WAL progress is recoverable.

    1. Child creates IN_PROGRESS WAL with completed=[0].
    2. Child dies via os._exit() while holding the lock.
    3. Parent re-runs wal_begin with same plan_id.
    4. Must get back completed=[0] (not empty, not lost).
    """
    ledger_str = str(tmp_ledger)
    child_script = """
import os, sys
sys.path.insert(0, "/home/hatch/workspace/cba-kb-engine-2042/src")
from cba_kb import drive_resilience as dr
ledger = os.environ["TEST_LEDGER"]
# Create WAL and mark op 0 complete, then crash.
entry = dr.wal_begin(ledger, "plan-ABC", [{"op": "a"}, {"op": "b"}])
dr.wal_mark_complete(ledger, 0)
# Verify it's persisted.
import json
from pathlib import Path
wal_p = Path(ledger + ".wal")
data = json.loads(wal_p.read_text())
assert data["completed"] == [0], f"Not persisted: {data}"
# Crash without cleanup.
os._exit(42)
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                     delete=False, dir="/tmp") as f:
        f.write(child_script)
        script_path = f.name
    try:
        proc = subprocess.Popen(
            [sys.executable, script_path],
            env={**os.environ, "TEST_LEDGER": ledger_str},
        )
        proc.wait(timeout=10)
        assert proc.returncode == 42
        # Parent: re-acquire and verify progress is preserved.
        entry = dr.wal_begin(ledger_str, "plan-ABC",
                             [{"op": "a"}, {"op": "b"}])
        assert entry["completed"] == [0], \
            f"Progress lost! Got: {entry['completed']}"
        assert entry["status"] == "IN_PROGRESS"
    finally:
        os.unlink(script_path)


def test_C_different_plan_rejected(tmp_ledger):
    """Test C: Different plan_id cannot overwrite IN_PROGRESS WAL."""
    dr.wal_begin(str(tmp_ledger), "plan-A", [{"op": "x"}])
    wal_path = Path(str(tmp_ledger) + ".wal")
    before = wal_path.read_bytes()
    with pytest.raises(ValueError, match="WAL_INCOMPLETE"):
        dr.wal_begin(str(tmp_ledger), "plan-B", [{"op": "y"}])
    after = wal_path.read_bytes()
    assert before == after, "WAL was modified despite rejection!"


def test_D_sequential_execution_ok(tmp_ledger):
    """Test D: Two sequential executors work; second can proceed after first.

    (True concurrent overlap is prevented by the exec lock; sequential
    is allowed and safe due to idempotency.)
    """
    # First completes.
    e1 = dr.wal_begin(str(tmp_ledger), "plan-1", [{"op": "x"}])
    dr.wal_mark_complete(str(tmp_ledger), 0)
    dr.wal_finish(str(tmp_ledger))
    # Second can start (WAL is COMPLETED, not IN_PROGRESS).
    e2 = dr.wal_begin(str(tmp_ledger), "plan-2", [{"op": "y"}])
    assert e2["plan_id"] == "plan-2"
    assert e2["status"] == "IN_PROGRESS"


def test_E_stale_lock_file_does_not_block(tmp_ledger):
    """Test E: A pre-existing .lock file without a live flock does not block.

    The lock file may persist on disk; only the flock state matters.
    """
    ledger_str = str(tmp_ledger)
    lock_path = Path(ledger_str + ".wal.lock")
    # Create a stale lock file (no process holds it).
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("stale")
    # wal_begin must succeed — the file existing doesn't mean locked.
    entry = dr.wal_begin(ledger_str, "test-stale", [{"op": "x"}])
    assert entry["plan_id"] == "test-stale"
    # The lock file still exists (we don't delete it).
    assert lock_path.exists()
