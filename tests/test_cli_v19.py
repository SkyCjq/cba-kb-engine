"""Test CLI wiring for REQ-190 v1.9 Statement/Claim commands.

Tests:
- source-intake
- statement-extract
- claim-extract
- verification-queue
- research-view
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


def test_cli_statement_extract(tmp_path):
    input_file = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    out_file = tmp_path / "statements.json"
    doc_id = compute_doc_id(input_file.read_text(encoding="utf-8"))

    res = run_cli(
        "statement-extract",
        "--input", str(input_file),
        "--doc-id", doc_id,
        "--identity-registry", str(id_reg_file),
        "--output", str(out_file),
    )
    assert res.returncode == 0, res.stderr
    statements = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(statements) == 4
    assert statements[0]["speaker_actor_ref"]["raw_name"] == "孟铎"
    assert statements[1]["speaker_actor_ref"]["raw_name"] == "顾全"


def test_cli_claim_extract(tmp_path):
    input_file = FIXTURES_DIR / "synthetic_interview_turns.txt"
    id_reg_file = FIXTURES_DIR / "synthetic_identity_registry.json"
    stmts_file = tmp_path / "statements.json"
    doc_id = compute_doc_id(input_file.read_text(encoding="utf-8"))

    # Extract statements first
    run_cli(
        "statement-extract",
        "--input", str(input_file),
        "--doc-id", doc_id,
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
    stmts_file = tmp_path / "statements.json"
    doc_id = compute_doc_id(input_file.read_text(encoding="utf-8"))

    run_cli(
        "statement-extract",
        "--input", str(input_file),
        "--doc-id", doc_id,
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
