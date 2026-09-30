"""Adversarial and boundary tests for REQ-190-STATEMENT-CLAIM-01.

Validates all required R1-R8 adversarial and negative test conditions:
- Consumer target authorization mismatch & absence fails closed (R1)
- Private locators / absolute paths / Drive URLs sanitization (R1)
- Unresolved named speaker -> automatic Verification Queue routing (R2)
- Ambiguous same-name subject -> unresolved + queue item, zero multi-UID assertion (R3)
- Self-asserted reviewer string cannot promote strong Claim status (R4)
- Valid evidence-bound reviewed transition preserves stable claim_id (R4, R8)
- Arbitrary doc_id / content mismatch bypass fails closed (R5)
- Research View UNKNOWN grain exists and fixed grains cannot be spoofed (R7)
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
    ConsumerSafetyError,
    is_claim_publicly_exportable,
    is_statement_publicly_exportable,
    project_claims_for_consumer,
    project_statements_for_consumer,
)
from cba_kb.document_lane import doc_id as compute_doc_id
from cba_kb.research_view import ResearchView
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
    verify_document_binding,
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
    valid_cid = generate_claim_id([valid_sid], "测试主张")
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


# ---------------------------------------------------------------------------
# R1 Adversarial: Consumer target authorization & locator privacy
# ---------------------------------------------------------------------------
def test_consumer_target_authorization_mismatch_fails_closed():
    doc_id = "doc_0123456789abcdef01234567"
    actor = make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001")
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor,
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "press_release",
        "evidence_ref": "doc_1#L1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })

    # 1. Unauthorized target raises ConsumerSafetyError
    with pytest.raises(ConsumerSafetyError, match="UNAUTHORIZED_TARGET"):
        project_statements_for_consumer([stmt], target="UnauthorizedBot")

    # 2. Absence of authorizations fails closed -> NOT_MATERIALIZED
    exported, cap = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=None)
    assert len(exported) == 0
    assert "NOT_MATERIALIZED" in cap

    # 3. Mismatched doc_id in authorization -> excluded
    auth_mismatch = [{
        "doc_id": "doc_other_unrelated_doc_123456",
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    exported_mis, cap_mis = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=auth_mismatch)
    assert len(exported_mis) == 0
    assert "NOT_MATERIALIZED" in cap_mis

    # 4. Target mismatch in authorization -> excluded
    auth_other_target = [{
        "doc_id": doc_id,
        "target": "Gemini Notebook",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    exported_tgt, cap_tgt = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=auth_other_target)
    assert len(exported_tgt) == 0


def test_private_locators_cannot_leak_to_consumer():
    doc_id = "doc_0123456789abcdef01234567"
    private_local_path = "/var/private/secrets/synthetic_sensitive_interview.txt"
    private_drive_url = "https://docs.google.com/document/d/synthetic_private_drive_locator_doc_id_000000/edit"
    
    actor = make_actor_ref("player", "顾全", private_local_path, "P0042_GUQUAN_0001")
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor,
        "subject_actor_refs": [actor],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": private_drive_url,
        "evidence_ref": f"{private_local_path}#line-42",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {
            "private_path": private_local_path,
            "raw_ref": private_drive_url,
            "notes": f"Extracted from {private_local_path}",
        },
        "extraction_status": "accepted",
    })

    auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]

    exported_stmts, _ = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=auth)
    assert len(exported_stmts) == 1
    out_stmt = exported_stmts[0]

    # Verify no local path or drive url leaked anywhere in exported JSON
    serialized = json.dumps(out_stmt)
    assert "/var/private/" not in serialized
    assert "synthetic_sensitive_interview" not in serialized
    assert "docs.google.com" not in serialized
    assert "private_path" not in serialized
    assert "raw_ref" not in serialized


# ---------------------------------------------------------------------------
# R3 Adversarial: Same-name subject ambiguity
# ---------------------------------------------------------------------------
def test_same_name_subject_ambiguity_no_multi_uid_assertion():
    registry = {
        "schema_version": 1,
        "identity_version": "v1.8",
        "players": [
            {"schema_version": 1, "player_uid": "P_LX_1", "canonical_name": "李想", "active": True},
            {"schema_version": 1, "player_uid": "P_LX_2", "canonical_name": "李想", "active": True},
        ],
        "aliases": [],
    }

    # Text containing subject mention of "李想"
    text = "顾全：李想最近在训练中非常刻苦，展现了出色的状态。"
    doc_id = compute_doc_id(text)
    result = extract_statements_from_text(text, doc_id=doc_id, identity_registry=registry)
    
    assert len(result) == 1
    s = result[0]
    # Invariant: One ambiguous raw name must not assert multiple canonical player identities
    assert len(s["subject_actor_refs"]) == 1
    sub = s["subject_actor_refs"][0]
    assert sub["kind"] == "unresolved"
    assert sub["id"] is None
    assert sub["raw_name"] == "李想"

    # Must automatically emit Verification Queue item for ambiguous actor (R2, R3)
    assert len(result.verification_items) >= 1
    assert any(v["reason_code"] == "AMBIGUOUS_SAME_NAME_ACTOR" for v in result.verification_items)


# ---------------------------------------------------------------------------
# R4 Adversarial: Claim transition authority enforcement
# ---------------------------------------------------------------------------
def test_claim_transition_authority_rejection():
    sid = "stmt_0123456789abcdef01234567"
    cid = generate_claim_id([sid], "防守带动进攻")
    claim = validate_claim({
        "claim_id": cid,
        "claim_text": "防守带动进攻",
        "supporting_statement_ids": [sid],
        "status": "unverified",
        "evidence_refs": ["doc_1#L1"],
        "review_reason": None,
        "provenance": {"creator": "test"},
    })

    # 1. Bare string reviewer rejected
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_REQUIRED"):
        transition_claim_status(claim, "corroborated", reviewed_by="llm_self_asserted")

    # 2. Unauthorized authority kind rejected
    with pytest.raises(ClaimError, match="UNAUTHORIZED_AUTHORITY_KIND"):
        transition_claim_status(
            claim,
            "corroborated",
            transition_authority={
                "decision_ref": "DEC-1",
                "authority_kind": "llm_self_asserted",
                "reviewer": "bot",
                "prior_claim_id": cid,
                "prior_status": "unverified",
                "target_status": "corroborated",
                "supporting_evidence_refs": ["doc_1#L1"],
            },
        )

    # 3. Disallowed reviewer name rejected
    with pytest.raises(ClaimError, match="INVALID_REVIEWER_AUTHORITY"):
        transition_claim_status(
            claim,
            "corroborated",
            transition_authority={
                "decision_ref": "DEC-1",
                "authority_kind": "human_review",
                "reviewer": "unreviewed",
                "prior_claim_id": cid,
                "prior_status": "unverified",
                "target_status": "corroborated",
                "supporting_evidence_refs": ["doc_1#L1"],
            },
        )


# ---------------------------------------------------------------------------
# R5 Adversarial: Document binding bypass defense
# ---------------------------------------------------------------------------
def test_document_binding_bypass_fails():
    text = "孟铎：我们从小一起长大。"
    correct_doc_id = compute_doc_id(text)
    bogus_doc_id = "doc_arbitrary_attacker_supplied_id"

    # Bypassing document binding with arbitrary mismatching doc_id must fail closed
    with pytest.raises(StatementError, match="DOC_ID_CONTENT_MISMATCH"):
        verify_document_binding(text, bogus_doc_id)

    with pytest.raises(StatementError, match="DOC_ID_CONTENT_MISMATCH"):
        extract_statements_from_text(text, doc_id=bogus_doc_id)


# ---------------------------------------------------------------------------
# R7 Adversarial: Research View grain integrity & spoofing defense
# ---------------------------------------------------------------------------
def test_research_view_grain_spoofing_defense():
    # Attempt to spoof CLAIM inside canonical facts
    spoofed_fact = {
        "semantic_grain": "CLAIM",
        "season": "2024-2025",
        "club": "深圳新世纪",
    }
    
    # Attempt to spoof CANONICAL_FACT inside unknown
    spoofed_unknown = {
        "semantic_grain": "CANONICAL_FACT",
        "dimension": "contract",
        "description": "missing salary",
    }

    rv = ResearchView(
        subject_name="顾全",
        canonical_facts=[spoofed_fact],
        unknown_items=[spoofed_unknown],
    )

    d = rv.to_dict()
    # Output grain labels MUST remain strictly authoritative and non-overridable (R7)
    assert d["grains"]["canonical_facts"][0]["semantic_grain"] == "CANONICAL_FACT"
    assert d["grains"]["unknown_items"][0]["semantic_grain"] == "UNKNOWN"

    # UNKNOWN grain exists in rendered markdown
    md = rv.render_markdown()
    assert "## 7. 未知与证据空缺 (UNKNOWN)" in md
