"""Tests for Drive move exact plan schema (M2)."""
from __future__ import annotations

import pytest

from cba_kb import drive_move_plan as dmp


def _valid_plan():
    return dmp.create_drive_move_plan(
        file_id="file123",
        source_id="src456",
        doc_id="doc789",
        source_parent="folderA",
        destination="folderB",
        expected_parent_set=["folderB"],
        disposition="ARCHIVE",
        approval_ref="human-approval-001",
        sha256="abc123" * 10,
        revision_id="rev1",
        mime_type="text/plain",
    )


def test_valid_plan():
    plan = _valid_plan()
    assert plan["file_id"] == "file123"
    assert plan["approval_ref"] == "human-approval-001"


def test_missing_approval_rejected():
    with pytest.raises(dmp.DriveMovePlanError, match="PLAN_APPROVAL_REQUIRED"):
        dmp.create_drive_move_plan(
            file_id="f", source_id="s", doc_id="d",
            source_parent="a", destination="b",
            expected_parent_set=["b"],
            disposition="ARCHIVE",
            approval_ref="",  # Empty!
            sha256="x" * 60,
        )


def test_invalid_disposition_rejected():
    with pytest.raises(dmp.DriveMovePlanError,
                       match="PLAN_INVALID_DISPOSITION"):
        dmp.create_drive_move_plan(
            file_id="f", source_id="s", doc_id="d",
            source_parent="a", destination="b",
            expected_parent_set=["b"],
            disposition="DELETE",  # Invalid!
            approval_ref="ok",
            sha256="x" * 60,
        )


def test_retain_destination_mismatch_rejected():
    with pytest.raises(dmp.DriveMovePlanError,
                       match="PLAN_RETAIN_DESTINATION_MISMATCH"):
        dmp.create_drive_move_plan(
            file_id="f", source_id="s", doc_id="d",
            source_parent="a", destination="b",  # Different!
            expected_parent_set=["a"],
            disposition="RETAIN_IN_PLACE",
            approval_ref="ok",
            sha256="x" * 60,
        )


def test_retain_in_place_valid():
    plan = dmp.create_drive_move_plan(
        file_id="f", source_id="s", doc_id="d",
        source_parent="a", destination="a",  # Same!
        expected_parent_set=["a"],
        disposition="RETAIN_IN_PLACE",
        approval_ref="ok",
        sha256="x" * 60,
    )
    assert plan["disposition"] == "RETAIN_IN_PLACE"


def test_missing_sha256_rejected():
    plan = _valid_plan()
    plan["identity_and_revision_evidence"] = {}
    with pytest.raises(dmp.DriveMovePlanError,
                       match="PLAN_EVIDENCE_MISSING_SHA256"):
        dmp.validate_drive_move_plan(plan)


def test_empty_parent_set_rejected():
    with pytest.raises(dmp.DriveMovePlanError,
                       match="PLAN_EXPECTED_PARENT_SET_INVALID"):
        dmp.create_drive_move_plan(
            file_id="f", source_id="s", doc_id="d",
            source_parent="a", destination="b",
            expected_parent_set=[],  # Empty!
            disposition="ARCHIVE",
            approval_ref="ok",
            sha256="x" * 60,
        )
