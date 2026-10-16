"""Drive placement transport adapter (REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01).

Bridges PlacementGuard interface to the production cba_kb.drive.Drive instance:
- create -> drive.ensure()
- reparent -> drive.move()
- meta -> drive.meta()
- list -> drive.list()
- delete -> drive.api.files().delete()

Evidence contract (for drive_plan._apply_drive_move):
- The guarded move workflow requires adapter.meta() to return the EVIDENCE
  CONTRACT: {"id", "parents", "sha256", "revision_id", "mime_type"}.
- Raw Google Drive API v3 metadata uses DIFFERENT field names:
  {"id", "mimeType", "parents", "headRevisionId", "md5Checksum", ...}.
- CRITICAL: md5Checksum is MD5, NOT SHA-256. It must NEVER be mapped to
  the "sha256" evidence field. sha256 evidence must come from an actual
  SHA-256 computation over the file content, or be None (fail-closed).
- Native Google Workspace files (Docs/Sheets/Slides) have NO md5Checksum
  and their content cannot be downloaded (drive.get rejects them), so
  sha256 evidence cannot be established for them -> fail-closed.
- Use DriveEvidenceAdapter (below) to bridge a real Drive instance to the
  evidence contract. PlacementDriveAdapter.meta() returns RAW fields and
  must NOT be used where the evidence contract is required.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

# Google Workspace native MIME prefix: these files have no binary content,
# no md5Checksum, and drive.get() rejects raw download for them.
NATIVE_MIME_PREFIX = "application/vnd.google-apps."

# Default upper bound for content download when computing sha256 evidence.
# Files larger than this cannot have sha256 evidence established
# (fail-closed). Operational parameter; Human may adjust.
DEFAULT_EVIDENCE_MAX_BYTES = 100 * 1024 * 1024  # 100 MiB

# Error codes for evidence translation failures.
DRIVE_EVIDENCE_TRANSLATE_FAILED = "DRIVE_EVIDENCE_TRANSLATE_FAILED"
DRIVE_EVIDENCE_SHA256_UNAVAILABLE = "DRIVE_EVIDENCE_SHA256_UNAVAILABLE"


def translate_drive_evidence(raw_meta: Dict[str, Any],
                             content_sha256: Optional[str] = None
                             ) -> Dict[str, Any]:
    """Convert raw Drive API v3 metadata to the evidence contract.

    Args:
        raw_meta: raw dict from drive.meta() (Drive API v3 fields:
            id, mimeType, parents, headRevisionId, md5Checksum, ...).
        content_sha256: SHA-256 hex digest computed over the actual file
            content, or None if it could not be established.

    Returns:
        {"id", "parents", "sha256", "revision_id", "mime_type"}.

    INVARIANT: md5Checksum is NEVER copied into "sha256". If
    content_sha256 is None, "sha256" is None and downstream strict
    checks fail closed.

    Raises:
        ValueError(DRIVE_EVIDENCE_TRANSLATE_FAILED): raw_meta is not a dict.
    """
    if not isinstance(raw_meta, dict):
        raise ValueError(
            f"{DRIVE_EVIDENCE_TRANSLATE_FAILED}: raw_meta must be a dict, "
            f"got {type(raw_meta).__name__}")
    # Explicit field mapping. Note what is DELIBERATELY absent:
    # md5Checksum -> NOTHING. MD5 is not SHA-256.
    return {
        "id": raw_meta.get("id"),
        "parents": raw_meta.get("parents", []),
        "mime_type": raw_meta.get("mimeType"),
        "revision_id": raw_meta.get("headRevisionId"),
        "sha256": content_sha256,
    }


def is_native_mime(mime_type: Optional[str]) -> bool:
    """True if the MIME type is a native Google Workspace type."""
    return bool(mime_type) and str(mime_type).startswith(NATIVE_MIME_PREFIX)


class PlacementDriveAdapter:
    """Production Drive adapter for PlacementGuard adhering to cba_kb.drive.Drive contract."""

    def __init__(self, drive: Any) -> None:
        self.drive = drive

    def create(
        self,
        name: str,
        parent: Optional[str] = None,
        mime: str = "text/plain",
        content: Optional[bytes] = None,
        appProperties: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Bridge creation to drive.ensure() using a persistent cba_key."""
        app_props = appProperties or {}
        key = app_props.get("cba_key") or app_props.get("placement_idempotency_key")
        if not key:
            key = f"placement-{uuid.uuid4().hex}"

        payload = content if content is not None else b""
        fid = self.drive.ensure(parent, key, name, mime, payload)
        return {"id": fid}

    def reparent(self, file_id: str, new_parent: str, old_parent: Optional[str] = None) -> Dict[str, Any]:
        """Bridge reparenting to drive.move()."""
        return self.drive.move(file_id, destination=new_parent, previous=old_parent)

    def meta(self, file_id: str) -> Dict[str, Any]:
        """Bridge metadata readback to drive.meta()."""
        return self.drive.meta(file_id)

    def list(self, parent: str) -> List[Dict[str, Any]]:
        """Bridge directory listing to drive.list()."""
        return self.drive.list(parent)

    def delete(self, file_id: str) -> Any:
        """Bridge deletion to drive.api.files().delete()."""
        api_files = getattr(self.drive, "api", self.drive)
        files_resource = getattr(api_files, "files", lambda: api_files)()
        return files_resource.delete(fileId=file_id, supportsAllDrives=True).execute(num_retries=0)


class DriveEvidenceAdapter:
    """Evidence-contract adapter for the guarded drive_move workflow.

    Wraps a production ``cba_kb.drive.Drive`` instance and exposes the
    EVIDENCE CONTRACT expected by ``drive_plan._apply_drive_move``:

        meta(file_id) -> {"id", "parents", "sha256", "revision_id",
                          "mime_type"}

    Field provenance:
    - ``parents``: raw ``parents`` from Drive API.
    - ``mime_type``: raw ``mimeType`` from Drive API.
    - ``revision_id``: raw ``headRevisionId`` from Drive API.
    - ``sha256``: SHA-256 hex digest computed over downloaded content
      (bounded download). NEVER md5Checksum.

    Fail-closed cases (sha256 -> None, downstream strict check rejects):
    - Native Google Workspace files (no binary content, download rejected).
    - Files larger than max_evidence_bytes (bounded download aborts).
    - Download failures.

    ``reparent`` delegates to ``drive.move`` with the exact interface
    ``_apply_drive_move`` uses: reparent(file_id, new_parent, old_parent).
    """

    def __init__(self, drive: Any,
                 max_evidence_bytes: int = DEFAULT_EVIDENCE_MAX_BYTES) -> None:
        self.drive = drive
        self.max_evidence_bytes = max_evidence_bytes
        self.reparent_calls = 0

    def meta(self, file_id: str) -> Dict[str, Any]:
        """Return the evidence contract for a file (fail-closed)."""
        raw = self.drive.meta(file_id)
        mime = raw.get("mimeType")
        content_sha256: Optional[str] = None
        if not is_native_mime(mime):
            # Binary file: establish sha256 via bounded content download.
            try:
                from .common import digest
                content = self.drive.get_bounded(
                    file_id, self.max_evidence_bytes)
                content_sha256 = digest(content)
            except Exception:
                # Any download/computation failure -> sha256 unavailable.
                # Downstream _require_exact fails closed. Never propagate
                # a guessed or partial value.
                content_sha256 = None
        # Native files: content_sha256 stays None -> fail-closed downstream.
        return translate_drive_evidence(raw, content_sha256)

    def reparent(self, file_id: str, new_parent: str,
                 old_parent: Optional[str] = None) -> Dict[str, Any]:
        """Execute the move via drive.move (real Drive API call)."""
        self.reparent_calls += 1
        return self.drive.move(file_id, destination=new_parent,
                               previous=old_parent)
