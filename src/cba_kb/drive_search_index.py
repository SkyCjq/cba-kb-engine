"""Persistent rights-aware keyword search index (2.0.4-2 slice 5, T2).

An inverted keyword index over (file_id, title, body-text) built from the
discovery registry. Rights-aware: only documents with a valid ``approved``
grant are visible. This is document-level authorization (the current data
model has no per-reader ACLs) — ``private``/``review``/unapproved documents
are never returned, regardless of caller.

Design notes:
- The index stores terms -> file_ids and minimal per-doc metadata (title,
  rights_status). It does NOT store document body text or snippets — the
  leak surface of the index file is limited to terms and titles. Full
  content is fetched from the registry/document store after a
  rights-filtered match. Note: indexed keywords themselves are derived
  from document text; a keyword index inherently reveals term presence.
  This is a residual risk, not a leak of full content.
- ``principal`` is a required query parameter for audit and forward
  compatibility, but current enforcement is document-level approval.
  Per-reader ACLs are future work when the data model supports them.
- Persistence is atomic JSON, versioned. A corrupt index fails closed
  (refuses to load); rebuild from the registry is deterministic.
- Tokenization is simple lowercase word splitting; sufficient for the
  keyword-search contract. No stemming, no ranking beyond term overlap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .common import atomic

INDEX_CORRUPT = "SEARCH_INDEX_CORRUPT"
INDEX_VERSION = 1

# Rights statuses that are NEVER visible in search, regardless of grants.
NEVER_VISIBLE = {"private", "review"}

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens; empty input yields []."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _grant_valid(record: Dict[str, Any]) -> bool:
    """Strict grant validation aligned with drive_search.grant_rights.

    A grant is valid iff rights_status == "approved" and the stored
    rights_grant has approved=True with the required fields.
    """
    if record.get("rights_status") != "approved":
        return False
    grant = record.get("rights_grant")
    if not isinstance(grant, dict):
        return False
    if grant.get("approved") is not True:
        return False
    for field in ("approver", "file_id", "scope"):
        if not grant.get(field):
            return False
    return True


def build_index(
    registry: Dict[str, Dict[str, Any]],
    texts: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a deterministic rights-aware index from a registry.

    ``registry``: file_id -> record (with name/title, rights_status,
    rights_grant).
    ``texts``: file_id -> body text (optional; title is always indexed).

    Only ``approved`` documents with a strictly valid grant are marked
    visible. All others are indexed for term completeness but flagged
    invisible — they can never be returned.

    Returns the index dict (not yet persisted).
    """
    texts = texts or {}
    terms: Dict[str, Set[str]] = {}
    docs: Dict[str, Dict[str, Any]] = {}
    for file_id in sorted(registry.keys()):
        record = registry[file_id] or {}
        title = str(record.get("name") or record.get("title") or "")
        body = str(texts.get(file_id) or "")
        tokens = set(tokenize(title)) | set(tokenize(body))
        for token in tokens:
            terms.setdefault(token, set()).add(file_id)
        docs[file_id] = {
            "title": title,
            "rights_status": record.get("rights_status"),
            "visible": _grant_valid(record),
            "term_count": len(tokens),
        }
    return {
        "version": INDEX_VERSION,
        "kind": "search_index",
        "terms": {t: sorted(fids) for t, fids in sorted(terms.items())},
        "docs": docs,
    }


def save_index(path: Any, index: Dict[str, Any]) -> None:
    """Persist the index atomically."""
    data = dict(index, version=INDEX_VERSION, kind="search_index")
    atomic(Path(path),
           json.dumps(data, ensure_ascii=False, indent=2,
                      sort_keys=True).encode("utf-8"))


def load_index(path: Any) -> Dict[str, Any]:
    """Load the index, fail-closed on corruption.

    Validates version, kind, and the structure of terms/docs, including
    that every doc has a rights_status string and a boolean visible flag.
    A semantically corrupt index is rejected, never silently searched.
    """
    p = Path(path)
    if not p.exists():
        return {"version": INDEX_VERSION, "kind": "search_index",
                "terms": {}, "docs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{INDEX_CORRUPT}: {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
        raise ValueError(f"{INDEX_CORRUPT}: {p}: bad version/shape")
    if data.get("kind") != "search_index":
        raise ValueError(f"{INDEX_CORRUPT}: {p}: kind mismatch")
    terms = data.get("terms")
    docs = data.get("docs")
    if not isinstance(terms, dict) or not isinstance(docs, dict):
        raise ValueError(f"{INDEX_CORRUPT}: {p}: terms/docs not dicts")
    for term, fids in terms.items():
        if (not isinstance(term, str) or not isinstance(fids, list)
                or not all(isinstance(f, str) for f in fids)):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: bad term entry "
                             f"{term!r}")
    for fid, meta in docs.items():
        if not isinstance(meta, dict):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: bad doc entry "
                             f"{fid!r}")
        if not isinstance(meta.get("title"), str):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: doc {fid!r} "
                             f"missing title")
        if not isinstance(meta.get("rights_status"), str):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: doc {fid!r} "
                             f"missing/invalid rights_status")
        if not isinstance(meta.get("visible"), bool):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: doc {fid!r} "
                             f"missing/invalid visible flag")
    return data


def _visible_to(doc_meta: Dict[str, Any], principal: str) -> bool:
    """Document-level visibility: only approved docs are visible.

    ``principal`` is required for audit/forward-compatibility, but current
    enforcement is document-level. Defense in depth: BOTH rights_status
    == "approved" AND the visible flag must hold. A single tampered field
    cannot make a private/review document visible.
    """
    if not principal:
        return False
    if doc_meta.get("rights_status") != "approved":
        return False
    return doc_meta.get("visible") is True


def search_index(
    index: Dict[str, Any],
    query: str,
    principal: str,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    """Rights-filtered keyword search.

    Returns {"results": [{file_id, title, matched_terms}]}. Only approved
    documents are returned. No exclusion counts are reported — the
    response reveals nothing about non-visible matches (no keyword
    existence side channel).
    """
    if not query or not query.strip():
        raise ValueError("SEARCH_QUERY_REQUIRED")
    if limit <= 0:
        raise ValueError("SEARCH_LIMIT_POSITIVE_REQUIRED")
    if not principal:
        raise ValueError("SEARCH_PRINCIPAL_REQUIRED")

    query_terms = set(tokenize(query))
    if not query_terms:
        return {"results": []}

    terms = index.get("terms", {})
    docs = index.get("docs", {})
    matched: Set[str] = set()
    for qt in query_terms:
        matched.update(terms.get(qt, []))

    results = []
    for fid in sorted(matched):
        meta = docs.get(fid)
        if meta is None:
            continue
        if not _visible_to(meta, principal):
            continue
        doc_terms = {t for t, fids in terms.items() if fid in fids}
        results.append({
            "file_id": fid,
            "title": meta.get("title", ""),
            "matched_terms": sorted(query_terms & doc_terms),
        })
        if len(results) >= limit:
            break
    return {"results": results}


def revoke_grant(index: Dict[str, Any], file_id: str,
                 principal: str) -> bool:
    """Revoke a document's visibility (isolation).

    Sets the doc's ``visible`` flag to False. Returns True if the doc was
    found and visible. Memory-only — call ``save_index`` to persist, or
    use ``revoke_grant_persistent`` for an atomic load-revoke-save.

    Note: revocation is document-level (hides from all callers), matching
    the document-level approval model. ``principal`` is accepted for API
    compatibility and audit.
    """
    meta = (index.get("docs") or {}).get(file_id)
    if meta is None:
        return False
    if meta.get("visible") is True:
        meta["visible"] = False
        return True
    return False


def revoke_grant_persistent(index_path: Any, file_id: str,
                            principal: str) -> bool:
    """Atomically revoke a document's visibility in the persisted index.

    Loads, revokes, and saves. There is no window where the on-disk index
    still shows the revoked document.
    """
    index = load_index(index_path)
    changed = revoke_grant(index, file_id, principal)
    if changed:
        save_index(index_path, index)
    return changed
