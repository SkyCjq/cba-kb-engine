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
    LEDGER_WRITE_FAILED,
    LIST_FAILED,
    NATIVE_EXPORT_UNVERIFIED,
    NATIVE_READ_FAILED,
    NEW,
    REVIEW_REQUIRED,
    REVISION_DRIFT,
    REVISION_LINKED,
    TRANSPORT_UNBOUNDED,
    VERIFY_FAILED,
    DriveDiscoveryError,
    append_ledger,
    classify_identity,
    decide_drive_rights,
    discover_folder,
    download_bounded,
    drive_doc_id,
    drive_source_id,
    read_ledger_events,
    run_discovery,
    verify_object,
)
from cba_kb.transport import DownloadTooLargeError


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
        self.bounded_blobs = {}    # file_id -> bytes (served chunked)
        self.bounded_errors = {}   # file_id -> exception (mid-download fault)
        self.bounded_lie = None    # when set, get_bounded returns this (buggy transport)
        self.bounded_bytes_read = 0
        self.bounded_aborted_at = None

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

    # --- bounded download surface (mirrors Drive.get_bounded) ---
    # bounded_blobs: file_id -> bytes served in chunks of bounded_chunk_size.
    # Raises DownloadTooLargeError as soon as the bound would be exceeded,
    # without ever fully buffering an over-limit blob.
    bounded_chunk_size = 512

    def get_bounded(self, file_id, max_bytes):
        if file_id in self.bounded_errors:
            raise self.bounded_errors[file_id]
        if self.bounded_lie is not None:
            # Simulate a buggy transport returning more than asked.
            return self.bounded_lie
        data = self.bounded_blobs[file_id]
        buf = bytearray()
        for offset in range(0, len(data), self.bounded_chunk_size):
            chunk = data[offset:offset + self.bounded_chunk_size]
            if len(buf) + len(chunk) > max_bytes:
                self.bounded_aborted_at = len(buf)
                raise DownloadTooLargeError(
                    f"fake bound exceeded: {len(buf) + len(chunk)} > {max_bytes}")
            buf += chunk
        self.bounded_bytes_read = len(buf)
        return bytes(buf)

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
    drive.bounded_blobs = {"f1": b"tampered-bytes!!"}
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
    drive.bounded_blobs = {"f1": b"hello-drive"}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True)
    assert result["status"] == ACCESS_VERIFIED
    assert result["evidence"]["verified_bytes"] is True


def test_oversize_download_refused_not_truncated():
    drive = FakeDrive(items=[_meta("f1", size=str(10 << 20))])
    drive.bounded_blobs = {"f1": b"hello-drive"}
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

def _grant(**over):
    base = {"approved": True, "approver": "human-op", "file_id": "f1",
            "scope": "drive:read"}
    base.update(over)
    return base


def test_rights_default_deny_without_grants():
    status, reason = decide_drive_rights({"file_id": "f1"})
    assert (status, reason) == ("review", "DRIVE_RIGHTS_UNVERIFIED")


def test_rights_invalid_grant_values_are_never_approved():
    # Finding 2: False/None/{}/malformed must not become "approved".
    bad = [
        False, None, {}, {"rights": "ok"}, {"approved": "yes"},
        {"approved": 1}, _grant(approved=False), _grant(approver="  "),
        _grant(approver=None), _grant(file_id="f2"), _grant(revoked=True),
        _grant(expires_at="2020-01-01T00:00:00+00:00"),
        _grant(expires_at="not-a-date"),
        _grant(scope="drive:metadata"), _grant(scope=""),
        "approved", ["approved"],
    ]
    for grant in bad:
        status, reason = decide_drive_rights(
            {"file_id": "f1"}, grants={"f1": grant})
        assert (status, reason) == ("review", "DRIVE_RIGHTS_UNVERIFIED"), grant


def test_rights_valid_grant_is_honored():
    status, reason = decide_drive_rights(
        {"file_id": "f1"}, grants={"f1": _grant()})
    assert (status, reason) == ("approved", "DRIVE_RIGHTS_GRANTED")
    # no expiry / wildcard scope are also precise grants
    for grant in (_grant(expires_at=None), _grant(scope="*"),
                  _grant(expires_at="2999-01-01T00:00:00+00:00")):
        status, _ = decide_drive_rights({"file_id": "f1"}, grants={"f1": grant})
        assert status == "approved", grant


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


# ================================================== Finding 1: hard byte bound

def _lying_meta(file_id, declared_size, blob):
    return _meta(file_id, size=str(declared_size),
                 md5Checksum=hashlib.md5(blob).hexdigest())


def test_bounded_download_aborts_on_lying_metadata_without_full_buffer():
    """Q2 Finding 1 repro: meta says 1 byte, actual is 4096, limit 1024.

    Old HEAD returned ACCESS_VERIFIED/OK (RED). New HEAD must refuse with
    DOWNLOAD_TOO_LARGE and must NOT have fully buffered the 4096 bytes.
    """
    blob = b"x" * 4096
    drive = FakeDrive(items=[_lying_meta("f1", 1, blob)])
    drive.bounded_blobs = {"f1": blob}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert result["status"] == VERIFY_FAILED
    assert result["reason"] == DOWNLOAD_TOO_LARGE
    assert result["evidence"].get("verified_bytes") is not True
    assert result.get("digest") is None
    # The transport aborted mid-transfer: never fully buffered.
    assert drive.bounded_aborted_at == 1024
    assert drive.bounded_bytes_read == 0


def test_bounded_download_exactly_at_limit_succeeds():
    blob = b"y" * 1024
    drive = FakeDrive(items=[_lying_meta("f1", 1024, blob)])
    drive.bounded_blobs = {"f1": blob}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert result["status"] == ACCESS_VERIFIED
    assert result["evidence"]["verified_bytes"] is True
    assert drive.bounded_bytes_read == 1024


def test_bounded_download_one_byte_over_fails():
    blob = b"z" * 1025
    drive = FakeDrive(items=[_lying_meta("f1", 1025, blob)])
    drive.bounded_blobs = {"f1": blob}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert (result["status"], result["reason"]) == (VERIFY_FAILED, DOWNLOAD_TOO_LARGE)


def test_bounded_download_with_missing_size_still_bounded():
    blob = b"w" * 4096
    drive = FakeDrive(items=[_lying_meta("f1", 0, blob)])
    # wipe the size claim entirely: bound must not depend on metadata
    drive._items[0].pop("size")
    drive.bounded_blobs = {"f1": blob}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert (result["status"], result["reason"]) == (VERIFY_FAILED, DOWNLOAD_TOO_LARGE)
    assert drive.bounded_aborted_at == 1024


def test_bounded_download_mid_transfer_error_is_download_failed():
    blob = b"v" * 512
    drive = FakeDrive(items=[_lying_meta("f1", 512, blob)])
    drive.bounded_errors = {"f1": FakeHttpError(500, "chunk failed")}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert (result["status"], result["reason"]) == (VERIFY_FAILED, "DOWNLOAD_FAILED")


def test_buggy_transport_returning_oversize_is_rejected():
    """Defense in depth: even if get_bounded() misbehaves and returns more
    than max_bytes, the caller re-checks and fails closed."""
    blob = b"u" * 512
    drive = FakeDrive(items=[_lying_meta("f1", 512, blob)])
    drive.bounded_blobs = {"f1": blob}
    drive.bounded_lie = b"u" * 2048  # transport lies about the bound
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert (result["status"], result["reason"]) == (VERIFY_FAILED, DOWNLOAD_TOO_LARGE)


def test_drive_without_bounded_capability_fails_closed():
    """A transport that cannot enforce the hard bound must never have its
    bytes claimed as bounded-verified."""

    class LegacyDrive(FakeDrive):
        get_bounded = None  # no bounded capability

    blob = b"hello-drive"
    drive = LegacyDrive(items=[_meta("f1")])
    drive.blobs = {"f1": blob}
    objects, _ = discover_folder(drive, "folder-1")
    result = verify_object(drive, objects[0], verify_bytes=True, max_bytes=1024)
    assert result["status"] == VERIFY_FAILED
    assert result["reason"] == TRANSPORT_UNBOUNDED
    assert result["evidence"].get("verified_bytes") is not True


def test_download_bounded_helper_enforces_postcheck():
    drive = FakeDrive(items=[_meta("f1")])
    drive.bounded_lie = b"q" * 100
    with pytest.raises(DownloadTooLargeError):
        download_bounded(drive, "f1", 10)


# ================================================== Finding 3: ledger integrity

def _event(action="a"):
    return {
        "ts": "2026-10-15T00:00:00+00:00", "actor": "test", "action": action,
        "prev_state": "S0", "new_state": "S1", "evidence_ref": "f1",
        "policy_version": "p", "operator": "op", "approval_ref": "",
        "retry_count": 0, "note": "n",
    }


def test_ledger_short_write_raises_and_tail_is_detected(tmp_path, monkeypatch):
    """Real fault injection through the os.write path: the first write for
    our ledger fd is truncated to 10 bytes."""
    import os as _os
    real_open, real_write = _os.open, _os.write
    target_fds = set()
    armed = {"once": True}

    def fake_open(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        if _os.path.basename(_os.fspath(path)) == "short.jsonl":
            target_fds.add(fd)
        return fd

    def fake_write(fd, data):
        if armed["once"] and fd in target_fds:
            armed["once"] = False
            target_fds.discard(fd)
            n = min(10, len(data))
            return real_write(fd, data[:n])  # genuine short write, once
        return real_write(fd, data)

    monkeypatch.setattr(_os, "open", fake_open)
    monkeypatch.setattr(_os, "write", fake_write)
    ledger = tmp_path / "short.jsonl"
    with pytest.raises(IOError, match="LEDGER_SHORT_WRITE"):
        append_ledger(ledger, _event())
    # The partial tail is detected on read, fail-closed.
    with pytest.raises(ValueError, match="LEDGER_TAIL_INCOMPLETE"):
        read_ledger_events(ledger, strict=True)
    # A good event after the corrupt tail does not silently heal it.
    append_ledger(ledger, _event("b"))
    with pytest.raises(ValueError, match="LEDGER_CORRUPT|LEDGER_TAIL_INCOMPLETE"):
        read_ledger_events(ledger, strict=True)


def test_read_ledger_events_round_trip_and_strict_tail(tmp_path):
    ledger = tmp_path / "l.jsonl"
    append_ledger(ledger, _event("a"))
    append_ledger(ledger, _event("b"))
    events = read_ledger_events(ledger)
    assert [e["action"] for e in events] == ["a", "b"]
    # raw truncation (crash mid-append) is caught too
    raw = ledger.read_bytes()
    ledger.write_bytes(raw[:-7])
    with pytest.raises(ValueError, match="LEDGER_TAIL_INCOMPLETE"):
        read_ledger_events(ledger)


def test_run_discovery_ledger_failure_is_explicit_and_registry_untouched(
        tmp_path, monkeypatch):
    """If a ledger append fails mid-run, the run aborts with LEDGER_WRITE_FAILED
    and the registry is never persisted as 'reliably complete'."""
    import os as _os
    real_write = _os.write
    calls = {"n": 0}

    def fake_write(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:  # fail the per-object append, not the start event
            return 0  # short write -> IOError
        return real_write(fd, data)

    monkeypatch.setattr(_os, "write", fake_write)
    drive = FakeDrive(items=[_meta("f1")])
    drive.bounded_blobs = {"f1": b"hello-drive"}
    ledger = tmp_path / "ledger.jsonl"
    registry = tmp_path / "registry.json"
    with pytest.raises(DriveDiscoveryError) as exc:
        run_discovery(drive, "folder-1", ledger_path=ledger,
                      registry_path=registry)
    assert exc.value.code == LEDGER_WRITE_FAILED
    assert not registry.exists()


# ================================================== Finding 4: list errors

def test_list_raising_discovery_error_is_normalized_to_list_failed():
    drive = FakeDrive()
    def boom(parent):
        raise DriveDiscoveryError("PERMISSION_DENIED", file_id=parent,
                                  detail="wrapped adapter")
    drive.list = boom
    with pytest.raises(DriveDiscoveryError) as exc:
        discover_folder(drive, "folder-1")
    assert exc.value.code == LIST_FAILED
    assert exc.value.__cause__ is not None
    assert exc.value.__cause__.code == "PERMISSION_DENIED"


def test_max_items_boundary():
    drive = FakeDrive(items=[_meta("f1"), _meta("f2")])
    objects, _ = discover_folder(drive, "folder-1", max_items=2)
    assert len(objects) == 2  # exactly at the cap is fine
    with pytest.raises(DriveDiscoveryError) as exc:
        discover_folder(drive, "folder-1", max_items=1)
    assert exc.value.code == LIST_FAILED


# ================================================== Finding 5/6: read-only + CLI + report

class WriteDetectingDrive(FakeDrive):
    """Fails loudly if any Drive write path is ever invoked."""

    def _no_write(self, *a, **k):
        raise AssertionError("WRITE_ATTEMPTED on a read-only path")

    put = _no_write
    move = _no_write
    ensure = _no_write
    delete = _no_write
    copy = _no_write
    create = _no_write
    update = _no_write


def test_read_only_paths_never_invoke_writes(tmp_path):
    drive = WriteDetectingDrive(items=[
        _meta("f1"), _meta("nd1", mimeType="application/vnd.google-apps.document",
                           md5Checksum=None, name="Doc"),
    ])
    drive.bounded_blobs = {"f1": b"hello-drive"}
    drive.docs = {"nd1": {"revisionId": "r1", "title": "t", "body": {}}}
    ledger = tmp_path / "ledger.jsonl"
    report = run_discovery(drive, "folder-1", ledger_path=ledger,
                           verify_bytes=True)
    assert report["counts"]["discovered"] == 2
    assert report["counts"]["verified"] == 2  # no WRITE_ATTEMPTED raised


def test_cli_drive_discover_rejects_bad_max_bytes(tmp_path):
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {"CBA_KB_INSTANCE_ROOT": "", "PYTHONPATH": str(root / "src"),
           "PATH": "/usr/bin:/bin"}
    cmd = [sys.executable, "-m", "cba_kb.cli", "--root", str(root),
           "drive-discover", "--folder-id", "x",
           "--ledger", str(tmp_path / "l.jsonl"),
           "--max-bytes-mb", "0"]
    # Fails before any Drive construction: no credentials needed.
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=str(root))
    assert proc.returncode != 0
    assert "MAX_BYTES_MUST_BE_POSITIVE" in proc.stderr


def test_cli_parser_ownership():
    """drive-discover owns its args; archive-query keeps --visibility."""
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"}
    base = [sys.executable, "-m", "cba_kb.cli", "--root", str(root)]
    out = subprocess.run(base + ["drive-discover", "--help"],
                         capture_output=True, text=True, env=env, cwd=str(root))
    assert "--folder-id" in out.stdout and "--visibility" not in out.stdout
    out = subprocess.run(base + ["archive-query", "--help"],
                         capture_output=True, text=True, env=env, cwd=str(root))
    assert "--visibility" in out.stdout


def test_abnormal_states_land_in_ledger_and_report(tmp_path):
    drive = FakeDrive(items=[
        _meta("ok"), _meta("bad", md5Checksum="0" * 32),
    ])
    blob_ok, blob_bad = b"hello-drive", b"other-bytes"
    drive._items[0]["md5Checksum"] = hashlib.md5(blob_ok).hexdigest()
    drive.bounded_blobs = {"ok": blob_ok, "bad": blob_bad}
    ledger = tmp_path / "ledger.jsonl"
    report = run_discovery(drive, "folder-1", ledger_path=ledger,
                           verify_bytes=True)
    assert report["counts"]["verified"] == 1
    assert report["counts"]["failed"] == 1
    events = read_ledger_events(ledger)
    by_file = {e["evidence_ref"]: e for e in events
               if e["action"] == "drive_object_discovered"}
    assert by_file["bad"]["verification"]["reason"] == CHECKSUM_MISMATCH
    assert by_file["bad"]["new_state"] == VERIFY_FAILED
