"""Persistent content identity: dedup index + revision chains (2.0.4-2 slice 4, T2).

Two persistent stores that survive restarts, wired into the discovery and
poll paths:

- **Dedup index**: ``digest_key -> {canonical_file_id, file_ids[]}``.
  When a discovered file carries a verified digest already seen under a
  different file_id, it is marked ``DUPLICATE_CONTENT`` (the first-seen
  file stays canonical). Objects without a verified digest are never
  silently treated as unique — they are ``UNDEDUPLICABLE``.

- **Revision history**: ``source_id -> [{revision, doc_id, ts}]``.
  Appends are idempotent for the same (revision, doc_id); a conflicting
  doc_id for the same (source_id, revision) raises
  ``REVISION_IDENTITY_CONFLICT`` (fail-closed) instead of silently keeping
  the old mapping.

Both stores are JSON files persisted atomically. Corrupt files fail closed
(refuse to load) instead of guessing. Ledger events record dedup decisions
and revision links for audit.

Digest trust: dedup only applies to verified digests (metadata-claimed md5
for byte files without --verify-bytes, SHA-256 over canonical Doc JSON for
native docs, or byte-verified digests). An UNVERIFIED digest never marks
another file as duplicate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import atomic
from .drive_discovery import (
    ACCESS_VERIFIED,
    DriveDiscoveryError,
    append_ledger,
)

IDENTITY_CORRUPT = "IDENTITY_STORE_CORRUPT"
REVISION_IDENTITY_CONFLICT = "REVISION_IDENTITY_CONFLICT"

STORE_VERSION = 1
MAX_HISTORY_PER_SOURCE = 1000


# ---------------------------------------------------------------- stores

def _load_store(path: Path, kind: str) -> Dict[str, Any]:
    """Load a JSON identity store, fail-closed on corruption."""
    if not path.exists():
        return {"version": STORE_VERSION, "kind": kind, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{IDENTITY_CORRUPT}: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        raise ValueError(f"{IDENTITY_CORRUPT}: {path}: bad version/shape")
    if not isinstance(data.get("entries"), dict):
        raise ValueError(f"{IDENTITY_CORRUPT}: {path}: entries not a dict")
    return data


def _save_store(path: Path, data: Dict[str, Any]) -> None:
    data = dict(data, version=STORE_VERSION)
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2,
                            sort_keys=True).encode("utf-8"))


def load_dedup_index(path: Any) -> Dict[str, Any]:
    """Load the persistent dedup index (creates empty if missing)."""
    return _load_store(Path(path), "dedup_index")


def save_dedup_index(path: Any, index: Dict[str, Any]) -> None:
    """Persist the dedup index atomically."""
    _save_store(Path(path), index)


def load_revision_history(path: Any) -> Dict[str, Any]:
    """Load the persistent revision history (creates empty if missing)."""
    return _load_store(Path(path), "revision_history")


def save_revision_history(path: Any, history: Dict[str, Any]) -> None:
    """Persist the revision history atomically."""
    _save_store(Path(path), history)


# ---------------------------------------------------------------- dedup

def content_key(digest_kind: Optional[str],
                digest: Optional[str]) -> Optional[str]:
    """Stable key for the dedup index; None when undeduplicable."""
    if not digest_kind or not digest:
        return None
    return f"{digest_kind}:{digest}"


def check_duplicate(
    index: Dict[str, Any],
    file_id: str,
    digest_kind: Optional[str],
    digest: Optional[str],
) -> Tuple[str, Optional[str]]:
    """Check one file against the dedup index.

    Returns (classification, canonical_file_id):
    - ("DUPLICATE_CONTENT", canonical) if the digest is known under another file.
    - ("CONTENT_NEW", file_id) if the digest is new (caller should record it).
    - ("UNDEDUPLICABLE", None) if there is no verified digest.
    - ("CONTENT_KNOWN", file_id) if this file_id is already the canonical one
      (re-delivery; idempotent).
    """
    key = content_key(digest_kind, digest)
    if key is None:
        return "UNDEDUPLICABLE", None
    entries = index["entries"]
    entry = entries.get(key)
    if entry is None:
        return "CONTENT_NEW", file_id
    canonical = entry.get("canonical_file_id")
    if canonical == file_id:
        return "CONTENT_KNOWN", file_id
    return "DUPLICATE_CONTENT", canonical


def record_digest(
    index: Dict[str, Any],
    file_id: str,
    digest_kind: str,
    digest: str,
    ts: str,
) -> str:
    """Record a file's digest in the index. Returns the canonical file_id.

    The first file_id seen for a digest becomes canonical; later files with
    the same digest are recorded as aliases but do not displace it.
    """
    key = content_key(digest_kind, digest)
    if key is None:
        raise ValueError("IDENTITY_DIGEST_REQUIRED")
    entries = index["entries"]
    entry = entries.get(key)
    if entry is None:
        entries[key] = {
            "canonical_file_id": file_id,
            "file_ids": [file_id],
            "digest_kind": digest_kind,
            "digest": digest,
            "first_seen_ts": ts,
        }
        return file_id
    if file_id not in entry["file_ids"]:
        entry["file_ids"].append(file_id)
    return entry["canonical_file_id"]


# ------------------------------------------------------------- revisions

def append_revision(
    history: Dict[str, Any],
    source_id: str,
    revision: Any,
    doc_id: str,
    ts: str,
) -> List[Dict[str, Any]]:
    """Append to a source's persistent revision chain.

    Idempotent for the same (revision, doc_id). Raises
    ``ValueError(REVISION_IDENTITY_CONFLICT)`` if the same (source_id,
    revision) already maps to a different doc_id — fail-closed, never
    silently keeps the old mapping.
    """
    entries = history["entries"]
    chain = entries.setdefault(source_id, [])
    for item in chain:
        if item.get("revision") == revision:
            if item.get("doc_id") != doc_id:
                raise ValueError(
                    f"{REVISION_IDENTITY_CONFLICT}: source {source_id!r} "
                    f"revision {revision!r} already maps to "
                    f"{item.get('doc_id')!r}, cannot remap to {doc_id!r}")
            return chain
    chain.append({"revision": revision, "doc_id": doc_id, "ts": ts})
    if len(chain) > MAX_HISTORY_PER_SOURCE:
        del chain[:-MAX_HISTORY_PER_SOURCE]
    return chain


# ---------------------------------------------------------------- wiring

def wire_identity(
    *,
    dedup_index: Dict[str, Any],
    revision_history: Dict[str, Any],
    file_id: str,
    source_id: str,
    doc_id: str,
    revision: Any,
    digest_kind: Optional[str],
    digest: Optional[str],
    verify_status: str,
    ts: str,
    ledger_path: Any = None,
    actor: str = "muse",
    policy_version: str = "2.0.4-2-slice4",
) -> Dict[str, Any]:
    """Wire one discovered file into the identity stores.

    Returns a dict with:
    - ``dedup``: DUPLICATE_CONTENT | CONTENT_NEW | CONTENT_KNOWN | UNDEDUPLICABLE
    - ``canonical_file_id``: set for duplicates
    - ``revision_chain``: the source's chain after append

    Only ACCESS_VERIFIED files participate in dedup (unverified digests are
    not trustworthy). Revision history is updated for all files with a
    source_id. Emits ledger events when ``ledger_path`` is given.
    """
    result: Dict[str, Any] = {
        "dedup": "UNDEDUPLICABLE",
        "canonical_file_id": None,
        "revision_chain": [],
    }
    # Revision history first (fail-closed on conflict aborts before dedup).
    if source_id:
        chain = append_revision(revision_history, source_id, revision,
                                doc_id, ts)
        result["revision_chain"] = chain
        if ledger_path is not None and len(chain) > 1:
            append_ledger(ledger_path, {
                "ts": ts, "actor": actor,
                "action": "drive_identity_revision_linked",
                "prev_state": "DISCOVERED", "new_state": "REVISION_LINKED",
                "evidence_ref": file_id, "policy_version": policy_version,
                "operator": actor, "approval_ref": "", "retry_count": 0,
                "note": f"source {source_id} revision {revision} -> {doc_id}",
                "source_id": source_id, "revision": revision,
                "doc_id": doc_id,
            })
    # Dedup only on verified digests.
    if verify_status == ACCESS_VERIFIED:
        dedup, canonical = check_duplicate(dedup_index, file_id,
                                           digest_kind, digest)
        if dedup == "CONTENT_NEW" and digest_kind and digest:
            record_digest(dedup_index, file_id, digest_kind, digest, ts)
        result["dedup"] = dedup
        result["canonical_file_id"] = canonical
        if dedup == "DUPLICATE_CONTENT" and ledger_path is not None:
            append_ledger(ledger_path, {
                "ts": ts, "actor": actor,
                "action": "drive_identity_duplicate",
                "prev_state": "DISCOVERED", "new_state": "DUPLICATE_CONTENT",
                "evidence_ref": file_id, "policy_version": policy_version,
                "operator": actor, "approval_ref": "", "retry_count": 0,
                "note": f"duplicate of {canonical} (digest {digest_kind})",
                "canonical_file_id": canonical,
                "digest_kind": digest_kind,
            })
    return result
