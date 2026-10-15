"""Persistent rights-aware keyword search index (2.0.4-2 slice 5, T2).

An inverted keyword index over (file_id, title, body-text) built from the
discovery registry. Rights-aware at query time: a principal only sees
documents they hold an explicit grant for. A revoked grant removes the
document from that principal's view (isolation); the index itself is
rights-agnostic storage, filtering happens per query.

Design notes:
- The index stores terms -> file_ids and minimal per-doc metadata (title,
  rights_status, grantees). It does NOT store document body text or
  snippets — the leak surface of the index file is limited to terms and
  titles. Full content is fetched from the registry/document store after
  a rights-filtered match.
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

    ``registry``: file_id -> record (must have name/title, rights_status,
    and optionally rights_grant with grantee).
    ``texts``: file_id -> body text (optional; title is always indexed).

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
        grant = record.get("rights_grant") or {}
        grantee = grant.get("grantee")
        docs[file_id] = {
            "title": title,
            "rights_status": record.get("rights_status"),
            "grantees": [grantee] if grantee else [],
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

    Validates version, kind, and the structure of terms/docs. A
    semantically corrupt index (e.g. terms mapping to non-lists, docs
    missing rights fields) is rejected, never silently searched.
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
        if not isinstance(meta.get("grantees"), list):
            raise ValueError(f"{INDEX_CORRUPT}: {p}: doc {fid!r} "
                             f"missing grantees")
    return data


def _visible_to(doc_meta: Dict[str, Any], principal: str) -> bool:
    """A principal sees a doc only with an explicit grant.

    `granted` status alone is not enough — the principal must be listed in
    `grantees`. `private`/`review`/missing grants are invisible. (Public
    documents are out of scope for 2.0.4-2 and never indexed as visible.)
    """
    if not principal:
        return False
    return principal in (doc_meta.get("grantees") or [])


def search_index(
    index: Dict[str, Any],
    query: str,
    principal: str,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    """Rights-filtered keyword search.

    Returns {"results": [{file_id, title, matched_terms}], "excluded": n}.
    Only documents visible to `principal` are returned; matches the
    principal cannot see are counted in `excluded`, never listed.
    """
    if not query or not query.strip():
        raise ValueError("SEARCH_QUERY_REQUIRED")
    if limit <= 0:
        raise ValueError("SEARCH_LIMIT_POSITIVE_REQUIRED")
    if not principal:
        raise ValueError("SEARCH_PRINCIPAL_REQUIRED")

    query_terms = set(tokenize(query))
    if not query_terms:
        return {"results": [], "excluded": 0}

    terms = index.get("terms", {})
    docs = index.get("docs", {})
    # Union of file_ids matching any query term.
    matched: Set[str] = set()
    for qt in query_terms:
        matched.update(terms.get(qt, []))

    results = []
    excluded = 0
    for fid in sorted(matched):
        meta = docs.get(fid)
        if meta is None:
            excluded += 1
            continue
        if not _visible_to(meta, principal):
            excluded += 1
            continue
        doc_terms = {t for t, fids in terms.items() if fid in fids}
        results.append({
            "file_id": fid,
            "title": meta.get("title", ""),
            "matched_terms": sorted(query_terms & doc_terms),
        })
        if len(results) >= limit:
            break
    return {"results": results, "excluded": excluded}


def revoke_grant(index: Dict[str, Any], file_id: str,
                 principal: str) -> bool:
    """Remove a principal from a doc's grantees (isolation).

    Returns True if the principal was removed. The doc stays in the index
    for other grantees; the revoked principal immediately loses visibility.
    Call `save_index` to persist.
    """
    meta = (index.get("docs") or {}).get(file_id)
    if meta is None:
        return False
    grantees = meta.get("grantees") or []
    if principal in grantees:
        meta["grantees"] = [g for g in grantees if g != principal]
        return True
    return False
