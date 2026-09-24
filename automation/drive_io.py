from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import P2AError, canonical_json_bytes, sha256_bytes


@dataclass(frozen=True)
class DriveRead:
    file_id: str
    name: str
    folder_id: str
    revision: int
    content: bytes


class DriveStore(Protocol):
    def read(self, file_id: str) -> DriveRead: ...
    def find(self, folder_id: str, name: str) -> DriveRead | None: ...
    def create(self, folder_id: str, name: str, content: bytes) -> DriveRead: ...
    def update(self, file_id: str, content: bytes) -> DriveRead: ...


@dataclass
class _Record:
    name: str
    folder_id: str
    revision: int
    content: bytes


class MemoryDriveStore:
    """Deterministic fake with Drive-like IDs and in-place revisions."""

    def __init__(self) -> None:
        self.records: dict[str, _Record] = {}
        self._counter = 0

    def seed(self, file_id: str, folder_id: str, name: str, content: bytes, *, revision: int = 1) -> None:
        self.records[file_id] = _Record(name, folder_id, revision, content)

    def read(self, file_id: str) -> DriveRead:
        try:
            record = self.records[file_id]
        except KeyError as exc:
            raise P2AError("DRIVE_FILE_NOT_FOUND", "Drive file ID not found", file_id=file_id) from exc
        return DriveRead(file_id, record.name, record.folder_id, record.revision, record.content)

    def find(self, folder_id: str, name: str) -> DriveRead | None:
        matches = [self.read(file_id) for file_id, item in self.records.items() if item.folder_id == folder_id and item.name == name]
        if len(matches) > 1:
            raise P2AError("DUPLICATE_HISTORY_NAME", "Multiple files have the same immutable history name", folder_id=folder_id, name=name)
        return matches[0] if matches else None

    def create(self, folder_id: str, name: str, content: bytes) -> DriveRead:
        if self.find(folder_id, name) is not None:
            raise P2AError("DUPLICATE_HISTORY_NAME", "Immutable history name already exists", folder_id=folder_id, name=name)
        self._counter += 1
        file_id = f"mem-{self._counter:08d}"
        self.seed(file_id, folder_id, name, content)
        return self.read(file_id)

    def update(self, file_id: str, content: bytes) -> DriveRead:
        current = self.read(file_id)
        self.records[file_id] = _Record(current.name, current.folder_id, current.revision + 1, content)
        return self.read(file_id)


class DirectoryDriveStore:
    """Persistent non-Production canary store with stable opaque file IDs."""

    INDEX = ".p2a-drive-index.json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / self.INDEX
        if not self.index_path.exists():
            self._write_index({"files": {}})

    def _load_index(self) -> dict:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise P2AError("DRIVE_INDEX_INVALID", "Canary store index is unreadable", error=str(exc)) from exc

    def _write_index(self, index: dict) -> None:
        temp = self.index_path.with_suffix(".tmp")
        temp.write_bytes(canonical_json_bytes(index))
        os.replace(temp, self.index_path)

    def _path(self, file_id: str) -> Path:
        return self.root / "objects" / file_id

    def read(self, file_id: str) -> DriveRead:
        index = self._load_index()
        try:
            item = index["files"][file_id]
            content = self._path(file_id).read_bytes()
        except (KeyError, OSError) as exc:
            raise P2AError("DRIVE_FILE_NOT_FOUND", "Canary file ID not found", file_id=file_id) from exc
        return DriveRead(file_id, item["name"], item["folder_id"], item["revision"], content)

    def find(self, folder_id: str, name: str) -> DriveRead | None:
        index = self._load_index()
        ids = [file_id for file_id, item in index["files"].items() if item["folder_id"] == folder_id and item["name"] == name]
        if len(ids) > 1:
            raise P2AError("DUPLICATE_HISTORY_NAME", "Duplicate canary history name", folder_id=folder_id, name=name)
        return self.read(ids[0]) if ids else None

    def create(self, folder_id: str, name: str, content: bytes) -> DriveRead:
        if self.find(folder_id, name) is not None:
            raise P2AError("DUPLICATE_HISTORY_NAME", "Immutable canary history already exists", folder_id=folder_id, name=name)
        index = self._load_index()
        file_id = str(uuid.uuid4())
        path = self._path(file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        index["files"][file_id] = {"name": name, "folder_id": folder_id, "revision": 1}
        self._write_index(index)
        return self.read(file_id)

    def update(self, file_id: str, content: bytes) -> DriveRead:
        current = self.read(file_id)
        index = self._load_index()
        self._path(file_id).write_bytes(content)
        index["files"][file_id]["revision"] = current.revision + 1
        self._write_index(index)
        return self.read(file_id)

    def seed(self, file_id: str, folder_id: str, name: str, content: bytes) -> DriveRead:
        index = self._load_index()
        if file_id in index["files"]:
            raise P2AError("DUPLICATE_TASK_ID", "Canary seed ID already exists", file_id=file_id)
        path = self._path(file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        index["files"][file_id] = {"name": name, "folder_id": folder_id, "revision": 1}
        self._write_index(index)
        return self.read(file_id)


class GoogleDriveStore:
    """Authenticated provider-backed Drive store using the existing Engine transport."""

    MIME = "application/octet-stream"

    @classmethod
    def _mime_for_name(cls, name: str) -> str:
        if name.endswith(".json"):
            return "application/json"
        if name.endswith(".jsonl"):
            return "application/x-ndjson"
        if name.endswith((".yaml", ".yml")):
            return "text/yaml"
        if name.endswith(".md"):
            return "text/markdown"
        return cls.MIME

    def __init__(self, drive: Any) -> None:
        self.drive = drive

    @classmethod
    def from_trusted_runtime(cls, engine_root: str | Path, instance_root: str | Path) -> "GoogleDriveStore":
        try:
            from cba_kb.drive import Drive
            from cba_kb.instance import load_instance

            instance = load_instance(engine_root, instance_root)
            return cls(Drive(engine_root, instance=instance))
        except Exception as exc:
            raise P2AError("PROVIDER_AUTH_UNAVAILABLE", "Authenticated Google Drive provider is unavailable", error=type(exc).__name__) from exc

    def read(self, file_id: str) -> DriveRead:
        try:
            meta = self.drive.meta(file_id)
            content = self.drive.get(file_id)
        except Exception as exc:
            raise P2AError("PROVIDER_READ_FAILED", "Google Drive raw read failed", file_id=file_id, error=type(exc).__name__) from exc
        parents = meta.get("parents") or []
        return DriveRead(file_id, meta["name"], parents[0] if parents else "", int(meta["version"]), content)

    def find(self, folder_id: str, name: str) -> DriveRead | None:
        try:
            matches = [item for item in self.drive.list(folder_id) if item.get("name") == name]
        except Exception as exc:
            raise P2AError("PROVIDER_READ_FAILED", "Google Drive folder read failed", folder_id=folder_id, error=type(exc).__name__) from exc
        if len(matches) > 1:
            raise P2AError("DUPLICATE_HISTORY_NAME", "Provider history name is not unique", folder_id=folder_id, name=name)
        return self.read(matches[0]["id"]) if matches else None

    def create(self, folder_id: str, name: str, content: bytes) -> DriveRead:
        key = "p2a:" + sha256_bytes((folder_id + "\0" + name).encode("utf-8"))
        mime = self._mime_for_name(name)
        try:
            file_id = self.drive.ensure(folder_id, key, name, mime, content)
        except Exception as exc:
            raise P2AError("TRANSITION_HISTORY_WRITE_FAILED", "Provider history write failed", folder_id=folder_id, name=name, error=type(exc).__name__) from exc
        result = self.read(file_id)
        if result.content != content:
            raise P2AError("TRANSITION_READBACK_MISMATCH", "Provider-created history bytes differ", file_id=file_id)
        return result

    def update(self, file_id: str, content: bytes) -> DriveRead:
        before = self.read(file_id)
        try:
            mime = self.drive.meta(file_id)["mimeType"]
        except Exception as exc:
            raise P2AError("PROVIDER_READ_FAILED", "Provider stable MIME read failed", file_id=file_id, error=type(exc).__name__) from exc
        try:
            acknowledgement = self.drive.put(file_id, content, mime)
        except Exception as exc:
            raise P2AError("TRANSITION_POINTER_UPDATE_FAILED", "Provider stable pointer update failed", file_id=file_id, error=type(exc).__name__) from exc
        if acknowledgement.get("id") != file_id:
            raise P2AError("TRANSITION_POINTER_UPDATE_FAILED", "Provider acknowledgement changed stable file ID", expected=file_id, observed=acknowledgement.get("id"))
        after = self.read(file_id)
        if after.revision <= before.revision:
            raise P2AError("STABLE_REVISION_NOT_ADVANCED", "Provider stable revision did not advance", before=before.revision, after=after.revision)
        if after.content != content:
            raise P2AError("TRANSITION_READBACK_MISMATCH", "Provider stable readback bytes differ", file_id=file_id)
        return after
