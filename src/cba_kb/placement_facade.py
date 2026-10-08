"""Mandatory placement enforcement facade (REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01).

F-01 bounded repair: routes the real Drive creation chains through fail-closed
placement enforcement without changing where artifacts are placed.

This module wraps a ``cba_kb.drive.Drive`` instance in a transparent proxy.
Every read operation delegates unchanged. The two creation operations
(``ensure`` / ``ensure_copy``) are intercepted and enforced BEFORE delegating:

1. Parent validation (fail-closed): empty / root / forbidden parents are
   rejected with INVALID_PLACEMENT via ``PlacementRegistry.validate_parent``.
2. Post-creation readback (fail-closed): the created object's parents are read
   back and must EXACTLY equal {requested parent}; otherwise INVALID_PLACEMENT.
   (PlacementGuard D1: residual extra parents, e.g. ["folder-1", "root"], are
   misplacements and must fail closed.)
3. Audit trail: every guarded creation is recorded for placement auditing.

What this does NOT do (by design, per the frozen contract's
"do not touch existing gate/projector/target semantics" clause):
- It does not re-resolve placement via the (class, role) matrix; the caller
  keeps deciding the target folder. It enforces the universal safety
  invariants: never to a forbidden location, never silently misplaced.
- The full matrix-based placement (``PlacementGuard.create_artifact``) remains
  the path for new intake flows.

Wiring points (each a one-line change at the entry point, no logic change):
- ``cba_kb.release.publish``: ``drive = PlacementEnforcedDrive(drive)``
- ``automation.drive_io.GoogleDriveStore.from_trusted_runtime``: wrap the Drive
- ``scripts/prepare_production.py`` reserve-staging call site: wrap the Drive
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .placement import PlacementError, PlacementRegistry


@dataclass(frozen=True)
class GuardedCreationRecord:
    """Audit record for one creation that passed through enforcement."""

    file_id: str
    parent_id: str
    key: str
    name: str
    operation: str  # "ensure" | "ensure_copy"


class PlacementEnforcedDrive:
    """Transparent Drive proxy with mandatory placement enforcement on creation.

    All attributes except ``ensure`` / ``ensure_copy`` delegate directly to the
    wrapped drive. The two creation methods enforce placement before delegating.
    """

    def __init__(self, drive: Any, registry: Optional[PlacementRegistry] = None) -> None:
        # Set _drive first: __getattr__ must never recurse during __init__.
        self.__dict__["_drive"] = drive
        self.__dict__["_registry"] = registry or PlacementRegistry(folder_map={})
        self.__dict__["_records"] = []

    def __getattr__(self, name: str) -> Any:
        # Called only when normal attribute lookup fails.
        drive = self.__dict__.get("_drive")
        if drive is None:
            raise AttributeError(name)
        return getattr(drive, name)

    # ------------------------------------------------------------------
    # enforcement internals
    # ------------------------------------------------------------------
    def _enforce_parent(self, parent_id: Optional[str], *, operation: str, key: str, name: str) -> None:
        """Fail-closed parent validation. Raises PlacementError on violation."""
        try:
            self._registry.validate_parent(parent_id)
        except PlacementError:
            raise
        except Exception as exc:  # fail-closed on unexpected validator errors
            raise PlacementError(
                "INVALID_PLACEMENT",
                f"Parent validation failed for {operation} '{name}'",
                operation=operation,
                key=key,
                name=name,
                cause=str(exc),
            ) from exc

    def _verify_readback(
        self, file_id: str, parent_id: str, *, operation: str, key: str, name: str
    ) -> None:
        """Fail-closed readback: created object must live under the requested parent."""
        try:
            meta = self._drive.meta(file_id)
        except Exception as exc:
            raise PlacementError(
                "PLACEMENT_READBACK_FAILED",
                f"Could not read back placement for {operation} '{name}'",
                operation=operation,
                key=key,
                name=name,
                file_id=file_id,
                cause=str(exc),
            ) from exc
        actual_parents = set(meta.get("parents") or [])
        # Exact set equality (PlacementGuard D1 contract): a residual extra
        # parent -- e.g. ["folder-1", "root"] -- IS a misplacement and must
        # fail closed. An enforcement layer is meant to be stricter than the
        # raw operation it wraps; "not stricter" was the round-1 error.
        if actual_parents != {parent_id}:
            raise PlacementError(
                "INVALID_PLACEMENT",
                f"Readback parents {sorted(actual_parents)} do not contain requested parent '{parent_id}'",
                operation=operation,
                key=key,
                name=name,
                file_id=file_id,
                expected_parent=parent_id,
                actual_parents=sorted(actual_parents),
            )
        self._records.append(
            GuardedCreationRecord(
                file_id=file_id,
                parent_id=parent_id,
                key=key,
                name=name,
                operation=operation,
            )
        )

    # ------------------------------------------------------------------
    # enforced creation (signatures mirror cba_kb.drive.Drive)
    # ------------------------------------------------------------------
    def ensure(
        self,
        parent: str,
        key: str,
        name: str,
        mime: str,
        content: Optional[bytes] = None,
        *,
        index: Optional[Dict[str, Any]] = None,
    ) -> str:
        self._enforce_parent(parent, operation="ensure", key=key, name=name)
        file_id = self._drive.ensure(parent, key, name, mime, content, index=index)
        self._verify_readback(file_id, parent, operation="ensure", key=key, name=name)
        return file_id

    def ensure_copy(
        self,
        parent: str,
        key: str,
        file_id: str,
        name: str,
        *,
        index: Optional[Dict[str, Any]] = None,
    ) -> str:
        self._enforce_parent(parent, operation="ensure_copy", key=key, name=name)
        new_id = self._drive.ensure_copy(parent, key, file_id, name, index=index)
        self._verify_readback(new_id, parent, operation="ensure_copy", key=key, name=name)
        return new_id

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    def guarded_creations(self) -> List[GuardedCreationRecord]:
        """Every creation that passed through enforcement, in order."""
        return list(self._records)


def placement_enforced(drive: Any, registry: Optional[PlacementRegistry] = None) -> PlacementEnforcedDrive:
    """One-line wiring helper for entry points."""
    if isinstance(drive, PlacementEnforcedDrive):
        return drive
    return PlacementEnforcedDrive(drive, registry)
