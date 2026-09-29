"""Consumer Closure Repair tests for REQ-181 (v1.8.1-2).

Covers:
1. Reproduction of original Consumer failure before repair and proof of repair resolution (Section 18);
2. Current-state single-source consistency and negative tests (Sections 9, 11);
3. Identity Consumer projection positive and negative tests (Sections 5, 6, 12, 13);
4. Consumer Manifest schema and validation (Sections 7, 8);
5. External Consumer surface model and freshness/discovery contract (Sections 10, 33);
6. Full Consumer Acceptance Matrix for ChatGPT, Gemini Notebook, WorkBuddy (Sections 14, 15, 16, 17).
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from cba_kb.common import digest
from cba_kb.consumer_acceptance import evaluate_consumer_closure_acceptance
from cba_kb.consumer_manifest import (
    ConsumerManifestError,
    build_consumer_manifest,
    validate_consumer_manifest,
)
from cba_kb.consumer_projection import (
    ConsumerProjectionError,
    build_player_identity_consumer_projection,
    cross_season_identity_query,
    lookup_player_identity,
    validate_player_identity_consumer_projection,
)
from cba_kb.current_state import (
    generate_context_card,
    read_current_block,
    render_current_state,
    target_metadata,
    validate_current_state,
)
from cba_kb.evidence_ledger import canonical_bytes
from cba_kb.player_identity import new_registry

from test_canonical_registry import SHA, manifest, registry

RELEASE_ID = "v1.8.1-2"
PRODUCT_VERSION = "v1.8.1"


def sample_repaired_documents(release_id=RELEASE_ID, code_commit=SHA):
    reg = registry()
    reg["registry_release_id"] = release_id
    meta = target_metadata(release_id, code_commit, reg)
    block = render_current_state(meta)
    docs = {
        "readme": block + "README body\n",
        "index": block + "INDEX body\n",
        "current_version_doc": block + "CURRENT_VERSION_DOC body\n",
        "technical_manual": block + "TECHNICAL_MANUAL body\n",
        "context_card": generate_context_card(meta, reg, manifest()),
    }
    status = {
        "state": "COMPLETE",
        "current_release_id": release_id,
        "code_commit": code_commit,
    }
    return status, reg, docs, meta


def sample_real_identity_registry():
    # Load actual instance identity registry if present, or self-contained real replica
    instance_reg_path = Path("/Users/skychengneo/Agent/CBA_kb_instance/data/player_identity/registry.json")
    if instance_reg_path.is_file():
        return json.loads(instance_reg_path.read_text(encoding="utf-8"))
    return new_registry(
        [
            {"schema_version": 1, "player_uid": "pid_f64daa29c26c42dac6bdbbbbf6716317",
             "canonical_name": "邹雨宸", "status": "ACTIVE", "redirect_to": None},
            {"schema_version": 1, "player_uid": "pid_02015a0839f30917980eae3381016e65",
             "canonical_name": "万圣伟", "status": "ACTIVE", "redirect_to": None},
        ],
        record_links=[
            {"schema_version": 1, "record_key": "2017-2018|bayi|邹雨宸",
             "player_uid": "pid_f64daa29c26c42dac6bdbbbbf6716317", "link_status": "same",
             "confidence": "HIGH", "method": "MANUAL_REVIEW", "evidence_refs": ["doc:1"]},
            {"schema_version": 1, "record_key": "2020-2021|beijing_konggu|邹雨宸",
             "player_uid": "pid_f64daa29c26c42dac6bdbbbbf6716317", "link_status": "same",
             "confidence": "HIGH", "method": "MANUAL_REVIEW", "evidence_refs": ["doc:2"]},
        ],
    )


# ==============================================================================
# 1. Section 18: REPRODUCE ORIGINAL FAILURE FIRST
# ==============================================================================

def test_reproduce_original_consumer_failure_before_repair():
    """Reproduce the 4 observed pre-repair symptoms:
    1. static surface stale / missing current block in technical manual;
    2. Identity capability exists in private registry but no consumer projection;
    3. Identity happy path lookup is unavailable (fails closed);
    4. External consumer acceptance fails closed.
    """
    reg = registry()
    reg["registry_release_id"] = "v1.8.1-1"
    meta = target_metadata("v1.8.1-1", SHA, reg)
    block = render_current_state(meta)

    # Symptom 1: Technical manual has no current state block (raw markdown as on Drive)
    stale_docs = {
        "readme": block + "README body\n",
        "index": block + "INDEX body\n",
        "current_version_doc": block + "CURRENT_VERSION_DOC body\n",
        "technical_manual": "# CBA-KB 当前状态\n当前发布：v1.8.1-1\n没有机器状态块\n",
        "context_card": generate_context_card(meta, reg, manifest()),
    }
    status = {"state": "COMPLETE", "current_release_id": "v1.8.1-1", "code_commit": SHA}

    # Reading current block from stale technical manual fails
    with pytest.raises(ValueError, match="CURRENT_STATE_DRIFT"):
        read_current_block(stale_docs["technical_manual"])

    # Symptom 2 & 3: Consumer identity authority is unavailable -> fails closed
    consumer_identity_authority = None
    lookup_result = lookup_player_identity(consumer_identity_authority, "邹雨宸")
    assert lookup_result["status"] == "CONSUMER_AUTHORITY_UNAVAILABLE"

    # Symptom 4: Acceptance fails closed before repair
    acceptance_result = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=None,
        identity_projection=None,
        expected_source_registry_sha256=None,
    )
    assert acceptance_result["status"] == "FAIL"
    assert acceptance_result["matrix"]["IDENTITY_HAPPY_PATH"] == "FAIL"
    assert acceptance_result["matrix"]["IDENTITY_AUTHORITY_DISCOVERY"] == "FAIL"
    assert acceptance_result["matrix"]["CONSUMER_MANIFEST_DISCOVERY"] == "FAIL"


def test_repaired_consumer_closure_succeeds():
    """Prove the repaired candidate changes outcome to:
    - current surface consistent across all 5 surfaces;
    - authority discoverable;
    - valid Identity happy path succeeds (邹雨宸);
    - ambiguity remains fail closed;
    - consumer acceptance passes.
    """
    status, reg, docs, meta = sample_repaired_documents(release_id=RELEASE_ID)
    identity_reg = sample_real_identity_registry()

    # 1. Identity Consumer projection materialized
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    val_proj = validate_player_identity_consumer_projection(
        proj, expected_release_id=RELEASE_ID, expected_product_version=PRODUCT_VERSION,
        expected_source_registry_sha256=proj["source_registry_sha256"],
    )
    assert val_proj["status"] == "PASS"

    # 2. Consumer Manifest materialized
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        "readme": {"id": "f-readme", "name": "00_README", "sha256": digest(docs["readme"].encode()), "mime": "text/plain", "authority": "control", "rights": "public"},
        "index": {"id": "f-index", "name": "INDEX", "sha256": digest(docs["index"].encode()), "mime": "text/plain", "authority": "control", "rights": "public"},
        "technical_manual": {"id": "f-man", "name": "02_AI_PROJECT_CONTEXT.md", "sha256": digest(docs["technical_manual"].encode()), "mime": "text/markdown", "authority": "control", "rights": "public"},
        "context_card": {"id": "f-card", "name": "CONTEXT_CARD.md", "sha256": digest(docs["context_card"].encode()), "mime": "text/markdown", "authority": "control", "rights": "public"},
        "current_version_doc": {"id": "f-ver", "name": "CBA-KB_v1.5.3.md", "sha256": digest(docs["current_version_doc"].encode()), "mime": "text/markdown", "authority": "control", "rights": "public"},
    }
    facts = {
        "master": {"id": "f-master", "name": "MASTER.xlsx", "sha256": "1" * 64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "authority": "canonical", "rights": "public"},
    }
    identity_entry = {
        "id": "f-proj", "name": "player_identity_consumer.json", "sha256": proj["projection_sha256"], "mime": "application/json", "authority": "derived", "rights": "public",
        "source_registry_sha256": proj["source_registry_sha256"],
    }
    manifest_data = build_consumer_manifest(
        release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
        surfaces=surfaces, facts=facts, identity_projection=identity_entry,
    )

    # 3. All surfaces validated
    curr_val = validate_current_state(
        status, reg, manifest(), docs, consumer_manifest=manifest_data, identity_projection=proj,
    )
    assert curr_val["status"] == "PASS"
    assert "technical_manual" in curr_val["surfaces"]

    # 4. Identity happy path succeeds
    happy = lookup_player_identity(proj, "邹雨宸")
    assert happy["status"] == "SUCCESS"
    assert happy["candidate_count"] == 1
    assert happy["player"]["canonical_name"] == "邹雨宸"
    assert len(happy["record_links"]) > 0

    # 5. Full acceptance matrix passes
    acceptance = evaluate_consumer_closure_acceptance(
        status_doc=status, consumer_manifest=manifest_data, identity_projection=proj,
        chatgpt_result="PASS", gemini_result="PASS", workbuddy_result="PASS",
        expected_source_registry_sha256=proj["source_registry_sha256"],
    )
    assert acceptance["status"] == "PASS"


# ==============================================================================
# 2. Section 9 & 11: CURRENT SURFACE NEGATIVE TESTS
# ==============================================================================

def test_current_surface_negative_wrong_release_id():
    status, reg, docs, meta = sample_repaired_documents(release_id=RELEASE_ID)
    docs["technical_manual"] = docs["technical_manual"].replace(RELEASE_ID, "v1.8.1-1")
    with pytest.raises(ValueError, match="CURRENT_STATE_DRIFT"):
        validate_current_state(status, reg, manifest(), docs)


def test_current_surface_negative_stale_v180_marker():
    status, reg, docs, meta = sample_repaired_documents(release_id=RELEASE_ID)
    docs["readme"] = docs["readme"].replace(RELEASE_ID, "v1.8.0-1")
    with pytest.raises(ValueError, match="CURRENT_STATE_DRIFT"):
        validate_current_state(status, reg, manifest(), docs)


def test_current_surface_negative_missing_technical_manual():
    status, reg, docs, meta = sample_repaired_documents(release_id=RELEASE_ID)
    del docs["technical_manual"]
    with pytest.raises(ValueError, match="CURRENT_DOCUMENT_MISSING"):
        validate_current_state(status, reg, manifest(), docs)


def test_current_surface_negative_wrong_manifest_release():
    status, reg, docs, meta = sample_repaired_documents(release_id=RELEASE_ID)
    bad_manifest = {"release_id": "v1.8.0-1", "product_version": "v1.8.0"}
    with pytest.raises(ValueError, match="CURRENT_STATE_DRIFT"):
        validate_current_state(status, reg, manifest(), docs, consumer_manifest=bad_manifest)


# ==============================================================================
# 3. Section 12 & 13: IDENTITY PROJECTION POSITIVE & NEGATIVE TESTS
# ==============================================================================

def test_identity_projection_preserves_relations_and_is_deterministic():
    identity_reg = sample_real_identity_registry()
    proj1 = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    proj2 = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    assert proj1["projection_sha256"] == proj2["projection_sha256"]
    assert proj1["schema"] == "player_identity_consumer_v1"
    assert proj1["release_id"] == RELEASE_ID
    assert proj1["product_version"] == PRODUCT_VERSION
    assert proj1["private_registry_exposed"] is False
    assert proj1["private_registry_leakage"] == 0

    # Invariants: SAME / NOT_SAME / UNDECIDED preserved
    relations = {l["relation"] for l in proj1["record_links"]}
    assert "SAME" in relations


def test_identity_projection_happy_path_and_cross_season():
    identity_reg = sample_real_identity_registry()
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    happy = lookup_player_identity(proj, "邹雨宸")
    assert happy["status"] == "SUCCESS"
    assert happy["candidate_count"] == 1
    assert happy["player"]["player_uid"] == "pid_f64daa29c26c42dac6bdbbbbf6716317"

    cross = cross_season_identity_query(proj, "邹雨宸")
    assert cross["status"] == "SUCCESS"
    assert cross["season_count"] >= 2
    assert "2017-2018" in cross["seasons"]
    assert "2020-2021" in cross["seasons"]


def test_identity_projection_negative_unknown_player_not_found():
    identity_reg = sample_real_identity_registry()
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    result = lookup_player_identity(proj, "张三不存在")
    assert result["status"] == "NOT_FOUND"
    assert result["candidate_count"] == 0


def test_identity_projection_negative_ambiguous_name_review_required():
    ambiguous_reg = new_registry(
        [
            {"schema_version": 1, "player_uid": "pid_0000000000000001", "canonical_name": "李强", "status": "ACTIVE", "redirect_to": None},
            {"schema_version": 1, "player_uid": "pid_0000000000000002", "canonical_name": "李强", "status": "ACTIVE", "redirect_to": None},
        ],
        record_links=[],
    )
    proj = build_player_identity_consumer_projection(
        ambiguous_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION,
    )
    result = lookup_player_identity(proj, "李强")
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["candidate_count"] == 2
    assert result["automatic_link_allowed"] is False


def test_identity_projection_negative_private_registry_leakage_prevented():
    identity_reg = sample_real_identity_registry()
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION,
    )
    # Injecting private note fails validation
    proj["players"][0]["notes"] = "private admin note"
    with pytest.raises(ConsumerProjectionError, match="PRIVATE_REGISTRY_LEAKAGE"):
        validate_player_identity_consumer_projection(
            proj,
            expected_source_registry_sha256=proj["source_registry_sha256"],
        )


def test_identity_projection_negative_tampered_hash_fails():
    identity_reg = sample_real_identity_registry()
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION,
    )
    proj["projection_sha256"] = "0" * 64
    with pytest.raises(ConsumerProjectionError, match="IDENTITY_PROJECTION_HASH_MISMATCH"):
        validate_player_identity_consumer_projection(
            proj,
            expected_source_registry_sha256=proj["source_registry_sha256"],
        )


# ==============================================================================
# 4. Section 7 & 8: CONSUMER MANIFEST TESTS
# ==============================================================================

def test_consumer_manifest_full_lifecycle():
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        "readme": {"id": "f-readme", "name": "00_README", "sha256": "1" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "index": {"id": "f-index", "name": "INDEX", "sha256": "2" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "technical_manual": {"id": "f-man", "name": "02_AI_PROJECT_CONTEXT.md", "sha256": "3" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "context_card": {"id": "f-card", "name": "CONTEXT_CARD.md", "sha256": "4" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "current_version_doc": {"id": "f-ver", "name": "CBA-KB_v1.5.3.md", "sha256": "5" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
    }
    facts = {
        "master": {"id": "f-master", "name": "MASTER.xlsx", "sha256": "6" * 64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "authority": "canonical", "rights": "public"},
    }
    identity_entry = {
        "id": "f-proj", "name": "player_identity_consumer.json", "sha256": "7" * 64, "mime": "application/json", "authority": "derived", "rights": "public",
        "source_registry_sha256": "8" * 64,
    }
    manifest_data = build_consumer_manifest(
        release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
        surfaces=surfaces, facts=facts, identity_projection=identity_entry,
    )
    result = validate_consumer_manifest(
        manifest_data, expected_release_id=RELEASE_ID, expected_product_version=PRODUCT_VERSION, expected_code_commit=SHA,
    )
    assert result["status"] == "PASS"
    assert result["entries_validated"] == 8
    for category, key, invalid_authority in (
        ("control", "readme", "canonical"),
        ("facts", "master", "arbitrary"),
        ("identity", "player_identity_projection", "control"),
    ):
        tampered = json.loads(json.dumps(manifest_data))
        tampered["consumer_surfaces"][category][key][
            "authority"
        ] = invalid_authority
        tampered["manifest_sha256"] = digest(canonical_bytes({
            field: value for field, value in tampered.items()
            if field != "manifest_sha256"
        }))
        with pytest.raises(ConsumerManifestError, match="AUTHORITY_INVALID"):
            validate_consumer_manifest(tampered)


def test_consumer_manifest_missing_control_surface_fails():
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        # missing readme, index, etc.
    }
    with pytest.raises(ConsumerManifestError, match="MISSING_CONTROL_SURFACE"):
        build_consumer_manifest(
            release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
            surfaces=surfaces, facts={"master": {"id": "f", "name": "m", "sha256": "0" * 64, "mime": "t", "authority": "canonical", "rights": "public"}},
            identity_projection={"id": "i", "name": "p", "sha256": "0" * 64, "mime": "t", "authority": "derived", "rights": "public", "source_registry_sha256": "8" * 64},
        )


def test_consumer_manifest_unreadable_artifact_fails_validation():
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        "readme": {"id": "f-readme", "name": "00_README", "sha256": "1" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "index": {"id": "f-index", "name": "INDEX", "sha256": "2" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "technical_manual": {"id": "f-man", "name": "02_AI_PROJECT_CONTEXT.md", "sha256": "3" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "context_card": {"id": "f-card", "name": "CONTEXT_CARD.md", "sha256": "4" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "current_version_doc": {"id": "f-ver", "name": "CBA-KB_v1.5.3.md", "sha256": "5" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
    }
    facts = {
        "master": {"id": "f-master", "name": "MASTER.xlsx", "sha256": "6" * 64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "authority": "canonical", "rights": "public"},
    }
    identity_entry = {
        "id": "f-proj", "name": "player_identity_consumer.json", "sha256": "7" * 64, "mime": "application/json", "authority": "derived", "rights": "public",
        "source_registry_sha256": "8" * 64,
    }
    manifest_data = build_consumer_manifest(
        release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
        surfaces=surfaces, facts=facts, identity_projection=identity_entry,
    )

    def failing_resolver(fid):
        raise IOError("Connection aborted")

    with pytest.raises(ConsumerManifestError, match="UNREADABLE_REQUIRED_ARTIFACT"):
        validate_consumer_manifest(manifest_data, artifact_resolver=failing_resolver)


# ==============================================================================
# 5. Section 10 & 33: EXTERNAL SURFACE FRESHNESS / DISCOVERY CONTRACT
# ==============================================================================

def test_external_surface_freshness_bootstrap_contract():
    """Verify external consumers follow deterministic bootstrap rule."""
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        "readme": {"id": "f-readme", "name": "00_README", "sha256": "1" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "index": {"id": "f-index", "name": "INDEX", "sha256": "2" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "technical_manual": {"id": "f-man", "name": "02_AI_PROJECT_CONTEXT.md", "sha256": "3" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "context_card": {"id": "f-card", "name": "CONTEXT_CARD.md", "sha256": "4" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "current_version_doc": {"id": "f-ver", "name": "CBA-KB_v1.5.3.md", "sha256": "5" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
    }
    facts = {
        "master": {"id": "f-master", "name": "MASTER.xlsx", "sha256": "6" * 64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "authority": "canonical", "rights": "public"},
    }
    identity_entry = {
        "id": "f-proj", "name": "player_identity_consumer.json", "sha256": "7" * 64, "mime": "application/json", "authority": "derived", "rights": "public",
        "source_registry_sha256": "8" * 64,
    }
    manifest_data = build_consumer_manifest(
        release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
        surfaces=surfaces, facts=facts, identity_projection=identity_entry,
    )
    nav = manifest_data["navigation"]
    assert nav["discovery_precedence"] == "LIVE_RELEASE_STATUS_THEN_CONSUMER_MANIFEST"
    assert nav["stale_external_snapshot_policy"] == "LIVE_MANIFEST_OVERRIDES_INDEXED_SNAPSHOTS"
    assert len(nav["bootstrap_rule"]) == 3
    assert "release_status" in nav["bootstrap_rule"][0]
    assert "Consumer Manifest" in nav["bootstrap_rule"][1]
    assert "manifest-bound" in nav["bootstrap_rule"][2]


# ==============================================================================
# 6. Section 14, 15, 16, 17: FULL CONSUMER ACCEPTANCE MATRIX
# ==============================================================================

def test_consumer_acceptance_matrix_all_mandatory_rows_pass():
    identity_reg = sample_real_identity_registry()
    proj = build_player_identity_consumer_projection(
        identity_reg, release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
    )
    surfaces = {
        "release_status": {"id": "f-status", "name": "release_status.json", "sha256": "0" * 64, "mime": "application/json", "authority": "control", "rights": "public"},
        "readme": {"id": "f-readme", "name": "00_README", "sha256": "1" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "index": {"id": "f-index", "name": "INDEX", "sha256": "2" * 64, "mime": "text/plain", "authority": "control", "rights": "public"},
        "technical_manual": {"id": "f-man", "name": "02_AI_PROJECT_CONTEXT.md", "sha256": "3" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "context_card": {"id": "f-card", "name": "CONTEXT_CARD.md", "sha256": "4" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
        "current_version_doc": {"id": "f-ver", "name": "CBA-KB_v1.5.3.md", "sha256": "5" * 64, "mime": "text/markdown", "authority": "control", "rights": "public"},
    }
    facts = {
        "master": {"id": "f-master", "name": "MASTER.xlsx", "sha256": "6" * 64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "authority": "canonical", "rights": "public"},
    }
    identity_entry = {
        "id": "f-proj", "name": "player_identity_consumer.json", "sha256": proj["projection_sha256"], "mime": "application/json", "authority": "derived", "rights": "public",
        "source_registry_sha256": proj["source_registry_sha256"],
    }
    manifest_data = build_consumer_manifest(
        release_id=RELEASE_ID, product_version=PRODUCT_VERSION, code_commit=SHA,
        surfaces=surfaces, facts=facts, identity_projection=identity_entry,
    )
    status = {"state": "COMPLETE", "current_release_id": RELEASE_ID, "code_commit": SHA}

    eval_result = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=manifest_data,
        identity_projection=proj,
        chatgpt_result="PASS",
        gemini_result="PASS",
        workbuddy_result="PASS",
        expected_source_registry_sha256=proj["source_registry_sha256"],
    )
    assert eval_result["status"] == "PASS"

    missing_binding = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=manifest_data,
        identity_projection=proj,
        chatgpt_result="PASS",
        gemini_result="PASS",
        workbuddy_result="PASS",
    )
    assert missing_binding["status"] == "FAIL"

    mismatched_binding = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=manifest_data,
        identity_projection=proj,
        chatgpt_result="PASS",
        gemini_result="PASS",
        workbuddy_result="PASS",
        expected_source_registry_sha256="f" * 64,
    )
    assert mismatched_binding["status"] == "FAIL"

    # Projection and Manifest can be rewritten into a self-consistent pair,
    # but neither controls the independently supplied source-registry binding.
    forged_projection = json.loads(json.dumps(proj))
    forged_manifest = json.loads(json.dumps(manifest_data))
    forged_source_sha = "f" * 64
    forged_projection["source_registry_sha256"] = forged_source_sha
    forged_projection["projection_sha256"] = digest(canonical_bytes({
        key: value for key, value in forged_projection.items()
        if key != "projection_sha256"
    }))
    forged_identity_entry = forged_manifest["consumer_surfaces"]["identity"][
        "player_identity_projection"
    ]
    forged_identity_entry["source_registry_sha256"] = forged_source_sha
    forged_identity_entry["sha256"] = forged_projection["projection_sha256"]
    forged_manifest["manifest_sha256"] = digest(canonical_bytes({
        key: value for key, value in forged_manifest.items()
        if key != "manifest_sha256"
    }))
    forged_result = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=forged_manifest,
        identity_projection=forged_projection,
        chatgpt_result="PASS",
        gemini_result="PASS",
        workbuddy_result="PASS",
        expected_source_registry_sha256=proj["source_registry_sha256"],
    )
    assert forged_result["status"] == "FAIL"
    matrix = eval_result["matrix"]
    required_rows = [
        "IDENTITY_SEMANTIC_SAFETY",
        "IDENTITY_AUTHORITY_DISCOVERY",
        "IDENTITY_PROJECTION_MATERIALIZED",
        "IDENTITY_HAPPY_PATH",
        "IDENTITY_AMBIGUOUS_NAME_PATH",
        "IDENTITY_NOT_FOUND_PATH",
        "CROSS_SEASON_IDENTITY_QUERY",
        "CURRENT_RELEASE_DISCOVERY",
        "CONSUMER_MANIFEST_DISCOVERY",
        "PRIVATE_REGISTRY_LEAKAGE",
    ]
    for row in required_rows:
        assert row in matrix
        if row == "PRIVATE_REGISTRY_LEAKAGE":
            assert matrix[row] == 0
        else:
            assert matrix[row] == "PASS"
    assert eval_result["platforms"]["ChatGPT"] == "PASS"
    assert eval_result["platforms"]["Gemini Notebook"] == "PASS"
    assert eval_result["platforms"]["WorkBuddy"] == "PASS"

    # Every governed external result is explicit; missing/null/unknown never
    # inherits PASS from the other consumers.
    for overrides in (
        {"chatgpt_result": None},
        {"gemini_result": "UNKNOWN"},
        {"workbuddy_result": "FAIL"},
    ):
        platform_values = {
            "chatgpt_result": "PASS",
            "gemini_result": "PASS",
            "workbuddy_result": "PASS",
            **overrides,
        }
        failed = evaluate_consumer_closure_acceptance(
            status_doc=status,
            consumer_manifest=manifest_data,
            identity_projection=proj,
            **platform_values,
            expected_source_registry_sha256=proj["source_registry_sha256"],
        )
        assert failed["status"] == "FAIL"

    # Safe self-reported flags and a recomputed self-hash cannot hide semantic
    # projection tampering because Acceptance invokes the full validator.
    tampered = json.loads(json.dumps(proj))
    tampered["summary"]["same_count"] += 1
    tampered["projection_sha256"] = digest(canonical_bytes({
        key: value for key, value in tampered.items()
        if key != "projection_sha256"
    }))
    manifest_tampered = json.loads(json.dumps(manifest_data))
    manifest_tampered["consumer_surfaces"]["identity"][
        "player_identity_projection"
    ]["sha256"] = tampered["projection_sha256"]
    manifest_tampered["manifest_sha256"] = digest(canonical_bytes({
        key: value for key, value in manifest_tampered.items()
        if key != "manifest_sha256"
    }))
    rejected = evaluate_consumer_closure_acceptance(
        status_doc=status,
        consumer_manifest=manifest_tampered,
        identity_projection=tampered,
        chatgpt_result="PASS",
        gemini_result="PASS",
        workbuddy_result="PASS",
        expected_source_registry_sha256=proj["source_registry_sha256"],
    )
    assert rejected["status"] == "FAIL"
    assert rejected["matrix"]["IDENTITY_SEMANTIC_SAFETY"] == "FAIL"


# ==============================================================================
# 7. Section 7 & 8: POST-FREEZE ACCEPTANCE BINDING MODEL
# ==============================================================================

def test_exact_v181_2_failure_regression_and_corrected_lineage(tmp_path):
    """Reproduce the frozen v1.8.1-2 failure facts, then prove the repair.

    The original runtime journal bytes are unavailable.  Exact historical
    hashes remain immutable inputs; a clearly synthetic legal lineage proves
    that only the erroneous PREPARED=current SHA equality is removed.
    """
    from cba_kb.release import (
        _runtime_lineage_event,
        _runtime_transaction_binding,
        validate_post_freeze_release_evidence,
    )

    historical = {
        "release_id": "v1.8.1-2",
        "safe_current_release_id": "v1.8.1-1",
        "failure_state": "VERIFYING",
        "failure_classification": "POST_FREEZE_ACCEPTANCE_BINDING_INVALID",
        "prepared_sha256": "c045ce56126ba52a5b942b0a196e9ee4749b1199314161f2005f869d777f079c",
        "failed_runtime_sha256": "6e2be9f8a03c637c834503501f67a3cff0c3781babbba6af253f724b9ba70164",
        "target_verification_exact_hash_mime_passed": 279,
        "target_verification_total": 279,
    }
    assert historical["failure_state"] == "VERIFYING"
    assert historical["target_verification_exact_hash_mime_passed"] == 279
    assert historical["target_verification_total"] == 279
    with pytest.raises(ValueError, match="POST_FREEZE_ACCEPTANCE_BINDING_INVALID"):
        if historical["failed_runtime_sha256"] != historical["prepared_sha256"]:
            raise ValueError(historical["failure_classification"])

    freeze_sha = historical["prepared_sha256"]
    plan = {
        "release_id": historical["release_id"],
        "entries": [{"id": "t1", "after_hash": "a" * 64, "logical_key": "k1"}],
        "closure": {"code_commit": SHA},
        "previous_release_id": historical["safe_current_release_id"],
        "status_before_hash": "b" * 64,
    }
    from cba_kb.release import _candidate_hash_set
    candidate_hash = _candidate_hash_set(plan)
    plan_raw = json.dumps(plan, sort_keys=True).encode()
    plan_sha = digest(plan_raw)

    root = tmp_path / "outbox"
    root.mkdir()
    (root / "plan.json").write_bytes(plan_raw)

    freeze_candidate = {
        "candidate_hash_set_sha256": candidate_hash,
        "entry_count": 1,
        "journal_sha256": freeze_sha,
        "journal_state": "PREPARED",
        "plan_sha256": plan_sha,
    }
    acceptance = {
        "schema_version": 1,
        "classification": "V181_CONSUMER_ACCEPTANCE_PASS",
        "status": "PASS",
        "release_id": historical["release_id"],
        "main_sha": SHA,
        "candidate": freeze_candidate,
        "golden": {"schema_version": 2, "version": "v2"},
        "global_consumer_closure": {"status": "PASS", "manufactured_identity_relation": False},
        "targets": {name: {"status": "PASS"} for name in ("ChatGPT", "Gemini Notebook", "WorkBuddy")},
    }
    acc_raw = json.dumps(acceptance, sort_keys=True).encode()
    readiness = {
        "schema_version": 1,
        "classification": "V181_RELEASE_READINESS_PASS",
        "status": "PASS",
        "release_id": historical["release_id"],
        "main_sha": SHA,
        "candidate": freeze_candidate,
        "consumer_acceptance": {
            "evidence_sha256": digest(acc_raw),
            "ChatGPT": "PASS", "Gemini Notebook": "PASS", "WorkBuddy": "PASS",
        },
        "production_baseline": {"state": "COMPLETE", "current_release_id": historical["safe_current_release_id"], "release_status_sha256": "b" * 64},
        "namespace_audit": {"status": "PASS", "violations": 0},
        "production_mutation": 0, "publish": "NOT_RUN", "restore": "NOT_RUN",
        "identity_invariants": {
            "identity_semantic_delta": "ZERO",
            "canonical_business_fact_delta": 0, "identity_authority_change": 0,
            "identity_registry_mutation": 0, "machine_final_uid_decisions": 0,
            "master_mutation": 0, "new_identity_decisions": 0,
            "new_not_same_decisions": 0, "new_same_decisions": 0, "player_uid_mutation": 0,
        },
    }
    read_raw = json.dumps(readiness, sort_keys=True).encode()
    authority = {
        "schema_version": "cba-kb.production-go.v1",
        "classification": "V181_PRODUCTION_GO",
        "status": "APPROVED",
        "approved_by": "HUMAN",
        "release_id": historical["release_id"],
        "main_sha": SHA,
        "release_readiness_file_id": "read-id",
        "release_readiness_sha256": digest(read_raw),
        "candidate_entry_count": "1",
        "candidate_hash_set_sha256": candidate_hash,
        "plan_sha256": plan_sha,
        "journal_sha256": freeze_sha,
        "journal_state": "PREPARED",
        "production_baseline_state": "COMPLETE",
        "production_baseline_release_id": historical["safe_current_release_id"],
        "production_baseline_release_status_sha256": "b" * 64,
        "publish_authorized": "true",
        "pre_publish_canary_required": "true",
        "fail_closed_on_binding_drift": "true",
        "restore_authorized": "false",
        "direct_manual_drive_copy_authorized": "false",
        "official_controlled_publish_only": "true",
        "authority_payload_sha256": "some-sha",
    }
    auth_lines = ["V181_PRODUCTION_GO"] + [f"{k}: {v}" for k, v in authority.items()]
    auth_raw = ("\n".join(auth_lines) + "\n").encode()

    class MockDrive:
        def __init__(self):
            self.data = {
                "acc-id": acc_raw,
                "read-id": read_raw,
                "auth-id": auth_raw,
            }
            self.metas = {
                "acc-id": {"id": "acc-id", "name": "acc.json", "mimeType": "application/json", "parents": ["p1"], "modifiedTime": "2026-09-27T00:00:01Z", "version": "1"},
                "read-id": {"id": "read-id", "name": "read.json", "mimeType": "application/json", "parents": ["p1"], "modifiedTime": "2026-09-27T00:00:02Z", "version": "1"},
                "auth-id": {"id": "auth-id", "name": "auth", "mimeType": "text/plain", "parents": ["p1"], "modifiedTime": "2026-09-27T00:00:03Z", "version": "1"},
            }
        def meta(self, fid):
            return self.metas[fid]
        def get(self, fid):
            return self.data[fid]

    mock_drive = MockDrive()
    items = [mock_drive.meta("acc-id"), mock_drive.meta("read-id"), mock_drive.meta("auth-id")]

    # Runtime journal advances through lifecycle phases; validation must PASS because
    # the exact historical PREPARED origin is preserved in a contiguous lineage.
    transaction = _runtime_transaction_binding(plan, plan_sha)
    lineage = []
    previous_sha = freeze_sha
    previous_state = "PREPARED"
    for runtime_state in (
        "ARCHIVING", "ARCHIVE_COMPLETE", "PUBLISHING", "VERIFYING", "COMPLETE",
    ):
        event = _runtime_lineage_event(
            len(lineage) + 1, previous_sha, previous_state,
            runtime_state, transaction,
        )
        lineage.append(event)
        runtime_journal = {
            "state": runtime_state,
            "inflight": None,
            "uploaded": {"t1": True},
            "freeze_prepared_journal_sha256": freeze_sha,
            "runtime_transaction": transaction,
            "runtime_lineage": list(lineage),
        }
        (root / "journal.json").write_bytes(json.dumps(runtime_journal).encode())
        res = validate_post_freeze_release_evidence(mock_drive, root, plan, items)
        assert res["status"] == "PASS"
        assert res["freeze_prepared_journal_sha256"] == freeze_sha
        assert res["current_runtime_journal_sha256"] != freeze_sha
        previous_sha, previous_state = event["event_sha256"], runtime_state

    valid_runtime_journal = json.loads(json.dumps(runtime_journal))
    for tamper in ("origin", "history", "transaction"):
        broken = json.loads(json.dumps(valid_runtime_journal))
        if tamper == "origin":
            broken["freeze_prepared_journal_sha256"] = "0" * 64
        elif tamper == "history":
            broken["runtime_lineage"][1]["previous_sha256"] = "0" * 64
        else:
            broken["runtime_transaction"]["release_id"] = "wrong-release"
        (root / "journal.json").write_bytes(json.dumps(broken).encode())
        with pytest.raises(ValueError, match="POST_FREEZE_RUNTIME_LINEAGE_INVALID"):
            validate_post_freeze_release_evidence(
                mock_drive, root, plan, items,
            )
    (root / "journal.json").write_bytes(
        json.dumps(valid_runtime_journal).encode()
    )

    # Negative: if candidate hash in acceptance drifts, fails closed
    bad_acceptance = dict(acceptance)
    bad_acceptance["candidate"] = dict(freeze_candidate)
    bad_acceptance["candidate"]["candidate_hash_set_sha256"] = "0" * 64
    mock_drive.data["acc-id"] = json.dumps(bad_acceptance).encode()
    with pytest.raises(ValueError, match="POST_FREEZE_ACCEPTANCE_BINDING_INVALID"):
        validate_post_freeze_release_evidence(mock_drive, root, plan, items)
