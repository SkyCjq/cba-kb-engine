"""TEST vs PRODUCTION authorization model tests (M4 isolation).

Verifies the fail-closed authorization model for drive_move:
- TEST (test_go) vs PRODUCTION (production_go) are mutually exclusive.
- allowed_folders is REQUIRED in both modes; a bare go string is NOT enough.
- All touched folders must be in allowed_folders.
- TEST mode must never touch ctx["production_folders"].
- Zero-write negative cases: no auth, ambiguous auth, missing allowlist,
  folder not allowed, production folder in TEST mode, expired revision,
  missing hash.

All tests assert reparent_calls == 0 on failure (zero writes).
"""
import hashlib

import pytest

from cba_kb import drive_plan as dp
from cba_kb.drive_move_plan import create_drive_move_plan


class FakeDriveAdapter:
    """Minimal fake for authorization tests."""

    def __init__(self):
        self.files = {}
        self.reparent_calls = 0

    def meta(self, file_id):
        if file_id not in self.files:
            raise ValueError(f"File not found: {file_id}")
        f = self.files[file_id]
        return {"id": file_id, "parents": f["parents"],
                "sha256": f.get("sha256"),
                "revision_id": f.get("revision_id"),
                "mime_type": f.get("mime_type")}

    def reparent(self, file_id, new_parent, old_parent=None):
        self.reparent_calls += 1
        f = self.files[file_id]
        if old_parent and old_parent in f["parents"]:
            f["parents"].remove(old_parent)
        if new_parent not in f["parents"]:
            f["parents"].append(new_parent)
        return {"id": file_id}


def _setup(tmp_path, revision="r1", actual_revision=None):
    """Create approved plan + adapter; return (plan_path, ledger, adapter).

    revision: expected revision in the plan.
    actual_revision: revision in adapter metadata (defaults to revision).
    """
    actual_revision = revision if actual_revision is None else actual_revision
    adapter = FakeDriveAdapter()
    adapter.files["f1"] = {"parents": ["folderA"],
                           "sha256": "s" * 64,
                           "revision_id": actual_revision,
                           "mime_type": "text/plain"}
    move_plan = create_drive_move_plan(
        file_id="f1", source_id="s1", doc_id="d1",
        source_parent="folderA", destination="folderB",
        expected_parent_set=["folderB"], disposition="ARCHIVE",
        approval_ref="human-001", sha256="s" * 64,
        revision_id=revision, mime_type="text/plain")
    plan = dp.create_plan([{"op": "drive_move",
                            "params": {"move_plan": move_plan}}])
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "boss", ledger_path)
    return plan_path, ledger_path, adapter


def _assert_zero_write(out, adapter):
    assert out["failed"] is not None
    assert adapter.reparent_calls == 0
    assert adapter.files["f1"]["parents"] == ["folderA"]


# --- No auth / ambiguous auth -------------------------------------------

def test_no_auth_rejected_zero_write(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path, {"drive_adapter": adapter})
    assert "DRIVE_MOVE_AUTH_REQUIRED" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_both_go_refs_rejected_as_ambiguous(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "production_go": "PROD-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert "DRIVE_MOVE_AUTH_AMBIGUOUS" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


# --- Allowlist required ---------------------------------------------------

def test_production_go_without_allowlist_rejected(tmp_path):
    """A bare production_go string is NOT sufficient (must bind scope)."""
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "human-prod-go-001"})
    assert "DRIVE_MOVE_ALLOWLIST_REQUIRED" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_test_go_without_allowlist_rejected(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001"})
    assert "DRIVE_MOVE_ALLOWLIST_REQUIRED" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_empty_allowlist_rejected(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": []})
    assert "DRIVE_MOVE_ALLOWLIST_REQUIRED" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


# --- Folder scoping -------------------------------------------------------

def test_destination_not_in_allowlist_rejected(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         # folderB (destination) missing from allowlist
                         "allowed_folders": ["folderA"]})
    assert "DRIVE_MOVE_FOLDER_NOT_ALLOWED" in out["failed"]["error"]
    assert "folderB" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_test_mode_touching_production_folder_blocked(tmp_path):
    """TEST authorization must never touch known production folders."""
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": ["folderA", "folderB"],
                         "production_folders": ["folderB"]})
    assert "DRIVE_MOVE_PRODUCTION_FOLDER_BLOCKED" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


# --- Happy paths ----------------------------------------------------------

def test_test_go_with_allowlist_succeeds(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is None
    assert adapter.reparent_calls == 1
    assert adapter.files["f1"]["parents"] == ["folderB"]
    result = out["completed"][0]["result"]
    assert result["auth_env"] == "test"
    assert result["go_ref"] == "TEST-001"


def test_production_go_with_allowlist_succeeds(tmp_path):
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "production_go": "PROD-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is None
    assert adapter.reparent_calls == 1
    result = out["completed"][0]["result"]
    assert result["auth_env"] == "production"


# --- Evidence negatives still zero-write under TEST auth -------------------

def test_expired_revision_rejected_zero_write(tmp_path):
    """Plan expects r1 but actual is r2 (stale evidence) -> reject."""
    plan_path, ledger_path, adapter = _setup(tmp_path, revision="r1",
                                             actual_revision="r2")
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert "DRIVE_MOVE_EVIDENCE_MISMATCH" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_missing_hash_rejected_zero_write(tmp_path):
    """Actual metadata without sha256 -> reject (fail-closed)."""
    plan_path, ledger_path, adapter = _setup(tmp_path)
    del adapter.files["f1"]["sha256"]
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert "DRIVE_MOVE_EVIDENCE_MISMATCH" in out["failed"]["error"]
    _assert_zero_write(out, adapter)


def test_readback_failure_detected(tmp_path):
    """Tampered parents after apply -> readback via meta() detects mismatch."""
    plan_path, ledger_path, adapter = _setup(tmp_path)
    out = dp.apply_plan(plan_path, ledger_path,
                        {"drive_adapter": adapter,
                         "test_go": "TEST-001",
                         "allowed_folders": ["folderA", "folderB"]})
    assert out["failed"] is None  # apply succeeded
    # Attacker/tamper: move file elsewhere after apply.
    adapter.files["f1"]["parents"] = ["folderX"]
    # Independent readback (as m4_drive_e2e.py does via drive.meta).
    final = adapter.meta("f1")
    assert set(final.get("parents", [])) != {"folderB"}
    # The script's readback check would fail here (exit 2).


def test_cleanup_failure_tracked_not_silent(tmp_path):
    """Script cleanup logic: failed deletes -> residuals, exit 3.

    Simulates the m4_drive_e2e.py finally-block contract: every
    created ID that fails deletion must be recorded in
    residual_file_ids and must NOT report full PASS.
    """
    created_ids = ["file1", "file2", "file3"]
    residuals = []
    # Simulate: file2 deletion fails.
    def fake_delete(fid):
        if fid == "file2":
            raise RuntimeError("API error")
    for fid in reversed(created_ids):
        try:
            fake_delete(fid)
        except Exception:  # noqa: BLE001
            residuals.append(fid)
    assert residuals == ["file2"]
    # Contract: non-empty residuals -> exit 3, not 0.
    exit_code = 3 if residuals else 0
    assert exit_code == 3
