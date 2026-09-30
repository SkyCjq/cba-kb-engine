"""Test CLI wiring for REQ-190 v1.9 Statement/Claim commands.

Tests:
- source-intake
- statement-extract with normalized-document mandatory binding (G4-R3)
- claim-extract
- verification-queue
- research-view
- negative bypass tests for G4-R3
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from cba_kb.document_lane import doc_id as compute_doc_id

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "requirements/REQ-190-STATEMENT-CLAIM-01/fixtures"


def run_cli(*args):
    cmd = [
        sys.executable,
        "-m",
        "cba_kb.cli",
        *args,
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    res = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    return res


def test_cli_source_intake(tmp_path):
    input_file = tmp_path / "raw.txt"
    input_file.write_text("顾全：那场比赛我们顶住了很大压力。", encoding="utf-8")
    out_file = tmp_path / "envelope.json"

    res = run_cli(
        "source-intake",
        "--input", str(input_file),
        "--source-provider", "local_file",
        "--source-locator", "raw.txt",
        "--output", str(out_file),
    )
    assert res.returncode == 0, res.stderr
    envelope = json.loads(out_file.read_text(encoding="utf-8"))
    assert envelope["source_provider"] == "local_file"
    assert envelope["intake_status"] == "accepted"
    assert envelope["normalized_document_ref"].startswith("doc_")
    assert "顾全" in envelope["normalized_document"]["normalized_text"]
    assert "顶住了很大压力" in envelope["normalized_document"]["normalized_text"]


def test_cli_source_intake_to_statement_extract_e2e(tmp_path):
    """G4-R3: True E2E test from source-intake envelope -> statement-extract."""
    raw_input = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    envelope_file = tmp_path / "envelope.json"
    stmts_file = tmp_path / "statements.json"

    # 1. Intake
    intake_res = run_cli(
        "source-intake",
        "--input", str(raw_input),
        "--source-provider", "wechat_browser_clip",
        "--source-locator", "interview_turns.txt",
        "--output", str(envelope_file),
    )
    assert intake_res.returncode == 0, intake_res.stderr

    # 2. Extract using --normalized-document
    extract_res = run_cli(
        "statement-extract",
        "--normalized-document", str(envelope_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert extract_res.returncode == 0, extract_res.stderr
    statements = json.loads(stmts_file.read_text(encoding="utf-8"))
    assert len(statements) == 4
    assert statements[0]["speaker_actor_ref"]["raw_name"] == "孟铎"
    assert statements[1]["speaker_actor_ref"]["raw_name"] == "顾全"


def test_cli_statement_extract_mandatory_binding_and_bypasses(tmp_path):
    """G4-R3: Verify that arbitrary unnormalized raw input fails closed without legacy flag."""
    raw_input = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    doc_id = compute_doc_id(raw_input.read_text(encoding="utf-8"))
    stmts_file = tmp_path / "statements.json"

    # 1. Calling statement-extract with raw text and without --normalized-document or legacy flag must fail
    res_fail = run_cli(
        "statement-extract",
        "--input", str(raw_input),
        "--doc-id", doc_id,
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_fail.returncode != 0
    assert "UNNORMALIZED_RAW_INPUT_FORBIDDEN" in res_fail.stderr

    # 2. Calling with explicit --allow-unnormalized-raw-input succeeds
    res_legacy = run_cli(
        "statement-extract",
        "--allow-unnormalized-raw-input",
        "--input", str(raw_input),
        "--doc-id", doc_id,
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_legacy.returncode == 0, res_legacy.stderr

    # 3. Tampered content_hash in normalized artifact fails
    envelope_file = tmp_path / "tampered_envelope.json"
    run_cli(
        "source-intake",
        "--input", str(raw_input),
        "--source-provider", "local_file",
        "--source-locator", "test.txt",
        "--output", str(envelope_file),
    )
    env_data = json.loads(envelope_file.read_text(encoding="utf-8"))
    env_data["normalized_document"]["content_hash"] = "0" * 64
    envelope_file.write_text(json.dumps(env_data), encoding="utf-8")

    res_tamper = run_cli(
        "statement-extract",
        "--normalized-document", str(envelope_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_tamper.returncode != 0
    assert "CONTENT_HASH_MISMATCH" in res_tamper.stderr


def test_cli_claim_extract(tmp_path):
    input_file = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    envelope_file = tmp_path / "envelope.json"
    stmts_file = tmp_path / "statements.json"

    # Intake -> Statement extract
    run_cli(
        "source-intake",
        "--input", str(input_file),
        "--source-provider", "wechat_browser_clip",
        "--source-locator", "turns.txt",
        "--output", str(envelope_file),
    )
    run_cli(
        "statement-extract",
        "--normalized-document", str(envelope_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )

    out_claim_file = tmp_path / "claim.json"
    res = run_cli(
        "claim-extract",
        "--statements", str(stmts_file),
        "--claim-text", "深圳男篮在逆境中贯彻防守部署",
        "--output", str(out_claim_file),
    )
    assert res.returncode == 0, res.stderr
    claim = json.loads(out_claim_file.read_text(encoding="utf-8"))
    assert claim["status"] == "unverified"
    assert claim["claim_id"].startswith("claim_")
    assert len(claim["supporting_statement_ids"]) == 4


def test_cli_verification_queue(tmp_path):
    queue_file = tmp_path / "queue.json"
    from cba_kb.verification_queue import VerificationQueue
    q = VerificationQueue()
    q.create_and_add(
        object_type="actor",
        object_ref={"raw_name": "郑永刚"},
        reason_code="UNRESOLVED_ACTOR",
        evidence_refs=["doc_1#L1"],
        created_from="test",
    )
    q.save(queue_file)

    out_filter = tmp_path / "filtered.json"
    res = run_cli(
        "verification-queue",
        "--queue", str(queue_file),
        "--object-type", "actor",
        "--output", str(out_filter),
    )
    assert res.returncode == 0, res.stderr
    result = json.loads(out_filter.read_text(encoding="utf-8"))
    assert result["count"] == 1
    assert result["items"][0]["reason_code"] == "UNRESOLVED_ACTOR"


def test_cli_research_view(tmp_path):
    input_file = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    envelope_file = tmp_path / "envelope.json"
    stmts_file = tmp_path / "statements.json"

    run_cli(
        "source-intake",
        "--input", str(input_file),
        "--source-provider", "wechat_browser_clip",
        "--source-locator", "turns.txt",
        "--output", str(envelope_file),
    )
    run_cli(
        "statement-extract",
        "--normalized-document", str(envelope_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )

    # Markdown format
    md_out = tmp_path / "view.md"
    res = run_cli(
        "research-view",
        "--name", "顾全",
        "--player-uid", "P0042_GUQUAN_0001",
        "--statements", str(stmts_file),
        "--format", "markdown",
        "--output", str(md_out),
    )
    assert res.returncode == 0, res.stderr
    md_text = md_out.read_text(encoding="utf-8")
    assert "研究视图" in md_text
    assert "STATEMENTS" in md_text

    # JSON format
    json_out = tmp_path / "view.json"
    res2 = run_cli(
        "research-view",
        "--name", "顾全",
        "--player-uid", "P0042_GUQUAN_0001",
        "--statements", str(stmts_file),
        "--format", "json",
        "--output", str(json_out),
    )
    assert res2.returncode == 0, res2.stderr
    view_data = json.loads(json_out.read_text(encoding="utf-8"))
    assert view_data["subject_name"] == "顾全"
    assert view_data["summary_counts"]["statements_count"] == 4


def test_normalized_artifact_complete_schema_required_gen6(tmp_path):
    """G6-R3 Mandatory Attack Oracle Matrix for Normalized Artifact Schema.

    - source-intake envelope -> statement-extract -> PASS
    - complete bare normalized_document -> PASS
    - normalized_text only -> FAIL
    - missing doc_id -> FAIL
    - missing content_hash -> FAIL
    - content_hash mismatch -> FAIL
    - doc_id mismatch -> FAIL
    - raw text without legacy flag -> FAIL
    - raw text with explicit legacy flag -> PASS only as legacy behavior
    """
    raw_input = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    envelope_file = tmp_path / "envelope.json"
    stmts_file = tmp_path / "statements.json"

    # 1. source-intake envelope -> statement-extract -> PASS
    run_cli(
        "source-intake",
        "--input", str(raw_input),
        "--source-provider", "wechat_browser_clip",
        "--source-locator", "turns.txt",
        "--output", str(envelope_file),
    )
    res_env = run_cli(
        "statement-extract",
        "--normalized-document", str(envelope_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_env.returncode == 0, res_env.stderr

    # 2. complete bare normalized_document -> PASS
    envelope_data = json.loads(envelope_file.read_text(encoding="utf-8"))
    bare_doc = envelope_data["normalized_document"]
    bare_file = tmp_path / "bare_doc.json"
    bare_file.write_text(json.dumps(bare_doc), encoding="utf-8")
    res_bare = run_cli(
        "statement-extract",
        "--normalized-document", str(bare_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_bare.returncode == 0, res_bare.stderr

    # 3. normalized_text only -> FAIL
    text_only_file = tmp_path / "text_only.json"
    text_only_file.write_text(json.dumps({"normalized_text": bare_doc["normalized_text"]}), encoding="utf-8")
    res_text_only = run_cli(
        "statement-extract",
        "--normalized-document", str(text_only_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_text_only.returncode != 0
    assert "NORMALIZED_DOCUMENT_SCHEMA_INVALID" in res_text_only.stderr

    # 4. missing doc_id -> FAIL
    no_doc_id = dict(bare_doc)
    no_doc_id.pop("doc_id", None)
    no_doc_id_file = tmp_path / "no_doc_id.json"
    no_doc_id_file.write_text(json.dumps(no_doc_id), encoding="utf-8")
    res_no_doc_id = run_cli(
        "statement-extract",
        "--normalized-document", str(no_doc_id_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_no_doc_id.returncode != 0
    assert "NORMALIZED_DOCUMENT_SCHEMA_INVALID" in res_no_doc_id.stderr

    # 5. missing content_hash -> FAIL
    no_hash = dict(bare_doc)
    no_hash.pop("content_hash", None)
    no_hash_file = tmp_path / "no_hash.json"
    no_hash_file.write_text(json.dumps(no_hash), encoding="utf-8")
    res_no_hash = run_cli(
        "statement-extract",
        "--normalized-document", str(no_hash_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_no_hash.returncode != 0
    assert "NORMALIZED_DOCUMENT_SCHEMA_INVALID" in res_no_hash.stderr

    # 6. content_hash mismatch -> FAIL
    bad_hash = dict(bare_doc, content_hash="0" * 64)
    bad_hash_file = tmp_path / "bad_hash.json"
    bad_hash_file.write_text(json.dumps(bad_hash), encoding="utf-8")
    res_bad_hash = run_cli(
        "statement-extract",
        "--normalized-document", str(bad_hash_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_bad_hash.returncode != 0
    assert "CONTENT_HASH_MISMATCH" in res_bad_hash.stderr

    # 7. doc_id mismatch -> FAIL
    bad_doc_id = dict(bare_doc, doc_id="doc_000000000000000000000000")
    bad_doc_id_file = tmp_path / "bad_doc_id.json"
    bad_doc_id_file.write_text(json.dumps(bad_doc_id), encoding="utf-8")
    res_bad_doc_id = run_cli(
        "statement-extract",
        "--normalized-document", str(bad_doc_id_file),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_bad_doc_id.returncode != 0
    assert "DOC_ID_MISMATCH" in res_bad_doc_id.stderr

    # 8. raw text without legacy flag -> FAIL
    res_raw_fail = run_cli(
        "statement-extract",
        "--input", str(raw_input),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_raw_fail.returncode != 0
    assert "UNNORMALIZED_RAW_INPUT_FORBIDDEN" in res_raw_fail.stderr

    # 9. raw text with explicit legacy flag -> PASS only as legacy behavior
    res_raw_pass = run_cli(
        "statement-extract",
        "--allow-unnormalized-raw-input",
        "--input", str(raw_input),
        "--identity-registry", str(id_reg_file),
        "--output", str(stmts_file),
    )
    assert res_raw_pass.returncode == 0, res_raw_pass.stderr
