"""Tests for the rights-aware search index (slice 5, F-01 root-cause fix).

All tests run the real build_index -> save/load -> search_index path.
Uses the REAL grant format from drive_search.grant_rights.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cba_kb import drive_search_index as si

# Portable: src dir derived from this test file's location, not hardcoded.
SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


def _grant(file_id, approver="boss", **over):
    g = {"approved": True, "approver": approver, "file_id": file_id,
         "scope": "drive:read", "granted_at": "2026-10-15T00:00:00Z",
         "note": ""}
    g.update(over)
    return g


def _rec(name, status, file_id, grant=None):
    r = {"name": name, "rights_status": status}
    if grant is not None:
        r["rights_grant"] = grant
    return r


def _registry():
    return {
        "f1": _rec("Q3 sales report", "approved", "f1", _grant("f1")),
        "f2": _rec("Q3 forecast", "review", "f2"),
        "f3": _rec("secret plans", "private", "f3"),
    }


def _texts():
    return {"f1": "revenue grew", "f2": "revenue draft", "f3": "revenue secret"}


def _search(idx, reg, query, principal="alice"):
    return si.search_index(idx, query, principal, registry=reg)


# ---------------- F-01: strict grant validation via the single authority

def test_valid_grant_visible():
    reg = _registry()
    idx = si.build_index(reg, _texts())
    out = _search(idx, reg, "revenue")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_wrong_scope_invisible():
    reg = {"f1": _rec("doc", "approved", "f1", _grant("f1", scope="drive:write"))}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


def test_wrong_file_id_invisible():
    reg = {"f1": _rec("doc", "approved", "f1", _grant("other"))}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


def test_revoked_grant_invisible():
    reg = {"f1": _rec("doc", "approved", "f1", _grant("f1", revoked=True))}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


def test_expired_grant_invisible():
    reg = {"f1": _rec("doc", "approved", "f1",
                      _grant("f1", expires_at="2020-01-01T00:00:00Z"))}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


def test_unparseable_expiry_invisible():
    reg = {"f1": _rec("doc", "approved", "f1",
                      _grant("f1", expires_at="not-a-date"))}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


@pytest.mark.parametrize("bad_grant", [False, None, {}])
def test_falsy_grant_invisible(bad_grant):
    reg = {"f1": _rec("doc", "approved", "f1", bad_grant)}
    idx = si.build_index(reg, {"f1": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


def test_private_review_with_grant_invisible():
    reg = {
        "f1": _rec("priv", "private", "f1", _grant("f1")),
        "f2": _rec("rev", "review", "f2", _grant("f2")),
    }
    idx = si.build_index(reg, {"f1": "hello", "f2": "hello"})
    assert _search(idx, reg, "hello")["results"] == []


# ---------------- two-phase: index AND current registry must agree

def test_stale_index_visible_but_registry_revoked():
    """Old index says visible; current registry revoked -> invisible."""
    reg = _registry()
    idx = si.build_index(reg, _texts())
    # Registry grant revoked AFTER index build.
    reg["f1"]["rights_grant"]["revoked"] = True
    assert _search(idx, reg, "revenue")["results"] == []


def test_stale_index_visible_but_grant_expired():
    reg = _registry()
    idx = si.build_index(reg, _texts())
    reg["f1"]["rights_grant"]["expires_at"] = "2020-01-01T00:00:00Z"
    assert _search(idx, reg, "revenue")["results"] == []


def test_index_revoked_registry_still_approved():
    """Index visible=False cannot be overridden by the registry."""
    reg = _registry()
    idx = si.build_index(reg, _texts())
    si.revoke_grant(idx, "f1", "alice")
    # Registry still has a valid grant, but index says no.
    assert _search(idx, reg, "revenue")["results"] == []


def test_rebuild_from_revoked_registry_stays_invisible():
    reg = _registry()
    reg["f1"]["rights_grant"]["revoked"] = True
    idx = si.build_index(reg, _texts())
    assert _search(idx, reg, "revenue")["results"] == []


def test_forged_index_without_registry_grant():
    """Forged index (approved/visible=True) + no valid registry grant."""
    idx = {"version": 1, "kind": "search_index",
           "terms": {"hello": ["f1"]},
           "docs": {"f1": {"title": "x", "rights_status": "approved",
                           "visible": True}}}
    reg = {"f1": _rec("x", "private", "f1")}
    assert _search(idx, reg, "hello")["results"] == []


def test_missing_registry_fail_closed():
    idx = si.build_index(_registry(), _texts())
    with pytest.raises(ValueError, match="SEARCH_REGISTRY_REQUIRED"):
        si.search_index(idx, "revenue", "alice", registry=None)


def test_missing_record_in_registry_invisible():
    reg = _registry()
    idx = si.build_index(reg, _texts())
    del reg["f1"]
    assert _search(idx, reg, "revenue")["results"] == []


# ---------------- F-05: no side channel (strengthened)

def test_no_side_channel_ungranted_hit_vs_absent():
    reg = {
        "f1": _rec("ok", "approved", "f1", _grant("f1")),
        "f2": _rec("hidden", "private", "f2"),
    }
    idx = si.build_index(reg, {"f1": "hello",
                               "f2": "quartzunicorn hidden"})
    o1 = _search(idx, reg, "quartzunicorn")
    o2 = _search(idx, reg, "zzzabsent")
    assert o1 == {"results": []}
    assert o2 == {"results": []}
    assert o1 == o2


# ---------------- F-02/F-03 regressions

def test_missing_rights_status_rejected(tmp_path):
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({
        "version": 1, "kind": "search_index",
        "terms": {"revenue": ["f1"]},
        "docs": {"f1": {"title": "x", "visible": True}},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="SEARCH_INDEX_CORRUPT"):
        si.load_index(p)


def test_revoke_grant_persistent_no_window(tmp_path):
    p = tmp_path / "idx.json"
    reg = _registry()
    idx = si.build_index(reg, _texts())
    si.save_index(p, idx)
    assert si.revoke_grant_persistent(p, "f1", "alice") is True
    idx2 = si.load_index(p)
    assert _search(idx2, reg, "revenue")["results"] == []


def test_persist_reload_roundtrip(tmp_path):
    p = tmp_path / "idx.json"
    reg = _registry()
    idx = si.build_index(reg, _texts())
    si.save_index(p, idx)
    idx2 = si.load_index(p)
    out = _search(idx2, reg, "revenue")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_cli_search_index_end_to_end(tmp_path):
    """At least one test through the real CLI wiring."""
    reg_p = tmp_path / "reg.json"
    idx_p = tmp_path / "idx.json"
    reg_p.write_text(json.dumps(_registry()), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "drive-build-index",
         "--registry", str(reg_p), "--index", str(idx_p)],
        capture_output=True, text=True, cwd=SRC_DIR)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "drive-search-index",
         "--index", str(idx_p), "--registry", str(reg_p),
         "--query", "sales", "--principal", "alice"],
        capture_output=True, text=True, cwd=SRC_DIR)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert [x["file_id"] for x in out["results"]] == ["f1"]
