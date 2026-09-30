"""Acceptance test suite for REQ-190-STATEMENT-CLAIM-01.

Validates all 13 required acceptance categories:
1. structured-turn exact attribution
2. direct quote vs narrator separation
3. ambiguous attribution -> review_required
4. player actor reuse without identity mutation
5. unresolved non-player actor with id=null
6. deterministic statement/claim IDs
7. no Fact mutation
8. rights/private evidence fail-closed
9. claim status authority safety
10. Verification Queue routing
11. provider-neutral Source Intake normalization
12. Research View semantic-grain separation
13. compatibility with existing Document/Identity/Consumer tests
+ real canary reference coverage (P0265 贺希宁 + 郑永刚) when staged locally.
"""
from __future__ import annotations

import json
import os
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
    generate_relation_id,
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
from cba_kb.document_lane import doc_id as compute_doc_id
from cba_kb.research_view import ResearchView
from cba_kb.source_intake import normalize_source_payload, validate_source_intake
from cba_kb.statement import (
    StatementError,
    extract_statements_from_text,
    generate_statement_id,
    validate_statement,
)
from cba_kb.verification_queue import VerificationQueue, validate_verification_item

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "requirements/REQ-190-STATEMENT-CLAIM-01/fixtures"


@pytest.fixture
def identity_registry():
    path = FIXTURES_DIR / "synthetic_identity_registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def structured_turn_text():
    path = FIXTURES_DIR / "synthetic_interview_turns.txt"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def narrative_quotes_text():
    path = FIXTURES_DIR / "synthetic_narrative_quotes.txt"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Structured-turn exact attribution (A02, B1)
# ---------------------------------------------------------------------------
def test_structured_turn_exact_attribution(identity_registry, structured_turn_text):
    doc_id = compute_doc_id(structured_turn_text)
    statements = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )

    assert len(statements) == 4
    # Turn 1: 孟铎
    s1 = statements[0]
    assert s1["attribution_type"] == "structured_turn"
    assert s1["speaker_actor_ref"]["raw_name"] == "孟铎"
    assert s1["speaker_actor_ref"]["kind"] == "player"
    assert s1["speaker_actor_ref"]["id"] == "P0018_MENGDUO_0001"
    assert s1["extraction_status"] == "accepted"
    assert "那场比赛在第四节最后阶段" in s1["statement_text_or_controlled_excerpt"]

    # Turn 2: 顾全
    s2 = statements[1]
    assert s2["attribution_type"] == "structured_turn"
    assert s2["speaker_actor_ref"]["raw_name"] == "顾全"
    assert s2["speaker_actor_ref"]["kind"] == "player"
    assert s2["speaker_actor_ref"]["id"] == "P0042_GUQUAN_0001"
    assert s2["extraction_status"] == "accepted"
    assert "我们当时顶住了很大压力" in s2["statement_text_or_controlled_excerpt"]


# ---------------------------------------------------------------------------
# 2. Direct quote vs narrator separation (A03, B2)
# ---------------------------------------------------------------------------
def test_direct_quote_vs_narrator_separation(identity_registry, narrative_quotes_text):
    doc_id = compute_doc_id(narrative_quotes_text)
    statements = extract_statements_from_text(
        narrative_quotes_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )

    # Narrative sentences like "在2024-2025赛季常规赛关键对决中..." must NOT be attributed to any player
    for s in statements:
        assert "关键对决中" not in s["statement_text_or_controlled_excerpt"]
        assert "出战38分钟" not in s["statement_text_or_controlled_excerpt"]

    # Direct quote 1: 贺希宁
    hexining_stmts = [s for s in statements if s["speaker_actor_ref"]["raw_name"] == "贺希宁"]
    assert len(hexining_stmts) == 1
    hs = hexining_stmts[0]
    assert hs["attribution_type"] == "direct_quote"
    assert hs["statement_text_or_controlled_excerpt"] == "我们一直在按照教练的部署去打，关键时刻每个人都站了出来。"
    assert hs["extraction_status"] == "accepted"

    # Direct quote 2: 沈梓捷
    shenzijie_stmts = [s for s in statements if s["speaker_actor_ref"]["raw_name"] == "沈梓捷"]
    assert len(shenzijie_stmts) == 1
    ss = shenzijie_stmts[0]
    assert ss["attribution_type"] == "direct_quote"
    assert ss["statement_text_or_controlled_excerpt"] == "回到熟悉的主场感觉非常亲切，大家在防守端互相信任。"
    assert ss["speaker_actor_ref"]["id"] == "P0310_SHENZIJIE_0001"


# ---------------------------------------------------------------------------
# 3. Ambiguous attribution -> review_required (A04, B9)
# ---------------------------------------------------------------------------
def test_ambiguous_attribution_review_required(identity_registry, narrative_quotes_text):
    doc_id = compute_doc_id(narrative_quotes_text)
    statements = extract_statements_from_text(
        narrative_quotes_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )

    indirect_stmts = [s for s in statements if s["attribution_type"] == "indirect_attribution"]
    assert len(indirect_stmts) >= 1
    ind = indirect_stmts[0]
    assert ind["extraction_status"] == "review_required"
    assert ind["speaker_actor_ref"]["kind"] == "unresolved"
    assert ind["speaker_actor_ref"]["id"] is None
    # Check that verification queue item was automatically emitted (R2)
    assert len(statements.verification_items) >= 1
    assert any(v["reason_code"] == "AMBIGUOUS_ATTRIBUTION" for v in statements.verification_items)


# ---------------------------------------------------------------------------
# 4. Player actor reuse without identity mutation (A05, B3)
# ---------------------------------------------------------------------------
def test_player_actor_reuse_without_mutation(identity_registry):
    # Resolving known active player
    actor = resolve_actor(
        "贺希宁",
        evidence_ref="doc_1#L1",
        identity_registry=identity_registry,
    )
    assert actor["kind"] == "player"
    assert actor["id"] == "P0265_HEXINING_0001"
    assert actor["raw_name"] == "贺希宁"

    # Resolving via alias "大鸟"
    alias_actor = resolve_actor(
        "大鸟",
        evidence_ref="doc_1#L2",
        identity_registry=identity_registry,
    )
    assert alias_actor["kind"] == "player"
    assert alias_actor["id"] == "P0310_SHENZIJIE_0001"

    # Verify input identity registry was NOT mutated
    assert len(identity_registry["players"]) == 6
    assert len(identity_registry["aliases"]) == 1


# ---------------------------------------------------------------------------
# 5. Unresolved non-player actor with id=null & same-name fail-safe (A06, B4)
# ---------------------------------------------------------------------------
def test_unresolved_non_player_actor_and_same_name_safety(identity_registry):
    # Non-player / coach (郑永刚)
    coach = resolve_actor(
        "郑永刚",
        evidence_ref="doc_1#L6",
        identity_registry=identity_registry,
    )
    assert coach["kind"] == "unresolved"
    assert coach["id"] is None
    assert coach["raw_name"] == "郑永刚"
    assert coach["evidence_ref"] == "doc_1#L6"

    # Same-name ambiguity: "李想" exists twice as active players -> same-name must never create identity!
    ambiguous = resolve_actor(
        "李想",
        evidence_ref="doc_1#L8",
        identity_registry=identity_registry,
    )
    assert ambiguous["kind"] == "unresolved"
    assert ambiguous["id"] is None
    assert ambiguous["raw_name"] == "李想"


# ---------------------------------------------------------------------------
# 6. Deterministic statement/claim IDs and replay (A01, B6)
# ---------------------------------------------------------------------------
def test_deterministic_statement_and_claim_ids(identity_registry, structured_turn_text):
    doc_id = compute_doc_id(structured_turn_text)
    run1 = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )
    run2 = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )

    assert len(run1) == len(run2)
    for s1, s2 in zip(run1, run2):
        assert s1["statement_id"] == s2["statement_id"]
        assert s1 == s2

    # Deterministic claim generation
    claim1 = create_claim_from_statements(
        "深圳男篮在第四节压力下贯彻防守部署",
        run1[:2],
    )
    claim2 = create_claim_from_statements(
        "深圳男篮在第四节压力下贯彻防守部署",
        run2[:2],
    )
    assert claim1["claim_id"] == claim2["claim_id"]
    assert claim1 == claim2


# ---------------------------------------------------------------------------
# 7. No Fact mutation (A07)
# ---------------------------------------------------------------------------
def test_no_fact_mutation(identity_registry, structured_turn_text):
    # Snapshot before
    before_reg = json.dumps(identity_registry, sort_keys=True)

    doc_id = compute_doc_id(structured_turn_text)
    statements = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )
    claim = create_claim_from_statements("深圳男篮团队氛围单纯", statements)

    # Invariant: Claim is research layer, never written to canonical facts or identity
    assert "fact_id" not in claim
    assert "canonical" not in claim
    after_reg = json.dumps(identity_registry, sort_keys=True)
    assert before_reg == after_reg


# ---------------------------------------------------------------------------
# 8. Rights/private evidence fail-closed & target authorization (A08, B7, R1)
# ---------------------------------------------------------------------------
def test_rights_fail_closed(identity_registry, structured_turn_text):
    doc_id = compute_doc_id(structured_turn_text)

    # Private statements
    private_stmts = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
        rights={"classification": "private", "public_export_allowed": False, "evidence": []},
        provenance={"private_path": "/var/private/secrets/secret.txt"},
    )
    claim = create_claim_from_statements("测试主张", private_stmts)

    # Without authorizations -> fail-closed
    exported_unauth, cap_unauth = project_statements_for_consumer(private_stmts)
    assert len(exported_unauth) == 0
    assert "NOT_MATERIALIZED" in cap_unauth

    auth = [{
        "doc_id": doc_id,
        "target": "ChatGPT",
        "allowed_scope": "statement_claim_research",
        "authorization_basis": "PUBLIC",
        "frozen_at": "2026-09-30T00:00:00Z",
    }]

    # Even with target authorization, private statements must NOT be exported
    exported_stmts, cap_stmts = project_statements_for_consumer(
        private_stmts, target="ChatGPT", authorizations=auth
    )
    assert len(exported_stmts) == 0
    assert "NOT_MATERIALIZED" in cap_stmts

    exported_claims, cap_claims = project_claims_for_consumer(
        [claim], private_stmts, target="ChatGPT", authorizations=auth
    )
    assert len(exported_claims) == 0
    assert "NOT_MATERIALIZED" in cap_claims

    # Public statements with valid rights and evidence
    public_stmts = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
        rights={"classification": "public", "public_export_allowed": True, "evidence": ["license_cc_by"]},
        provenance={"source_locator": "public_press_interview"},
    )
    pub_claim = create_claim_from_statements("公开主张", public_stmts)

    exported_pub_stmts, pub_cap = project_statements_for_consumer(
        public_stmts, target="ChatGPT", authorizations=auth
    )
    assert len(exported_pub_stmts) == len(public_stmts)
    assert pub_cap == "MATERIALIZED"

    exported_pub_claims, pub_claim_cap = project_claims_for_consumer(
        [pub_claim], public_stmts, target="ChatGPT", authorizations=auth
    )
    assert len(exported_pub_claims) == 1
    assert pub_claim_cap == "MATERIALIZED"


# ---------------------------------------------------------------------------
# 9. Claim status authority safety (A11, B8, R4, R8)
# ---------------------------------------------------------------------------
def test_claim_status_authority_safety(identity_registry, structured_turn_text):
    doc_id = compute_doc_id(structured_turn_text)
    statements = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )

    # Automatic creation defaults to unverified
    claim = create_claim_from_statements("深圳男篮在第四节贯彻防守", statements[:2])
    assert claim["status"] == "unverified"
    orig_claim_id = claim["claim_id"]

    # Attempting to create claim directly with corroborated status without reviewed authority must fail
    with pytest.raises(ClaimError, match="REQUIRES_TRANSITION_AUTHORITY"):
        create_claim_from_statements(
            "未经审核推断",
            statements[:2],
            status="corroborated",
        )

    # Free-form reviewer string alone without transition authority must fail (R4)
    with pytest.raises(ClaimError, match="TRANSITION_AUTHORITY_REQUIRED"):
        transition_claim_status(
            claim,
            "corroborated",
            reviewed_by="human_lead_editor",
            resolution_note="Verified against game tape and press audio",
        )

    # Proper status transition under valid evidence-bound transition authority (R4)
    valid_authority = {
        "decision_ref": "DEC-20260930-GAME-TAPE-01",
        "authority_kind": "human_review",
        "reviewer": "human_lead_editor",
        "prior_claim_id": claim["claim_id"],
        "prior_status": claim["status"],
        "target_status": "corroborated",
        "supporting_evidence_refs": claim["evidence_refs"],
    }
    transitioned = transition_claim_status(
        claim,
        "corroborated",
        transition_authority=valid_authority,
        resolution_note="Verified against game tape and press audio",
    )
    assert transitioned["status"] == "corroborated"
    assert transitioned["provenance"]["reviewed_by"] == "human_lead_editor"
    # Claim ID must be stable across lifecycle (R8)
    assert transitioned["claim_id"] == orig_claim_id


# ---------------------------------------------------------------------------
# 10. Verification Queue routing (A04, B9, R2)
# ---------------------------------------------------------------------------
def test_verification_queue_routing(tmp_path):
    queue = VerificationQueue()

    # Route an ambiguous actor
    vid1 = queue.create_and_add(
        object_type="actor",
        object_ref={"raw_name": "郑永刚", "candidate_kinds": ["coach", "person"]},
        reason_code="UNRESOLVED_ACTOR",
        evidence_refs=["doc_1#L6"],
        created_from="statement_extractor",
    )
    assert vid1.startswith("vq_")

    # Route an ambiguous statement attribution
    vid2 = queue.create_and_add(
        object_type="statement",
        object_ref="stmt_indirect_0001",
        reason_code="AMBIGUOUS_ATTRIBUTION",
        evidence_refs=["doc_1#L7"],
        created_from="statement_extractor",
    )

    items = queue.list_items()
    assert len(items) == 2
    assert all(i["status"] == "open" for i in items)

    # Test queue persistence and load
    q_file = tmp_path / "queue.json"
    queue.save(q_file)

    loaded = VerificationQueue.load(q_file)
    assert len(loaded.list_items()) == 2
    assert loaded.get(vid1)["reason_code"] == "UNRESOLVED_ACTOR"


# ---------------------------------------------------------------------------
# 11. Provider-neutral Source Intake normalization (A09, R5)
# ---------------------------------------------------------------------------
def test_provider_neutral_source_intake_normalization():
    content = "顾全：那场比赛在第四节最后阶段，大家的心态是怎么调整的？\r\n顾全：我们当时顶住了很大压力。"

    # Fed via local_file
    env_local, norm_local = normalize_source_payload(
        content.encode("utf-8"),
        source_provider="local_file",
        source_item_id_or_locator="/private/inbox/guquan.txt",
    )

    # Fed via google_drive_doc
    env_drive, norm_drive = normalize_source_payload(
        content,
        source_provider="google_drive_doc",
        source_item_id_or_locator="synthetic_drive_doc_01",
    )

    # Invariant A09: provider swap produces equivalent normalized content & identical doc_id
    assert norm_local == norm_drive
    assert env_local["content_hash"] == env_drive["content_hash"]
    assert env_local["normalized_document_ref"] == env_drive["normalized_document_ref"]
    assert "normalized_document" in env_local


# ---------------------------------------------------------------------------
# 12. Research View semantic-grain separation & UNKNOWN grain (A10, B10, R7)
# ---------------------------------------------------------------------------
def test_research_view_semantic_grain_separation(identity_registry, structured_turn_text):
    doc_id = compute_doc_id(structured_turn_text)
    statements = extract_statements_from_text(
        structured_turn_text,
        doc_id=doc_id,
        identity_registry=identity_registry,
    )
    claim = create_claim_from_statements("顾全谈第四节心态", statements[:2])

    queue = VerificationQueue()
    queue.create_and_add(
        object_type="actor",
        object_ref={"raw_name": "郑永刚"},
        reason_code="UNRESOLVED_ACTOR",
        evidence_refs=[f"{doc_id}#L6"],
        created_from="unit_test",
    )

    rv = ResearchView(
        subject_name="顾全",
        subject_player_uid="P0042_GUQUAN_0001",
        canonical_facts=[{
            "season": "2024-2025",
            "club": "深圳新世纪",
            "registration_type": "国内球员注册",
            "contract_type": "D类",
        }],
        documents=[{
            "doc_id": doc_id,
            "title": "顾全专访",
            "source_locator": "wechat:guquan_interview",
            "rights": {"classification": "private"},
        }],
        statements=statements,
        claims=[claim],
        verification_items=queue.list_items(),
        unknown_items=[{
            "dimension": "contract_amount",
            "description": "2025赛季后合同金额未公开",
        }],
    )

    d = rv.to_dict()
    assert d["summary_counts"]["canonical_facts_count"] == 1
    assert d["summary_counts"]["statements_count"] == 4
    assert d["summary_counts"]["claims_count"] == 1
    assert d["summary_counts"]["open_verification_items_count"] == 1
    assert d["summary_counts"]["unknown_items_count"] == 1

    md = rv.render_markdown()
    assert "CANONICAL_FACTS" in md
    assert "STATEMENTS" in md
    assert "CLAIMS" in md
    assert "VERIFICATION_QUEUE" in md
    assert "UNKNOWN" in md
    assert "## 7. 未知与证据空缺 (UNKNOWN)" in md


# ---------------------------------------------------------------------------
# 13. Evidence-bound Relationship View (D07)
# ---------------------------------------------------------------------------
def test_evidence_bound_relationship_view():
    actor_a = make_actor_ref("player", "孟铎", "doc_1#L1", "P0018_MENGDUO_0001")
    actor_b = make_actor_ref("player", "顾全", "doc_1#L2", "P0042_GUQUAN_0001")
    supporting_sid = "stmt_0123456789abcdef01234567"

    rel_id = generate_relation_id("teammate_of", actor_a, actor_b, [supporting_sid])
    rel = validate_relation_assertion({
        "relation_id": rel_id,
        "relation_type": "teammate_of",
        "from_actor": actor_a,
        "to_actor": actor_b,
        "evidence_refs": ["doc_1#L1-L2"],
        "supporting_statement_ids": [supporting_sid],
        "confidence": "HIGH",
    })

    assert rel["relation_type"] == "teammate_of"
    assert rel["from_actor"]["raw_name"] == "孟铎"
    assert rel["to_actor"]["raw_name"] == "顾全"


# ---------------------------------------------------------------------------
# 14. Real Canary Reference Test (A13, A15, R6)
# ---------------------------------------------------------------------------
def test_real_canary_coverage_when_staged():
    """Test dual real canary coverage (P0265 贺希宁 and coach 郑永刚) when staged in private instance."""
    instance_root_env = os.environ.get("CBA_KB_INSTANCE_ROOT")
    if not instance_root_env:
        pytest.skip("CBA_KB_INSTANCE_ROOT not set; skipping real canary check")
    instance_root = Path(instance_root_env)

    # 1. Canary 1: 贺希宁 WeChat interview
    wechat_dir = instance_root / "inbox/documents/wechat"
    hexining_candidates = list(wechat_dir.glob("*贺希宁*.txt")) if wechat_dir.is_dir() else []
    if not hexining_candidates:
        pytest.skip("Real canary file for P0265 (贺希宁) not staged locally")

    hexining_file = hexining_candidates[0]
    raw_text = hexining_file.read_text(encoding="utf-8", errors="replace")
    env1, norm_text1 = normalize_source_payload(
        raw_text,
        source_provider="wechat_browser_clip",
        source_item_id_or_locator=hexining_file.name,
    )
    assert env1["intake_status"] == "accepted"
    assert env1["normalized_document_ref"].startswith("doc_")

    id_reg_path = FIXTURES_DIR / "synthetic_identity_registry.json"
    id_reg = json.loads(id_reg_path.read_text(encoding="utf-8"))

    stmts1 = extract_statements_from_text(
        norm_text1,
        doc_id=env1["normalized_document_ref"],
        identity_registry=id_reg,
    )
    assert len(stmts1) > 0
    hexining_stmts = [s for s in stmts1 if s["speaker_actor_ref"]["raw_name"] == "贺希宁"]
    assert len(hexining_stmts) > 0
    assert hexining_stmts[0]["speaker_actor_ref"]["id"] == "P0265_HEXINING_0001"
    assert hexining_stmts[0]["speaker_actor_ref"]["kind"] == "player"

    # 2. Canary 2: 郑永刚 local PDF file
    local_dir = instance_root / "inbox/documents/local"
    zyg_candidates = sorted(list(local_dir.glob("*郑永刚*.pdf")), reverse=True) if local_dir.is_dir() else []
    if not zyg_candidates:
        pytest.skip("Real canary PDF for 郑永刚 not staged locally")

    zyg_file = zyg_candidates[0]
    raw_pdf_bytes = zyg_file.read_bytes()
    env2, norm_text2 = normalize_source_payload(
        raw_pdf_bytes,
        source_provider="local_file",
        source_item_id_or_locator=zyg_file.name,
        media_type="application/pdf",
    )
    assert env2["intake_status"] == "accepted"
    assert env2["normalized_document_ref"].startswith("doc_")
    assert len(norm_text2) > 100

    # Coach / non-player case: 郑永刚 resolved as unresolved with id=None
    coach_actor = resolve_actor(
        "郑永刚",
        evidence_ref=f"{env2['normalized_document_ref']}#coach",
        identity_registry=id_reg,
    )
    assert coach_actor["kind"] == "unresolved"
    assert coach_actor["id"] is None
    assert coach_actor["raw_name"] == "郑永刚"
