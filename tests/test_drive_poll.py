"""Tests for cba_kb.drive_poll (2.0.4-2 slice 3, T2).

Contract under test (scripted fakes prove contract behavior, not real API):
- First poll initializes the watch; later polls process changes incrementally.
- New/modified/removed files update the registry; per-file ledger events.
- Crash mid-batch: token not advanced; restart re-delivers; no silent loss,
  registry stays correct via idempotent upsert.
- Per-file failure does not abort the poll (partial success).
- Corrupt state fails closed.
"""
import json

import pytest

from cba_kb import drive_poll as dp
from cba_kb.drive_discovery import DriveDiscoveryError


class FakeDrive:
    """Minimal drive stub: meta() only (verify_bytes=False path)."""

    def __init__(self, files):
        self.files = dict(files)

    def meta(self, file_id):
        if file_id not in self.files:
            raise PermissionError(f"denied: {file_id}")
        return self.files[file_id]


def _file_meta(file_id, name="report", version="1", md5="abc123"):
    return {"id": file_id, "name": name, "mimeType": "text/plain",
            "version": version, "headRevisionId": f"hr{version}",
            "md5Checksum": md5, "parents": ["folder1"],
            "modifiedTime": f"2026-10-15T00:00:{version}Z"}


class FakeSource:
    """Scripted change source."""

    def __init__(self):
        self.token = "tok0"
        self.pending = []
        self.fail_list = False

    def get_start_token(self):
        return self.token

    def _next_token(self):
        n = int(self.token[3:]) + 1
        self.token = f"tok{n}"
        return self.token

    def list_changes(self, since_token):
        if self.fail_list:
            raise RuntimeError("transport boom")
        # Note: no strict token assertion — after a crash the poller retries
        # with the old token, which the real Changes API accepts.
        out = self.pending
        self.pending = []
        return out, self._next_token()

    def add_change(self, file_id, version="1", removed=False):
        if removed:
            self.pending.append({"change_id": f"{file_id}@removed",
                                 "file_id": file_id, "removed": True,
                                 "file": {}})
        else:
            meta = _file_meta(file_id, version=version)
            self.pending.append({
                "change_id": f"{file_id}@{meta['modifiedTime']}",
                "file_id": file_id, "removed": False, "file": meta})


@pytest.fixture
def env(tmp_path):
    return {
        "registry": tmp_path / "registry.json",
        "ledger": tmp_path / "ledger.jsonl",
        "state": tmp_path / "poll_state.json",
    }


def _poll(source, drive, env, **kw):
    return dp.poll_once(source, drive, registry_path=env["registry"],
                        ledger_path=env["ledger"], state_path=env["state"],
                        **kw)


def _ledger_actions(env):
    return [json.loads(line)["action"]
            for line in env["ledger"].read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ core

def test_poll_initializes_watch(env):
    source, drive = FakeSource(), FakeDrive({})
    out = _poll(source, drive, env)
    assert out["initialized"] is True
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state["page_token"] == "tok0"
    assert state["polls_completed"] == 0
    assert POLL_STARTED in _ledger_actions(env) if False else True
    assert dp.POLL_STARTED in _ledger_actions(env)


def test_poll_discovers_new_file(env):
    source, drive = FakeSource(), FakeDrive({})
    _poll(source, drive, env)
    drive.files["f1"] = _file_meta("f1")
    source.add_change("f1", version="1")
    out = _poll(source, drive, env)
    assert out["counts"]["processed"] == 1
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["f1"]["verify_status"] == "ACCESS_VERIFIED"
    assert registry["f1"]["name"] == "report"
    assert dp.POLL_FILE_PROCESSED in _ledger_actions(env)
    assert dp.POLL_COMPLETED in _ledger_actions(env)
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state["polls_completed"] == 1
    assert state["files_processed"] == 1


def test_poll_handles_modified_file(env):
    source = FakeSource()
    drive = FakeDrive({"f1": _file_meta("f1", version="1")})
    _poll(source, drive, env)
    source.add_change("f1", version="1")
    _poll(source, drive, env)
    # File modified: new version delivered.
    drive.files["f1"] = _file_meta("f1", version="2")
    source.add_change("f1", version="2")
    out = _poll(source, drive, env)
    assert out["counts"]["processed"] == 1
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["f1"]["revision"] == "hr2"  # head_revision_id wins


def test_poll_tombstones_removed_file(env):
    source = FakeSource()
    drive = FakeDrive({"f1": _file_meta("f1", version="1")})
    _poll(source, drive, env)
    source.add_change("f1", version="1")
    _poll(source, drive, env)
    # File deleted on Drive.
    del drive.files["f1"]
    source.add_change("f1", removed=True)
    out = _poll(source, drive, env)
    assert out["counts"]["removed"] == 1
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["f1"]["verify_status"] == "FILE_REMOVED"
    # Tombstoned files are excluded from search results.
    from cba_kb.drive_search import search_drive_registry
    res = search_drive_registry(registry, "report")
    assert res["results"] == []


def test_poll_empty_changes_is_noop(env):
    source, drive = FakeSource(), FakeDrive({})
    _poll(source, drive, env)
    out = _poll(source, drive, env)
    assert out["counts"]["changes_seen"] == 0
    assert out["counts"]["processed"] == 0
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state["polls_completed"] == 1  # init watch does not count as a poll


def test_poll_partial_failure_continues(env):
    class Http403(Exception):
        def __init__(self):
            super().__init__("forbidden")
            self.status_code = 403

    class DenyDrive(FakeDrive):
        def meta(self, file_id):
            if file_id == "f2":
                raise Http403()
            return super().meta(file_id)

    source = FakeSource()
    drive = DenyDrive({"f1": _file_meta("f1"), "f3": _file_meta("f3")})
    _poll(source, drive, env)
    # f2's meta raises 403 -> REVIEW_REQUIRED (fail-closed, still recorded);
    # a malformed change (no file_id) -> failed.
    source.add_change("f1")
    source.add_change("f2")
    source.add_change("f3")
    source.pending.append({"change_id": "bad1", "file_id": None,
                           "removed": False, "file": {}})
    out = _poll(source, drive, env)
    assert out["counts"]["processed"] == 3
    assert out["counts"]["failed"] == 1
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert set(registry) == {"f1", "f2", "f3"}
    assert registry["f2"]["verify_status"] == "REVIEW_REQUIRED"
    actions = _ledger_actions(env)
    assert dp.POLL_FILE_FAILED in actions
    assert dp.POLL_COMPLETED in actions  # poll still completes
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state["files_failed"] == 1


def test_poll_corrupt_state_fails_closed(env):
    env["state"].write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="POLL_STATE_CORRUPT"):
        _poll(FakeSource(), FakeDrive({}), env)


def test_poll_list_failure_raises_and_keeps_token(env):
    source = FakeSource()
    drive = FakeDrive({})
    _poll(source, drive, env)
    token_before = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]
    source.fail_list = True
    with pytest.raises(DriveDiscoveryError, match="POLL_LIST_FAILED"):
        _poll(source, drive, env)
    token_after = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]
    assert token_before == token_after


def test_poll_crash_mid_batch_recovers_without_loss(env, monkeypatch):
    """Simulate a crash during the batch (registry persist fails). The token
    must not advance; a retry re-delivers and completes with no loss."""
    source = FakeSource()
    drive = FakeDrive({"f1": _file_meta("f1"), "f2": _file_meta("f2")})
    _poll(source, drive, env)
    source.add_change("f1")
    source.add_change("f2")
    token_before = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]

    real_atomic = dp.atomic
    calls = {"n": 0}

    def flaky_atomic(path, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated disk failure")
        return real_atomic(path, data)

    monkeypatch.setattr(dp, "atomic", flaky_atomic)
    with pytest.raises(OSError, match="simulated disk failure"):
        _poll(source, drive, env)
    # Token NOT advanced: the batch was not checkpointed.
    token_after = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]
    assert token_after == token_before

    # Retry: re-delivery completes; registry correct; no silent loss.
    monkeypatch.setattr(dp, "atomic", real_atomic)
    source.pending = [  # re-deliver (as the Changes API would)
        {"change_id": f"f1@{_file_meta('f1')['modifiedTime']}",
         "file_id": "f1", "removed": False, "file": _file_meta("f1")},
        {"change_id": f"f2@{_file_meta('f2')['modifiedTime']}",
         "file_id": "f2", "removed": False, "file": _file_meta("f2")},
    ]
    out = _poll(source, drive, env)
    assert out["counts"]["processed"] == 2
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert set(registry) == {"f1", "f2"}
    assert all(r["verify_status"] == "ACCESS_VERIFIED"
               for r in registry.values())


def test_poll_duplicate_delivery_is_idempotent(env):
    """Same change delivered twice: second delivery is skipped, no duplicate
    per-file ledger events."""
    source = FakeSource()
    drive = FakeDrive({"f1": _file_meta("f1")})
    _poll(source, drive, env)
    source.add_change("f1")
    _poll(source, drive, env)
    processed_before = _ledger_actions(env).count(dp.POLL_FILE_PROCESSED)
    # Force re-delivery of the same change (old token replay).
    meta = _file_meta("f1")
    source.pending.append({"change_id": f"f1@{meta['modifiedTime']}",
                           "file_id": "f1", "removed": False, "file": meta})
    out = _poll(source, drive, env)
    assert out["counts"]["duplicate_skipped"] == 1
    assert out["counts"]["processed"] == 0
    assert _ledger_actions(env).count(dp.POLL_FILE_PROCESSED) == processed_before


def test_run_poll_loop_respects_max_polls(env):
    source, drive = FakeSource(), FakeDrive({})
    out = dp.run_poll_loop(source, drive, registry_path=env["registry"],
                           ledger_path=env["ledger"], state_path=env["state"],
                           interval_seconds=0, max_polls=3)
    assert out["polls_completed"] == 3


# ------------------------------------------------------- adapter contract

def test_drive_changes_source_contract():
    """Contract test against the documented changes() shape (scripted fake
    transport, not the real API)."""

    class FakeChanges:
        def __init__(self):
            self.calls = []

        def getStartPageToken(self):
            outer = self

            class Call:
                def execute(self):
                    outer.calls.append("getStartPageToken")
                    return {"startPageToken": "tokStart"}
            return Call()

        def list(self, **kw):
            outer = self
            assert kw["pageToken"] == "tokStart"

            class Call:
                def execute(self):
                    outer.calls.append("list")
                    return {
                        "changes": [
                            {"fileId": "f1", "removed": False,
                             "file": _file_meta("f1")},
                            {"fileId": "f2", "removed": True, "file": {}},
                        ],
                        "newStartPageToken": "tokNext",
                    }
            return Call()

    class FakeApi:
        def changes(self):
            return FakeChanges()

    class FakeDriveWithApi:
        def __init__(self):
            self.api = FakeApi()

    src = dp.DriveChangesSource(FakeDriveWithApi())
    assert src.get_start_token() == "tokStart"
    changes, new_token = src.list_changes("tokStart")
    assert new_token == "tokNext"
    assert changes[0]["file_id"] == "f1" and not changes[0]["removed"]
    assert changes[1]["file_id"] == "f2" and changes[1]["removed"]
    assert changes[1]["change_id"] == "f2@removed"


def test_normalize_change_shapes():
    raw = {"fileId": "f9", "removed": False,
           "file": _file_meta("f9", version="3")}
    c = dp.normalize_change(raw)
    assert c["file_id"] == "f9" and not c["removed"]
    assert "f9@" in c["change_id"]


# ------------------------------------------------- Q2-R2 blocker fixes

def test_rediscovery_preserves_rights_grant(env):
    """Q2-R2 Blocker 1: re-delivery after eviction must not wipe the local
    rights grant. The grant survives re-discovery."""
    from cba_kb.drive_search import grant_rights, search_drive_registry
    source = FakeSource()
    drive = FakeDrive({"a": _file_meta("a", version="1")})
    _poll(source, drive, env)
    source.add_change("a", version="1")
    _poll(source, drive, env)
    # Operator grants rights via the real grant path.
    grant_rights(env["registry"], env["ledger"], "a", approver="op")
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["a"]["rights_status"] == "approved"
    assert search_drive_registry(registry, "report")["results"]
    # File modified on Drive -> re-discovery.
    drive.files["a"] = _file_meta("a", version="2")
    source.add_change("a", version="2")
    _poll(source, drive, env)
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["a"]["rights_status"] == "approved"
    assert registry["a"]["rights_grant"]["approver"] == "op"
    out = search_drive_registry(registry, "report")
    assert [r["file_id"] for r in out["results"]] == ["a"]


def test_processed_ids_evict_oldest_first(env):
    """Q2-R2 Blocker 1b: eviction drops the oldest IDs, not lexicographic."""
    source = FakeSource()
    drive = FakeDrive({})
    _poll(source, drive, env)
    # Seed the state with 3 IDs in processing order.
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    state["processed_change_ids"] = ["c@3", "a@1", "b@2"]
    env["state"].write_text(json.dumps(state), encoding="utf-8")
    # Shrink the cap temporarily and trigger a save.
    old_max = dp.MAX_PROCESSED_IDS
    dp.MAX_PROCESSED_IDS = 2
    try:
        drive.files["z"] = _file_meta("z")
        source.add_change("z")
        _poll(source, drive, env)
    finally:
        dp.MAX_PROCESSED_IDS = old_max
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    ids = state["processed_change_ids"]
    # Oldest ("c@3") evicted; newest kept in processing order.
    assert "c@3" not in ids
    assert ids[-2:] == ["b@2", ids[-1]]  # order preserved, b@2 before z's id
    assert len(ids) == 2


def test_corrupt_processed_ids_type_fails_closed(env):
    """Q2-R2 Blocker 2: a dict in processed_change_ids must be rejected,
    not silently treated as processed."""
    source = FakeSource()
    drive = FakeDrive({"a": _file_meta("a")})
    _poll(source, drive, env)
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    state["processed_change_ids"] = {"a@1": False}
    env["state"].write_text(json.dumps(state), encoding="utf-8")
    source.add_change("a", version="1")
    with pytest.raises(ValueError, match="POLL_STATE_CORRUPT"):
        _poll(source, drive, env)
    # Token must not advance on fail-closed.
    state2 = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state2["page_token"] == state["page_token"]


def test_corrupt_ledger_fails_closed_on_recovery(env):
    """Q2-R2 Blocker 3: an incomplete/corrupt ledger refuses the poll."""
    source = FakeSource()
    drive = FakeDrive({})
    _poll(source, drive, env)
    env["ledger"].write_text("{incomplete", encoding="utf-8")
    source.add_change("x", version="1")
    with pytest.raises(DriveDiscoveryError, match="POLL_LEDGER_CORRUPT"):
        _poll(source, drive, env)
    state = json.loads(env["state"].read_text(encoding="utf-8"))
    assert state["polls_completed"] == 0  # no poll completed


def test_tombstone_clears_rights_grant(env):
    """Tombstoning clears the local grant; re-add starts fresh."""
    from cba_kb.drive_search import grant_rights
    source = FakeSource()
    drive = FakeDrive({"a": _file_meta("a", version="1")})
    _poll(source, drive, env)
    source.add_change("a", version="1")
    _poll(source, drive, env)
    grant_rights(env["registry"], env["ledger"], "a", approver="op")
    # File removed.
    del drive.files["a"]
    source.add_change("a", removed=True)
    _poll(source, drive, env)
    registry = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert registry["a"]["verify_status"] == "FILE_REMOVED"
    assert "rights_grant" not in registry["a"]
    assert registry["a"]["rights_status"] == "review"


# --------------------------------------- init vs recovery ledger matrix

class CountingSource(FakeSource):
    """FakeSource that counts remote calls."""

    def __init__(self):
        super().__init__()
        self.get_start_token_calls = 0
        self.list_changes_calls = 0

    def get_start_token(self):
        self.get_start_token_calls += 1
        return super().get_start_token()

    def list_changes(self, since_token):
        self.list_changes_calls += 1
        return super().list_changes(since_token)


def _valid_ledger_line(action="drive_poll_started"):
    return json.dumps({
        "ts": "2026-10-15T00:00:00+00:00", "actor": "muse", "action": action,
        "prev_state": "IDLE", "new_state": "WATCHING",
        "evidence_ref": "x", "policy_version": "test",
        "operator": "muse", "approval_ref": "", "retry_count": 0,
        "note": "n",
    }, ensure_ascii=False) + "\n"


def test_init_no_state_no_ledger(env):
    source, drive = CountingSource(), FakeDrive({})
    out = dp.poll_once(source, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["initialized"] is True
    assert source.get_start_token_calls == 1


def test_init_no_state_empty_ledger(env):
    env["ledger"].write_text("", encoding="utf-8")
    source, drive = CountingSource(), FakeDrive({})
    out = dp.poll_once(source, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["initialized"] is True


def test_init_no_state_valid_ledger(env):
    env["ledger"].write_text(_valid_ledger_line(), encoding="utf-8")
    source, drive = CountingSource(), FakeDrive({})
    out = dp.poll_once(source, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["initialized"] is True
    # The valid prior content is preserved; init appends after it.
    lines = env["ledger"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_init_no_state_corrupt_ledger_zero_side_effects(env):
    """Q2-R3 Blocker: corrupt ledger + no state must refuse with ZERO side
    effects — no remote calls, no state file, ledger bytes unchanged."""
    corrupt = b"{incomplete"
    env["ledger"].write_bytes(corrupt)
    source, drive = CountingSource(), FakeDrive({})
    with pytest.raises(DriveDiscoveryError, match="POLL_LEDGER_CORRUPT"):
        dp.poll_once(source, drive, registry_path=env["registry"],
                     ledger_path=env["ledger"], state_path=env["state"])
    assert source.get_start_token_calls == 0
    assert source.list_changes_calls == 0
    assert not env["state"].exists()
    assert env["ledger"].read_bytes() == corrupt
    assert not env["registry"].exists()


def test_init_no_state_json_damaged_ledger_zero_side_effects(env):
    """Ledger with newline but broken JSON: same zero-side-effect refusal."""
    corrupt = b'{"ts": "x"}\n{broken\n'
    env["ledger"].write_bytes(corrupt)
    source, drive = CountingSource(), FakeDrive({})
    with pytest.raises(DriveDiscoveryError, match="POLL_LEDGER_CORRUPT"):
        dp.poll_once(source, drive, registry_path=env["registry"],
                     ledger_path=env["ledger"], state_path=env["state"])
    assert source.get_start_token_calls == 0
    assert source.list_changes_calls == 0
    assert not env["state"].exists()
    assert env["ledger"].read_bytes() == corrupt


def test_recovery_corrupt_ledger_still_fails_closed(env):
    source, drive = CountingSource(), FakeDrive({})
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    token_before = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]
    env["ledger"].write_bytes(b"{incomplete")
    with pytest.raises(DriveDiscoveryError, match="POLL_LEDGER_CORRUPT"):
        dp.poll_once(source, drive, registry_path=env["registry"],
                     ledger_path=env["ledger"], state_path=env["state"])
    assert source.list_changes_calls == 0
    token_after = json.loads(env["state"].read_text(encoding="utf-8"))["page_token"]
    assert token_after == token_before
    assert env["ledger"].read_bytes() == b"{incomplete"


def test_recovery_valid_ledger_polls_normally(env):
    source = CountingSource()
    drive = FakeDrive({"f1": _file_meta("f1")})
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    source.add_change("f1")
    out = dp.poll_once(source, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["counts"]["processed"] == 1
    assert source.list_changes_calls == 1


def test_corrupt_state_never_polls(env):
    env["state"].write_text("{bad", encoding="utf-8")
    source, drive = CountingSource(), FakeDrive({})
    with pytest.raises(ValueError, match="POLL_STATE_CORRUPT"):
        dp.poll_once(source, drive, registry_path=env["registry"],
                     ledger_path=env["ledger"], state_path=env["state"])
    assert source.get_start_token_calls == 0
    assert source.list_changes_calls == 0


# -------------------------------- slice4: identity wiring in poll_once

class FakeNativeDrive(FakeDrive):
    """FakeDrive that also serves native-doc JSON."""

    def __init__(self, files, docs):
        super().__init__(files)
        self.docs = dict(docs)

    def document_json(self, file_id):
        if file_id not in self.docs:
            raise PermissionError(f"denied: {file_id}")
        return self.docs[file_id]


def _native_meta(file_id, name="doc", version="1"):
    return {"id": file_id, "name": name,
            "mimeType": "application/vnd.google-apps.document",
            "version": version, "headRevisionId": f"hr{version}",
            "parents": ["folder1"],
            "modifiedTime": f"2026-10-15T00:00:{version}Z"}


def test_poll_dedup_two_native_docs_same_content(env):
    content = {"body": {"content": [{"paragraph": {"text": "hello"}}]}}
    drive = FakeNativeDrive(
        {"f1": _native_meta("f1"), "f2": _native_meta("f2")},
        {"f1": content, "f2": content})
    source = FakeSource()
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    source.add_change("f1")
    source.add_change("f2")
    out = dp.poll_once(source, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["counts"]["processed"] == 2
    reg = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert reg["f1"]["dedup"] in ("CONTENT_NEW", "CONTENT_KNOWN")
    assert reg["f2"]["dedup"] == "DUPLICATE_CONTENT"
    assert reg["f2"]["duplicate_of"] == "f1"
    # Dedup index persisted; a restart re-detects the duplicate.
    dedup_path = env["registry"].with_suffix(".dedup.json")
    assert dedup_path.exists()


def test_poll_dedup_survives_restart(env):
    content = {"body": {"content": [{"paragraph": {"text": "hello"}}]}}
    drive = FakeNativeDrive(
        {"f1": _native_meta("f1"), "f2": _native_meta("f2")},
        {"f1": content, "f2": content})
    source = FakeSource()
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    source.add_change("f1")
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    # Simulate a full restart: new source, same files on disk.
    source2 = FakeSource()
    source2.token = "tok0"
    source2.pending = []
    source2.add_change("f2")
    out = dp.poll_once(source2, drive, registry_path=env["registry"],
                       ledger_path=env["ledger"], state_path=env["state"])
    assert out["counts"]["processed"] == 1
    reg = json.loads(env["registry"].read_text(encoding="utf-8"))
    assert reg["f2"]["dedup"] == "DUPLICATE_CONTENT"


def test_poll_revision_chain_persisted(env):
    drive = FakeNativeDrive(
        {"f1": _native_meta("f1", version="1")},
        {"f1": {"body": "v1"}})
    source = FakeSource()
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    source.add_change("f1")
    dp.poll_once(source, drive, registry_path=env["registry"],
                 ledger_path=env["ledger"], state_path=env["state"])
    hist_path = env["registry"].with_suffix(".revisions.json")
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    src_id = json.loads(env["registry"].read_text(encoding="utf-8"))["f1"]["source_id"]
    assert len(hist["entries"][src_id]) == 1
