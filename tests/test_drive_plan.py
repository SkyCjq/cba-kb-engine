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


def test_verify_partial_log_fails(env):
    """Q2-R2 note: a partial apply_log must not verify."""
    plan = dp.create_plan([
        {"op": "rebuild_index",
         "params": {"registry": str(env["registry"]),
                    "index": str(env["index"])}},
        {"op": "rebuild_index",
         "params": {"registry": str(env["registry"]),
                    "index": str(env["index"])}},
    ])
    dp.save_plan(env["plan"], plan)
    dp.approve_plan(env["plan"], "boss", env["ledger"])
    # Simulate a partial apply: only first op logged.
    data = json.loads(env["plan"].read_text(encoding="utf-8"))
    data["apply_log"] = [{"op": "rebuild_index", "index": 0, "result": {}}]
    env["plan"].write_text(json.dumps(data), encoding="utf-8")
    ok, errors = dp.verify_plan(env["plan"])
    assert not ok
    assert any("PLAN_INCOMPLETE" in e for e in errors)


# ---------------- Drive move integration (F6)

class FakeDriveAdapter:
    """Fake Drive adapter for testing (no real API calls)."""
    def __init__(self):
        self.files = {}  # file_id -> {"parents": [...], "name": str, ...}
        self.reparent_calls = 0  # Count actual reparent invocations.

    def meta(self, file_id):
        if file_id not in self.files:
            raise ValueError(f"File not found: {file_id}")
        f = self.files[file_id]
        # Return all fields; missing keys → None (not "") to test strictness.
        return {"id": file_id, "parents": f["parents"],
                "sha256": f.get("sha256"),
                "revision_id": f.get("revision_id"),
                "mime_type": f.get("mime_type")}

    def reparent(self, file_id, new_parent, old_parent=None):
        self.reparent_calls += 1
        if file_id not in self.files:
            raise ValueError(f"File not found: {file_id}")
        parents = self.files[file_id]["parents"]
        if old_parent and old_parent in parents:
            parents.remove(old_parent)
        if new_parent not in parents:
            parents.append(new_parent)
        return {"id": file_id, "parents": parents}


def test_drive_move_success(tmp_path):
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["folderA"], "name": "doc",
                           "sha256": "x" * 60, "revision_id": "r1",
                           "mime_type": "text/plain"}

    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256="x" * 60,
        revision_id="r1",
        mime_type="text/plain",
    )
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "human-prod-go-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is None
    assert adapter.files["f1"]["parents"] == ["folderB"]


def test_drive_move_parent_mismatch_rejected(tmp_path):
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["folderX"], "name": "doc",  # Wrong!
                           "sha256": "x" * 60}

    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",  # Expects A
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256="x" * 60,
    )
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "human-prod-go-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is not None
    assert "PARENT_MISMATCH" in out["failed"]["error"]
    # File was NOT moved.
    assert adapter.files["f1"]["parents"] == ["folderX"]


def test_drive_move_no_production_go_rejected(tmp_path):
    """F-01: Without production_go, drive_move fails closed (no write)."""
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["folderA"], "sha256": "x" * 60}

    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256="x" * 60,
    )
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    # No production_go in ctx!
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter})
    assert out["failed"] is not None
    assert "DRIVE_MOVE_AUTH_REQUIRED" in out["failed"]["error"]
    # File NOT moved.
    assert adapter.files["f1"]["parents"] == ["folderA"]


def test_drive_move_evidence_mismatch_rejected(tmp_path):
    """F-01: Stale sha256 fails closed (no write)."""
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    # Actual file has different sha256!
    adapter.files["f1"] = {"parents": ["folderA"], "sha256": "y" * 60}

    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256="x" * 60,  # Stale!
    )
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "go-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.files["f1"]["parents"] == ["folderA"]


def test_drive_move_destination_expected_mismatch_rejected():
    """F-02: Validator rejects destination not in expected_parent_set."""
    from cba_kb.drive_move_plan import create_drive_move_plan, DriveMovePlanError
    import pytest
    with pytest.raises(DriveMovePlanError,
                       match="PLAN_ARCHIVE_DESTINATION_MISMATCH"):
        create_drive_move_plan(
            file_id="f1", source_id="s1", doc_id="d1",
            source_parent="A", destination="B",
            expected_parent_set=["C"],  # Q2's counterexample!
            disposition="ARCHIVE",
            approval_ref="human-001",
            sha256="x" * 60,
        )


def test_drive_move_verify_catches_moved_away(tmp_path):
    """F-03: verify_plan detects file moved after apply."""
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["folderA"], "sha256": "x" * 60}

    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256="x" * 60,
    )
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "go-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is None

    # Verify passes right after apply.
    ok, errors = dp.verify_plan(plan_path, {"drive_adapter": adapter})
    assert ok, f"Should verify: {errors}"

    # Now someone moves the file elsewhere (simulating drift).
    adapter.files["f1"]["parents"] = ["folderZ"]

    # Verify must FAIL.
    ok, errors = dp.verify_plan(plan_path, {"drive_adapter": adapter})
    assert not ok
    assert any("drive_move verify failed" in e for e in errors)


# ============ F-01 R3: Strict evidence validation matrix ============

def _f01_setup(tmp_path, file_meta, plan_evidence):
    """Full call chain: create→save→approve→apply. Returns (out, adapter)."""
    from cba_kb.drive_move_plan import create_drive_move_plan
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["A"], **file_meta}
    mp = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="A", destination="B",
        expected_parent_set=["B"],
        disposition="ARCHIVE",
        approval_ref="human-001",
        sha256=plan_evidence.get("sha256", "x" * 60),
        revision_id=plan_evidence.get("revision_id", ""),
        mime_type=plan_evidence.get("mime_type", ""),
    )
    # Override evidence directly for None cases (create_ doesn't allow None).
    if "sha256" in plan_evidence or "revision_id" in plan_evidence or "mime_type" in plan_evidence:
        mp["identity_and_revision_evidence"].update(plan_evidence)
        # Re-validate after manual update.
        from cba_kb.drive_move_plan import validate_drive_move_plan
        # Note: validator requires sha256 non-empty; None will fail here.
        # For testing handler strictness, we bypass validator for None cases
        # by catching and re-raising as handler test.
    plan = dp.create_plan([{"op": "drive_move", "params": {"move_plan": mp}}])
    pp, lp = tmp_path / "p.json", tmp_path / "l.jsonl"
    dp.save_plan(pp, plan)
    dp.approve_plan(pp, "boss", lp)
    out = dp.apply_plan(pp, lp, {"drive_adapter": adapter,
                                 "production_go": "synth-go-001",
                         "allowed_folders": ["A", "B"]})
    return out, adapter


def test_f01_revision_id_missing_in_actual(tmp_path):
    """revision_id expected but missing in actual → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60},  # No revision_id!
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0
    assert adapter.files["f1"]["parents"] == ["A"]


def test_f01_revision_id_none_in_actual(tmp_path):
    """revision_id=None in actual → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": None},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_revision_id_mismatch(tmp_path):
    """revision_id mismatch → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": "r999"},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_mime_type_missing_in_actual(tmp_path):
    """mime_type expected but missing in actual → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": "r123"},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123",
                       "mime_type": "text/plain"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_mime_type_empty_in_actual(tmp_path):
    """mime_type='' in actual → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": "r123", "mime_type": ""},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123",
                       "mime_type": "text/plain"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_mime_type_mismatch(tmp_path):
    """mime_type mismatch → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": "r123",
                   "mime_type": "application/pdf"},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123",
                       "mime_type": "text/plain"},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_sha256_mismatch(tmp_path):
    """sha256 mismatch → FAIL, 0 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "y" * 60},
        plan_evidence={"sha256": "x" * 60},
    )
    assert out["failed"] is not None
    assert "EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_f01_all_evidence_match(tmp_path):
    """All three match exactly → PASS, exactly 1 reparent."""
    out, adapter = _f01_setup(
        tmp_path,
        file_meta={"sha256": "x" * 60, "revision_id": "r123",
                   "mime_type": "text/plain"},
        plan_evidence={"sha256": "x" * 60, "revision_id": "r123",
                       "mime_type": "text/plain"},
    )
    assert out["failed"] is None, f"Unexpected failure: {out['failed']}"
    assert adapter.reparent_calls == 1
    assert adapter.files["f1"]["parents"] == ["B"]
