from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import P2AError, canonical_json_bytes


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
