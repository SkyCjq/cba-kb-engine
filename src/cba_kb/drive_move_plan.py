"""Drive move exact plan schema (FINAL INTEGRATION M2).

This module defines the EXACT plan schema for Google Drive file moves
(reparent). It validates that a proposed move plan is complete, authorized,
and safe BEFORE any Drive API call.

Exact plan schema:
{
  "file_id": str,                    # Drive file ID to move
  "source_id": str,                  # Source record ID
  "doc_id": str,                     # Document record ID
  "source_parent": str,              # Current parent folder ID (must match)
  "destination": str,                # Target folder ID
  "expected_parent_set": [str],      # Exact expected parents after move
  "disposition": str,                # "ARCHIVE" | "RETAIN_IN_PLACE"
  "approval_ref": str,               # Human approval reference (required)
  "identity_and_revision_evidence": {
    "sha256": str,                   # Expected file hash
    "revision_id": str,              # Expected revision
    "mime_type": str,
  }
}

Safety invariants (fail-closed):
- approval_ref is REQUIRED. No approval → no plan.
- source_parent must match the actual current parent (verified at apply).
- expected_parent_set must be exact (no partial matches).
- disposition must be explicit (no default move).
- For RETAIN_IN_PLACE, destination must equal source_parent.

This module does NOT execute moves. Execution requires:
1. A validated plan from this module.
2. Integration with drive_plan.py's guarded apply workflow.
3. Human Production GO for real Drive writes (separate authorization).
"""
from __future__ import annotations

from typing import Any, Dict, List

PLAN_SCHEMA_VERSION = 1

# Required fields for a Drive move plan.
REQUIRED_FIELDS = (
    "file_id",
    "source_id",
    "doc_id",
    "source_parent",
    "destination",
    "expected_parent_set",
    "disposition",
    "approval_ref",
    "identity_and_revision_evidence",
)

VALID_DISPOSITIONS = ("ARCHIVE", "RETAIN_IN_PLACE")


class DriveMovePlanError(ValueError):
    """Raised when a Drive move plan is invalid (fail-closed)."""
    pass


def validate_drive_move_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a Drive move exact plan. Returns the plan if valid.

    Raises DriveMovePlanError if any invariant is violated.
    This is fail-closed: any missing or invalid field → error.
    """
    if not isinstance(plan, dict):
        raise DriveMovePlanError("PLAN_NOT_A_DICT")

    # Check all required fields present.
    for field in REQUIRED_FIELDS:
        if field not in plan or plan[field] is None:
            raise DriveMovePlanError(f"PLAN_MISSING_FIELD: {field}")

    # approval_ref is mandatory (no default, no empty).
    approval_ref = plan["approval_ref"]
    if not isinstance(approval_ref, str) or not approval_ref.strip():
        raise DriveMovePlanError("PLAN_APPROVAL_REQUIRED")

    # disposition must be explicit.
    disposition = plan["disposition"]
    if disposition not in VALID_DISPOSITIONS:
        raise DriveMovePlanError(
            f"PLAN_INVALID_DISPOSITION: {disposition}")

    # expected_parent_set must be a non-empty list of strings.
    expected = plan["expected_parent_set"]
    if not isinstance(expected, list) or not expected:
        raise DriveMovePlanError("PLAN_EXPECTED_PARENT_SET_INVALID")
    for parent in expected:
        if not isinstance(parent, str) or not parent.strip():
            raise DriveMovePlanError("PLAN_EXPECTED_PARENT_SET_INVALID")

    # For RETAIN_IN_PLACE, destination must equal source_parent.
    if disposition == "RETAIN_IN_PLACE":
        if plan["destination"] != plan["source_parent"]:
            raise DriveMovePlanError(
                "PLAN_RETAIN_DESTINATION_MISMATCH: destination must equal "
                "source_parent for RETAIN_IN_PLACE")

    # identity_and_revision_evidence must have sha256.
    evidence = plan["identity_and_revision_evidence"]
    if not isinstance(evidence, dict):
        raise DriveMovePlanError("PLAN_EVIDENCE_NOT_A_DICT")
    if not evidence.get("sha256"):
        raise DriveMovePlanError("PLAN_EVIDENCE_MISSING_SHA256")

    # All checks passed.
    return plan


def create_drive_move_plan(
    file_id: str,
    source_id: str,
    doc_id: str,
    source_parent: str,
    destination: str,
    expected_parent_set: List[str],
    disposition: str,
    approval_ref: str,
    sha256: str,
    revision_id: str = "",
    mime_type: str = "",
) -> Dict[str, Any]:
    """Create a validated Drive move exact plan.

    Raises DriveMovePlanError if validation fails.
    """
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "file_id": file_id,
        "source_id": source_id,
        "doc_id": doc_id,
        "source_parent": source_parent,
        "destination": destination,
        "expected_parent_set": expected_parent_set,
        "disposition": disposition,
        "approval_ref": approval_ref,
        "identity_and_revision_evidence": {
            "sha256": sha256,
            "revision_id": revision_id,
            "mime_type": mime_type,
        },
    }
    return validate_drive_move_plan(plan)
