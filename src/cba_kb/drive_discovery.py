"""Drive read-only discovery for the 2.0.4-2 remainder (slice 1: F1/F2/G1/GAT-02 groundwork).

READ-ONLY CONTRACT (hard, fail-closed): this module only ever calls
``drive.list()`` / ``drive.meta()`` / ``drive.get()`` / ``drive.document_json()``.
It never calls put/move/ensure/delete/copy or any other write path, and it
never constructs write requests. The ``drive`` parameter is duck-typed;
``cba_kb.drive`` is deliberately NOT imported here so ``googleapiclient``
stays out of unit tests.

Permission errors (HTTP 401/403, detected via ``resp.status`` exactly like
``googleapiclient`` HttpError) are fail-closed: listing aborts the whole run,
per-object verification degrades to REVIEW_REQUIRED with an explicit reason.
Nothing is ever returned partially or silently.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import atomic, digest
from .transport import reason as transport_reason

DRIVE_PROVIDER = "google_drive"
NATIVE_MIME_PREFIX = "application/vnd.google-apps."
NATIVE_DOC_MIME = "application/vnd.google-apps.document"

DEFAULT_MAX_BYTES = 64 << 20  # 64 MiB download ceiling for verify_bytes

# verify_object statuses
ACCESS_VERIFIED = "ACCESS_VERIFIED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
VERIFY_FAILED = "VERIFY_FAILED"

# verify_object reasons (stable strings, also used as ledger evidence)
OK = "OK"
ACCESS_DENIED = "ACCESS_DENIED"
NATIVE_READ_FAILED = "NATIVE_READ_FAILED"
NATIVE_EXPORT_UNVERIFIED = "NATIVE_EXPORT_UNVERIFIED"
CHECKSUM_MISSING = "CHECKSUM_MISSING"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
REVISION_DRIFT = "REVISION_DRIFT"
DOWNLOAD_TOO_LARGE = "DOWNLOAD_TOO_LARGE"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
META_FAILED = "META_FAILED"
LIST_FAILED = "LIST_FAILED"
PERMISSION_DENIED = "PERMISSION_DENIED"

LEDGER_REQUIRED_FIELDS = (
    "ts", "actor", "action", "prev_state", "new_state", "evidence_ref",
    "policy_version", "operator", "approval_ref", "retry_count", "note",
)

# classify_identity classifications
NEW = "NEW"
DUPLICATE_SAME_REVISION = "DUPLICATE_SAME_REVISION"
REVISION_LINKED = "REVISION_LINKED"


class DriveDiscoveryError(RuntimeError):
    """Fatal discovery failure. Carries a stable ``code`` and the ``file_id``.

    Supported codes: LIST_FAILED, META_FAILED, PERMISSION_DENIED,
    NATIVE_EXPORT_UNVERIFIED, NATIVE_READ_FAILED, CHECKSUM_MISMATCH,
    REVISION_DRIFT, DOWNLOAD_TOO_LARGE. Per-object problems are returned as
    ``verify_object`` status dicts instead of raising; this error is reserved
    for run-level failures (e.g. the folder listing itself cannot be read).
    """

    def __init__(self, code: str, file_id: Optional[str] = None, detail: str = "") -> None:
        self.code = code
        self.file_id = file_id
        message = f"{code} file_id={file_id}" if file_id else code
        if detail:
            message += f" {detail}"
        super().__init__(message)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_status(exc: BaseException) -> Optional[int]:
    """HTTP status from a googleapiclient-style error, else None."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    return status


def _is_permission_error(exc: BaseException) -> bool:
    return _error_status(exc) in (401, 403)


def drive_source_id(file_id: str) -> str:
    """Stable source identity bound to (provider, file_id). Never rebuilt silently."""
    return "src_" + digest(b"google_drive\0" + file_id.encode("utf-8"))[:24]


def drive_doc_id(file_id: str, revision: Any) -> str:
    """Document identity bound to (provider, file_id, revision): new revision -> new doc id."""
    return "doc_" + digest(
        b"google_drive\0" + file_id.encode("utf-8") + b"\0" + str(revision).encode("utf-8")
    )[:24]


def _normalize_item(item: Dict[str, Any], discovered_ts: str) -> Dict[str, Any]:
    return {
        "provider": DRIVE_PROVIDER,
        "file_id": item.get("id"),
        "name": item.get("name"),
        "mime_type": item.get("mimeType"),
        "parents": list(item.get("parents") or []),
        "version": item.get("version"),
        "head_revision_id": item.get("headRevisionId"),
        "md5_checksum": item.get("md5Checksum"),
        "size": item.get("size"),
        "modified_time": item.get("modifiedTime"),
        "web_view_link": item.get("webViewLink"),
        "discovered_ts": discovered_ts,
    }


def discover_folder(
    drive: Any, folder_id: str, *, max_items: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """List a Drive folder and normalize every object.

    FAIL-CLOSED: any exception from ``drive.list()`` raises
    ``DriveDiscoveryError(LIST_FAILED)``; partial results are never returned.
    Trashed items are filtered (the live query already excludes them; this is
    belt-and-braces) and counted in ``skipped_trashed`` with an explicit
    reason. ``max_items`` is a hard cap: exceeding it raises instead of
    truncating silently.
    """
    if not folder_id:
        raise DriveDiscoveryError(LIST_FAILED, file_id=None, detail="empty folder_id refused")
    started_ts = _utcnow()
    try:
        items = drive.list(folder_id)
    except DriveDiscoveryError:
        raise
    except Exception as exc:
        raise DriveDiscoveryError(
            LIST_FAILED, file_id=folder_id, detail=transport_reason(exc)
        ) from exc
    finished_ts = _utcnow()
    objects: List[Dict[str, Any]] = []
    skipped_trashed = 0
    for item in items or []:
        if item.get("trashed"):
            skipped_trashed += 1
            continue
        objects.append(_normalize_item(item, finished_ts))
        if max_items is not None and len(objects) > max_items:
            raise DriveDiscoveryError(
                LIST_FAILED, file_id=folder_id,
                detail=f"item cap max_items={max_items} exceeded; refusing silent truncation",
            )
    report = {
        "folder_id": folder_id,
        "discovered_count": len(objects),
        "skipped_trashed": skipped_trashed,
        "skipped_trashed_reason": "trashed=true filtered during discovery" if skipped_trashed else None,
        "started_ts": started_ts,
        "finished_ts": finished_ts,
    }
    return objects, report


def _revision_of(obj: Dict[str, Any]) -> Any:
    return obj.get("head_revision_id") or obj.get("version")


def verify_object(
    drive: Any,
    obj: Dict[str, Any],
    *,
    verify_bytes: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Dict[str, Any]:
    """Re-fetch meta and verify one discovered object. Never raises for
    per-object problems; always returns a status dict.

    Status is one of ACCESS_VERIFIED / REVIEW_REQUIRED / VERIFY_FAILED:
    - 401/403 on meta or download -> REVIEW_REQUIRED / ACCESS_DENIED
    - version/headRevisionId drift vs the discovery snapshot -> VERIFY_FAILED / REVISION_DRIFT
    - native Google Doc -> read via ``drive.document_json``; digest is
      sha256 of canonical JSON, digest_kind="native_doc_json"
    - other native mime -> REVIEW_REQUIRED / NATIVE_EXPORT_UNVERIFIED.
      A binary SHA is NEVER claimed for native objects.
    - binary + verify_bytes -> ``drive.get`` bytes are md5-compared against the
      meta claim; missing md5 -> REVIEW_REQUIRED / CHECKSUM_MISSING;
      mismatch -> VERIFY_FAILED / CHECKSUM_MISMATCH; oversize ->
      VERIFY_FAILED / DOWNLOAD_TOO_LARGE (never truncated silently).
    """
    file_id = obj.get("file_id")
    try:
        meta = drive.meta(file_id)
    except Exception as exc:
        if _is_permission_error(exc):
            return {
                "file_id": file_id, "status": REVIEW_REQUIRED, "reason": ACCESS_DENIED,
                "version": obj.get("version"), "head_revision_id": obj.get("head_revision_id"),
                "digest_kind": None, "digest": None,
                "evidence": {"meta_error": transport_reason(exc)},
            }
        return {
            "file_id": file_id, "status": VERIFY_FAILED, "reason": META_FAILED,
            "version": obj.get("version"), "head_revision_id": obj.get("head_revision_id"),
            "digest_kind": None, "digest": None,
            "evidence": {"meta_error": transport_reason(exc)},
        }

    version = meta.get("version")
    head_revision_id = meta.get("headRevisionId")
    if (version, head_revision_id) != (obj.get("version"), obj.get("head_revision_id")):
        return {
            "file_id": file_id, "status": VERIFY_FAILED, "reason": REVISION_DRIFT,
            "version": version, "head_revision_id": head_revision_id,
            "digest_kind": None, "digest": None,
            "evidence": {
                "discovery_version": obj.get("version"),
                "discovery_head_revision_id": obj.get("head_revision_id"),
                "meta_version": version,
                "meta_head_revision_id": head_revision_id,
            },
        }

    mime = meta.get("mimeType") or ""
    base = {
        "file_id": file_id, "version": version, "head_revision_id": head_revision_id,
        "evidence": {
            "mime_type": mime, "name": meta.get("name"),
            "parents": list(meta.get("parents") or []),
        },
    }

    if mime == NATIVE_DOC_MIME:
        try:
            doc = drive.document_json(file_id)
        except Exception as exc:
            if _is_permission_error(exc):
                return {**base, "status": REVIEW_REQUIRED, "reason": ACCESS_DENIED,
                        "digest_kind": None, "digest": None,
                        "evidence": {**base["evidence"], "native_error": transport_reason(exc)}}
            return {**base, "status": REVIEW_REQUIRED, "reason": NATIVE_READ_FAILED,
                    "digest_kind": None, "digest": None,
                    "evidence": {**base["evidence"], "native_error": transport_reason(exc)}}
        canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        return {**base, "status": ACCESS_VERIFIED, "reason": OK,
                "digest_kind": "native_doc_json", "digest": digest(canonical),
                "evidence": {**base["evidence"], "verified_bytes": False,
                             "note": "digest over canonical native-doc JSON, not raw bytes"}}

    if mime.startswith(NATIVE_MIME_PREFIX):
        return {**base, "status": REVIEW_REQUIRED, "reason": NATIVE_EXPORT_UNVERIFIED,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"],
                             "note": "no verified export/identity policy for this native type; "
                                     "binary SHA never claimed"}}

    md5_claim = meta.get("md5Checksum")
    if not verify_bytes:
        return {**base, "status": ACCESS_VERIFIED, "reason": OK,
                "digest_kind": "md5", "digest": md5_claim,
                "evidence": {**base["evidence"], "verified_bytes": False,
                             "note": "digest is the meta-claimed md5; bytes not downloaded"}}
    if not md5_claim:
        return {**base, "status": REVIEW_REQUIRED, "reason": CHECKSUM_MISSING,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "note": "meta carries no md5Checksum; refusing to invent one"}}
    try:
        size = int(meta.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > max_bytes:
        return {**base, "status": VERIFY_FAILED, "reason": DOWNLOAD_TOO_LARGE,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "size": size, "max_bytes": max_bytes,
                             "note": "download refused; never truncated silently"}}
    try:
        data = drive.get(file_id)
    except Exception as exc:
        if _is_permission_error(exc):
            return {**base, "status": REVIEW_REQUIRED, "reason": ACCESS_DENIED,
                    "digest_kind": None, "digest": None,
                    "evidence": {**base["evidence"], "download_error": transport_reason(exc)}}
        return {**base, "status": VERIFY_FAILED, "reason": DOWNLOAD_FAILED,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "download_error": transport_reason(exc)}}
    actual_md5 = hashlib.md5(data).hexdigest()
    if actual_md5 != md5_claim:
        return {**base, "status": VERIFY_FAILED, "reason": CHECKSUM_MISMATCH,
                "digest_kind": "md5", "digest": actual_md5,
                "evidence": {**base["evidence"], "expected_md5": md5_claim,
                             "actual_md5": actual_md5, "bytes": len(data)}}
    return {**base, "status": ACCESS_VERIFIED, "reason": OK,
            "digest_kind": "md5", "digest": actual_md5,
            "evidence": {**base["evidence"], "verified_bytes": True, "bytes": len(data)}}


def classify_identity(
    known: Dict[str, Dict[str, Any]], obj: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    """Classify one object against the persistent registry.

    ``known`` maps file_id -> {revision, source_id, doc_id}.
    Returns (classification, record):
    - NEW: fresh source_id + revision-bound doc_id
    - DUPLICATE_SAME_REVISION: same file_id+revision; source_id is NOT rebuilt
    - REVISION_LINKED: same file_id, new revision; source_id kept, new doc_id
    """
    file_id = obj.get("file_id")
    revision = _revision_of(obj)
    previous = (known or {}).get(file_id)
    if previous is None:
        return NEW, {
            "source_id": drive_source_id(file_id),
            "doc_id": drive_doc_id(file_id, revision),
            "revision": revision,
            "new_source": True,
        }
    if previous.get("revision") == revision:
        return DUPLICATE_SAME_REVISION, {
            "source_id": previous.get("source_id"),
            "doc_id": previous.get("doc_id"),
            "revision": revision,
            "new_source": False,
        }
    return REVISION_LINKED, {
        "source_id": previous.get("source_id"),
        "doc_id": drive_doc_id(file_id, revision),
        "revision": revision,
        "previous_revision": previous.get("revision"),
        "new_source": False,
    }


def decide_drive_rights(
    obj: Dict[str, Any], *, grants: Optional[Dict[str, Any]] = None
) -> Tuple[str, str]:
    """DEFAULT-DENY rights decision for a discovered Drive object.

    Returns ("review", "DRIVE_RIGHTS_UNVERIFIED") unless an explicit per-file
    grant for this file_id is passed in ``grants``. Unknown rights are never
    auto-approved, and "no grant system configured" is not a grant.
    """
    file_id = obj.get("file_id") if isinstance(obj, dict) else None
    if grants and file_id in grants:
        return "approved", "DRIVE_RIGHTS_GRANTED"
    return "review", "DRIVE_RIGHTS_UNVERIFIED"


def append_ledger(ledger_path: Any, event: Dict[str, Any]) -> None:
    """Append one JSONL ledger event with O_APPEND atomicity.

    All LEDGER_REQUIRED_FIELDS must be present or ValueError is raised.
    Extra fields are preserved. File is created with 0o600.
    """
    missing = [f for f in LEDGER_REQUIRED_FIELDS if f not in event]
    if missing:
        raise ValueError(f"LEDGER_EVENT_FIELD_MISSING: {missing}")
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_registry(registry_path: Path) -> Dict[str, Dict[str, Any]]:
    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("REGISTRY_CORRUPT: top-level JSON object required")
        return data
    return {}


def run_discovery(
    drive: Any,
    folder_id: str,
    *,
    ledger_path: Any,
    registry_path: Any = None,
    verify_bytes: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    actor: str = "muse",
    operator: str = "muse",
    policy_version: str = "2.0.4-2-slice1",
) -> Dict[str, Any]:
    """Orchestrate read-only Drive discovery: list -> verify each ->
    classify identity -> ledger per object -> persist registry (atomic JSON).

    Returns a machine report with exact counts. Raises DriveDiscoveryError on
    listing failure (fail-closed); per-object verification failures are
    recorded in the ledger and counted, never raised.
    """
    if not folder_id:
        raise DriveDiscoveryError(LIST_FAILED, file_id=None, detail="empty folder_id refused")
    ledger = Path(ledger_path)
    registry_file = Path(registry_path) if registry_path else Path(str(ledger) + ".registry.json")
    started_ts = _utcnow()

    append_ledger(ledger, {
        "ts": started_ts, "actor": actor, "action": "drive_discovery_started",
        "prev_state": "IDLE", "new_state": "DISCOVERING",
        "evidence_ref": folder_id, "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": "read-only discovery; no writes will be issued",
    })

    known = _load_registry(registry_file)
    objects, discovery_report = discover_folder(drive, folder_id)

    counts = {"discovered": 0, "verified": 0, "review": 0, "failed": 0,
              "duplicate": 0, "revision_linked": 0}
    for obj in objects:
        counts["discovered"] += 1
        verified = verify_object(drive, obj, verify_bytes=verify_bytes, max_bytes=max_bytes)
        classification, record = classify_identity(known, obj)
        rights_status, rights_reason = decide_drive_rights(obj)
        if verified["status"] == ACCESS_VERIFIED:
            counts["verified"] += 1
        elif verified["status"] == REVIEW_REQUIRED:
            counts["review"] += 1
        else:
            counts["failed"] += 1
        if classification == DUPLICATE_SAME_REVISION:
            counts["duplicate"] += 1
        elif classification == REVISION_LINKED:
            counts["revision_linked"] += 1

        known[obj["file_id"]] = {
            "revision": record["revision"],
            "source_id": record["source_id"],
            "doc_id": record["doc_id"],
            "classification": classification,
            "name": obj.get("name"),
            "mime_type": obj.get("mime_type"),
            "verify_status": verified["status"],
            "verify_reason": verified["reason"],
            "rights_status": rights_status,
            "rights_reason": rights_reason,
            "discovered_ts": obj.get("discovered_ts"),
        }
        append_ledger(ledger, {
            "ts": _utcnow(), "actor": actor, "action": "drive_object_discovered",
            "prev_state": "UNDISCOVERED", "new_state": verified["status"],
            "evidence_ref": obj["file_id"], "policy_version": policy_version,
            "operator": operator, "approval_ref": "", "retry_count": 0,
            "note": f"classification={classification} rights={rights_status}/{rights_reason}",
            "classification": classification,
            "record": record,
            "verification": verified,
            "rights_status": rights_status,
            "rights_reason": rights_reason,
        })

    atomic(registry_file, json.dumps(known, ensure_ascii=False, indent=2,
                                    sort_keys=True).encode("utf-8"))
    finished_ts = _utcnow()
    append_ledger(ledger, {
        "ts": finished_ts, "actor": actor, "action": "drive_discovery_finished",
        "prev_state": "DISCOVERING", "new_state": "DISCOVERY_COMPLETE",
        "evidence_ref": folder_id, "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": f"counts={counts}",
        "counts": counts,
    })
    return {
        "folder_id": folder_id,
        "base_sha": None,
        "counts": counts,
        "discovery": discovery_report,
        "ledger_path": str(ledger),
        "registry_path": str(registry_file),
        "started_ts": started_ts,
        "finished_ts": finished_ts,
    }
