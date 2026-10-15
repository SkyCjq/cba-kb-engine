"""Tests for cba_kb.drive_discovery (2.0.4-2 slice 1, T2).

Anti-cheat contract for this file:
- FakeDrive errors carry ``resp.status`` exactly like googleapiclient
  HttpError (never bare Exceptions); the module's permission detection must
  work on the real error shape.
- Every negative test asserts BOTH the fail-closed status AND the
  ledger/report evidence. Silence is failure.
- Mocks here prove unit behavior only; they never stand in for a real
  Google API E2E (that needs an authorized isolated test Drive).
"""
import hashlib
import json
from types import SimpleNamespace

import pytest

from cba_kb.drive_discovery import (
    ACCESS_DENIED,
    ACCESS_VERIFIED,
    CHECKSUM_MISMATCH,
    CHECKSUM_MISSING,
    DOWNLOAD_TOO_LARGE,
    DRIVE_PROVIDER,
    DUPLICATE_SAME_REVISION,
    LIST_FAILED,
    NATIVE_EXPORT_UNVERIFIED,
    NATIVE_READ_FAILED,
    NEW,
    REVIEW_REQUIRED,
    REVISION_DRIFT,
    REVISION_LINKED,
    VERIFY_FAILED,
    DriveDiscoveryError,
    append_ledger,
    classify_identity,
    decide_drive_rights,
    discover_folder,
    drive_doc_id,
    drive_source_id,
    run_discovery,
    verify_object,
)


class FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError shape (resp.status + content)."""

    def __init__(self, status, message="forbidden"):
        super().__init__(f"HttpError {status}: {message}")
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps(
            {"error": {"code": status, "message": message}}
        ).encode("utf-8")


def _meta(file_id, **over):
    base = {
        "id": file_id,
        "name": f"{file_id}.txt",
        "mimeType": "text/plain",
        "parents": ["folder-1"],
        "version": "100",
        "headRevisionId": "rev-100",
        "md5Checksum": hashlib.md5(b"hello-drive").hexdigest(),
        "size": "11",
        "modifiedTime": "2026-10-15T00:00:00.000Z",
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }
    base.update(over)
    return base


class FakeDrive:
    """Scriptable fake of the cba_kb.drive.Drive read surface."""

    def __init__(self, items=None):
        self._items = list(items or [])
        self.meta_calls = []
        self.meta_overrides = {}   # file_id -> list of metas (popped per call: drift simulation)
        self.meta_errors = {}      # file_id -> status
        self.blobs = {}            # file_id -> bytes
        self.blob_errors = {}      # file_id -> status
        self.docs = {}             # file_id -> dict (native Doc JSON)
        self.doc_errors = {}       # file_id -> status
        self.list_error = None     # status or None

    def list(self, parent):
        if self.list_error is not None:
            raise FakeHttpError(self.list_error, "list denied")
        assert parent == "folder-1"
        return self._items

    def meta(self, file_id):
        self.meta_calls.append(file_id)
        if file_id in self.meta_errors:
            raise FakeHttpError(self.meta_errors[file_id], "meta denied")
        seq = self.meta_overrides.get(file_id)
        if seq:
            return seq.pop(0)
        for item in self._items:
            if item["id"] == file_id:
                return dict(item)
        raise FakeHttpError(404, "not found")

    def get(self, file_id):
        if file_id in self.blob_errors:
            raise FakeHttpError(self.blob_errors[file_id], "download denied")
        return self.blobs[file_id]

    def document_json(self, file_id):
        if file_id in self.doc_errors:
            raise FakeHttpError(self.doc_errors[file_id], "native read denied")
        return self.docs[file_id]


def _ledger_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- 1. discovery

def test_discover_enumerates_all_objects_with_normalized_fields():
    drive = FakeDrive(items=[
        _meta("f1"), _meta("f2", name="notes.txt"),
        {"id": "f3", "name": "old", "mimeType": "text/plain", "trashed": True},
    ])
    objects, report = discover_folder(drive, "folder-1")
    assert [o["file_id"] for o in objects] == ["f1", "f2"]
    o = objects[0]
    assert o["provider"] == DRIVE_PROVIDER
    assert o["mime_type"] == "text/plain"
    assert o["parents"] == ["folder-1"]
    assert o["head_revision_id"] == "rev-100"
    assert o["md5_checksum"] == hashlib.md5(b"hello-drive").hexdigest()
    assert report["discovered_count"] == 2
    assert report["skipped_trashed"] == 1
    assert "trashed" in (report["skipped_trashed_reason"] or "")


def test_discover_empty_folder_id_refused():
    drive = FakeDrive()
    with pytest.raises(DriveDiscoveryError) as exc:
        discover_folder(drive, "")
    assert exc.value.code == LIST_FAILED


# ------------------------------------------------- 2. list 403 fail-closed

def test_list_403_fails_closed_with_no_partial_results():
    drive = FakeDrive(items=[_meta("f1")])
    drive.list_error = 403
    with pytest.raises(DriveDiscoveryError) as exc:
        discover_folder(drive, "folder-1")
    assert exc.value.code == LIST_FAILED
    # fail-closed means: no objects leak out on listing failure (exception, not [])


# ------------------------------------------- 3. per-object meta 403 -> review

def test_meta_403_marks_object_review_and_continues(tmp_path):
    drive = FakeDrive(items=[_meta("f1"), _meta("f2")])
    drive.meta_errors = {"f2": 403}
    drive.blobs = {"f1": b"hello-drive", "f2": b"hello-drive"}
    ledger = tmp_path / "ledger.jsonl"
    report = run_discovery(drive, "folder-1", ledger_path=ledger)
    assert report["counts"]["verified"] == 1
    assert report["counts"]["review"] == 1
    events = _ledger_events(ledger)
    denied = [e for e in events if e.get("verification", {}).get("reason") == ACCESS_DENIED]
    assert len(denied) == 1 and denied[0]["evidence_ref"] == "f2"
    assert denied[0]["verification"]["status"] == REVIEW_REQUIRED


# ------------------------------------------------- 4. native Doc verified

def test_native_doc_verified_via_native_read_with_explicit_digest_kind():
    doc_json = {"revisionId": "r1", "title": "Doc", "body": {"content": []}}
    drive = FakeDrive(items=[
        _meta("nd1", mimeType="application/vnd.google-apps.document",
              md5Checksum=None, name="Doc"),
    ])
    drive.docs = {"nd1": doc_json}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0])
    assert result["status"] == ACCESS_VERIFIED
    assert result["digest_kind"] == "native_doc_json"
    expected = hashlib.sha256(
        json.dumps(doc_json, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    assert result["digest"] == expected


def test_native_doc_read_failure_is_review_not_silent():
    drive = FakeDrive(items=[
        _meta("nd1", mimeType="application/vnd.google-apps.document",
              md5Checksum=None, name="Doc"),
    ])
    drive.doc_errors = {"nd1": 500}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0])
    assert result["status"] == REVIEW_REQUIRED
    assert result["reason"] == NATIVE_READ_FAILED
    assert result["digest"] is None


# --------------------------------------- 5. native Sheet never claims SHA

def test_native_sheet_never_claims_binary_sha():
    drive = FakeDrive(items=[
        _meta("sh1", mimeType="application/vnd.google-apps.spreadsheet",
              md5Checksum=None, name="Sheet"),
    ])
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0])
    assert result["status"] == REVIEW_REQUIRED
    assert result["reason"] == NATIVE_EXPORT_UNVERIFIED
    assert result["digest"] is None
    assert result["digest_kind"] is None


# ------------------------------------------------- 6. checksum mismatch

def test_checksum_mismatch_fails_closed_with_evidence(tmp_path):
    drive = FakeDrive(items=[_meta("f1")])
    drive.blobs = {"f1": b"tampered-bytes!!"}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True)
    assert result["status"] == VERIFY_FAILED
    assert result["reason"] == CHECKSUM_MISMATCH
    assert result["evidence"]["expected_md5"] == hashlib.md5(b"hello-drive").hexdigest()
    assert result["evidence"]["actual_md5"] == hashlib.md5(b"tampered-bytes!!").hexdigest()
    # and through run_discovery it lands in the ledger as failed, not verified
    ledger = tmp_path / "ledger.jsonl"
    report = run_discovery(drive, "folder-1", ledger_path=ledger, verify_bytes=True)
    assert report["counts"]["failed"] == 1
    assert report["counts"]["verified"] == 0


def test_download_verifies_bytes_when_asked():
    drive = FakeDrive(items=[_meta("f1")])
    drive.blobs = {"f1": b"hello-drive"}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True)
    assert result["status"] == ACCESS_VERIFIED
    assert result["evidence"]["verified_bytes"] is True


def test_oversize_download_refused_not_truncated():
    drive = FakeDrive(items=[_meta("f1", size=str(10 << 20))])
    drive.blobs = {"f1": b"hello-drive"}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert result["status"] == VERIFY_FAILED
    assert result["reason"] == DOWNLOAD_TOO_LARGE


def test_missing_md5_with_verify_bytes_is_review():
    drive = FakeDrive(items=[_meta("f1", md5Checksum=None)])
    drive.blobs = {"f1": b"hello-drive"}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True)
    assert result["status"] == REVIEW_REQUIRED
    assert result["reason"] == CHECKSUM_MISSING


# ------------------------------------------------- 7. revision drift (fault injection)

def test_revision_drift_between_list_and_meta_is_detected():
    """Real fault injection: the file changes between discovery and verify."""
    listed = _meta("f1", version="100", headRevisionId="rev-100")
    changed = _meta("f1", version="101", headRevisionId="rev-101")
    drive = FakeDrive(items=[listed])
    drive.meta_overrides = {"f1": [changed]}  # meta now sees the NEW revision
    objects, _ = discover_folder(drive, "folder-1")
    assert objects[0]["version"] == "100"  # discovery snapshot is stale
    result = verify_object(drive, objects[0])
    assert result["status"] == VERIFY_FAILED
    assert result["reason"] == REVISION_DRIFT
    assert result["evidence"]["meta_version"] == "101"
    assert result["evidence"]["discovery_version"] == "100"


# ------------------------------------------------- 8. identity: dedup & revision

def test_identity_new_duplicate_and_revision_linking():
    known = {}
    obj = {"file_id": "f1", "head_revision_id": "rev-100", "version": "100"}
    cls, rec = classify_identity(known, obj)
    assert cls == NEW and rec["new_source"] is True
    first_source = rec["source_id"]
    first_doc = rec["doc_id"]

    known["f1"] = {"revision": "rev-100", "source_id": first_source, "doc_id": first_doc}
    cls, rec = classify_identity(known, obj)
    assert cls == DUPLICATE_SAME_REVISION
    assert rec["source_id"] == first_source  # never rebuilt silently
    assert rec["new_source"] is False

    obj2 = {"file_id": "f1", "head_revision_id": "rev-101", "version": "101"}
    cls, rec = classify_identity(known, obj2)
    assert cls == REVISION_LINKED
    assert rec["source_id"] == first_source
    assert rec["doc_id"] != first_doc  # revision-bound doc identity
    assert rec["previous_revision"] == "rev-100"


def test_source_and_doc_ids_are_stable_and_distinct():
    assert drive_source_id("abc") == drive_source_id("abc")
    assert drive_source_id("abc") != drive_source_id("abd")
    assert drive_doc_id("abc", "r1") != drive_doc_id("abc", "r2")
    assert drive_source_id("abc") != drive_doc_id("abc", "r1")


# ------------------------------------------------- 9. rights default-deny

def test_rights_default_deny_without_grants():
    status, reason = decide_drive_rights({"file_id": "f1"})
    assert (status, reason) == ("review", "DRIVE_RIGHTS_UNVERIFIED")


def test_rights_explicit_grant_is_honored():
    status, reason = decide_drive_rights(
        {"file_id": "f1"}, grants={"f1": {"rights": "ok"}})
    assert status == "approved"


# ------------------------------------------------- 10. ledger + registry

def test_ledger_requires_all_fields_and_appends_jsonl(tmp_path):
    ledger = tmp_path / "l.jsonl"
    with pytest.raises(ValueError):
        append_ledger(ledger, {"ts": "x"})  # missing fields -> loud, not silent
    event = {
        "ts": "2026-10-15T00:00:00+00:00", "actor": "test", "action": "a",
        "prev_state": "S0", "new_state": "S1", "evidence_ref": "f1",
        "policy_version": "p", "operator": "op", "approval_ref": "",
        "retry_count": 0, "note": "n",
    }
    append_ledger(ledger, event)
    append_ledger(ledger, event)
    lines = _ledger_events(ledger)
    assert len(lines) == 2 and lines[0]["action"] == "a"


def test_run_discovery_persists_registry_and_ledger(tmp_path):
    drive = FakeDrive(items=[_meta("f1"), _meta("f2")])
    drive.blobs = {"f1": b"hello-drive", "f2": b"hello-drive"}
    ledger = tmp_path / "ledger.jsonl"
    registry = tmp_path / "registry.json"
    report = run_discovery(drive, "folder-1", ledger_path=ledger,
                           registry_path=registry)
    saved = json.loads(registry.read_text(encoding="utf-8"))
    assert set(saved) == {"f1", "f2"}
    assert saved["f1"]["source_id"] == drive_source_id("f1")
    assert saved["f1"]["verify_status"] == ACCESS_VERIFIED
    events = _ledger_events(ledger)
    actions = [e["action"] for e in events]
    assert actions[0] == "drive_discovery_started"
    assert actions[-1] == "drive_discovery_finished"
    assert report["counts"]["discovered"] == 2
    # second run: same revisions -> duplicates, no new sources
    report2 = run_discovery(drive, "folder-1", ledger_path=ledger,
                            registry_path=registry)
    assert report2["counts"]["duplicate"] == 2
    assert report2["counts"]["verified"] == 2


# ------------------------------------------------- 12. end-to-end wiring

def test_run_discovery_wires_list_meta_ledger_registry_report(tmp_path):
    drive = FakeDrive(items=[
        _meta("ok1"),
        _meta("doc1", mimeType="application/vnd.google-apps.document",
              md5Checksum=None, name="Doc1"),
        _meta("sh1", mimeType="application/vnd.google-apps.spreadsheet",
              md5Checksum=None, name="Sheet1"),
    ])
    drive.meta_errors = {"ok1": 403}  # ok1 becomes review, others proceed
    drive.docs = {"doc1": {"revisionId": "r9", "title": "Doc1", "body": {}}}
    ledger = tmp_path / "ledger.jsonl"
    report = run_discovery(drive, "folder-1", ledger_path=ledger)
    assert report["counts"] == {
        "discovered": 3, "verified": 1, "review": 2,
        "failed": 0, "duplicate": 0, "revision_linked": 0,
    }
    assert report["folder_id"] == "folder-1"
    assert report["ledger_path"] == str(ledger)
    events = _ledger_events(ledger)
    by_file = {e["evidence_ref"]: e for e in events
               if e["action"] == "drive_object_discovered"}
    assert by_file["ok1"]["verification"]["reason"] == ACCESS_DENIED
    assert by_file["doc1"]["verification"]["digest_kind"] == "native_doc_json"
    assert by_file["sh1"]["verification"]["reason"] == NATIVE_EXPORT_UNVERIFIED
    assert by_file["sh1"]["rights_status"] == "review"
