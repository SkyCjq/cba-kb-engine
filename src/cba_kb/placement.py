"""Drive placement enforcement guard (REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01).

Implements fail-closed placement resolution, forbidden root enforcement,
two-phase reparenting transaction, strict readback verification, and active artifact auditing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class PlacementStatus(str, Enum):
    COMMITTED = "COMMITTED"
    UNCOMMITTED = "UNCOMMITTED"
    FAILED = "FAILED"


class PlacementError(RuntimeError):
    """Raised when placement resolution or verification violates safety invariants."""

    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        self.code = code
        self.details = kwargs
        super().__init__(f"[{code}] {message} ({kwargs})" if kwargs else f"[{code}] {message}")


@dataclass(frozen=True)
class ArtifactPlacementRequest:
    req_id: str
    artifact_class: str
    artifact_role: str
    name: str
    content: Optional[bytes] = None
    mime: str = "text/plain"
    must_reparent: bool = False
    override_parent_id: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class PlacementRecord:
    file_id: str
    name: str
    parent_id: str
    status: PlacementStatus
    req_id: str
    artifact_class: str
    artifact_role: str
    idempotency_key: Optional[str] = None


# 10 categories mapping to destination parent directory keys
# 1. 待处理: 00_inbox_待处理/<batch_id>/
# 2. 原始 Source: 10_sources_原始证据/
# 3. OCR 衍生: 与原 PDF 同目录 (SOURCE_DERIVED -> SOURCES)
# 4. 正式结构化事实: 20_data_结构化事实/
# 5. 人工笔记: 30_notes_人工知识/
# 6. AI View/索引: 40_ai_投喂与索引/
# 7. registry/taxonomy: 60_config_配置与词表/
# 8. 治理产物: 80_requirements_需求与版本/00_ACTIVE/<REQ-ID>/{CURRENT,HISTORY,EVIDENCE,STAGING}
# 9. 中间产物: <REQ-ID>/STAGING (关闭后进 90_archive_历史归档/)
# 10. 版本开发文档: v2.0.2_版本开发归档/
PLACEMENT_MATRIX = {
    ("INBOX", "BATCH"): "INBOX",
    ("SOURCE", "ORIGINAL"): "SOURCES",
    ("SOURCE_DERIVED", "OCR"): "SOURCE_DERIVED",
    ("DATA", "CANONICAL"): "DATA",
    ("NOTES", "MANUAL"): "NOTES",
    ("AI_VIEW", "INDEX"): "AI_VIEW",
    ("CONFIG", "REGISTRY"): "CONFIG",
    ("CANONICAL", "GOVERNANCE"): "GOVERNANCE_CURRENT",
    ("ACTIVE_WORK", "STAGING"): "GOVERNANCE_STAGING",
    ("HISTORY", "ARCHIVE"): "GOVERNANCE_HISTORY",
    ("HISTORY", "AUDIT_LOG"): "GOVERNANCE_HISTORY",
    ("EVIDENCE", "RAW"): "GOVERNANCE_EVIDENCE",
    ("STAGING_ARCHIVE", "ARCHIVE"): "STAGING_ARCHIVE",
    ("VERSION_DEV", "DOC"): "VERSION_DEV",
}

AUDIT_RULES = {
    "DISALLOWED_CLASSES_IN_ACTIVE": {"HISTORY", "ARCHIVED", "LEGACY"},
    "ACTIVE_CLASSES": {"ACTIVE_WORK", "CANONICAL", "DATA", "AI_VIEW", "CONFIG", "VERSION_DEV"},
}


class PlacementRegistry:
    def __init__(
        self,
        folder_map: Dict[str, str],
        disallowed_roots: Optional[Set[str]] = None,
    ) -> None:
        self.folder_map = dict(folder_map)
        self.disallowed_roots = disallowed_roots or {"root", "my_drive", "cba_kb_root", ""}

    def validate_parent(self, parent_id: Optional[str]) -> None:
        if not parent_id or parent_id in self.disallowed_roots:
            raise PlacementError(
                "ROOT_PLACEMENT_FORBIDDEN",
                f"Direct placement to root or forbidden folder '{parent_id}' is rejected",
                parent_id=parent_id,
            )

    def resolve_parent_id(
        self,
        req_id: str,
        artifact_class: str,
        artifact_role: str,
    ) -> str:
        key = (artifact_class, artifact_role)
        if key not in PLACEMENT_MATRIX:
            raise PlacementError(
                "UNMAPPED_ARTIFACT",
                f"Artifact ({artifact_class}, {artifact_role}) is not mapped in placement matrix",
                req_id=req_id,
                artifact_class=artifact_class,
                artifact_role=artifact_role,
            )

        target_folder_key = PLACEMENT_MATRIX[key]
        if target_folder_key not in self.folder_map:
            raise PlacementError(
                "UNRESOLVED_FOLDER_KEY",
                f"Target folder key '{target_folder_key}' not resolved in folder map",
                folder_key=target_folder_key,
            )

        resolved_id = self.folder_map[target_folder_key]
        self.validate_parent(resolved_id)
        return resolved_id


class PlacementGuard:
    def __init__(
        self,
        drive: Any,
        registry: PlacementRegistry,
    ) -> None:
        self.drive = drive
        self.registry = registry
        self._records: Dict[str, PlacementRecord] = {}
        self._idempotency_map: Dict[str, str] = {}

    def get_uncommitted(self) -> List[PlacementRecord]:
        return [r for r in self._records.values() if r.status == PlacementStatus.UNCOMMITTED]

    def assert_safe_to_dispatch(self, file_id: str) -> None:
        if file_id not in self._records:
            return
        rec = self._records[file_id]
        if rec.status != PlacementStatus.COMMITTED:
            raise PlacementError(
                "UNCOMMITTED_PLACEMENT",
                f"Artifact {file_id} status is {rec.status}; dispatch/transition blocked",
                file_id=file_id,
                status=rec.status,
            )

    def create_artifact(self, req: ArtifactPlacementRequest) -> PlacementRecord:
        # Check idempotency
        if req.idempotency_key and req.idempotency_key in self._idempotency_map:
            existing_fid = self._idempotency_map[req.idempotency_key]
            return self._records[existing_fid]

        # Resolve expected parent
        expected_parent = self.registry.resolve_parent_id(
            req_id=req.req_id,
            artifact_class=req.artifact_class,
            artifact_role=req.artifact_role,
        )

        # Enforce history vs active invariant
        if req.override_parent_id:
            self.registry.validate_parent(req.override_parent_id)
            if req.artifact_class in AUDIT_RULES["DISALLOWED_CLASSES_IN_ACTIVE"]:
                # Check if override is an active folder
                active_parent_ids = {
                    self.registry.folder_map.get(k)
                    for k in ("GOVERNANCE_CURRENT", "DATA", "AI_VIEW", "CONFIG")
                }
                if req.override_parent_id in active_parent_ids:
                    raise PlacementError(
                        "INVALID_PLACEMENT",
                        f"HISTORY artifact class '{req.artifact_class}' cannot be placed in active folder '{req.override_parent_id}'",
                        artifact_class=req.artifact_class,
                        parent_id=req.override_parent_id,
                    )
            target_parent = req.override_parent_id
        else:
            target_parent = expected_parent

        # Two-phase transaction if reparent is needed
        if req.must_reparent:
            # Stage 1: created uncommitted
            temp_parent = "temp-staging-root"
            created = self.drive.create(name=req.name, parent=temp_parent, mime=req.mime)
            file_id = created["id"]
            record = PlacementRecord(
                file_id=file_id,
                name=req.name,
                parent_id=temp_parent,
                status=PlacementStatus.UNCOMMITTED,
                req_id=req.req_id,
                artifact_class=req.artifact_class,
                artifact_role=req.artifact_role,
                idempotency_key=req.idempotency_key,
            )
            self._records[file_id] = record

            # Stage 2: reparent to target
            try:
                self.drive.reparent(file_id, new_parent=target_parent, old_parent=temp_parent)
            except Exception as e:
                raise PlacementError(
                    "REPARENT_FAILED",
                    f"Reparenting failed for {file_id}: {e}",
                    file_id=file_id,
                ) from e
        else:
            created = self.drive.create(name=req.name, parent=target_parent, mime=req.mime)
            file_id = created["id"]
            record = PlacementRecord(
                file_id=file_id,
                name=req.name,
                parent_id=target_parent,
                status=PlacementStatus.UNCOMMITTED,
                req_id=req.req_id,
                artifact_class=req.artifact_class,
                artifact_role=req.artifact_role,
                idempotency_key=req.idempotency_key,
            )
            self._records[file_id] = record

        # Readback verification
        meta = self.drive.meta(file_id)
        actual_parents = meta.get("parents", [])
        if target_parent not in actual_parents:
            record.status = PlacementStatus.FAILED
            raise PlacementError(
                "INVALID_PLACEMENT",
                f"Readback parent does not match resolved parent. Expected {target_parent}, got {actual_parents}",
                file_id=file_id,
                expected=target_parent,
                actual=actual_parents,
            )

        # Commit transaction
        record.parent_id = target_parent
        record.status = PlacementStatus.COMMITTED
        if req.idempotency_key:
            self._idempotency_map[req.idempotency_key] = file_id

        return record


def audit_active_placements(
    drive: Any,
    registry: PlacementRegistry,
    active_artifacts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Audit all active artifacts against placement registry and root restrictions."""
    violations = []
    root_objects = 0

    for artifact in active_artifacts:
        fid = artifact.get("id")
        name = artifact.get("name", "unknown")
        req_id = artifact.get("req_id", "v2.0.2")
        a_class = artifact.get("artifact_class", "CANONICAL")
        a_role = artifact.get("artifact_role", "GOVERNANCE")

        try:
            meta = drive.meta(fid)
            parents = meta.get("parents", [])
            for p in parents:
                if p in registry.disallowed_roots:
                    root_objects += 1
                    violations.append({
                        "file_id": fid,
                        "name": name,
                        "violation": "PLACED_IN_ROOT",
                        "parent": p,
                    })

            expected_parent = registry.resolve_parent_id(req_id, a_class, a_role)
            if expected_parent not in parents:
                violations.append({
                    "file_id": fid,
                    "name": name,
                    "violation": "PARENT_MISMATCH",
                    "expected": expected_parent,
                    "actual": parents,
                })
        except Exception as e:
            violations.append({
                "file_id": fid,
                "name": name,
                "violation": "AUDIT_EXCEPTION",
                "error": str(e),
            })

    return {
        "status": "PASS" if not violations and root_objects == 0 else "FAIL",
        "violations": violations,
        "root_new_objects": root_objects,
        "total_audited": len(active_artifacts),
    }
