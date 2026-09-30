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
    find_private_locator_in_object,
    is_claim_publicly_exportable,
    is_private_locator,
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


# ---------------------------------------------------------------------------
# G4-R1 Adversarial: Claim evidence_refs locator sanitization
# ---------------------------------------------------------------------------
def test_claim_evidence_refs_locator_sanitization_g4_r1():
    doc_id = "doc_0123456789abcdef01234567"
    actor_a = make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001")
    actor_b = make_actor_ref("player", "孟铎", "doc_1#L2", "P0018_MENGDUO_0001")

    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor_a,
        "subject_actor_refs": [actor_b],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "https://example.com/press",
        "evidence_ref": f"{doc_id}#turn-1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })

    private_local_evidence = "/var/private/secrets/raw_evidence.txt#line-10"
    private_drive_evidence = "https://docs.google.com/document/d/synthetic_fake_drive_doc_id_000000000000/edit"
    nested_private_evidence = "/var/private/internal/nested_ref.txt"

    cid = generate_claim_id([stmt["statement_id"]], "防守带动进攻")
    valid_authority = {
        "decision_ref": "DEC-20260930-VERIFIED-01",
        "authority_kind": "human_review",
        "reviewer": "human_lead_editor",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "corroborated",
        "supporting_evidence_refs": [nested_private_evidence],
    }

    raw_claim = create_claim_from_statements(
        "防守带动进攻",
        [stmt],
        status="corroborated",
        transition_authority=valid_authority,
        relation_assertion={
            "relation_id": "rel_0123456789abcdef01234567",
            "relation_type": "teammate_of",
            "from_actor": actor_a,
            "to_actor": actor_b,
            "evidence_refs": [private_local_evidence],
            "supporting_statement_ids": [stmt["statement_id"]],
            "confidence": "HIGH",
        },
    )
    # Inject raw private evidence into the actual contract evidence_refs field
    raw_claim["evidence_refs"] = [private_local_evidence, private_drive_evidence, nested_private_evidence]
    validated_claim = validate_claim(raw_claim)

    auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]

    exported, cap = project_claims_for_consumer([validated_claim], [stmt], target="ChatGPT", authorizations=auth)
    assert cap == "MATERIALIZED"
    assert len(exported) == 1
    clean_claim = exported[0]

    # G4-R1: evidence_refs must be sanitized, never raw private values
    assert "/var/private/" not in str(clean_claim["evidence_refs"])
    assert "docs.google.com" not in str(clean_claim["evidence_refs"])
    for ev in clean_claim["evidence_refs"]:
        assert ev.startswith("consumer:chatgpt:")

    # G4-R1: relation_assertion evidence_refs must be sanitized
    assert "/var/private/" not in str(clean_claim["relation_assertion"]["evidence_refs"])

    # G4-R1: nested transition authority evidence refs in provenance must be sanitized
    serialized_claim = json.dumps(clean_claim)
    assert "/var/private/" not in serialized_claim
    assert "docs.google.com" not in serialized_claim

    # G4-R1: No non-contract supporting_evidence_refs top-level field
    assert "supporting_evidence_refs" not in clean_claim


# ---------------------------------------------------------------------------
# G4-R2 Adversarial: strong-status authority persistence & validation
# ---------------------------------------------------------------------------
def test_strong_status_authority_persistence_and_serialization_g4_r2():
    sid = "stmt_0123456789abcdef01234567"
    cid = generate_claim_id([sid], "防守带动进攻")
    stmt = {
        "statement_id": sid,
        "doc_id": "doc_0123456789abcdef01234567",
        "speaker_actor_ref": make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001"),
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守带动进攻",
        "source_ref": "press_release",
        "evidence_ref": "doc_1#L1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    }

    valid_authority = {
        "decision_ref": "DEC-20260930-GAME-01",
        "authority_kind": "human_review",
        "reviewer": "lead_editor",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "corroborated",
        "supporting_evidence_refs": ["doc_1#L1"],
    }

    # 1. Direct strong-status creation persists authority in provenance (G4-R2)
    claim = create_claim_from_statements(
        "防守带动进攻",
        [stmt],
        status="corroborated",
        transition_authority=valid_authority,
    )
    assert claim["status"] == "corroborated"
    assert "transitions" in claim["provenance"]
    assert len(claim["provenance"]["transitions"]) == 1
    assert claim["provenance"]["transitions"][0]["authority"]["decision_ref"] == "DEC-20260930-GAME-01"
    assert claim["provenance"]["latest_transition_authority"]["decision_ref"] == "DEC-20260930-GAME-01"

    # 2. Round-trip JSON serialization retains full authority and passes validation
    serialized = json.dumps(claim)
    deserialized = json.loads(serialized)
    validated = validate_claim(deserialized)
    assert validated["status"] == "corroborated"
    assert validated["provenance"]["transitions"][0]["authority"]["decision_ref"] == "DEC-20260930-GAME-01"

    # 3. If provenance loses the authority chain, validate_claim MUST reject it
    tampered_no_auth = dict(claim)
    tampered_no_auth["provenance"] = {"creator": "attacker"}
    with pytest.raises(ClaimError, match="REQUIRES_PERSISTED_TRANSITION_AUTHORITY"):
        validate_claim(tampered_no_auth)

    # 4. If authority target status mismatches claim status, validate_claim MUST reject it
    tampered_mismatch = dict(claim)
    tampered_mismatch["provenance"] = {
        "transitions": [{
            "from_status": "unverified",
            "to_status": "contradicted",
            "authority": dict(valid_authority, target_status="contradicted"),
        }]
    }
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_CHAIN_INVALID"):
        validate_claim(tampered_mismatch)


# ---------------------------------------------------------------------------
# Gen6 Precision Amendment 01: Mandatory Adversarial Tests
# ---------------------------------------------------------------------------
def test_serialized_strong_claim_authority_revalidated_gen6():
    """G6-R1: Valid serialized strong Claim and JSON round-trip must PASS with same claim_id and authority chain."""
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": "doc_0123456789abcdef01234567",
        "speaker_actor_ref": make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001"),
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "https://example.com/press",
        "evidence_ref": "doc_1#L1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })
    cid = generate_claim_id([stmt["statement_id"]], "防守带动进攻")
    valid_authority = {
        "decision_ref": "DEC-20260930-GAME-01",
        "authority_kind": "human_review",
        "reviewer": "lead_editor",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "corroborated",
        "supporting_evidence_refs": ["doc_1#L1"],
        "timestamp": "2026-09-30T12:00:00Z",
        "note": "Verified game tape",
    }
    claim = create_claim_from_statements(
        "防守带动进攻",
        [stmt],
        status="corroborated",
        transition_authority=valid_authority,
    )
    assert claim["status"] == "corroborated"
    assert claim["claim_id"] == cid
    assert claim["provenance"]["transitions"][0]["authority"]["decision_ref"] == "DEC-20260930-GAME-01"

    # JSON round-trip
    serialized = json.dumps(claim)
    deserialized = json.loads(serialized)
    validated = validate_claim(deserialized)
    assert validated["claim_id"] == cid
    assert validated["status"] == "corroborated"
    assert validated["provenance"]["transitions"][0]["authority"]["reviewer"] == "lead_editor"
    assert validated["provenance"]["transitions"][0]["authority"]["prior_claim_id"] == cid


def test_serialized_strong_claim_forged_authority_matrix_gen6():
    """G6-R1 Mandatory attack oracles for serialized strong claims fail closed."""
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": "doc_0123456789abcdef01234567",
        "speaker_actor_ref": make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001"),
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "https://example.com/press",
        "evidence_ref": "doc_1#L1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })
    cid = generate_claim_id([stmt["statement_id"]], "防守带动进攻")
    base_auth = {
        "decision_ref": "DEC-20260930-GAME-01",
        "authority_kind": "human_review",
        "reviewer": "lead_editor",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "corroborated",
        "supporting_evidence_refs": ["doc_1#L1"],
        "timestamp": "2026-09-30T12:00:00Z",
    }
    valid_claim = create_claim_from_statements(
        "防守带动进攻",
        [stmt],
        status="corroborated",
        transition_authority=base_auth,
    )

    # 1. reviewer=llm_self_asserted -> FAIL
    bad_reviewer = json.loads(json.dumps(valid_claim))
    bad_reviewer["provenance"]["transitions"][0]["authority"]["reviewer"] = "llm_self_asserted"
    bad_reviewer["provenance"]["latest_transition_authority"]["reviewer"] = "llm_self_asserted"
    with pytest.raises(ClaimError, match="INVALID_REVIEWER_AUTHORITY"):
        validate_claim(bad_reviewer)

    # 2. authority_kind outside VALID_AUTHORITY_KINDS -> FAIL
    bad_kind = json.loads(json.dumps(valid_claim))
    bad_kind["provenance"]["transitions"][0]["authority"]["authority_kind"] = "unauthorized_ai_bot"
    bad_kind["provenance"]["latest_transition_authority"]["authority_kind"] = "unauthorized_ai_bot"
    with pytest.raises(ClaimError, match="UNAUTHORIZED_AUTHORITY_KIND"):
        validate_claim(bad_kind)

    # 3. wrong prior_claim_id -> FAIL
    bad_cid = json.loads(json.dumps(valid_claim))
    bad_cid["provenance"]["transitions"][0]["authority"]["prior_claim_id"] = "claim_000000000000000000000000"
    bad_cid["provenance"]["latest_transition_authority"]["prior_claim_id"] = "claim_000000000000000000000000"
    with pytest.raises(ClaimError, match="PRIOR_CLAIM_ID_MISMATCH"):
        validate_claim(bad_cid)

    # 4. transition.from_status != authority.prior_status -> FAIL
    bad_from = json.loads(json.dumps(valid_claim))
    bad_from["provenance"]["transitions"][0]["from_status"] = "review_required"
    with pytest.raises(ClaimError, match="PRIOR_STATUS_MISMATCH"):
        validate_claim(bad_from)

    # 5. authority.target_status != claim.status -> FAIL
    bad_target = json.loads(json.dumps(valid_claim))
    bad_target["provenance"]["transitions"][0]["authority"]["target_status"] = "contradicted"
    bad_target["provenance"]["latest_transition_authority"]["target_status"] = "contradicted"
    with pytest.raises(ClaimError, match="TARGET_STATUS_MISMATCH"):
        validate_claim(bad_target)

    # 6. empty supporting_evidence_refs -> FAIL
    empty_ev = json.loads(json.dumps(valid_claim))
    empty_ev["provenance"]["transitions"][0]["authority"]["supporting_evidence_refs"] = []
    empty_ev["provenance"]["latest_transition_authority"]["supporting_evidence_refs"] = []
    with pytest.raises(ClaimError, match="SUPPORTING_EVIDENCE_REFS_REQUIRED"):
        validate_claim(empty_ev)

    # 7. missing decision_ref -> FAIL
    missing_dec = json.loads(json.dumps(valid_claim))
    missing_dec["provenance"]["transitions"][0]["authority"].pop("decision_ref", None)
    missing_dec["provenance"]["latest_transition_authority"].pop("decision_ref", None)
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_FIELDS_MISSING"):
        validate_claim(missing_dec)


def test_consumer_authorization_scope_basis_matrix_gen6():
    """G6-R2: Consumer authorization scope and basis matrix fails closed."""
    doc_id = "doc_0123456789abcdef01234567"
    actor = make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001")
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor,
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "https://example.com/press",
        "evidence_ref": "doc_1#L1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })
    claim = create_claim_from_statements("防守带动进攻", [stmt])

    # 1. Valid scope=statement_claim_research + basis=PUBLIC + matching target/doc -> MATERIALIZED
    valid_auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    stmts_out, cap_s = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=valid_auth)
    claims_out, cap_c = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=valid_auth)
    assert cap_s == "MATERIALIZED"
    assert cap_c == "MATERIALIZED"
    assert len(stmts_out) == 1
    assert len(claims_out) == 1

    # 2. scope=documents_only -> NOT_MATERIALIZED
    doc_only_auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "documents_only",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    _, cap_doc = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=doc_only_auth)
    assert "NOT_MATERIALIZED" in cap_doc

    # 3. basis=DENIED -> NOT_MATERIALIZED
    denied_auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "DENIED",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    _, cap_denied = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=denied_auth)
    assert "NOT_MATERIALIZED" in cap_denied

    # 4. missing authorization -> NOT_MATERIALIZED
    _, cap_none = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=None)
    assert "NOT_MATERIALIZED" in cap_none

    # 5. target mismatch -> NOT_MATERIALIZED
    gemini_auth = [{
        "doc_id": doc_id,
        "target": "Gemini Notebook",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    _, cap_target_mis = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=gemini_auth)
    assert "NOT_MATERIALIZED" in cap_target_mis

    # 6. doc mismatch -> NOT_MATERIALIZED
    diff_doc_auth = [{
        "doc_id": "doc_999999999999999999999999",
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]
    _, cap_doc_mis = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=diff_doc_auth)
    assert "NOT_MATERIALIZED" in cap_doc_mis


def test_relation_actor_locator_cross_platform_sanitization_gen6():
    """G6-R2: Cross-platform local path and Drive locators in relation actors/evidence must be sanitized."""
    doc_id = "doc_0123456789abcdef01234567"
    posix_path = "/opt/private/x"
    win_path = r"C:\Users\alice\secret.txt"
    unc_path = r"\\server\share\secret.txt"
    file_url = "file:///private/x"
    drive_url = "https://docs.google.com/document/d/synthetic_fake_drive_doc_id_000000000000/edit"

    actor_posix = make_actor_ref("player", "顾全", posix_path, "P0042_GUQUAN_0001")
    actor_win = make_actor_ref("player", "孟铎", win_path, "P0018_MENGDUO_0001")
    actor_unc = make_actor_ref("player", "顾全", unc_path, "P0042_GUQUAN_0001")
    actor_file = make_actor_ref("player", "孟铎", file_url, "P0018_MENGDUO_0001")

    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor_posix,
        "subject_actor_refs": [actor_win],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "防守第一",
        "source_ref": "https://example.com/press",
        "evidence_ref": f"{doc_id}#turn-1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })

    claim = create_claim_from_statements(
        "防守带动进攻",
        [stmt],
        relation_assertion={
            "relation_id": "rel_0123456789abcdef01234567",
            "relation_type": "teammate_of",
            "from_actor": actor_unc,
            "to_actor": actor_file,
            "evidence_refs": [drive_url],
            "supporting_statement_ids": [stmt["statement_id"]],
            "confidence": "HIGH",
        },
    )

    auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]

    exported, cap = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=auth)
    assert cap == "MATERIALIZED"
    assert len(exported) == 1
    mat_claim = exported[0]
    serialized = json.dumps(mat_claim)

    # Verify no raw private locator survives in materialized claim
    assert "/opt/private/x" not in serialized
    assert "C:\\Users\\alice" not in serialized
    assert "\\\\server\\share" not in serialized
    assert "file:///private/x" not in serialized
    assert "docs.google.com" not in serialized


def test_relation_assertion_bounded_to_enclosing_claim_gen6():
    """G6-R4: Relation assertion must be strictly bounded by enclosing Claim's statements and evidence."""
    actor_a = make_actor_ref("player", "顾全", "doc_1#L1", "P0042_GUQUAN_0001")
    actor_b = make_actor_ref("player", "孟铎", "doc_1#L2", "P0018_MENGDUO_0001")
    stmt_1 = "stmt_0123456789abcdef01234567"
    stmt_unrelated = "stmt_999999999999999999999999"

    base_claim = {
        "claim_id": "claim_0123456789abcdef01234567",
        "claim_text": "顾全与孟铎配合默契",
        "supporting_statement_ids": [stmt_1],
        "status": "unverified",
        "evidence_refs": ["doc_1#L1", "doc_1#L2"],
        "review_reason": None,
        "provenance": {"source": "interview"},
    }

    # 1. Unrelated relation statement ID -> FAIL
    c_unrelated_sid = dict(base_claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": ["doc_1#L1"],
        "supporting_statement_ids": [stmt_unrelated],
        "confidence": "HIGH",
    })
    with pytest.raises(ClaimError, match="RELATION_SUPPORTING_STATEMENTS_NOT_SUBSET_OF_CLAIM"):
        validate_claim(c_unrelated_sid)

    # 2. Mixture of one valid + one unrelated relation statement ID -> FAIL
    c_mixed_sid = dict(base_claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": ["doc_1#L1"],
        "supporting_statement_ids": [stmt_1, stmt_unrelated],
        "confidence": "HIGH",
    })
    with pytest.raises(ClaimError, match="RELATION_SUPPORTING_STATEMENTS_NOT_SUBSET_OF_CLAIM"):
        validate_claim(c_mixed_sid)

    # 3. Unrelated relation evidence ref -> FAIL
    c_unrelated_ev = dict(base_claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": ["doc_99#L99"],
        "supporting_statement_ids": [stmt_1],
        "confidence": "HIGH",
    })
    with pytest.raises(ClaimError, match="RELATION_EVIDENCE_REFS_NOT_SUBSET_OF_CLAIM"):
        validate_claim(c_unrelated_ev)

    # 4. Empty relation evidence -> FAIL under existing relation validator
    c_empty_ev = dict(base_claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": [],
        "supporting_statement_ids": [stmt_1],
        "confidence": "HIGH",
    })
    with pytest.raises(ClaimError, match="RELATION_EVIDENCE_REFS_REQUIRED"):
        validate_claim(c_empty_ev)

    # 5. Relation statement IDs subset of Claim AND relation evidence refs subset of Claim -> PASS
    c_valid = dict(base_claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": ["doc_1#L1"],
        "supporting_statement_ids": [stmt_1],
        "confidence": "HIGH",
    })
    validated = validate_claim(c_valid)
    assert validated["relation_assertion"]["relation_id"] == "rel_0123456789abcdef01234567"


def test_claim_transition_chain_invariant_adversarial():
    """Universal invariant: Strong Claim transition-chain validation matrix.

    Invariants tested:
    - first from_status must come from INITIAL_CLAIM_STATUSES ('unverified' or 'review_required').
    - bogus initial status fails closed.
    - valid-but-disconnected initial status fails closed.
    - null evidence ref ([None]) fails closed.
    - empty evidence refs ([] and [""]) fails closed.
    - unrelated evidence ref not bound to claim fails closed.
    - broken multi-transition continuity fails closed.
    - valid single transition passes.
    - valid multi-transition round trip passes with all transitions preserved.
    """
    doc_id = "doc_0123456789abcdef01234567"
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": make_actor_ref("player", "郭艾伦", "doc_1#L1", "P0001_GUOAILUN_0001"),
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "比赛很激烈",
        "source_ref": "https://example.com/press",
        "evidence_ref": f"{doc_id}#turn-1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })
    claim_ev = [f"{doc_id}#turn-1"]
    cid = generate_claim_id([stmt["statement_id"]], "比赛很激烈")

    base_auth = {
        "decision_ref": "DEC-TEST-001",
        "authority_kind": "human_review",
        "reviewer": "lead_editor",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "corroborated",
        "supporting_evidence_refs": [claim_ev[0]],
    }

    base_claim_dict = {
        "claim_id": cid,
        "claim_text": "比赛很激烈",
        "supporting_statement_ids": [stmt["statement_id"]],
        "status": "corroborated",
        "evidence_refs": list(claim_ev),
        "review_reason": None,
    }

    # 1. Bogus initial status -> FAIL
    c_bogus_init = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "bogus_status",
            "to_status": "corroborated",
            "authority": dict(base_auth, prior_status="bogus_status"),
        }],
    })
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_CHAIN_INVALID"):
        validate_claim(c_bogus_init)

    # 2. Valid status but disconnected (not an allowed initial status) -> FAIL
    c_discon_init = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "disputed",
            "to_status": "corroborated",
            "authority": dict(base_auth, prior_status="disputed"),
        }],
    })
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_CHAIN_INVALID"):
        validate_claim(c_discon_init)

    # 3. Null evidence ref in authority ([None]) -> FAIL
    c_null_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[None]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_NULL_FORBIDDEN"):
        validate_claim(c_null_ev)

    # 4. Empty evidence refs in authority ([]) -> FAIL
    c_empty_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[]),
        }],
    })
    with pytest.raises(ClaimError, match="SUPPORTING_EVIDENCE_REFS_REQUIRED"):
        validate_claim(c_empty_ev)

    # 5. Empty string evidence ref in authority ([""]) -> FAIL
    c_empty_str_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[""]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_EMPTY_FORBIDDEN"):
        validate_claim(c_empty_str_ev)

    # 5a. Empty dict in authority ([{}]) -> FAIL
    c_empty_dict_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[{}]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_SEMANTIC_EMPTY_FORBIDDEN"):
        validate_claim(c_empty_dict_ev)

    # 5b. Empty nested list in authority ([[]]) -> FAIL
    c_empty_nested_list_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[[]]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_SEMANTIC_EMPTY_FORBIDDEN"):
        validate_claim(c_empty_nested_list_ev)

    # 5c. Whitespace-only string in authority (["   "]) -> FAIL
    c_ws_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=["   \t\n  "]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_EMPTY_FORBIDDEN"):
        validate_claim(c_ws_ev)

    # 5d. Nested semantic-empty collections in authority -> FAIL
    c_nested_empty_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=[{"nested": [None, "", {}, []]}]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_SEMANTIC_EMPTY_FORBIDDEN"):
        validate_claim(c_nested_empty_ev)

    # 6. Unrelated evidence ref not bound to claim -> FAIL
    c_unrelated_ev = dict(base_claim_dict, provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": dict(base_auth, supporting_evidence_refs=["unrelated_doc#L99"]),
        }],
    })
    with pytest.raises(ClaimError, match="AUTHORITY_EVIDENCE_NOT_BOUND_TO_CLAIM"):
        validate_claim(c_unrelated_ev)

    # 7. Broken multi-transition continuity -> FAIL
    c_broken_cont = dict(base_claim_dict, provenance={
        "transitions": [
            {
                "from_status": "unverified",
                "to_status": "review_required",
                "authority": dict(base_auth, prior_status="unverified", target_status="review_required"),
            },
            {
                "from_status": "contradicted",
                "to_status": "corroborated",
                "authority": dict(base_auth, prior_status="contradicted", target_status="corroborated"),
            },
        ],
    })
    with pytest.raises(ClaimError, match="TRANSITION_CHAIN_DISCONTINUOUS"):
        validate_claim(c_broken_cont)

    # 8. Valid single transition -> PASS
    c_valid_single = create_claim_from_statements(
        "比赛很激烈",
        [stmt],
        status="corroborated",
        transition_authority=base_auth,
    )
    assert validate_claim(c_valid_single)["status"] == "corroborated"

    # 9. Valid multi-transition round trip -> PASS
    c_initial = create_claim_from_statements("比赛很激烈", [stmt], status="unverified")
    assert c_initial["status"] == "unverified"

    auth_step1 = {
        "decision_ref": "DEC-TEST-002",
        "authority_kind": "human_review",
        "reviewer": "fact_checker",
        "prior_claim_id": cid,
        "prior_status": "unverified",
        "target_status": "review_required",
        "supporting_evidence_refs": [claim_ev[0]],
    }
    c_step1 = transition_claim_status(c_initial, "review_required", transition_authority=auth_step1)
    assert c_step1["status"] == "review_required"

    auth_step2 = {
        "decision_ref": "DEC-TEST-003",
        "authority_kind": "human_review",
        "reviewer": "lead_editor",
        "prior_claim_id": cid,
        "prior_status": "review_required",
        "target_status": "corroborated",
        "supporting_evidence_refs": [claim_ev[0]],
    }
    c_corroborated = transition_claim_status(c_step1, "corroborated", transition_authority=auth_step2)
    validated_multi = validate_claim(c_corroborated)
    assert validated_multi["status"] == "corroborated"
    assert len(validated_multi["provenance"]["transitions"]) == 2

    # 10. Valid structured non-empty evidence bound to claim -> PASS
    structured_ev = {"doc_id": doc_id, "turn": 1, "anchor": "L1-L5"}
    auth_struct = dict(base_auth, supporting_evidence_refs=[structured_ev])
    c_struct = dict(base_claim_dict, evidence_refs=[structured_ev], provenance={
        "transitions": [{
            "from_status": "unverified",
            "to_status": "corroborated",
            "authority": auth_struct,
        }],
    })
    val_struct = validate_claim(c_struct)
    assert val_struct["status"] == "corroborated"
    assert val_struct["evidence_refs"] == [structured_ev]


def test_consumer_locator_free_invariant_adversarial():
    """Universal invariant: Final consumer projection locator-free recursive postcondition.

    Invariants tested:
    - Normal Chinese text with slashes / punctuation is NOT falsely flagged (MATERIALIZED).
    - Leak in claim_text (/opt/private/claim) fails closed as NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS).
    - Leak in actor.raw_name (/opt/private/person) fails closed as NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS).
    - Leak in nested dict/list fails closed as NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS).
    - Windows absolute path, UNC path, file URL, Drive URL fail closed.
    - Statement leak propagates failure to Claim projection.
    """
    doc_id = "doc_0123456789abcdef01234567"
    actor_normal = make_actor_ref("player", "郭艾伦", "doc_1#L1", "P0001_GUOAILUN_0001")
    stmt = validate_statement({
        "statement_id": "stmt_0123456789abcdef01234567",
        "doc_id": doc_id,
        "speaker_actor_ref": actor_normal,
        "subject_actor_refs": [],
        "time_anchor": "2024-03-03",
        "statement_text_or_controlled_excerpt": "2024/2025赛季场均得分/篮板表现优秀",
        "source_ref": "https://example.com/press",
        "evidence_ref": f"{doc_id}#turn-1",
        "attribution_type": "structured_turn",
        "rights": {"classification": "public", "public_export_allowed": True, "evidence": ["cc"]},
        "provenance": {"source": "interview"},
        "extraction_status": "accepted",
    })

    auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]

    claim = create_claim_from_statements(
        "2024/2025赛季场均得分/篮板表现优秀",
        [stmt],
        status="unverified",
    )

    # 1. Normal Chinese text with slashes / standard terminology must pass MATERIALIZED
    exp_stmts, cap_stmts = project_statements_for_consumer([stmt], target="ChatGPT", authorizations=auth)
    assert cap_stmts == "MATERIALIZED"
    assert len(exp_stmts) == 1
    exp_claims, cap_claims = project_claims_for_consumer([claim], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_claims == "MATERIALIZED"
    assert len(exp_claims) == 1

    # 2. Private locator in claim_text -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    claim_leaky_text = dict(claim, claim_text="/opt/private/claim")
    _, cap_bad_text = project_claims_for_consumer([claim_leaky_text], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_bad_text == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 3. Private locator in actor.raw_name -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    actor_leaky = make_actor_ref("player", "/opt/private/person", "doc_1#L1", "P0001_GUOAILUN_0001")
    stmt_leaky_actor = dict(stmt, speaker_actor_ref=actor_leaky)
    _, cap_stmt_actor = project_statements_for_consumer([stmt_leaky_actor], target="ChatGPT", authorizations=auth)
    assert cap_stmt_actor == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    claim_for_leaky_stmt = create_claim_from_statements("正常断言", [stmt_leaky_actor], status="unverified")
    _, cap_claim_actor = project_claims_for_consumer([claim_for_leaky_stmt], [stmt_leaky_actor], target="ChatGPT", authorizations=auth)
    assert cap_claim_actor == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 4. Windows absolute path leaf -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    claim_win = dict(claim, claim_text=r"C:\Users\private\document.txt")
    _, cap_win = project_claims_for_consumer([claim_win], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_win == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 5. UNC path leaf -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    claim_unc = dict(claim, claim_text=r"\\corp\share\secrets.docx")
    _, cap_unc = project_claims_for_consumer([claim_unc], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_unc == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 6. file:// URL leaf -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    claim_file_url = dict(claim, claim_text="file:///var/secret/keys.json")
    _, cap_file_url = project_claims_for_consumer([claim_file_url], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_file_url == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 7. Drive URL leaf in nested actor ref inside relation_assertion -> NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)
    actor_drive = make_actor_ref(
        "unresolved",
        "https://docs.google.com/document/d/synthetic_fake_drive_doc_id_000000000000/edit",
        "doc_1#L1",
    )
    claim_nested = dict(claim, relation_assertion={
        "relation_id": "rel_0123456789abcdef01234567",
        "relation_type": "teammate_of",
        "from_actor": actor_normal,
        "to_actor": actor_drive,
        "evidence_refs": [claim["evidence_refs"][0]],
        "supporting_statement_ids": claim["supporting_statement_ids"],
        "confidence": "HIGH",
    })
    _, cap_nested = project_claims_for_consumer([claim_nested], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_nested == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 8. Closure B: Private locator as top-level dict key
    assert find_private_locator_in_object({"/opt/private/key": "val"}) == "/opt/private/key"
    assert find_private_locator_in_object({"C:\\secrets\\pass.txt": 1}) == "C:\\secrets\\pass.txt"
    assert find_private_locator_in_object({"\\\\corp\\share\\sec": True}) == "\\\\corp\\share\\sec"
    assert find_private_locator_in_object({"file:///var/log/secret": "ok"}) == "file:///var/log/secret"
    assert find_private_locator_in_object({"https://docs.google.com/document/d/fake/edit": "ok"}) == "https://docs.google.com/document/d/fake/edit"

    # 9. Closure B: Private locator as nested dict key
    nested_dict_leak = {"level1": {"level2": {"/etc/shadow": "root"}}}
    assert find_private_locator_in_object(nested_dict_leak) == "/etc/shadow"

    claim_nested_key_leak = dict(claim, provenance={"/opt/private/config": "secret"})
    _, cap_nested_key = project_claims_for_consumer([claim_nested_key_leak], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_nested_key == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 10. Closure B: Private locator in nested list / value
    nested_list_leak = {"items": [1, [2, {"safe": ["normal", "/Users/private/data.json"]}]]}
    assert find_private_locator_in_object(nested_list_leak) == "/Users/private/data.json"

    nested_list_tuple_set = {"data": (1, {"nested_set": {"normal", r"C:\secrets\key.pem"}})}
    assert find_private_locator_in_object(nested_list_tuple_set) == r"C:\secrets\key.pem"

    claim_nested_list_key_leak = dict(claim, provenance={"nested_list": [{"safe": 1}, {"/opt/private/path": 2}]})
    _, cap_nested_list = project_claims_for_consumer([claim_nested_list_key_leak], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_nested_list == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"

    # 11. Closure B: Normal Chinese and business keys are not harmed -> MATERIALIZED
    normal_business_obj = {
        "赛季信息": "2024-2025",
        "球员姓名": "郭艾伦",
        "statement_text_or_controlled_excerpt": "比赛很激烈",
        "business_metrics": {
            "得分": 30,
            "助攻": 10,
            "efficiency_rating": 25.5,
        },
    }
    assert find_private_locator_in_object(normal_business_obj) is None

    claim_normal_keys = dict(claim, provenance={
        "source": "interview",
        "赛季信息": "2024-2025",
        "custom_business_metrics": {"得分效率": 1.25, "胜率评估": "HIGH"},
    })
    exp_normal, cap_normal = project_claims_for_consumer([claim_normal_keys], [stmt], target="ChatGPT", authorizations=auth)
    assert cap_normal == "MATERIALIZED"
    assert len(exp_normal) == 1
    assert exp_normal[0]["provenance"]["赛季信息"] == "2024-2025"
