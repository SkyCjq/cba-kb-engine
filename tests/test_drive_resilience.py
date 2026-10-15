"""Tests for concurrency control and crash recovery (slice 7)."""
from __future__ import annotations

import json
import threading
import time

import pytest

from cba_kb import drive_plan as dp
from cba_kb import drive_resilience as dr


def _grant(fid):
    return {"approved": True, "approver": "boss", "file_id": fid,
            "scope": "drive:read", "granted_at": "2026-10-15T00:00:00Z",
            "note": ""}


@pytest.fixture()
def env(tmp_path):
    reg = {
        "f1": {"name": "doc one", "rights_status": "approved",
               "rights_grant": _grant("f1")},
    }
    reg_p = tmp_path / "reg.json"
    reg_p.write_text(json.dumps(reg), encoding="utf-8")
    return {
        "registry": reg_p,
        "index": tmp_path / "idx.json",
        "ledger": tmp_path / "ledger.jsonl",
        "plan": tmp_path / "plan.json",
    }


def _ctx(env):
    from cba_kb.drive_search import load_registry
    return {"registry": load_registry(env["registry"])}


def test_file_lock_exclusive(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("x")
    acquired = []

    def _holder():
        with dr.file_lock(target, timeout_s=5.0):
            acquired.append("holder")
            time.sleep(0.3)

    def _waiter():
        time.sleep(0.1)
        with dr.file_lock(target, timeout_s=5.0):
            acquired.append("waiter")

    t1 = threading.Thread(target=_holder)
    t2 = threading.Thread(target=_waiter)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert acquired == ["holder", "waiter"]


def test_file_lock_timeout(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("x")
    with dr.file_lock(target, timeout_s=5.0):
        with pytest.raises(TimeoutError, match="LOCK_TIMEOUT"):
            with dr.file_lock(target, timeout_s=0.2):
                pass


def test_wal_detects_incomplete(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    # Simulate a crash: WAL begun but not finished.
    dr.wal_begin(env["ledger"], plan["plan_id"], plan["operations"])
    incomplete = dr.wal_check_incomplete(env["ledger"])
    assert incomplete is not None
    assert incomplete["plan_id"] == plan["plan_id"]
    assert incomplete["status"] == "IN_PROGRESS"


def test_wal_finish_clears(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dr.wal_begin(env["ledger"], plan["plan_id"], plan["operations"])
    dr.wal_finish(env["ledger"])
    assert dr.wal_check_incomplete(env["ledger"]) is None


def test_resilient_apply(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    out = dp.apply_plan(env["plan"], env["ledger"], _ctx(env),
                        resilient=True, timeout_s=10.0)
    assert out["failed"] is None
    assert len(out["completed"]) == 1
    # WAL is finished.
    assert dr.wal_check_incomplete(env["ledger"]) is None


def test_timeout_fails_closed():
    def _slow():
        time.sleep(2.0)
        return "done"
    with pytest.raises(TimeoutError, match="OPERATION_TIMEOUT"):
        dr.run_with_timeout(_slow, timeout_s=0.3)


def test_idempotent_ops():
    assert dr.is_idempotent("rebuild_index")
    assert dr.is_idempotent("purge_tombstones")
    assert dr.is_idempotent("revoke_index_grant")
    assert not dr.is_idempotent("unknown_op")
