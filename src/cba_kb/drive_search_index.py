"""Persistent rights-aware keyword search index (2.0.4-2 slice 5, T2).

An inverted keyword index over (file_id, title, body-text) built from the
discovery registry. Rights-aware: only documents with a strictly valid
``approved`` grant are visible.

AUTHORITATIVE SOURCE: there is exactly ONE grant validator in this
codebase — ``drive_search._rights_approved_for_search`` (which calls
``drive_discovery.decide_drive_rights``: exact scope, file binding,
revocation, expiry). This module MUST NOT invent a second, weaker rule.
``build_index`` and ``search_index`` both delegate to it, using the
registry's outer file_id as the object identity (never the grant's
self-claimed file_id).

Two-phase visibility — BOTH required:
- Build time: ``visible=True`` is derived ONLY from the strict validator.
- Query time: the CURRENT authoritative registry is re-checked per hit.
  ``query_result_visible = index_allows AND current_registry_grant_valid``.
  A stale ``visible=True`` in the index cannot survive a revoked/expired
  grant in the registry; a ``visible=False`` in the index cannot be
  overridden by the registry. A missing/corrupt registry fails closed —
  the query never silently falls back to index-only.

Design notes:
- The index stores terms -> file_ids and minimal per-doc metadata (title,
  rights_status, visible). It does NOT store document body text.
  Note: indexed keywords are derived from document text, so term presence
  is inherently visible in the index file (residual risk, documented).
- ``principal`` is a required query parameter for audit and forward
  compatibility; current enforcement is document-level approval.
  Per-reader ACLs are future work when the data model supports them.
- Persistence is atomic JSON, versioned. A corrupt index fails closed
  (refuses to load); rebuild from the registry is deterministic.
- Tokenization is simple lowercase word splitting.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .common import atomic
from .drive_search import _rights_approved_for_search

INDEX_CORRUPT = "SEARCH_INDEX_CORRUPT"
INDEX_VERSION = 1

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens; empty input yields []."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def build_index(
    registry: Dict[str, Dict[str, Any]],
    texts: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a deterministic rights-aware index from a registry.

    ``registry``: file_id -> record (with name/title, rights_status,
    rights_grant). The OUTER dict key is the object identity used for
    grant validation — never the grant's self-claimed file_id.
    ``texts``: file_id -> body text (optional; title is always indexed).

    ``visible`` is derived SOLELY from
    ``drive_search._rights_approved_for_search`` (strict: status+grant
    must agree, exact scope, file binding, revocation, expiry). There is
    no second validation rule in this module.

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
        ok, _ = _rights_approved_for_search(file_id, record)
        docs[file_id] = {
            "title": title,
            "rights_status": record.get("rights_status"),
            "visible": bool(ok),
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


def _index_allows(doc_meta: Dict[str, Any]) -> bool:
    """Build-time gate: BOTH rights_status and the visible flag must hold.

    Defense in depth against a tampered index file: flipping only one
    field cannot make a private/review document visible.
    """
    if doc_meta.get("rights_status") != "approved":
        return False
    return doc_meta.get("visible") is True


def search_index(
    index: Dict[str, Any],
    query: str,
    principal: str,
    registry: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    """Rights-filtered keyword search with two-phase validation.

    ``registry`` (the CURRENT authoritative registry) is REQUIRED. For
    each candidate hit:
      visible = index_allows(file) AND registry_grant_valid(file)
    where ``registry_grant_valid`` is
    ``drive_search._rights_approved_for_search`` on the live record.

    A ``visible=False`` in the index is never overridden by the registry.
    A missing/corrupt registry, a missing record, or a failed re-check
    excludes the hit — the query never silently falls back to index-only.

    Returns {"results": [{file_id, title, matched_terms}]}. No exclusion
    counts are reported (no keyword-existence side channel).
    """
    if not query or not query.strip():
        raise ValueError("SEARCH_QUERY_REQUIRED")
    if limit <= 0:
        raise ValueError("SEARCH_LIMIT_POSITIVE_REQUIRED")
    if not principal:
        raise ValueError("SEARCH_PRINCIPAL_REQUIRED")
    if not isinstance(registry, dict):
        raise ValueError("SEARCH_REGISTRY_REQUIRED")

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
        # Phase 1: index build-time gate (never overridden upward).
        if not _index_allows(meta):
            continue
        # Phase 2: current authoritative registry re-check.
        record = registry.get(fid)
        if not isinstance(record, dict):
            continue
        ok, _ = _rights_approved_for_search(fid, record)
        if not ok:
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
    """Revoke a document's visibility in the index (isolation).

    Sets the doc's ``visible`` flag to False. Returns True if the doc was
    found and visible. Memory-only — call ``save_index`` to persist, or
    use ``revoke_grant_persistent`` for an atomic load-revoke-save.

    NOTE: this revokes the INDEX snapshot only. It does NOT update the
    authoritative registry or write a ledger event. A rebuild from an
    unrevoked registry will restore visibility. Authoritative revocation
    belongs to the registry/grant flow; this is a best-effort index
    hygiene operation.
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
    still shows the revoked document. Same caveat as ``revoke_grant``:
    this does not touch the authoritative registry.
    """
    index = load_index(index_path)
    changed = revoke_grant(index, file_id, principal)
    if changed:
        save_index(index_path, index)
    return changed
