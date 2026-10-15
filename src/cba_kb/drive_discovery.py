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
from .transport import DownloadTooLargeError, reason as transport_reason

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
TRANSPORT_UNBOUNDED = "TRANSPORT_UNBOUNDED"
META_FAILED = "META_FAILED"
LIST_FAILED = "LIST_FAILED"
PERMISSION_DENIED = "PERMISSION_DENIED"
LEDGER_WRITE_FAILED = "LEDGER_WRITE_FAILED"

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
    REVISION_DRIFT, DOWNLOAD_TOO_LARGE, TRANSPORT_UNBOUNDED,
    LEDGER_WRITE_FAILED. Per-object problems are returned as
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

    FAIL-CLOSED: every exception from ``drive.list()`` — including an
    already-raised ``DriveDiscoveryError`` — is normalized to
    ``DriveDiscoveryError(LIST_FAILED)`` with the original chained for
    diagnosis (a LIST_FAILED arriving already-normalized passes through
    unchanged). Partial results are never returned.
    Trashed items are filtered (the live query already excludes them; this is
    belt-and-braces) and counted in ``skipped_trashed`` with an explicit
    reason. ``max_items`` is a logical enumeration guard, not a memory bound:
    exceeding it raises instead of truncating silently.
    """
    if not folder_id:
        raise DriveDiscoveryError(LIST_FAILED, file_id=None, detail="empty folder_id refused")
    started_ts = _utcnow()
    try:
        items = drive.list(folder_id)
    except DriveDiscoveryError as exc:
        if exc.code == LIST_FAILED:
            raise
        raise DriveDiscoveryError(
            LIST_FAILED, file_id=folder_id,
            detail=f"normalized from {exc.code}: {transport_reason(exc)}",
        ) from exc
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


def download_bounded(drive: Any, file_id: str, max_bytes: int) -> bytes:
    """Download with a hard byte bound enforced by the transport when capable.

    The bound is enforced *during* transfer by ``drive.get_bounded()``
    (chunked reads that abort once ``max_bytes`` is exceeded); the caller
    additionally re-checks the returned length (defense in depth).

    Residual: the transport checks the bound after each chunk lands, so its
    buffer may transiently hold up to ``max_bytes`` plus one chunk before
    aborting. Over-limit bytes are never checksummed, verified, or returned
    as success — the receipt is always VERIFY_FAILED/DOWNLOAD_TOO_LARGE.

    Raises:
    - ``DownloadTooLargeError``: the bound was exceeded. The transport must
      abort the transfer rather than return over-limit bytes as success.
    - ``DriveDiscoveryError(TRANSPORT_UNBOUNDED)``: this drive object has no
      ``get_bounded`` capability, so the hard limit cannot be reliably
      enforced. The caller must fail closed instead of claiming a bounded
      verification it cannot prove.
    Other download errors propagate for the caller to classify.
    """
    get_bounded = getattr(drive, "get_bounded", None)
    if get_bounded is None:
        raise DriveDiscoveryError(
            TRANSPORT_UNBOUNDED, file_id=file_id,
            detail="drive has no get_bounded(); hard byte limit unenforceable; "
                   "refusing to claim bounded verification")
    data = get_bounded(file_id, max_bytes)
    if len(data) > max_bytes:
        # Defense in depth: a conforming transport never returns more than
        # asked; treat a violation as a hard failure, never as success.
        raise DownloadTooLargeError(
            f"transport returned {len(data)} bytes over max_bytes={max_bytes}")
    return data


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
    - binary + verify_bytes -> bounded download via ``drive.get_bounded``:
      the hard byte limit is enforced *during* transfer (a lying or missing
      ``size`` in metadata can never cause unbounded buffering); the caller
      re-checks the returned length. Missing md5 -> REVIEW_REQUIRED /
      CHECKSUM_MISSING; mismatch -> VERIFY_FAILED / CHECKSUM_MISMATCH;
      over-limit -> VERIFY_FAILED / DOWNLOAD_TOO_LARGE (never truncated
      silently, never ACCESS_VERIFIED); drive without bounded capability ->
      VERIFY_FAILED / TRANSPORT_UNBOUNDED (fail-closed, never claimed).
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
                             "note": "digest is the meta-claimed md5; bytes were NOT "
                                     "downloaded or compared"}}
    if not md5_claim:
        return {**base, "status": REVIEW_REQUIRED, "reason": CHECKSUM_MISSING,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "note": "meta carries no md5Checksum; refusing to invent one"}}
    # Declared size is an early-reject optimization only, never the proof:
    # the hard bound is enforced during transfer by download_bounded().
    try:
        size = int(meta.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > max_bytes:
        return {**base, "status": VERIFY_FAILED, "reason": DOWNLOAD_TOO_LARGE,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "size": size, "max_bytes": max_bytes,
                             "note": "declared size exceeds bound; refused before transfer"}}
    try:
        data = download_bounded(drive, file_id, max_bytes)
    except DriveDiscoveryError as exc:
        if exc.code == TRANSPORT_UNBOUNDED:
            return {**base, "status": VERIFY_FAILED, "reason": TRANSPORT_UNBOUNDED,
                    "digest_kind": None, "digest": None,
                    "evidence": {**base["evidence"],
                                 "note": "transport cannot enforce the hard byte bound; "
                                         "bounded verification refused, never claimed"}}
        raise
    except DownloadTooLargeError as exc:
        return {**base, "status": VERIFY_FAILED, "reason": DOWNLOAD_TOO_LARGE,
                "digest_kind": None, "digest": None,
                "evidence": {**base["evidence"], "max_bytes": max_bytes,
                             "note": f"bound enforced during transfer: {exc}; "
                                     "over-limit bytes never verified or returned"}}
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


def _grant_not_expired(grant: Dict[str, Any]) -> bool:
    """True when the grant has no expiry or its expiry is in the future."""
    expires_at = grant.get("expires_at")
    if expires_at is None:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False  # unparseable expiry is not a valid grant
    now = datetime.now(timezone.utc)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now


def _scope_covers(scope: Any) -> bool:
    """Exact scope matching — never substring.

    ``scope`` is required: it must be one exact scope string or a collection
    of exact scope strings. A grant covers the discovery read path only when
    an exact ``"drive:read"`` (or the wildcard ``"*"``) is present. Anything
    else — absent (None), non-string scalars such as ``42``, dicts, or
    substring lookalikes like ``"not-drive:read"`` — fails closed to False
    (never raises, never approves).
    """
    if isinstance(scope, str):
        return scope in ("drive:read", "*")
    if isinstance(scope, (list, tuple, set, frozenset)):
        return any(isinstance(item, str) and item in ("drive:read", "*")
                   for item in scope)
    return False


def _valid_grant_record(obj: Dict[str, Any], grant: Any) -> bool:
    """Strict validation for one grant record. Anything else is not a grant.

    A grant is valid only when it is a dict carrying ALL of:
    - ``approved``: the boolean True (not merely truthy);
    - ``approver``: a non-blank string naming who granted it;
    - ``file_id``: equal to this object's file_id (grants never transfer
      across objects);
    and NONE of:
    - ``revoked``: True;
    - ``expires_at``: present and not in the future;
    - ``scope``: missing, or without an exact ``"drive:read"`` (or ``"*"``)
      entry — substring lookalikes and non-string values never match.
    ``ACCESS_VERIFIED`` (a content-verification outcome) and a rights grant
    (an authorization decision) are never derived from each other.
    """
    if not isinstance(grant, dict):
        return False
    if grant.get("approved") is not True:
        return False
    approver = grant.get("approver")
    if not isinstance(approver, str) or not approver.strip():
        return False
    file_id = obj.get("file_id") if isinstance(obj, dict) else None
    if grant.get("file_id") != file_id:
        return False
    if grant.get("revoked") is True:
        return False
    if not _grant_not_expired(grant):
        return False
    if not _scope_covers(grant.get("scope")):
        return False
    return True


def decide_drive_rights(
    obj: Dict[str, Any], *, grants: Optional[Dict[str, Any]] = None
) -> Tuple[str, str]:
    """DEFAULT-DENY rights decision for a discovered Drive object.

    Returns ("approved", "DRIVE_RIGHTS_GRANTED") only for a strictly valid
    grant record (see ``_valid_grant_record``). ``False``, ``None``, ``{}``,
    revoked, expired, scope-mismatched, or object-mismatched entries are all
    ("review", "DRIVE_RIGHTS_UNVERIFIED"). Unknown rights are never
    auto-approved, and "no grant system configured" is not a grant.
    """
    file_id = obj.get("file_id") if isinstance(obj, dict) else None
    grant = (grants or {}).get(file_id) if file_id else None
    if grant is not None and _valid_grant_record(obj, grant):
        return "approved", "DRIVE_RIGHTS_GRANTED"
    return "review", "DRIVE_RIGHTS_UNVERIFIED"


# Single os.write() with O_APPEND is atomic on Linux for writes <= PIPE_BUF.
# Ledger events are small by construction; anything larger takes the looped
# path, which cannot promise cross-process non-interleaving (documented).
_LEDGER_ATOMIC_MAX = 4096


def _write_all(fd: int, data: bytes) -> None:
    """Write all bytes or raise. Short writes and EINTR are handled, never
    mistaken for success: a 0-byte return on a blocking fd is a failure."""
    view = memoryview(data)
    total = 0
    while total < len(data):
        try:
            written = os.write(fd, view[total:])
        except InterruptedError:
            continue  # EINTR: retry, never treat as done
        if written <= 0:
            raise IOError(
                f"LEDGER_SHORT_WRITE: fd accepted {written} bytes, "
                f"{len(data) - total} bytes unwritten")
        total += written


def append_ledger(ledger_path: Any, event: Dict[str, Any]) -> None:
    """Append one JSONL ledger event, fail-closed on partial writes.

    All LEDGER_REQUIRED_FIELDS must be present or ValueError is raised.
    Extra fields are preserved. File is created with 0o600.

    Write integrity (no silent partial success): every ``os.write()`` byte
    count is verified. For events up to 4096 bytes a single atomic O_APPEND
    write is used — any short count raises ``IOError(LEDGER_SHORT_WRITE)``.
    Larger events are completed via retry loop; a write that makes no
    progress raises instead of returning success. ``fsync`` success only
    proves the bytes actually written reached storage, never that the event
    is complete — use ``read_ledger_events(strict=True)`` to detect an
    incomplete tail on recovery.
    """
    missing = [f for f in LEDGER_REQUIRED_FIELDS if f not in event]
    if missing:
        raise ValueError(f"LEDGER_EVENT_FIELD_MISSING: {missing}")
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if len(line) <= _LEDGER_ATOMIC_MAX:
            # Single write is atomic under O_APPEND on Linux; EINTR retries,
            # any short count fails loudly instead of passing as success.
            while True:
                try:
                    written = os.write(fd, line)
                except InterruptedError:
                    continue
                break
            if written != len(line):
                raise IOError(
                    f"LEDGER_SHORT_WRITE: wrote {written} of {len(line)} bytes; "
                    "event incomplete, tail must be treated as corrupt")
        else:
            _write_all(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_ledger_events(ledger_path: Any, *, strict: bool = True) -> List[Dict[str, Any]]:
    """Read a JSONL ledger, fail-closed on a corrupt or incomplete tail.

    A file that does not end with ``\\n`` has an incomplete final event
    (e.g. from a short write or a crash mid-append): in strict mode this
    raises ``ValueError(LEDGER_TAIL_INCOMPLETE)`` instead of silently
    dropping or half-parsing it. Every complete line must parse as a JSON
    object or ``ValueError(LEDGER_CORRUPT)`` is raised.
    """
    raw = Path(ledger_path).read_bytes()
    if not raw:
        return []
    if strict and not raw.endswith(b"\n"):
        raise ValueError(
            "LEDGER_TAIL_INCOMPLETE: file does not end with newline; "
            "the final event was not fully committed")
    events = []
    for lineno, chunk in enumerate(raw.split(b"\n"), start=1):
        if not chunk:
            continue
        try:
            item = json.loads(chunk.decode("utf-8"))
        except ValueError as exc:
            raise ValueError(
                f"LEDGER_CORRUPT: line {lineno} is not valid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(
                f"LEDGER_CORRUPT: line {lineno} is not a JSON object")
        events.append(item)
    return events


def _load_registry(registry_path: Path) -> Dict[str, Dict[str, Any]]:
    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("REGISTRY_CORRUPT: top-level JSON object required")
        return data
    return {}


def _append_ledger_run(ledger: Path, event: Dict[str, Any],
                       file_id: Optional[str]) -> None:
    """Ledger append inside a discovery run: any write failure becomes an
    explicit LEDGER_WRITE_FAILED run error. The registry is only persisted
    after all ledger appends succeed, so a damaged ledger can never be
    mistaken for a reliably completed run."""
    try:
        append_ledger(ledger, event)
    except Exception as exc:
        raise DriveDiscoveryError(
            LEDGER_WRITE_FAILED, file_id=file_id,
            detail=f"ledger append failed: {exc}") from exc


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

    _append_ledger_run(ledger, {
        "ts": started_ts, "actor": actor, "action": "drive_discovery_started",
        "prev_state": "IDLE", "new_state": "DISCOVERING",
        "evidence_ref": folder_id, "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": "read-only discovery; no writes will be issued",
    }, folder_id)

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
        _append_ledger_run(ledger, {
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
        }, obj["file_id"])

    # Q2-R2 Finding 1: the registry is persisted ONLY after every ledger
    # event (including the finished event) has been durably appended. A
    # ledger failure aborts the run before the registry is touched, so a
    # damaged ledger can never coincide with a "reliably complete" registry.
    finished_ts = _utcnow()
    _append_ledger_run(ledger, {
        "ts": finished_ts, "actor": actor, "action": "drive_discovery_finished",
        "prev_state": "DISCOVERING", "new_state": "DISCOVERY_COMPLETE",
        "evidence_ref": folder_id, "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": f"counts={counts}",
        "counts": counts,
    }, folder_id)
    atomic(registry_file, json.dumps(known, ensure_ascii=False, indent=2,
                                    sort_keys=True).encode("utf-8"))
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
