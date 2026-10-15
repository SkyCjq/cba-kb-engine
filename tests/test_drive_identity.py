"""Tests for persistent identity: dedup index + revision chains (slice 4)."""
from __future__ import annotations

import json

import pytest

from cba_kb import drive_identity as di
from cba_kb.drive_discovery import ACCESS_VERIFIED


@pytest.fixture()
def paths(tmp_path):
    return {
        "dedup": tmp_path / "r.dedup.json",
        "history": tmp_path / "r.revisions.json",
        "ledger": tmp_path / "ledger.jsonl",
    }


def _wire(dedup, history, ledger, **kw):
    base = dict(
        file_id="f1", source_id="s-f1", doc_id="d-f1@1", revision=1,
        digest_kind="native_doc_json", digest="aaa",
        verify_status=ACCESS_VERIFIED, ts="2026-10-15T00:00:00+00:00",
    )
    base.update(kw)
    return di.wire_identity(dedup_index=dedup, revision_history=history,
                            ledger_path=ledger, **base)


def test_dedup_two_files_same_digest(paths):
    dedup = di.load_dedup_index(paths["dedup"])
    history = di.load_revision_history(paths["history"])
    r1 = _wire(dedup, history, paths["ledger"])
    assert r1["dedup"] == "CONTENT_NEW"
    r2 = _wire(dedup, history, paths["ledger"], file_id="f2",
               source_id="s-f2", doc_id="d-f2@1")
    assert r2["dedup"] == "DUPLICATE_CONTENT"
    assert r2["canonical_file_id"] == "f1"


def test_dedup_persists_across_restart(paths):
    dedup = di.load_dedup_index(paths["dedup"])
    history = di.load_revision_history(paths["history"])
    _wire(dedup, history, paths["ledger"])
    di.save_dedup_index(paths["dedup"], dedup)
    di.save_revision_history(paths["history"], history)
    # Simulate restart: fresh load.
    dedup2 = di.load_dedup_index(paths["dedup"])
    history2 = di.load_revision_history(paths["history"])
    r = _wire(dedup2, history2, paths["ledger"], file_id="f2",
              source_id="s-f2", doc_id="d-f2@1")
    assert r["dedup"] == "DUPLICATE_CONTENT"
    assert r["canonical_file_id"] == "f1"


def test_dedup_needs_verified_digest(paths):
    dedup = di.load_dedup_index(paths["dedup"])
    history = di.load_revision_history(paths["history"])
    r = _wire(dedup, history, paths["ledger"], verify_status="REVIEW_REQUIRED")
    assert r["dedup"] == "UNDEDUPLICABLE"
    r2 = _wire(dedup, history, paths["ledger"], file_id="f2",
               source_id="s-f2", doc_id="d-f2@1",
               verify_status="REVIEW_REQUIRED")
    assert r2["dedup"] == "UNDEDUPLICABLE"


def test_revision_chain_appends_idempotent(paths):
    history = di.load_revision_history(paths["history"])
    di.append_revision(history, "s1", 1, "d1", "t1")
    di.append_revision(history, "s1", 2, "d2", "t2")
    # Same (revision, doc_id) is idempotent.
    di.append_revision(history, "s1", 2, "d2", "t2")
    assert len(history["entries"]["s1"]) == 2


def test_revision_conflict_fail_closed(paths):
    history = di.load_revision_history(paths["history"])
    di.append_revision(history, "s1", 1, "d1", "t1")
    with pytest.raises(ValueError, match="REVISION_IDENTITY_CONFLICT"):
        di.append_revision(history, "s1", 1, "dX", "t2")
    # The original mapping is untouched.
    assert history["entries"]["s1"][0]["doc_id"] == "d1"


def test_corrupt_store_fail_closed(paths):
    paths["dedup"].write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_STORE_CORRUPT"):
        di.load_dedup_index(paths["dedup"])
    paths["history"].write_text("[1,2", encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_STORE_CORRUPT"):
        di.load_revision_history(paths["history"])


def test_revision_history_persists_across_restart(paths):
    history = di.load_revision_history(paths["history"])
    di.append_revision(history, "s1", 1, "d1", "t1")
    di.save_revision_history(paths["history"], history)
    history2 = di.load_revision_history(paths["history"])
    assert history2["entries"]["s1"][0]["doc_id"] == "d1"


def test_wire_emits_ledger_events(paths):
    dedup = di.load_dedup_index(paths["dedup"])
    history = di.load_revision_history(paths["history"])
    _wire(dedup, history, paths["ledger"])
    # Second revision of same source -> revision_linked event.
    _wire(dedup, history, paths["ledger"], revision=2, doc_id="d-f1@2",
          digest="bbb")
    # Duplicate file -> duplicate event.
    _wire(dedup, history, paths["ledger"], file_id="f2", source_id="s-f2",
          doc_id="d-f2@1", digest="aaa")
    lines = paths["ledger"].read_text(encoding="utf-8").strip().splitlines()
    actions = [json.loads(ln)["action"] for ln in lines]
    assert "drive_identity_revision_linked" in actions
    assert "drive_identity_duplicate" in actions
