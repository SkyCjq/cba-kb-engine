"""Guarded Drive search + content-identity hardening (2.0.4-2 slice 2, T2).

Builds on slice-1 (``drive_discovery``): the discovery registry is the
searchable corpus. Search is rights-guarded by construction:

- Only ``ACCESS_VERIFIED`` + explicitly rights-``approved`` objects are ever
  returned as *results*.
- ``REVIEW_REQUIRED`` / ``VERIFY_FAILED`` objects are excluded by default and
  can only be *listed* (never returned as searchable content) with an
  explicit ``include_review`` flag.
- Rights are granted per file by an explicit, ledger-recorded operator
  action (``grant_rights``); there is no automatic approval path.

Content identity: two different Drive file_ids carrying the same verified
digest are ``DUPLICATE_CONTENT`` (the first-seen file_id is canonical).
Objects without a verified digest are ``UNDEDUPLICABLE`` — never silently
treated as unique.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .drive_discovery import (
    ACCESS_VERIFIED,
    LEDGER_REQUIRED_FIELDS,  # noqa: F401  (re-exported for ledger writers)
    REVIEW_REQUIRED,
    VERIFY_FAILED,
    append_ledger,
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


def record_revision(
    history: Dict[str, List[Dict[str, Any]]],
    source_id: str,
    revision: Any,
    doc_id: str,
    ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Append to a source's revision chain; same revision is never duplicated.

    Returns the (possibly new) chain for ``source_id``.
    """
    chain = history.setdefault(source_id, [])
    if not any(entry.get("revision") == revision for entry in chain):
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
    ``review`` rights state can transition (already-approved is idempotent).
    Every grant appends a ledger event naming the approver.
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
        return {"file_id": file_id, "rights_status": "approved",
                "note": "already approved; idempotent"}
    if prev != "review":
        raise ValueError(f"RIGHTS_GRANT_BAD_STATE: {file_id} rights_status={prev}")
    record["rights_status"] = "approved"
    record["rights_reason"] = "DRIVE_RIGHTS_GRANTED"
    record["rights_grant"] = {"approver": approver.strip(), "note": note,
                              "ts": _utcnow()}
    atomic(registry_file, json.dumps(registry, ensure_ascii=False, indent=2,
                                    sort_keys=True).encode("utf-8"))
    append_ledger(ledger_path, {
        "ts": _utcnow(), "actor": actor, "action": "drive_rights_granted",
        "prev_state": "review", "new_state": "approved",
        "evidence_ref": file_id, "policy_version": policy_version,
        "operator": approver.strip(), "approval_ref": "", "retry_count": 0,
        "note": note or f"rights granted by {approver.strip()}",
    })
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

    Results contain ONLY ACCESS_VERIFIED + rights-approved objects.
    ``excluded`` counts name exactly why each non-result was withheld.
    With ``include_review=True`` a separate ``review_listing`` names the
    withheld review items explicitly — they are listed, never returned as
    searchable content.
    """
    if not query or not query.strip():
        raise ValueError("SEARCH_QUERY_REQUIRED")
    if limit <= 0:
        raise ValueError("SEARCH_LIMIT_POSITIVE_REQUIRED")

    results: List[Dict[str, Any]] = []
    review_listing: List[Dict[str, Any]] = []
    excluded = {"rights_excluded": 0, "failed_excluded": 0,
                "review_excluded": 0, "no_match": 0}
    considered = 0
    for file_id, record in registry.items():
        considered += 1
        match_on = _matches(record, query.strip(), mime_prefix)
        if match_on is None:
            excluded["no_match"] += 1
            continue
        verify_status = record.get("verify_status")
        rights_status = record.get("rights_status")
        if verify_status == VERIFY_FAILED:
            excluded["failed_excluded"] += 1
            continue
        if rights_status != "approved":
            excluded["rights_excluded"] += 1
            continue
        if verify_status != ACCESS_VERIFIED:
            # REVIEW_REQUIRED (or unknown): withheld from results by default.
            excluded["review_excluded"] += 1
            if include_review:
                review_listing.append({
                    "file_id": file_id, "name": record.get("name"),
                    "verify_status": verify_status,
                    "verify_reason": record.get("verify_reason"),
                    "note": "listed, not searchable: verification not complete",
                })
            continue
        if len(results) < limit:
            results.append({
                "file_id": file_id, "name": record.get("name"),
                "mime_type": record.get("mime_type"),
                "doc_id": record.get("doc_id"),
                "source_id": record.get("source_id"),
                "verify_status": verify_status,
                "rights_status": rights_status,
                "match_on": match_on,
            })
    return {
        "query": query.strip(),
        "results": results,
        "review_listing": review_listing,
        "excluded": excluded,
        "total_considered": considered,
    }
