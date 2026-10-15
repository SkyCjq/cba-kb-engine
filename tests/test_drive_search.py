"""Tests for cba_kb.drive_search (2.0.4-2 slice 2, T2).

Guarded-search contract under test:
- Only ACCESS_VERIFIED + rights-approved objects are returned as results.
- Everything else is excluded with an exact reason; review items can be
  *listed* (never searched as content) with include_review=True.
- Rights grants are explicit, per-file, approver-named, ledger-recorded.
- Content dedup never silently treats undigested objects as unique.
"""
import json

import pytest

from cba_kb.drive_search import (
    CONTENT_NEW,
    DUPLICATE_CONTENT,
    UNDEDUPLICABLE,
    classify_content_duplicate,
    content_key,
    grant_rights,
    load_registry,
    record_revision,
    search_drive_registry,
)


def _rec(file_id, name, verify="ACCESS_VERIFIED", rights="review",
         mime="text/plain", reason="OK"):
    return {
        "revision": "rev-1", "source_id": f"src_{file_id}",
        "doc_id": f"doc_{file_id}", "classification": "NEW",
        "name": name, "mime_type": mime,
        "verify_status": verify, "verify_reason": reason,
        "rights_status": rights, "rights_reason": "DRIVE_RIGHTS_UNVERIFIED",
    }


def _registry():
    return {
        "f1": _rec("f1", "Shanghai Falcons report", rights="approved"),
        "f2": _rec("f2", "Shanghai Falcons notes"),               # review rights
        "f3": _rec("f3", "Falcons budget", verify="REVIEW_REQUIRED",
                   reason="ACCESS_DENIED", rights="approved"),
        "f4": _rec("f4", "Falcons archive", verify="VERIFY_FAILED",
                   reason="CHECKSUM_MISMATCH", rights="approved"),
        "f5": _rec("f5", "Unrelated doc", rights="approved"),
    }


# ------------------------------------------------- guarded search

def test_search_returns_only_verified_and_approved():
    out = search_drive_registry(_registry(), "falcons")
    assert [r["file_id"] for r in out["results"]] == ["f1"]
    assert out["excluded"]["rights_excluded"] == 1   # f2
    assert out["excluded"]["review_excluded"] == 1   # f3
    assert out["excluded"]["failed_excluded"] == 1    # f4
    assert out["excluded"]["no_match"] == 1          # f5
    assert out["total_considered"] == 5


def test_search_default_deny_with_no_grants():
    reg = {k: _rec(k, f"Falcons {k}") for k in ("a", "b")}
    out = search_drive_registry(reg, "falcons")
    assert out["results"] == []
    assert out["excluded"]["rights_excluded"] == 2


def test_include_review_lists_but_never_returns_review_items():
    out = search_drive_registry(_registry(), "falcons", include_review=True)
    assert [r["file_id"] for r in out["results"]] == ["f1"]
    listed = {i["file_id"] for i in out["review_listing"]}
    assert listed == {"f3"}
    assert out["review_listing"][0]["note"].startswith("listed, not searchable")


def test_search_rejects_empty_query_and_bad_limit():
    with pytest.raises(ValueError):
        search_drive_registry(_registry(), "  ")
    with pytest.raises(ValueError):
        search_drive_registry(_registry(), "falcons", limit=0)


def test_search_mime_prefix_filter():
    reg = {"f1": _rec("f1", "Falcons sheet", rights="approved",
                      mime="application/vnd.google-apps.spreadsheet")}
    out = search_drive_registry(reg, "falcons", mime_prefix="text/plain")
    assert out["results"] == [] and out["excluded"]["no_match"] == 1


# ------------------------------------------------- grant_rights

def test_grant_rights_makes_object_searchable_and_audited(tmp_path):
    registry = tmp_path / "r.json"
    ledger = tmp_path / "l.jsonl"
    registry.write_text(json.dumps({"f2": _rec("f2", "Shanghai Falcons notes")}),
                        encoding="utf-8")
    res = grant_rights(registry, ledger, "f2", approver="human-op",
                       note="cleared 2026-10-15")
    assert res["rights_status"] == "approved"
    saved = json.loads(registry.read_text(encoding="utf-8"))
    assert saved["f2"]["rights_status"] == "approved"
    assert saved["f2"]["rights_grant"]["approver"] == "human-op"
    events = [json.loads(line) for line in
              ledger.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    ev = events[0]
    assert ev["action"] == "drive_rights_granted"
    assert ev["prev_state"] == "review" and ev["new_state"] == "approved"
    assert ev["operator"] == "human-op"
    # and now it is searchable
    out = search_drive_registry(saved, "falcons")
    assert [r["file_id"] for r in out["results"]] == ["f2"]


def test_grant_rights_unknown_file_fails_closed(tmp_path):
    registry = tmp_path / "r.json"
    ledger = tmp_path / "l.jsonl"
    registry.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(ValueError, match="RIGHTS_GRANT_UNKNOWN_FILE"):
        grant_rights(registry, ledger, "nope", approver="human-op")


def test_grant_rights_requires_approver(tmp_path):
    registry = tmp_path / "r.json"
    ledger = tmp_path / "l.jsonl"
    registry.write_text(json.dumps({"f2": _rec("f2", "x")}), encoding="utf-8")
    with pytest.raises(ValueError, match="RIGHTS_GRANT_APPROVER_REQUIRED"):
        grant_rights(registry, ledger, "f2", approver="  ")


def test_grant_rights_idempotent_when_already_approved(tmp_path):
    registry = tmp_path / "r.json"
    ledger = tmp_path / "l.jsonl"
    registry.write_text(
        json.dumps({"f1": _rec("f1", "x", rights="approved")}), encoding="utf-8")
    res = grant_rights(registry, ledger, "f1", approver="human-op")
    assert res["note"].startswith("already approved")


# ------------------------------------------------- content dedup

def test_content_dedup_across_file_ids():
    index = {}
    cls, canon = classify_content_duplicate(index, "f1", "md5", "abc123")
    assert (cls, canon) == (CONTENT_NEW, "f1")
    index[content_key("md5", "abc123")] = "f1"
    cls, canon = classify_content_duplicate(index, "f2", "md5", "abc123")
    assert (cls, canon) == (DUPLICATE_CONTENT, "f1")
    cls, canon = classify_content_duplicate(index, "f3", "md5", "def456")
    assert cls == CONTENT_NEW


def test_undigested_objects_are_never_silently_unique():
    cls, canon = classify_content_duplicate({}, "f1", None, None)
    assert (cls, canon) == (UNDEDUPLICABLE, None)
    cls, canon = classify_content_duplicate({}, "f1", "md5", "")
    assert cls == UNDEDUPLICABLE


# ------------------------------------------------- revision chains

def test_revision_chain_appends_without_duplicates():
    history = {}
    chain = record_revision(history, "src_f1", "rev-1", "doc_a")
    chain = record_revision(history, "src_f1", "rev-2", "doc_b")
    chain = record_revision(history, "src_f1", "rev-2", "doc_b")  # repeat
    assert [e["revision"] for e in chain] == ["rev-1", "rev-2"]
    assert history["src_f1"] is chain


# ------------------------------------------------- registry loading

def test_load_registry_fails_closed_on_corruption(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="REGISTRY_CORRUPT"):
        load_registry(bad)
    with pytest.raises(ValueError, match="REGISTRY_MISSING"):
        load_registry(tmp_path / "missing.json")
    lst = tmp_path / "list.json"
    lst.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ValueError, match="REGISTRY_CORRUPT"):
        load_registry(lst)
