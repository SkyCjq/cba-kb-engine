"""Tests for the guarded plan-apply workflow (slice 6)."""
from __future__ import annotations

import json

import pytest

from cba_kb import drive_plan as dp


def _grant(fid):
    return {"approved": True, "approver": "boss", "file_id": fid,
            "scope": "drive:read", "granted_at": "2026-10-15T00:00:00Z",
            "note": ""}


@pytest.fixture()
def env(tmp_path):
    reg = {
        "f1": {"name": "doc one", "rights_status": "approved",
               "rights_grant": _grant("f1")},
        "f2": {"name": "doc two", "rights_status": "review"},
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


def test_unapproved_plan_refused(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    with pytest.raises(ValueError, match="PLAN_NOT_APPROVED"):
        dp.apply_plan(env["plan"], env["ledger"], _ctx(env))


def test_full_workflow_rebuild_index(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    ok, errors = dp.prepare_plan(plan, _ctx(env))
    assert ok, errors
    dp.approve_plan(env["plan"], "human-boss", env["ledger"])
    out = dp.apply_plan(env["plan"], env["ledger"], _ctx(env))
    assert out["failed"] is None
    assert len(out["completed"]) == 1
    # Independent readback.
    ok, errors = dp.verify_plan(env["plan"])
    assert ok, errors
    # Index exists and has the approved doc.
    from cba_kb.drive_search_index import load_index
    idx = load_index(env["index"])
    assert "f1" in idx["docs"]


def test_double_approval_refused(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    with pytest.raises(ValueError, match="PLAN_ALREADY_APPROVED"):
        dp.approve_plan(env["plan"], "boss2", env["ledger"])


def test_plan_tamper_detected(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    # Tamper with the operations.
    data = json.loads(env["plan"].read_text(encoding="utf-8"))
    data["operations"].append({"op": "purge_tombstones", "params": {}})
    env["plan"].write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="PLAN_CORRUPT"):
        dp.load_plan(env["plan"])


def test_prepare_catches_bad_params(env):
    plan = dp.create_plan(
        [{"op": "revoke_index_grant",
          "params": {"file_id": "nonexistent", "index": str(env["index"])}}])
    ok, errors = dp.prepare_plan(plan, _ctx(env))
    assert not ok
    assert errors


def test_revoke_index_grant_workflow(env):
    # Build index first.
    plan1 = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan1)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    dp.apply_plan(env["plan"], env["ledger"], _ctx(env))
    # Now revoke via a second plan.
    plan2 = dp.create_plan(
        [{"op": "revoke_index_grant",
          "params": {"file_id": "f1", "index": str(env["index"]),
                     "principal": "alice"}}])
    p2 = env["plan"].parent / "plan2.json"
    dp.save_plan(p2, plan2)
    dp.approve_plan(p2, "boss", env["ledger"])
    out = dp.apply_plan(p2, env["ledger"], _ctx(env))
    assert out["failed"] is None
    ok, errors = dp.verify_plan(p2)
    assert ok, errors


def test_ledger_records_approval_and_apply(env):
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    dp.apply_plan(env["plan"], env["ledger"], _ctx(env))
    lines = env["ledger"].read_text(encoding="utf-8").strip().splitlines()
    actions = [json.loads(ln)["action"] for ln in lines]
    assert "drive_plan_approved" in actions
    assert "drive_plan_op_applied" in actions
    assert "drive_plan_applied" in actions


# ---------------- Q2-R1 FAIL regressions (F1, F2, F7)

def test_forged_approval_rejected(env):
    """F1: hand-written approval in plan JSON (no ledger event) is rejected."""
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    # Forge: write approval directly, bypassing approve_plan (no ledger).
    plan["approval"] = {"approver": "attacker",
                        "approved_ts": "2026-10-15T00:00:00Z",
                        "plan_id": plan["plan_id"]}
    dp.save_plan(env["plan"], plan)
    with pytest.raises(ValueError, match="PLAN_NOT_APPROVED"):
        dp.apply_plan(env["plan"], env["ledger"], _ctx(env))


def test_tamper_recompute_with_stale_approval_rejected(env):
    """F2: modified ops + recomputed plan_id + stale approval rejected."""
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    # Tamper: change op, recompute plan_id, keep old approval.
    data = json.loads(env["plan"].read_text(encoding="utf-8"))
    data["operations"] = [{"op": "purge_tombstones",
                           "params": {"registry": str(env["registry"]),
                                      "older_than_days": 0}}]
    import hashlib
    canon = json.dumps(data["operations"], sort_keys=True,
                       separators=(",", ":")).encode()
    data["plan_id"] = hashlib.sha256(canon).hexdigest()
    # approval.plan_id still has the OLD plan_id.
    env["plan"].write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="PLAN_CORRUPT"):
        dp.load_plan(env["plan"])


def test_verify_never_applied_fails(env):
    """F7: verify on a never-applied plan returns False (not vacuous True)."""
    plan = dp.create_plan(
        [{"op": "rebuild_index",
          "params": {"registry": str(env["registry"]),
                     "index": str(env["index"])}}])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    ok, errors = dp.verify_plan(env["plan"])
    assert not ok
    assert any("PLAN_NOT_APPLIED" in e for e in errors)
