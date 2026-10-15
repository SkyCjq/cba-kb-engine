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


# ---------------- Q2-R1 FAIL regressions (F-01, F-02)

def test_f01_semantically_corrupt_dedup_entry_rejected(paths):
    """F-01: a valid-JSON dedup index with a structurally broken entry must
    fail closed, not be treated as a duplicate."""
    paths["dedup"].write_text(json.dumps({
        "version": 1, "kind": "dedup_index",
        "entries": {"native_doc_json:abc": {}},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_STORE_CORRUPT"):
        di.load_dedup_index(paths["dedup"])


def test_f01_kind_mismatch_rejected(paths):
    paths["dedup"].write_text(json.dumps({
        "version": 1, "kind": "revision_history", "entries": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_STORE_CORRUPT"):
        di.load_dedup_index(paths["dedup"])


def test_f01_malformed_revision_chain_rejected(paths):
    paths["history"].write_text(json.dumps({
        "version": 1, "kind": "revision_history",
        "entries": {"s1": [{"revision": 1}]},  # missing doc_id
        "sealed": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_STORE_CORRUPT"):
        di.load_revision_history(paths["history"])


def test_f02_evicted_revision_still_conflict_checked(paths):
    """F-02: after capacity eviction, a conflicting remap of an evicted
    revision must still raise REVISION_IDENTITY_CONFLICT."""
    history = di.load_revision_history(paths["history"])
    for i in range(di.MAX_HISTORY_PER_SOURCE + 1):
        di.append_revision(history, "s1", i, f"d{i}", "t")
    # Revision 0 was evicted from the active chain but sealed.
    assert len(history["entries"]["s1"]) == di.MAX_HISTORY_PER_SOURCE
    assert "s1" in history["sealed"]
    # Same doc_id for the evicted revision is idempotent (no raise).
    di.append_revision(history, "s1", 0, "d0", "t")
    # A conflicting doc_id for the evicted revision must raise.
    with pytest.raises(ValueError, match="REVISION_IDENTITY_CONFLICT"):
        di.append_revision(history, "s1", 0, "conflicting_doc", "t")


def test_f02_sealed_survives_save_reload(paths):
    history = di.load_revision_history(paths["history"])
    for i in range(di.MAX_HISTORY_PER_SOURCE + 1):
        di.append_revision(history, "s1", i, f"d{i}", "t")
    di.save_revision_history(paths["history"], history)
    history2 = di.load_revision_history(paths["history"])
    with pytest.raises(ValueError, match="REVISION_IDENTITY_CONFLICT"):
        di.append_revision(history2, "s1", 0, "conflicting_doc", "t")
