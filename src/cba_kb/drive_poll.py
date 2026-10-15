"""Drive incremental poll: changes -> per-file discovery -> crash-safe checkpoint
(2.0.4-2 slice 3, T2).

Continuously watches a Drive scope for changes and incrementally updates the
discovery registry. Crash-safe by construction:

- The change-source page token advances ONLY after the whole batch is
  processed. A crash mid-batch restarts from the old token; re-delivered
  changes are idempotent (registry upsert by file_id; per-file ledger events
  carry the change_id so duplicates are identifiable).
- Per-file durability ordering: the registry is atomically persisted BEFORE
  the per-file ledger event is appended, and both happen OUTSIDE the
  per-file error handler. A change_id is therefore treated as "processed"
  only after its registry update is durable. An infrastructure failure
  (disk/ledger) aborts the poll without advancing the token, so a retry
  re-delivers the change — a crash can never silently lose it.
- Per-file fail-closed: a PROCESSING error (bad data, unexpected verify
  failure) is recorded in the ledger and counted; the poll continues
  (partial success). Infrastructure errors abort instead of being
  mislabeled as file failures.

The poller core is transport-agnostic: it works against a change source
implementing ``get_start_token()`` / ``list_changes(since_token)``.
``DriveChangesSource`` is the real Drive Changes-API adapter for production.
Tests use scripted fakes — they prove contract behavior, never real API
behavior. A real Drive E2E requires an authorized isolated test Drive.

Security posture: read-only. The poller never writes to Drive; rights grants
remain explicit operator actions (see drive_search.grant_rights).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import atomic
from .drive_discovery import (
    ACCESS_VERIFIED,
    DRIVE_PROVIDER,  # noqa: F401  (re-exported for change-source authors)
    REVIEW_REQUIRED,
    VERIFY_FAILED,
    DriveDiscoveryError,
    _normalize_item,
    append_ledger,
    classify_identity,
    decide_drive_rights,
    read_ledger_events,
    verify_object,
)

POLL_STARTED = "drive_poll_started"
POLL_FILE_PROCESSED = "drive_poll_file_processed"
POLL_FILE_REMOVED = "drive_poll_file_removed"
POLL_FILE_FAILED = "drive_poll_file_failed"
POLL_COMPLETED = "drive_poll_completed"

STATE_CORRUPT = "POLL_STATE_CORRUPT"
STATE_VERSION = 1
# Bound on persisted processed-change IDs (oldest evicted). Bounds the
# re-delivery dedup window; the registry upsert stays idempotent regardless.
MAX_PROCESSED_IDS = 5000


# ----------------------------------------------------------- change sources

class DriveChangesSource:
    """Real Drive Changes-API adapter (production).

    Written against the documented ``drive.changes`` shape:
    ``getStartPageToken().execute()`` -> ``{"startPageToken": str}``;
    ``list(pageToken=..., ...).execute()`` -> ``{"changes": [...],
    "newStartPageToken": str, "nextPageToken": str|None}``.
    Each change is normalized to
    ``{"change_id", "file_id", "removed", "file"}``.
    """

    def __init__(self, drive: Any):
        self._changes = drive.api.changes()

    def get_start_token(self) -> str:
        resp = self._changes.getStartPageToken().execute()
        token = resp.get("startPageToken")
        if not token:
            raise DriveDiscoveryError(
                "POLL_TOKEN_FAILED", file_id=None,
                detail="changes.getStartPageToken returned no token")
        return token

    def list_changes(
        self, since_token: str
    ) -> Tuple[List[Dict[str, Any]], str]:
        changes: List[Dict[str, Any]] = []
        page_token: Optional[str] = since_token
        new_token = since_token
        while True:
            resp = self._changes.list(
                pageToken=page_token,
                spaces="drive",
                fields=("nextPageToken,newStartPageToken,"
                        "changes(fileId,removed,"
                        "file(id,name,mimeType,parents,version,headRevisionId,"
                        "md5Checksum,size,modifiedTime,webViewLink,trashed))"),
                pageSize=100,
            ).execute()
            for raw in resp.get("changes", []):
                changes.append(normalize_change(raw))
            new_token = resp.get("newStartPageToken") or new_token
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return changes, new_token


def normalize_change(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one raw change dict to the poller contract."""
    file_id = raw.get("fileId")
    removed = bool(raw.get("removed"))
    file_obj = raw.get("file") or {}
    if removed:
        change_id = f"{file_id}@removed"
    else:
        stamp = (file_obj.get("modifiedTime")
                 or file_obj.get("version") or "unknown")
        change_id = f"{file_id}@{stamp}"
    return {"change_id": change_id, "file_id": file_id,
            "removed": removed, "file": file_obj}


# ------------------------------------------------------------------ state

def _load_state(state_path: Path) -> Optional[Dict[str, Any]]:
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{STATE_CORRUPT}: {state_path}: {exc}") from exc
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise ValueError(f"{STATE_CORRUPT}: {state_path}: bad version/shape")
    if not state.get("page_token"):
        raise ValueError(f"{STATE_CORRUPT}: {state_path}: missing page_token")
    return state


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state = dict(state, version=STATE_VERSION)
    ids = state.get("processed_change_ids") or []
    if len(ids) > MAX_PROCESSED_IDS:
        state["processed_change_ids"] = ids[-MAX_PROCESSED_IDS:]
    atomic(state_path,
           json.dumps(state, ensure_ascii=False, indent=2,
                      sort_keys=True).encode("utf-8"))


# -------------------------------------------------------------- poll core

def _tombstone(known: Dict[str, Dict[str, Any]], file_id: str,
               change_id: str, ts: str) -> bool:
    """Mark a removed file in the registry. Returns True if the entry existed."""
    record = known.get(file_id)
    if record is None:
        return False
    record["verify_status"] = "FILE_REMOVED"
    record["verify_reason"] = "REMOVED_FROM_DRIVE"
    record["tombstoned_ts"] = ts
    record["tombstone_change_id"] = change_id
    return True


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def poll_once(
    source: Any,
    drive: Any,
    *,
    registry_path: Any,
    ledger_path: Any,
    state_path: Any,
    verify_bytes: bool = False,
    max_bytes: int = 64 * 1024 * 1024,
    checkpoint_every: int = 20,
    actor: str = "muse",
    operator: str = "muse",
    policy_version: str = "2.0.4-2-slice3",
) -> Dict[str, Any]:
    """Run one incremental poll. Returns a machine report.

    First call with no state file initializes the watch (records the start
    token, processes nothing). Subsequent calls process all changes since the
    saved token, then advance it.

    The processed-change-ID set is persisted in the state file (mid-batch
    every ``checkpoint_every`` files, and at batch end), so a crash retry
    skips already-durable work. The page token itself advances only at batch
    end: re-delivered changes are idempotent.
    """
    registry_file = Path(registry_path)
    ledger = Path(ledger_path)
    state_file = Path(state_path)
    started_ts = _utcnow()

    state = _load_state(state_file)
    if state is None:
        token = source.get_start_token()
        _save_state(state_file, {
            "page_token": token, "processed_change_ids": [],
            "polls_completed": 0, "files_processed": 0, "files_failed": 0,
            "last_poll_ts": started_ts, "last_error": None,
        })
        append_ledger(ledger, {
            "ts": started_ts, "actor": actor, "action": POLL_STARTED,
            "prev_state": "IDLE", "new_state": "WATCHING",
            "evidence_ref": str(state_file), "policy_version": policy_version,
            "operator": operator, "approval_ref": "", "retry_count": 0,
            "note": f"poll watch initialized at token {token}",
        })
        return {"initialized": True, "page_token": token,
                "started_ts": started_ts}

    append_ledger(ledger, {
        "ts": started_ts, "actor": actor, "action": POLL_STARTED,
        "prev_state": "WATCHING", "new_state": "POLLING",
        "evidence_ref": str(state_file), "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": f"poll from token {state['page_token']}",
    })

    from .drive_search import load_registry
    known = load_registry(registry_file) if registry_file.exists() else {}
    processed_ids = set(state.get("processed_change_ids") or [])

    try:
        changes, new_token = source.list_changes(state["page_token"])
    except Exception as exc:
        raise DriveDiscoveryError(
            "POLL_LIST_FAILED", file_id=None,
            detail=f"list_changes failed: {exc}") from exc

    counts = {"changes_seen": 0, "processed": 0, "removed": 0,
              "failed": 0, "duplicate_skipped": 0}
    for change in changes:
        counts["changes_seen"] += 1
        change_id = change.get("change_id")
        file_id = change.get("file_id")
        if not change_id or not file_id:
            counts["failed"] += 1
            append_ledger(ledger, {
                "ts": _utcnow(), "actor": actor, "action": POLL_FILE_FAILED,
                "prev_state": "POLLING", "new_state": "POLLING",
                "evidence_ref": str(file_id), "policy_version": policy_version,
                "operator": operator, "approval_ref": "", "retry_count": 0,
                "note": "malformed change: missing change_id/file_id",
                "change_id": change_id,
            })
            processed_ids.add(change_id)
            continue
        if change_id in processed_ids:
            counts["duplicate_skipped"] += 1
            continue
        try:
            if change.get("removed"):
                action = POLL_FILE_REMOVED
                existed = _tombstone(known, file_id, change_id, _utcnow())
                note = ("tombstoned" if existed
                        else "removal for unknown file; no registry entry")
                event_extra: Dict[str, Any] = {}
            else:
                action = POLL_FILE_PROCESSED
                obj = _normalize_item(change.get("file") or {}, _utcnow())
                # The change's file object may lack the id key; bind it.
                obj["file_id"] = file_id
                verified = verify_object(drive, obj, verify_bytes=verify_bytes,
                                         max_bytes=max_bytes)
                classification, record = classify_identity(known, obj)
                rights_status, rights_reason = decide_drive_rights(obj)
                known[file_id] = {
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
                note = (f"classification={classification} "
                        f"rights={rights_status}/{rights_reason}")
                event_extra = {"classification": classification,
                               "verification": verified}
        except Exception as exc:
            # Processing failure (not infrastructure): record, count, and
            # continue the poll. The change is marked so a retry does not
            # loop on it forever; the next real change to the file will
            # re-trigger processing.
            counts["failed"] += 1
            append_ledger(ledger, {
                "ts": _utcnow(), "actor": actor, "action": POLL_FILE_FAILED,
                "prev_state": "POLLING", "new_state": "POLLING",
                "evidence_ref": file_id, "policy_version": policy_version,
                "operator": operator, "approval_ref": "", "retry_count": 0,
                "note": f"{type(exc).__name__}: {exc}",
                "change_id": change_id,
            })
            processed_ids.add(change_id)
            continue
        # Infrastructure (outside the try): registry persist THEN ledger mark.
        # A failure here aborts the poll WITHOUT advancing the token, so a
        # retry re-delivers the change. The change is only treated as
        # "processed" after its registry update is durable — a crash can
        # never silently lose it.
        atomic(registry_file,
               json.dumps(known, ensure_ascii=False, indent=2,
                          sort_keys=True).encode("utf-8"))
        append_ledger(ledger, {
            "ts": _utcnow(), "actor": actor, "action": action,
            "prev_state": "POLLING", "new_state": "POLLING",
            "evidence_ref": file_id, "policy_version": policy_version,
            "operator": operator, "approval_ref": "", "retry_count": 0,
            "note": note,
            "change_id": change_id,
            **event_extra,
        })
        processed_ids.add(change_id)
        if action == POLL_FILE_REMOVED:
            counts["removed"] += 1
        else:
            counts["processed"] += 1
        # Mid-batch checkpoint: persist the processed-ID set so a crash retry
        # skips already-durable work. The page token still advances only at
        # batch end.
        if (counts["processed"] + counts["removed"] + counts["failed"]) \
                % checkpoint_every == 0:
            _save_state(state_file, {
                **state,
                "processed_change_ids": sorted(processed_ids),
                "last_poll_ts": _utcnow(),
            })

    finished_ts = _utcnow()
    _save_state(state_file, {
        "page_token": new_token,
        "processed_change_ids": sorted(processed_ids),
        "polls_completed": int(state.get("polls_completed") or 0) + 1,
        "files_processed": int(state.get("files_processed") or 0)
        + counts["processed"],
        "files_failed": int(state.get("files_failed") or 0) + counts["failed"],
        "last_poll_ts": finished_ts,
        "last_error": None,
    })
    append_ledger(ledger, {
        "ts": finished_ts, "actor": actor, "action": POLL_COMPLETED,
        "prev_state": "POLLING", "new_state": "WATCHING",
        "evidence_ref": str(state_file), "policy_version": policy_version,
        "operator": operator, "approval_ref": "", "retry_count": 0,
        "note": f"counts={counts}",
        "counts": counts,
        "new_page_token": new_token,
    })
    return {
        "initialized": False,
        "counts": counts,
        "page_token": new_token,
        "started_ts": started_ts,
        "finished_ts": finished_ts,
    }


def run_poll_loop(
    source: Any,
    drive: Any,
    *,
    registry_path: Any,
    ledger_path: Any,
    state_path: Any,
    interval_seconds: float = 60.0,
    max_polls: Optional[int] = None,
    **poll_kwargs: Any,
) -> Dict[str, Any]:
    """Continuously poll until interrupted (or max_polls reached).

    A keyboard interrupt stops the loop cleanly after the in-flight poll.
    """
    polls = 0
    try:
        while max_polls is None or polls < max_polls:
            poll_once(source, drive, registry_path=registry_path,
                      ledger_path=ledger_path, state_path=state_path,
                      **poll_kwargs)
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        pass
    return {"polls_completed": polls}
