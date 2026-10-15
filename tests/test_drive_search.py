"""Tests for cba_kb.drive_search (2.0.4-2 slice 2, T2).

Guarded-search contract under test:
- Only ACCESS_VERIFIED objects whose grant strictly validates
  (registry status AND base decide_drive_rights) are returned as results.
- Everything else is excluded with an exact per-object reason; review/failed
  items can be *listed* (never searched as content) with include_review=True.
- Rights grants are explicit, per-file, approver-named, ledger-FIRST
  recorded; a failed audit write never leaves a phantom approval.
- record_revision fails closed on identity conflict.
"""
import json

import pytest

from cba_kb.drive_discovery import DriveDiscoveryError, LEDGER_WRITE_FAILED
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


def _write_registry(path, registry):
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def _approved_via_grant(tmp_path, file_id="f1", name="Shanghai Falcons report"):
    """Build a registry + real grant through grant_rights (the production path)."""
    registry_path = tmp_path / "registry.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _write_registry(registry_path, {file_id: _rec(file_id, name)})
    result = grant_rights(registry_path, ledger_path, file_id, approver="op-1")
    assert result["rights_status"] == "approved"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    return registry, ledger_path


# ------------------------------------------------- guarded search

def test_search_returns_only_strictly_approved(tmp_path):
    registry, _ = _approved_via_grant(tmp_path)
    out = search_drive_registry(registry, "Falcons")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_search_default_deny_with_no_grants():
    out = search_drive_registry({"f1": _rec("f1", "Shanghai Falcons report")},
                                "Falcons")
    assert out["results"] == []
    assert out["excluded"]["rights_excluded"] == 1
    assert out["excluded"]["details"][0]["reason"].startswith(
        "RIGHTS_EXCLUDED:")


def test_search_evil_grants_are_all_excluded():
    """Q2 finding 1 repro: registry says approved, but the grant is bad —
    missing/wrong-scope/cross-file/revoked/expired must never enter results."""
    evil_grants = {
        "missing": None,
        "wrong_scope": {"approved": True, "approver": "op", "file_id": "f1",
                        "scope": "drive:metadata"},
        "cross_file": {"approved": True, "approver": "op", "file_id": "other",
                       "scope": "drive:read"},
        "revoked": {"approved": True, "approver": "op", "file_id": "f1",
                    "scope": "drive:read", "revoked": True},
        "expired": {"approved": True, "approver": "op", "file_id": "f1",
                    "scope": "drive:read",
                    "expires_at": "2020-01-01T00:00:00+00:00"},
        "not_approved": {"approved": False, "approver": "op", "file_id": "f1",
                         "scope": "drive:read"},
    }
    for label, grant in evil_grants.items():
        rec = _rec("f1", "Shanghai Falcons report", rights="approved")
        if grant is not None:
            rec["rights_grant"] = grant
        out = search_drive_registry({"f1": rec}, "Falcons")
        assert out["results"] == [], label
        assert out["excluded"]["rights_excluded"] == 1, label
        assert "RIGHTS_" in out["excluded"]["details"][0]["reason"], label


def test_search_grant_for_other_file_does_not_transfer():
    rec = _rec("f1", "Shanghai Falcons report", rights="approved")
    rec["rights_grant"] = {"approved": True, "approver": "op", "file_id": "f2",
                           "scope": "drive:read"}
    out = search_drive_registry({"f1": rec}, "Falcons")
    assert out["results"] == []


def test_excluded_reasons_distinguish_failures():
    """Q2 finding 5: CHECKSUM_MISMATCH vs REVISION_DRIFT must be
    distinguishable in the output."""
    registry = {
        "f1": _rec("f1", "Falcons report", verify="VERIFY_FAILED",
                   reason="CHECKSUM_MISMATCH", rights="approved"),
        "f2": _rec("f2", "Falcons notes", verify="VERIFY_FAILED",
                   reason="REVISION_DRIFT", rights="approved"),
    }
    out = search_drive_registry(registry, "Falcons")
    assert out["results"] == []
    reasons = {d["file_id"]: d["reason"] for d in out["excluded"]["details"]}
    assert reasons == {"f1": "VERIFY_FAILED:CHECKSUM_MISMATCH",
                       "f2": "VERIFY_FAILED:REVISION_DRIFT"}
    # include_review lists failed items with their reasons (never as results)
    out = search_drive_registry(registry, "Falcons", include_review=True)
    listed = {r["file_id"]: r["verify_reason"] for r in out["review_listing"]}
    assert listed == {"f1": "CHECKSUM_MISMATCH", "f2": "REVISION_DRIFT"}
    assert out["results"] == []


def test_include_review_lists_but_never_returns_review_items(tmp_path):
    registry_path = tmp_path / "registry.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _write_registry(registry_path, {
        "f1": _rec("f1", "Shanghai Falcons report"),
        "f2": _rec("f2", "Shanghai Falcons notes",
                   verify="REVIEW_REQUIRED", reason="ACCESS_DENIED"),
    })
    grant_rights(registry_path, ledger_path, "f1", approver="op-1")
    grant_rights(registry_path, ledger_path, "f2", approver="op-1")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    out = search_drive_registry(registry, "Falcons", include_review=True)
    assert [r["file_id"] for r in out["results"]] == ["f1"]
    assert [r["file_id"] for r in out["review_listing"]] == ["f2"]
    assert out["review_listing"][0]["verify_reason"] == "ACCESS_DENIED"
    reasons = {d["file_id"]: d["reason"] for d in out["excluded"]["details"]}
    assert reasons["f2"] == "REVIEW_WITHHELD:ACCESS_DENIED"


def test_search_rejects_empty_query_and_bad_limit():
    with pytest.raises(ValueError):
        search_drive_registry({}, "  ")
    with pytest.raises(ValueError):
        search_drive_registry({}, "x", limit=0)


def test_search_mime_prefix_filter(tmp_path):
    registry, _ = _approved_via_grant(tmp_path)
    out = search_drive_registry(registry, "Falcons", mime_prefix="image/")
    assert out["results"] == []
    assert out["excluded"]["no_match"] == 1


# ------------------------------------------------- grant_rights

def test_grant_rights_makes_object_searchable_and_audited(tmp_path):
    registry, ledger_path = _approved_via_grant(tmp_path)
    rec = registry["f1"]
    assert rec["rights_status"] == "approved"
    # The stored grant strictly validates through the base slice.
    from cba_kb.drive_discovery import decide_drive_rights
    status, _ = decide_drive_rights({"file_id": "f1"},
                                    grants={"f1": rec["rights_grant"]})
    assert status == "approved"
    # The audit event is in the ledger.
    events = [json.loads(line) for line in
              ledger_path.read_text(encoding="utf-8").splitlines()]
    grants = [e for e in events if e["action"] == "drive_rights_granted"]
    assert len(grants) == 1
    assert grants[0]["operator"] == "op-1"
    assert grants[0]["evidence_ref"] == "f1"


def test_grant_rights_ledger_failure_leaves_registry_untouched(tmp_path):
    """Q2 finding 2 repro: point the ledger at a directory (real
    IsADirectoryError). The registry must stay in review; a retry with a
    working ledger completes the grant and backfills the audit event."""
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, {"f1": _rec("f1", "Shanghai Falcons x")})
    ledger_dir = tmp_path / "ledger_dir"
    ledger_dir.mkdir()
    with pytest.raises(DriveDiscoveryError) as exc:
        grant_rights(registry_path, ledger_dir, "f1", approver="op-1")
    assert exc.value.code == LEDGER_WRITE_FAILED
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["f1"]["rights_status"] == "review"
    assert "rights_grant" not in registry["f1"]
    # Retry works and writes the audit event (no phantom approval).
    ledger_path = tmp_path / "ledger.jsonl"
    result = grant_rights(registry_path, ledger_path, "f1", approver="op-1")
    assert result["rights_status"] == "approved"
    events = [json.loads(line) for line in
              ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in events] == ["drive_rights_granted"]


def test_grant_rights_heals_stale_approval_with_audit(tmp_path):
    """Pre-fix records (approved status, incomplete grant) are healed:
    re-grant writes the missing audit event instead of idempotent silence."""
    registry_path = tmp_path / "registry.json"
    ledger_path = tmp_path / "ledger.jsonl"
    rec = _rec("f1", "Shanghai Falcons x", rights="approved")
    rec["rights_reason"] = "DRIVE_RIGHTS_GRANTED"
    rec["rights_grant"] = {"approver": "op", "note": "", "ts": "t"}  # old format
    _write_registry(registry_path, {"f1": rec})
    result = grant_rights(registry_path, ledger_path, "f1", approver="op-2")
    assert result["rights_status"] == "approved"
    events = [json.loads(line) for line in
              ledger_path.read_text(encoding="utf-8").splitlines()]
    assert len([e for e in events if e["action"] == "drive_rights_granted"]) == 1
    # Now strictly valid: searchable.
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    out = search_drive_registry(registry, "Falcons")
    assert [r["file_id"] for r in out["results"]] == ["f1"]


def test_grant_rights_unknown_file_fails_closed(tmp_path):
    registry_path = _write_registry(tmp_path / "registry.json", {})
    with pytest.raises(ValueError, match="RIGHTS_GRANT_UNKNOWN_FILE"):
        grant_rights(registry_path, tmp_path / "l.jsonl", "nope",
                     approver="op")


def test_grant_rights_requires_approver(tmp_path):
    registry_path = _write_registry(
        tmp_path / "registry.json", {"f1": _rec("f1", "Shanghai Falcons x")})
    with pytest.raises(ValueError, match="RIGHTS_GRANT_APPROVER_REQUIRED"):
        grant_rights(registry_path, tmp_path / "l.jsonl", "f1", approver="  ")


def test_grant_rights_idempotent_when_grant_valid(tmp_path):
    registry, ledger_path = _approved_via_grant(tmp_path)
    registry_path = tmp_path / "registry.json"
    result = grant_rights(registry_path, ledger_path, "f1", approver="op-1")
    assert result["note"] == "already approved; idempotent"
    events = [json.loads(line) for line in
              ledger_path.read_text(encoding="utf-8").splitlines()]
    assert len([e for e in events if e["action"] == "drive_rights_granted"]) == 1


# ------------------------------------------------- content identity helpers

def test_content_dedup_across_file_ids():
    index = {}
    cls, canon = classify_content_duplicate(index, "f1", "md5", "abc")
    assert (cls, canon) == (CONTENT_NEW, "f1")
    index[content_key("md5", "abc")] = "f1"
    cls, canon = classify_content_duplicate(index, "f2", "md5", "abc")
    assert (cls, canon) == (DUPLICATE_CONTENT, "f1")


def test_undigested_objects_are_never_silently_unique():
    cls, canon = classify_content_duplicate({}, "f1", None, None)
    assert (cls, canon) == (UNDEDUPLICABLE, None)
    assert content_key(None, "abc") is None
    assert content_key("md5", None) is None


def test_revision_chain_appends_without_duplicates():
    history = {}
    chain = record_revision(history, "s1", "r1", "doc_a")
    assert len(chain) == 1
    chain = record_revision(history, "s1", "r1", "doc_a")
    assert len(chain) == 1  # idempotent
    chain = record_revision(history, "s1", "r2", "doc_b")
    assert [e["revision"] for e in chain] == ["r1", "r2"]


def test_revision_identity_conflict_fails_closed():
    """Q2 finding 3: same (source, revision) remapped to another doc_id
    raises instead of silently keeping the old record."""
    history = {}
    record_revision(history, "s1", "r1", "doc_a")
    with pytest.raises(ValueError, match="REVISION_IDENTITY_CONFLICT"):
        record_revision(history, "s1", "r1", "doc_b")
    # The old record is intact; the conflict was not applied.
    assert history["s1"][0]["doc_id"] == "doc_a"
    assert len(history["s1"]) == 1


def test_load_registry_fails_closed_on_corruption(tmp_path):
    bad = tmp_path / "registry.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="REGISTRY_CORRUPT"):
        load_registry(bad)
    with pytest.raises(ValueError, match="REGISTRY_MISSING"):
        load_registry(tmp_path / "nope.json")
