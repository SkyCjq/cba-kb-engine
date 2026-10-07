"""Drive placement enforcement guard (REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01).

Implements fail-closed placement resolution, forbidden root enforcement,
two-phase reparenting transaction with root removal, strict set equality readback verification,
durable idempotency via Drive metadata/appProperties, class-based semantic validation,
and active artifact placement auditing.
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
    batch_id: Optional[str] = None
    source_pdf_parent_id: Optional[str] = None


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


# D2: Class-based semantic sets
HISTORY_CLASSES = {"HISTORY", "ARCHIVED", "LEGACY"}
STAGING_CLASSES = {"ACTIVE_WORK", "STAGING"}
CANONICAL_CLASSES = {"CANONICAL", "DATA"}
ACTIVE_ALLOWED_CLASSES = {"CANONICAL", "DATA", "NOTES", "AI_VIEW", "CONFIG", "VERSION_DEV"}

# 10 categories matrix
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


class PlacementRegistry:
    def __init__(
        self,
        folder_map: Dict[str, str],
        disallowed_roots: Optional[Set[str]] = None,
        allowed_override_parents: Optional[Set[str]] = None,
    ) -> None:
        self.folder_map = dict(folder_map)
        self.disallowed_roots = disallowed_roots or {"root", "my_drive", "cba_kb_root", ""}
        # D5: Minimal explicit allowlist for override_parent_id
        self.allowed_override_parents = set(allowed_override_parents) if allowed_override_parents else set()

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
        *,
        batch_id: Optional[str] = None,
        source_pdf_parent_id: Optional[str] = None,
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

        # D4: Dynamic resolution
        if target_folder_key == "INBOX":
            if batch_id:
                sub_key = f"INBOX/{batch_id}"
                if sub_key in self.folder_map:
                    resolved_id = self.folder_map[sub_key]
                else:
                    base_inbox = self.folder_map.get("INBOX")
                    resolved_id = f"{base_inbox}/{batch_id}" if base_inbox else f"inbox-{batch_id}"
            else:
                resolved_id = self.folder_map.get("INBOX", "")
        elif target_folder_key == "SOURCE_DERIVED":
            if source_pdf_parent_id:
                resolved_id = source_pdf_parent_id
            else:
                resolved_id = self.folder_map.get("SOURCE_DERIVED") or self.folder_map.get("SOURCES", "")
        elif target_folder_key.startswith("GOVERNANCE_"):
            gov_sub = target_folder_key.removeprefix("GOVERNANCE_")  # CURRENT, HISTORY, EVIDENCE, STAGING
            specific_key = f"{req_id}/{gov_sub}"
            if specific_key in self.folder_map:
                resolved_id = self.folder_map[specific_key]
            else:
                base_gov = self.folder_map.get(target_folder_key)
                resolved_id = f"{base_gov}/{req_id}" if base_gov else f"gov-{req_id}-{gov_sub}"
        elif target_folder_key == "STAGING_ARCHIVE":
            specific_key = f"ARCHIVE/{req_id}"
            if specific_key in self.folder_map:
                resolved_id = self.folder_map[specific_key]
            else:
                base_arch = self.folder_map.get("STAGING_ARCHIVE", "")
                resolved_id = f"{base_arch}/{req_id}" if base_arch else f"archive-{req_id}"
        else:
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

    def get_uncommitted(self) -> List[PlacementRecord]:
        return [r for r in self._records.values() if r.status == PlacementStatus.UNCOMMITTED]

    def assert_safe_to_dispatch(self, file_id: str) -> None:
        """D6: Dispatch gate. Unknown file ID or non-COMMITTED state is strictly rejected."""
        if file_id not in self._records:
            raise PlacementError(
                "UNKNOWN_FILE_DISPATCH",
                f"File ID '{file_id}' is unknown in placement registry; dispatch blocked",
                file_id=file_id,
            )
        rec = self._records[file_id]
        if rec.status != PlacementStatus.COMMITTED:
            raise PlacementError(
                "UNCOMMITTED_PLACEMENT",
                f"Artifact {file_id} status is {rec.status}; dispatch/transition blocked",
                file_id=file_id,
                status=rec.status,
            )

    def _find_durable_idempotent_file(self, parent_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """D3: Search for existing file with matching idempotency key in parent."""
        if not hasattr(self.drive, "list"):
            return None
        try:
            items = self.drive.list(parent_id)
            for item in items:
                app_props = item.get("appProperties") or {}
                if app_props.get("placement_idempotency_key") == idempotency_key or app_props.get("cba_key") == idempotency_key:
                    return item
        except Exception:
            pass
        return None

    def create_artifact(self, req: ArtifactPlacementRequest) -> PlacementRecord:
        # D5: Override check against explicit allowlist
        if req.override_parent_id:
            if req.override_parent_id not in self.registry.allowed_override_parents:
                raise PlacementError(
                    "OVERRIDE_NOT_ALLOWED",
                    f"Override parent '{req.override_parent_id}' is not in allowed override list",
                    override_parent_id=req.override_parent_id,
                )
            self.registry.validate_parent(req.override_parent_id)
            target_parent = req.override_parent_id
        else:
            target_parent = self.registry.resolve_parent_id(
                req_id=req.req_id,
                artifact_class=req.artifact_class,
                artifact_role=req.artifact_role,
                batch_id=req.batch_id,
                source_pdf_parent_id=req.source_pdf_parent_id,
            )

        # D2: Class semantic validation
        if req.artifact_class in HISTORY_CLASSES:
            # HISTORY class can only go to HISTORY/ARCHIVE destinations
            gov_history_ids = {
                self.registry.folder_map.get("GOVERNANCE_HISTORY"),
                self.registry.folder_map.get("STAGING_ARCHIVE"),
            }
            if not any(target_parent.startswith(h_id) for h_id in gov_history_ids if h_id):
                raise PlacementError(
                    "INVALID_PLACEMENT",
                    f"HISTORY class artifact '{req.artifact_class}' cannot be placed in non-history folder '{target_parent}'",
                    artifact_class=req.artifact_class,
                    parent_id=target_parent,
                )

        if req.artifact_class not in STAGING_CLASSES:
            # Non-staging artifacts cannot be placed in STAGING
            gov_staging_id = self.registry.folder_map.get("GOVERNANCE_STAGING")
            if gov_staging_id and (target_parent == gov_staging_id or target_parent.startswith(gov_staging_id)):
                raise PlacementError(
                    "INVALID_PLACEMENT",
                    f"Non-staging artifact class '{req.artifact_class}' cannot be placed in staging folder '{target_parent}'",
                    artifact_class=req.artifact_class,
                    parent_id=target_parent,
                )

        # D3: Durable idempotency check
        if req.idempotency_key:
            existing = self._find_durable_idempotent_file(target_parent, req.idempotency_key)
            if existing:
                rec = PlacementRecord(
                    file_id=existing["id"],
                    name=existing.get("name", req.name),
                    parent_id=target_parent,
                    status=PlacementStatus.COMMITTED,
                    req_id=req.req_id,
                    artifact_class=req.artifact_class,
                    artifact_role=req.artifact_role,
                    idempotency_key=req.idempotency_key,
                )
                self._records[existing["id"]] = rec
                return rec

        app_props = {}
        if req.idempotency_key:
            app_props["placement_idempotency_key"] = req.idempotency_key
            app_props["cba_key"] = req.idempotency_key

        # D1: Two-phase transaction with reparenting
        if req.must_reparent:
            temp_parent = "root"
            created = self.drive.create(
                name=req.name,
                parent=temp_parent,
                mime=req.mime,
                appProperties=app_props,
            )
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

            # Reparent: remove temp_parent and add target_parent
            try:
                self.drive.reparent(file_id, new_parent=target_parent, old_parent=temp_parent)
            except Exception as e:
                raise PlacementError(
                    "REPARENT_FAILED",
                    f"Reparenting failed for {file_id}: {e}",
                    file_id=file_id,
                ) from e
        else:
            created = self.drive.create(
                name=req.name,
                parent=target_parent,
                mime=req.mime,
                appProperties=app_props,
            )
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

        # D1: Exact set equality readback
        meta = self.drive.meta(file_id)
        actual_parents_set = set(meta.get("parents", []))
        expected_parents_set = {target_parent}

        # Any residual root or extra parent causes failure
        if actual_parents_set != expected_parents_set:
            record.status = PlacementStatus.FAILED
            raise PlacementError(
                "INVALID_PLACEMENT",
                f"Readback parents set {actual_parents_set} does not exactly match expected {expected_parents_set}",
                file_id=file_id,
                expected=expected_parents_set,
                actual=actual_parents_set,
            )

        # Commit transaction
        record.parent_id = target_parent
        record.status = PlacementStatus.COMMITTED
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
        batch_id = artifact.get("batch_id")
        source_pdf_parent_id = artifact.get("source_pdf_parent_id")

        try:
            meta = drive.meta(fid)
            parents = set(meta.get("parents", []))
            for p in parents:
                if p in registry.disallowed_roots:
                    root_objects += 1
                    violations.append({
                        "file_id": fid,
                        "name": name,
                        "violation": "PLACED_IN_ROOT",
                        "parent": p,
                    })

            expected_parent = registry.resolve_parent_id(
                req_id,
                a_class,
                a_role,
                batch_id=batch_id,
                source_pdf_parent_id=source_pdf_parent_id,
            )
            if parents != {expected_parent}:
                violations.append({
                    "file_id": fid,
                    "name": name,
                    "violation": "PARENT_MISMATCH",
                    "expected": {expected_parent},
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
