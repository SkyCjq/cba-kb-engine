"""Tests for the rights-aware search index (slice 5)."""
from __future__ import annotations

import json

import pytest

from cba_kb import drive_search_index as si


def _registry():
    return {
        "f1": {"name": "Q3 sales report", "rights_status": "granted",
               "rights_grant": {"grantee": "alice"}},
        "f2": {"name": "Q3 sales report draft", "rights_status": "granted",
               "rights_grant": {"grantee": "bob"}},
        "f3": {"name": "secret plans", "rights_status": "private"},
    }


def _texts():
    return {
        "f1": "revenue grew twenty percent in Q3",
        "f2": "draft revenue numbers pending review",
        "f3": "top secret revenue projections",
    }


def test_granted_principal_finds_own_docs():
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "revenue", "alice")
    assert [r["file_id"] for r in out["results"]] == ["f1"]
    assert out["excluded"] == 2  # f2 (bob's), f3 (private)


def test_ungranted_principal_sees_nothing():
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "revenue", "mallory")
    assert out["results"] == []
    assert out["excluded"] == 3


def test_private_never_visible_without_grant():
    idx = si.build_index(_registry(), _texts())
    out = si.search_index(idx, "secret", "alice")
    assert out["results"] == []


def test_revoke_grant_removes_visibility():
    idx = si.build_index(_registry(), _texts())
    assert si.revoke_grant(idx, "f1", "alice") is True
    out = si.search_index(idx, "revenue", "alice")
    assert out["results"] == []
    # Bob still sees his own doc.
    out2 = si.search_index(idx, "revenue", "bob")
    assert [r["file_id"] for r in out2["results"]] == ["f2"]


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
