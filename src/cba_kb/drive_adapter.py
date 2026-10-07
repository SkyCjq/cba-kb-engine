"""Drive placement transport adapter (REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01).

Bridges PlacementGuard interface to the production cba_kb.drive.Drive instance:
- create -> drive.ensure()
- reparent -> drive.move()
- meta -> drive.meta()
- list -> drive.list()
- delete -> drive.api.files().delete()
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional


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
