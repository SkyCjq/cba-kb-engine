"""Tests for the rights-aware search index (slice 5).

Uses the REAL grant record format from drive_search.grant_rights:
rights_status="approved", rights_grant={approved, approver, file_id, scope}.
"""
from __future__ import annotations

import json

import pytest

from cba_kb import drive_search_index as si


def _grant(file_id, approver="boss"):
    return {"approved": True, "approver": approver, "file_id": file_id,
            "scope": "drive:read", "granted_at": "2026-10-15T00:00:00Z",
            "note": ""}


def _registry():
    return {
        "f1": {"name": "Q3 sales report", "rights_status": "approved",
               "rights_grant": _grant("f1")},
        "f2": {"name": "Q3 forecast", "rights_status": "review"},
        "f3": {"name": "secret plans", "rights_status": "private"},
        "f4": {"name": "stale grant doc", "rights_status": "approved",
               "rights_grant": {"approved": False, "approver": "boss",
                                "file_id": "f4", "scope": "drive:read"}},
    }


def _texts():
    return {
        "f1": "revenue grew twenty percent in Q3",
        "f2": "draft revenue numbers pending review",
        "f3": "top secret revenue projections",
        "f4": "old revenue data",
    }


def test_approved_doc_visible():
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "revenue", "alice")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_private_review_never_visible():
    """F-01 regression: private/review docs are invisible to everyone,
    even if a grant-like structure is present."""
    reg = _registry()
    # Attack: private doc with a stale grant dict attached.
    reg["f3"]["rights_grant"] = _grant("f3")
    idx = si.build_index(reg, _texts())
    out = si.search_index(idx, "secret", "alice")
    assert out["results"] == []
    out2 = si.search_index(idx, "revenue", "alice")
    assert [r["file_id"] for r in out2["results"]] == ["f1"]


def test_invalid_grant_not_visible():
    """A doc with approved status but invalid grant is invisible."""
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "old", "alice")
    assert out["results"] == []


def test_no_excluded_side_channel():
    """F-05: the response must not leak keyword existence via counts."""
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "quartzunicorn", "mallory")
    assert out == {"results": []}
    out2 = si.search_index(idx, "zzznope", "mallory")
    assert out2 == {"results": []}
    assert out == out2


def test_revoke_grant_hides_doc():
    idx = si.build_index(_registry(), _texts())
    assert si.revoke_grant(idx, "f1", "alice") is True
    out = si.search_index(idx, "revenue", "alice")
    assert out["results"] == []


def test_revoke_grant_persistent_no_window(tmp_path):
    """F-03: atomic revoke leaves no on-disk window."""
    p = tmp_path / "idx.json"
    idx = si.build_index(_registry(), _texts())
    si.save_index(p, idx)
    assert si.revoke_grant_persistent(p, "f1", "alice") is True
    idx2 = si.load_index(p)
    out = si.search_index(idx2, "revenue", "alice")
    assert out["results"] == []


def test_index_persists_and_reloads(tmp_path):
    p = tmp_path / "idx.json"
    idx = si.build_index(_registry(), _texts())
    si.save_index(p, idx)
    idx2 = si.load_index(p)
    out = si.search_index(idx2, "revenue", "alice")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_corrupt_index_fail_closed(tmp_path):
    p = tmp_path / "idx.json"
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="SEARCH_INDEX_CORRUPT"):
        si.load_index(p)


def test_semantically_corrupt_index_rejected(tmp_path):
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({
        "version": 1, "kind": "search_index",
        "terms": {"revenue": "not-a-list"},
        "docs": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="SEARCH_INDEX_CORRUPT"):
        si.load_index(p)


def test_missing_rights_status_rejected(tmp_path):
    """F-02: a doc without rights_status must fail closed."""
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({
        "version": 1, "kind": "search_index",
        "terms": {"revenue": ["f1"]},
        "docs": {"f1": {"title": "x", "visible": True}},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="SEARCH_INDEX_CORRUPT"):
        si.load_index(p)


def test_kind_mismatch_rejected(tmp_path):
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({
        "version": 1, "kind": "dedup_index", "terms": {}, "docs": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="SEARCH_INDEX_CORRUPT"):
        si.load_index(p)


def test_rebuild_is_deterministic():
    idx1 = si.build_index(_registry(), _texts())
    idx2 = si.build_index(_registry(), _texts())
    assert idx1 == idx2


def test_empty_query_rejected():
    idx = si.build_index(_registry(), _texts())
    with pytest.raises(ValueError, match="SEARCH_QUERY_REQUIRED"):
        si.search_index(idx, "  ", "alice")


def test_missing_principal_rejected():
    idx = si.build_index(_registry(), _texts())
    with pytest.raises(ValueError, match="SEARCH_PRINCIPAL_REQUIRED"):
        si.search_index(idx, "revenue", "")
