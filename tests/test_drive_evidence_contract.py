"""Drive evidence contract tests (M4 pre-verification, no real OAuth).

Verifies the compatibility between:
- drive_plan._apply_drive_move's evidence contract:
  {"id", "parents", "sha256", "revision_id", "mime_type"}
- real drive.py's raw Drive API v3 fields:
  {"id", "mimeType", "parents", "headRevisionId", "md5Checksum", ...}

Uses MOCKED raw Google Drive API responses (no OAuth, no network).

Critical invariants under test:
1. md5Checksum is NEVER mapped to sha256 (different algorithms).
2. sha256 comes only from actual SHA-256 over downloaded content.
3. Native Google Workspace files -> sha256=None -> fail-closed.
4. Missing headRevisionId/mimeType -> None -> fail-closed when required.
5. reparent delegates to drive.move(file_id, destination, previous).
6. Post-move readback verifies expected_parent_set.
"""
import hashlib
import json

import pytest

from cba_kb.drive_adapter import (
    DEFAULT_EVIDENCE_MAX_BYTES,
    DRIVE_EVIDENCE_TRANSLATE_FAILED,
    DriveEvidenceAdapter,
    is_native_mime,
    translate_drive_evidence,
)


# --------------------------------------------------------------------------
# Mock raw Drive API v3 responses (as drive.py::meta() would return them).
# --------------------------------------------------------------------------

def raw_binary_file(file_id="f1", parent="folderA", content=b"hello drive",
                    revision="rev-100"):
    """Raw API response for a regular binary file."""
    return {
        "id": file_id,
        "name": "report.txt",
        "mimeType": "text/plain",
        "parents": [parent],
        "version": "42",
        "modifiedTime": "2026-10-16T00:00:00.000Z",
        "headRevisionId": revision,
        "md5Checksum": hashlib.md5(content).hexdigest(),
        "size": str(len(content)),
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }


def raw_native_doc(file_id="f2", parent="folderA"):
    """Raw API response for a native Google Doc (no md5Checksum)."""
    return {
        "id": file_id,
        "name": "native doc",
        "mimeType": "application/vnd.google-apps.document",
        "parents": [parent],
        "version": "7",
        "modifiedTime": "2026-10-16T00:00:00.000Z",
        "headRevisionId": "rev-native-7",
        # NOTE: no md5Checksum, no size for native files.
        "webViewLink": f"https://docs.google.com/document/d/{file_id}/edit",
    }


class FakeDrive:
    """Mimics cba_kb.drive.Drive's interface with raw API-shaped data."""

    def __init__(self):
        self.raw = {}        # file_id -> raw API dict
        self.content = {}    # file_id -> bytes (binary files only)
        self.move_calls = []  # records of drive.move invocations

    def meta(self, file_id):
        if file_id not in self.raw:
            raise ValueError(f"File not found: {file_id}")
        return dict(self.raw[file_id])

    def get_bounded(self, file_id, max_bytes):
        raw = self.meta(file_id)
        if is_native_mime(raw.get("mimeType")):
            raise ValueError(
                "Native object requires native adapter; raw download rejected")
        data = self.content.get(file_id, b"")
        if len(data) > max_bytes:
            raise ValueError(f"download exceeded max_bytes={max_bytes}")
        return data

    def move(self, file_id, destination, previous):
        self.move_calls.append(
            {"file_id": file_id, "destination": destination,
             "previous": previous})
        raw = self.meta(file_id)
        parents = list(raw.get("parents", []))
        if previous and previous in parents:
            parents.remove(previous)
        if destination not in parents:
            parents.append(destination)
        raw["parents"] = parents
        self.raw[file_id] = raw
        return dict(raw)


# --------------------------------------------------------------------------
# 1. Pure translation function
# --------------------------------------------------------------------------

def test_translate_maps_raw_fields_to_evidence_contract():
    raw = raw_binary_file()
    ev = translate_drive_evidence(raw, content_sha256="abc123")
    assert ev["id"] == "f1"
    assert ev["parents"] == ["folderA"]
    assert ev["mime_type"] == "text/plain"          # mimeType -> mime_type
    assert ev["revision_id"] == "rev-100"           # headRevisionId -> revision_id
    assert ev["sha256"] == "abc123"


def test_translate_never_maps_md5_to_sha256():
    """md5Checksum must NEVER appear in the sha256 field."""
    raw = raw_binary_file()
    md5 = raw["md5Checksum"]
    assert md5  # sanity: fixture has an md5
    ev = translate_drive_evidence(raw, content_sha256=None)
    assert ev["sha256"] is None
    assert ev["sha256"] != md5
    # Even when sha256 is provided, it must not equal the md5.
    real_sha = hashlib.sha256(b"hello drive").hexdigest()
    assert real_sha != md5  # sanity: they really differ
    ev2 = translate_drive_evidence(raw, content_sha256=real_sha)
    assert ev2["sha256"] == real_sha
    assert ev2["sha256"] != md5


def test_translate_rejects_non_dict():
    with pytest.raises(ValueError) as e:
        translate_drive_evidence("not-a-dict")
    assert DRIVE_EVIDENCE_TRANSLATE_FAILED in str(e.value)


def test_translate_missing_optional_fields_yield_none():
    ev = translate_drive_evidence({"id": "x"})
    assert ev["parents"] == []
    assert ev["mime_type"] is None
    assert ev["revision_id"] is None
    assert ev["sha256"] is None


# --------------------------------------------------------------------------
# 2. DriveEvidenceAdapter: factory + metadata translation
# --------------------------------------------------------------------------

def test_adapter_factory_instantiation():
    drive = FakeDrive()
    adapter = DriveEvidenceAdapter(drive)
    assert adapter.drive is drive
    assert adapter.max_evidence_bytes == DEFAULT_EVIDENCE_MAX_BYTES
    assert adapter.reparent_calls == 0


def test_adapter_meta_binary_file_computes_real_sha256():
    drive = FakeDrive()
    content = b"hello drive"
    drive.raw["f1"] = raw_binary_file(content=content)
    drive.content["f1"] = content
    adapter = DriveEvidenceAdapter(drive)

    ev = adapter.meta("f1")
    expected_sha = hashlib.sha256(content).hexdigest()
    assert ev["sha256"] == expected_sha
    # Proves MD5 was not disguised as SHA-256:
    assert ev["sha256"] != drive.raw["f1"]["md5Checksum"]
    assert ev["revision_id"] == "rev-100"
    assert ev["mime_type"] == "text/plain"
    assert ev["parents"] == ["folderA"]


def test_adapter_meta_native_doc_sha256_unavailable_fail_closed():
    """Native Google Doc: no md5, download rejected -> sha256=None."""
    drive = FakeDrive()
    drive.raw["f2"] = raw_native_doc()
    adapter = DriveEvidenceAdapter(drive)

    ev = adapter.meta("f2")
    assert ev["sha256"] is None  # fail-closed downstream
    assert ev["revision_id"] == "rev-native-7"  # headRevisionId still mapped
    assert ev["mime_type"] == "application/vnd.google-apps.document"


def test_adapter_meta_missing_head_revision_id():
    drive = FakeDrive()
    raw = raw_binary_file()
    del raw["headRevisionId"]
    drive.raw["f1"] = raw
    drive.content["f1"] = b"hello drive"
    adapter = DriveEvidenceAdapter(drive)

    ev = adapter.meta("f1")
    assert ev["revision_id"] is None  # downstream fails closed if required
    assert ev["sha256"] is not None   # sha256 still established


def test_adapter_meta_oversize_file_sha256_unavailable():
    drive = FakeDrive()
    big = b"x" * 100
    drive.raw["f1"] = raw_binary_file(content=big)
    drive.content["f1"] = big
    adapter = DriveEvidenceAdapter(drive, max_evidence_bytes=10)

    ev = adapter.meta("f1")
    assert ev["sha256"] is None  # bounded download aborted -> fail-closed


# --------------------------------------------------------------------------
# 3. Full guarded-apply chain through the evidence adapter
# --------------------------------------------------------------------------

def _make_plan_env(tmp_path, adapter, file_id, expected_sha, expected_rev,
                   expected_mime, disposition="ARCHIVE",
                   destination="folderB"):
    """Build approved plan + ctx wired to the evidence adapter."""
    from cba_kb import drive_plan as dp
    from cba_kb.drive_move_plan import create_drive_move_plan

    move_plan = create_drive_move_plan(
        file_id=file_id, source_id="s1", doc_id="d1",
        source_parent="folderA", destination=destination,
        expected_parent_set=[destination], disposition=disposition,
        approval_ref="human-approval-1",
        sha256=expected_sha, revision_id=expected_rev,
        mime_type=expected_mime)
    plan = dp.create_plan(
        [{"op": "drive_move", "params": {"move_plan": move_plan}}],
        creator="test")
    plan_path = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.jsonl"
    dp.save_plan(plan_path, plan)
    dp.approve_plan(plan_path, "human", ledger_path)
    ctx = {"drive_adapter": adapter, "production_go": "GO-2026-10-16-test",
         "allowed_folders": ["folderA", "folderB"]}
    return plan_path, ledger_path, ctx


def test_full_chain_binary_file_success_and_readback(tmp_path):
    """Happy path: evidence matches -> 1 reparent -> readback verified."""
    from cba_kb import drive_plan as dp

    content = b"hello drive"
    drive = FakeDrive()
    drive.raw["f1"] = raw_binary_file(content=content)
    drive.content["f1"] = content
    adapter = DriveEvidenceAdapter(drive)

    sha = hashlib.sha256(content).hexdigest()
    plan_path, ledger_path, ctx = _make_plan_env(
        tmp_path, adapter, "f1", sha, "rev-100", "text/plain")

    out = dp.apply_plan(plan_path, ledger_path, ctx)
    assert out["failed"] is None
    assert adapter.reparent_calls == 1
    # Real reparent interface: drive.move called with correct args.
    assert drive.move_calls == [
        {"file_id": "f1", "destination": "folderB", "previous": "folderA"}]
    # Post-op readback: parents now exactly {folderB}.
    assert drive.raw["f1"]["parents"] == ["folderB"]


def test_full_chain_sha256_mismatch_rejected_no_reparent(tmp_path):
    from cba_kb import drive_plan as dp

    content = b"hello drive"
    drive = FakeDrive()
    drive.raw["f1"] = raw_binary_file(content=content)
    drive.content["f1"] = content
    adapter = DriveEvidenceAdapter(drive)

    plan_path, ledger_path, ctx = _make_plan_env(
        tmp_path, adapter, "f1", "0" * 64, "rev-100", "text/plain")
    out = dp.apply_plan(plan_path, ledger_path, ctx)
    assert out["failed"] is not None
    assert "DRIVE_MOVE_EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0
    assert drive.move_calls == []
    assert drive.raw["f1"]["parents"] == ["folderA"]  # untouched


def test_full_chain_native_doc_rejected_no_reparent(tmp_path):
    """Native doc: sha256 cannot be established -> fail-closed, 0 reparent."""
    from cba_kb import drive_plan as dp

    drive = FakeDrive()
    drive.raw["f2"] = raw_native_doc()
    adapter = DriveEvidenceAdapter(drive)

    plan_path, ledger_path, ctx = _make_plan_env(
        tmp_path, adapter, "f2", "a" * 64, "rev-native-7",
        "application/vnd.google-apps.document")
    out = dp.apply_plan(plan_path, ledger_path, ctx)
    assert out["failed"] is not None
    assert "DRIVE_MOVE_EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0
    assert drive.move_calls == []


def test_full_chain_missing_revision_id_rejected(tmp_path):
    """Raw API without headRevisionId -> revision_id=None -> reject."""
    from cba_kb import drive_plan as dp

    content = b"hello drive"
    drive = FakeDrive()
    raw = raw_binary_file(content=content)
    del raw["headRevisionId"]
    drive.raw["f1"] = raw
    drive.content["f1"] = content
    adapter = DriveEvidenceAdapter(drive)

    plan_path, ledger_path, ctx = _make_plan_env(
        tmp_path, adapter, "f1", hashlib.sha256(content).hexdigest(),
        "rev-100", "text/plain")
    out = dp.apply_plan(plan_path, ledger_path, ctx)
    assert out["failed"] is not None
    assert "DRIVE_MOVE_EVIDENCE_MISMATCH" in out["failed"]["error"]
    assert adapter.reparent_calls == 0


def test_full_chain_auth_still_required(tmp_path):
    """Evidence adapter does not bypass the authorization gate."""
    from cba_kb import drive_plan as dp

    content = b"hello drive"
    drive = FakeDrive()
    drive.raw["f1"] = raw_binary_file(content=content)
    drive.content["f1"] = content
    adapter = DriveEvidenceAdapter(drive)

    sha = hashlib.sha256(content).hexdigest()
    plan_path, ledger_path, ctx = _make_plan_env(
        tmp_path, adapter, "f1", sha, "rev-100", "text/plain")
    del ctx["production_go"]  # no GO
    out = dp.apply_plan(plan_path, ledger_path, ctx)
    assert out["failed"] is not None
    assert "DRIVE_MOVE_AUTH_REQUIRED" in out["failed"]["error"]
    assert adapter.reparent_calls == 0
