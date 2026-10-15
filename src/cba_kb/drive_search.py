"""Guarded Drive search + content-identity helpers (2.0.4-2 slice 2, T2).

Builds on slice-1 (``drive_discovery``): the discovery registry is the
searchable corpus. Search is rights-guarded by construction:

- Only ``ACCESS_VERIFIED`` objects whose rights grant strictly validates
  (registry status AND the base slice's ``decide_drive_rights`` must agree)
  are ever returned as *results*.
- ``REVIEW_REQUIRED`` / ``VERIFY_FAILED`` objects are excluded by default;
  with an explicit ``include_review`` flag they are *listed* with exact
  reasons — never returned as searchable content.
- Rights are granted per file by an explicit, ledger-recorded operator
  action (``grant_rights``), ledger-first so a failed audit write never
  leaves a phantom approval; there is no automatic approval path.

Content identity: ``classify_content_duplicate`` and ``record_revision``
are available, unit-tested helpers. They are not yet wired into the
automated discovery path (slice-1's frozen registry does not persist
digests), so production search does not currently perform cross-file
content dedup — same digest is not treated as proof of duplication, and no
byte-level collision check is performed. Wiring a persistent digest index
is future work, tracked separately.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .drive_discovery import (
    ACCESS_VERIFIED,
    LEDGER_REQUIRED_FIELDS,  # noqa: F401  (re-exported for ledger writers)
    LEDGER_WRITE_FAILED,
    REVIEW_REQUIRED,
    VERIFY_FAILED,
    DriveDiscoveryError,
    append_ledger,
    decide_drive_rights,
)

CONTENT_NEW = "CONTENT_NEW"
DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
UNDEDUPLICABLE = "UNDEDUPLICABLE"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry(registry_path: Any) -> Dict[str, Dict[str, Any]]:
    """Load a discovery registry. Fail-closed on corrupt JSON.

    Raises ValueError(REGISTRY_CORRUPT_*) instead of returning partial data.
    """
    path = Path(registry_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"REGISTRY_MISSING: {path}") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"REGISTRY_CORRUPT: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"REGISTRY_CORRUPT: {path}: top-level JSON object required")
    return data


def content_key(digest_kind: Optional[str], digest: Optional[str]) -> Optional[str]:
    """Stable content key, or None when no verified digest exists."""
    if not digest_kind or not digest:
        return None
    return f"{digest_kind}:{digest}"


def classify_content_duplicate(
    digest_index: Dict[str, str],
    file_id: str,
    digest_kind: Optional[str],
    digest: Optional[str],
) -> Tuple[str, Optional[str]]:
    """Classify one object's content against the digest index.

    Returns (classification, canonical_file_id):
    - CONTENT_NEW: first file_id seen with this digest (index updated by caller)
    - DUPLICATE_CONTENT: same digest already seen under another file_id
    - UNDEDUPLICABLE: no verified digest; never silently treated as unique
    """
    key = content_key(digest_kind, digest)
    if key is None:
        return UNDEDUPLICABLE, None
    canonical = digest_index.get(key)
    if canonical is None or canonical == file_id:
        return CONTENT_NEW, file_id
    return DUPLICATE_CONTENT, canonical


def _rights_approved_for_search(
    file_id: str, record: Dict[str, Any]
) -> Tuple[bool, str]:
    """Strict rights check for the search path: the registry status string
    AND a strictly-valid grant must agree.

    The grant is re-validated through the base slice's
    ``decide_drive_rights`` (exact scope matching, approver/file binding,
    expiry, revocation). Any inconsistency — missing grant, invalid grant,
    or status/grant disagreement — fails closed to (False, reason) and the
    object is excluded from results. Never trusts ``rights_status`` alone.
    """
    if record.get("rights_status") != "approved":
        return False, record.get("rights_reason") or "RIGHTS_NOT_APPROVED"
    grant = record.get("rights_grant")
    grants = {file_id: grant} if isinstance(grant, dict) else {}
    status, reason = decide_drive_rights({"file_id": file_id}, grants=grants)
    if status != "approved":
        return False, f"RIGHTS_GRANT_INVALID:{reason}"
    return True, "DRIVE_RIGHTS_GRANTED"


def record_revision(
    history: Dict[str, List[Dict[str, Any]]],
    source_id: str,
    revision: Any,
    doc_id: str,
    ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Append to a source's revision chain; same revision is never duplicated.

    Fail-closed on identity conflict: if ``(source_id, revision)`` already
    maps to a *different* ``doc_id``, the mapping is unstable and a
    ``ValueError(REVISION_IDENTITY_CONFLICT)`` is raised instead of silently
    keeping the old record. Repeating the same ``(revision, doc_id)`` is
    idempotent.

    Returns the (possibly new) chain for ``source_id``.
    """
    chain = history.setdefault(source_id, [])
    for entry in chain:
        if entry.get("revision") == revision:
            if entry.get("doc_id") != doc_id:
                raise ValueError(
                    f"REVISION_IDENTITY_CONFLICT: source {source_id!r} "
                    f"revision {revision!r} already maps to "
                    f"{entry.get('doc_id')!r}, cannot remap to {doc_id!r}")
            return chain
    chain.append({"revision": revision, "doc_id": doc_id,
                  "ts": ts or _utcnow()})
    return chain


def grant_rights(
    registry_path: Any,
    ledger_path: Any,
    file_id: str,
    *,
    approver: str,
    note: str = "",
    actor: str = "muse",
    policy_version: str = "2.0.4-2-slice2",
) -> Dict[str, Any]:
    """Explicitly grant rights for one discovered file (guarded, audited).

    Fail-closed: unknown file_id raises ValueError; only objects currently in
    ``review`` rights state can transition. The generated grant is a complete
    strict-schema record (``approved``/``approver``/``file_id``/``scope``) so
    it passes the base slice's ``decide_drive_rights``.

    Ordering (audit-first): the ledger event is appended BEFORE the registry
    is persisted. If the ledger write fails, a ``DriveDiscoveryError`` is
    raised and the registry is untouched — a retry can then complete the
    grant instead of hitting a phantom "already approved".

    Already-approved is idempotent only when the stored grant strictly
    validates; a stale/incomplete grant (e.g. from the pre-fix order) is
    healed by re-running the grant flow, which backfills the audit event.
    """
    from .common import atomic

    registry_file = Path(registry_path)
    registry = load_registry(registry_file)
    record = registry.get(file_id)
    if record is None:
        raise ValueError(f"RIGHTS_GRANT_UNKNOWN_FILE: {file_id}")
    if not approver or not approver.strip():
        raise ValueError("RIGHTS_GRANT_APPROVER_REQUIRED")
    prev = record.get("rights_status")
    if prev == "approved":
        ok, _ = _rights_approved_for_search(file_id, record)
        if ok:
            return {"file_id": file_id, "rights_status": "approved",
                    "note": "already approved; idempotent"}
        # Stale/incomplete grant: heal by re-running the grant flow below,
        # which writes the missing audit event.
    elif prev != "review":
        raise ValueError(f"RIGHTS_GRANT_BAD_STATE: {file_id} rights_status={prev}")

    grant = {
        "approved": True,
        "approver": approver.strip(),
        "file_id": file_id,
        "scope": "drive:read",
        "granted_at": _utcnow(),
        "note": note,
    }
    event_ts = grant["granted_at"]
    try:
        append_ledger(ledger_path, {
            "ts": event_ts, "actor": actor, "action": "drive_rights_granted",
            "prev_state": prev, "new_state": "approved",
            "evidence_ref": file_id, "policy_version": policy_version,
            "operator": approver.strip(), "approval_ref": "", "retry_count": 0,
            "note": note or f"rights granted by {approver.strip()}",
            "grant": dict(grant),
        })
    except Exception as exc:
        raise DriveDiscoveryError(
            LEDGER_WRITE_FAILED, file_id=file_id,
            detail=f"rights grant ledger append failed: {exc}") from exc

    record["rights_status"] = "approved"
    record["rights_reason"] = "DRIVE_RIGHTS_GRANTED"
    record["rights_grant"] = grant
    atomic(registry_file, json.dumps(registry, ensure_ascii=False, indent=2,
                                    sort_keys=True).encode("utf-8"))
    return {"file_id": file_id, "rights_status": "approved",
            "approver": approver.strip()}


def _matches(record: Dict[str, Any], query: str,
             mime_prefix: Optional[str]) -> Optional[str]:
    name = (record.get("name") or "")
    if query.lower() in name.lower():
        hit = "name"
    else:
        return None
    if mime_prefix and not (record.get("mime_type") or "").startswith(mime_prefix):
        return None
    return hit


def search_drive_registry(
    registry: Dict[str, Dict[str, Any]],
    query: str,
    *,
    include_review: bool = False,
    mime_prefix: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Rights-guarded search over a discovery registry.

    Results contain ONLY ACCESS_VERIFIED objects whose rights grant strictly
    validates (registry status string AND ``decide_drive_rights`` must agree;
    see ``_rights_approved_for_search``). ``excluded`` counts each category
    and ``excluded.details`` names the exact reason per withheld object, so
    two different failures are never indistinguishable.
    With ``include_review=True``, withheld review/failed items are listed
    with their reasons — listed, never returned as searchable content.
    """
    if not query or not query.strip():
        raise ValueError("SEARCH_QUERY_REQUIRED")
    if limit <= 0:
        raise ValueError("SEARCH_LIMIT_POSITIVE_REQUIRED")

    results: List[Dict[str, Any]] = []
    review_listing: List[Dict[str, Any]] = []
    excluded = {"rights_excluded": 0, "failed_excluded": 0,
                "review_excluded": 0, "no_match": 0,
                "details": []}
    considered = 0
    for file_id, record in registry.items():
        considered += 1
        match_on = _matches(record, query.strip(), mime_prefix)
        if match_on is None:
            excluded["no_match"] += 1
            continue
        verify_status = record.get("verify_status")
        verify_reason = record.get("verify_reason")
        name = record.get("name")
        if verify_status == VERIFY_FAILED:
            excluded["failed_excluded"] += 1
            reason = f"VERIFY_FAILED:{verify_reason or 'UNKNOWN'}"
            excluded["details"].append(
                {"file_id": file_id, "name": name, "reason": reason})
            if include_review:
                review_listing.append({
                    "file_id": file_id, "name": name,
                    "verify_status": verify_status,
                    "verify_reason": verify_reason,
                    "note": "listed, not searchable: verification failed",
                })
            continue
        rights_ok, rights_reason = _rights_approved_for_search(file_id, record)
        if not rights_ok:
            excluded["rights_excluded"] += 1
            excluded["details"].append(
                {"file_id": file_id, "name": name,
                 "reason": f"RIGHTS_EXCLUDED:{rights_reason}"})
            continue
        if verify_status != ACCESS_VERIFIED:
            # REVIEW_REQUIRED (or unknown): withheld from results by default.
            excluded["review_excluded"] += 1
            excluded["details"].append(
                {"file_id": file_id, "name": name,
                 "reason": f"REVIEW_WITHHELD:{verify_reason or 'UNKNOWN'}"})
            if include_review:
                review_listing.append({
                    "file_id": file_id, "name": name,
                    "verify_status": verify_status,
                    "verify_reason": verify_reason,
                    "note": "listed, not searchable: verification not complete",
                })
            continue
        if len(results) < limit:
            results.append({
                "file_id": file_id, "name": name,
                "mime_type": record.get("mime_type"),
                "doc_id": record.get("doc_id"),
                "source_id": record.get("source_id"),
                "verify_status": verify_status,
                "rights_status": "approved",
                "match_on": match_on,
            })
    return {
        "query": query.strip(),
        "results": results,
        "review_listing": review_listing,
        "excluded": excluded,
        "total_considered": considered,
    }
