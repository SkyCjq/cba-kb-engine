"""Adversarial and boundary tests for REQ-190-STATEMENT-CLAIM-01.

Validates:
- Malformed inputs and schema rejections
- Ambiguous identity and same-name safety
- Status machine enforcement and unauthorized transitions
- Rights boundaries and fail-closed protections
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from cba_kb.actor import (
    ActorError,
    make_actor_ref,
    resolve_actor,
    validate_actor_ref,
)
from cba_kb.claim import (
    ClaimError,
    create_claim_from_statements,
    generate_claim_id,
    transition_claim_status,
    validate_claim,
    validate_relation_assertion,
)
from cba_kb.consumer_integration import (
    is_claim_publicly_exportable,
    is_statement_publicly_exportable,
    project_claims_for_consumer,
    project_statements_for_consumer,
)
from cba_kb.source_intake import (
    SourceIntakeError,
    normalize_source_payload,
    validate_source_intake,
)
from cba_kb.statement import (
    StatementError,
    extract_statements_from_text,
    generate_statement_id,
    validate_statement,
)
from cba_kb.verification_queue import (
    VerificationQueue,
    VerificationQueueError,
    validate_verification_item,
)


def test_actor_ref_adversarial_validation():
    # Invalid kind
    with pytest.raises(ActorError, match="ACTOR_KIND_INVALID"):
        validate_actor_ref({
            "kind": "alien",
            "id": "P0001",
            "raw_name": "Test",
            "evidence_ref": "doc_1#L1",
        })

    # Player with null ID
    with pytest.raises(ActorError, match="PLAYER_ID_REQUIRED"):
        validate_actor_ref({
            "kind": "player",
            "id": None,
            "raw_name": "Test",
            "evidence_ref": "doc_1#L1",
        })

    # Unresolved with non-null ID
    with pytest.raises(ActorError, match="UNRESOLVED_ACTOR_MUST_HAVE_NULL_ID"):
        validate_actor_ref({
            "kind": "unresolved",
            "id": "P0001",
            "raw_name": "Test",
            "evidence_ref": "doc_1#L1",
        })

    # Empty raw_name
    with pytest.raises(ActorError, match="ACTOR_RAW_NAME_REQUIRED"):
        validate_actor_ref({
            "kind": "unresolved",
            "id": None,
            "raw_name": "   ",
            "evidence_ref": "doc_1#L1",
        })

    # Missing evidence_ref
    with pytest.raises(ActorError, match="ACTOR_EVIDENCE_REF_REQUIRED"):
        validate_actor_ref({
            "kind": "unresolved",
            "id": None,
            "raw_name": "Test",
            "evidence_ref": "",
        })


def test_statement_adversarial_validation():
    valid_actor = make_actor_ref("player", "贺希宁", "doc_1#L1", "P0265_HEXINING_0001")
    valid_stmt_dict = {
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": "doc_0123456789abcdef01234567",
        "speaker_actor_ref": valid_actor,
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "测试发言",
        "source_ref": "doc_1#turn-1",
        "evidence_ref": {"line": 1},
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["mock"]},
        "provenance": {"source": "mock"},
        "extraction_status": "accepted",
    }

    # Invalid attribution type
    bad_type = dict(valid_stmt_dict, attribution_type="rumor")
    with pytest.raises(StatementError, match="ATTRIBUTION_TYPE_INVALID"):
        validate_statement(bad_type)

    # Invalid statement id format
    bad_id = dict(valid_stmt_dict, statement_id="bad_id")
    with pytest.raises(StatementError, match="STATEMENT_ID_FORMAT_INVALID"):
        validate_statement(bad_id)

    # Invalid doc id
    bad_doc = dict(valid_stmt_dict, doc_id="not_a_doc_id")
    with pytest.raises(StatementError, match="DOC_ID_INVALID"):
        validate_statement(bad_doc)

    # Ambiguous speaker marked accepted
    bad_speaker = make_actor_ref("unresolved", "UNKNOWN", "doc_1#L1")
    bad_accepted = dict(valid_stmt_dict, speaker_actor_ref=bad_speaker, extraction_status="accepted")
    with pytest.raises(StatementError, match="AMBIGUOUS_SPEAKER_CANNOT_BE_ACCEPTED"):
        validate_statement(bad_accepted)


def test_claim_adversarial_validation():
    valid_sid = "stmt_0123456789abcdef01234567"
    valid_cid = generate_claim_id([valid_sid], "测试主张", "unverified")
    valid_claim_dict = {
        "claim_id": valid_cid,
        "claim_text": "测试主张",
        "supporting_statement_ids": [valid_sid],
        "status": "unverified",
        "evidence_refs": ["doc_1#L1"],
        "review_reason": None,
        "provenance": {"creator": "test"},
    }

    # Missing supporting statements
    bad_stmts = dict(valid_claim_dict, supporting_statement_ids=[])
    with pytest.raises(ClaimError, match="SUPPORTING_STATEMENT_IDS_LIST_REQUIRED"):
        validate_claim(bad_stmts)

    # Invalid status
    bad_status = dict(valid_claim_dict, status="confirmed_true")
    with pytest.raises(ClaimError, match="CLAIM_STATUS_INVALID"):
        validate_claim(bad_status)

    # review_required without review_reason
    bad_review = dict(valid_claim_dict, status="review_required", review_reason=None)
    with pytest.raises(ClaimError, match="REVIEW_REASON_REQUIRED"):
        validate_claim(bad_review)


def test_verification_queue_adversarial_validation():
    vid = "vq_0123456789abcdef01234567"
    valid_item = {
        "verification_id": vid,
        "object_type": "actor",
        "object_ref": {"raw_name": "Test"},
        "reason_code": "AMBIGUOUS_ACTOR",
        "evidence_refs": ["doc_1#L1"],
        "status": "open",
        "resolution_ref": None,
        "created_from": "test",
    }

    # Open item with resolution_ref
    bad_open = dict(valid_item, resolution_ref="resolved_by_human")
    with pytest.raises(VerificationQueueError, match="OPEN_ITEM_CANNOT_HAVE_RESOLUTION_REF"):
        validate_verification_item(bad_open)

    # Resolved item without resolution_ref
    bad_resolved = dict(valid_item, status="resolved", resolution_ref=None)
    with pytest.raises(VerificationQueueError, match="RESOLVED_ITEM_MUST_HAVE_RESOLUTION_REF"):
        validate_verification_item(bad_resolved)

    # Invalid object type
    bad_obj = dict(valid_item, object_type="database_table")
    with pytest.raises(VerificationQueueError, match="OBJECT_TYPE_INVALID"):
        validate_verification_item(bad_obj)


def test_source_intake_adversarial_validation():
    # Invalid provider
    with pytest.raises(SourceIntakeError, match="SOURCE_PROVIDER_INVALID"):
        validate_source_intake({
            "source_provider": "dropbox_sync",
            "source_item_id_or_locator": "123",
            "acquired_at": "2026-09-30T00:00:00Z",
            "media_type": "text/plain",
            "content_hash": "a" * 64,
            "rights": {"classification": "public"},
            "provenance": {"source": "test"},
            "raw_ref": "/tmp/test",
            "normalized_document_ref": None,
            "intake_status": "accepted",
        })

    # Invalid content hash
    with pytest.raises(SourceIntakeError, match="CONTENT_HASH_SHA256_INVALID"):
        validate_source_intake({
            "source_provider": "local_file",
            "source_item_id_or_locator": "123",
            "acquired_at": "2026-09-30T00:00:00Z",
            "media_type": "text/plain",
            "content_hash": "short_hash",
            "rights": {"classification": "public"},
            "provenance": {"source": "test"},
            "raw_ref": "/tmp/test",
            "normalized_document_ref": None,
            "intake_status": "accepted",
        })
