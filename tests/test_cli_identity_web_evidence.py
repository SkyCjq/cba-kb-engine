import json
import os
from pathlib import Path
import subprocess
import sys

from cba_kb.identity_coverage import prepare_review_packet
from cba_kb.identity_web_evidence import build_evidence_manifest


ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
        },
        capture_output=True,
        text=True,
    )


def proposal(record_key):
    return {
        "record_key": record_key,
        "proposal_type": "EXISTING_IDENTITY_CANDIDATE",
        "candidate_group_id": None,
        "candidate_player_uid": "pid_0000000000000001",
        "candidate_name_display_only": "中文球员",
        "proposed_relation": "KEEP_UNDECIDED",
        "machine_suggestion": "KEEP_UNDECIDED",
        "machine_reason": "synthetic",
        "evidence_refs": ["evidence"],
    }


def test_cli_web_evidence_private_workflow(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    (instance / "inputs").mkdir()
    (instance / "outputs").mkdir()
    (instance / "inputs/answers.html").write_text(
        '<span data-claim-type="OFFICIAL_PLAYER_NAME" '
        'data-claim-value="中文球员"></span>',
        encoding="utf-8",
    )
    discovery = {
        "query": "官方注册",
        "discovered_url": "https://www.cbaleague.com/player/1",
        "discovery_provider": "search_engine",
        "discovered_at": "2026-09-18T00:00:00Z",
        "title_hint": "中文球员",
    }
    (instance / "inputs/discovery_input.json").write_text(
        json.dumps([discovery]),
        encoding="utf-8",
    )
    (instance / "inputs/responses.json").write_text(
        json.dumps([{
            "discovery": {
                **discovery,
                "schema_version": 1,
                "authority": "DISCOVERY_ONLY",
                "discovered_domain": "www.cbaleague.com",
            },
            "body_path": "inputs/answers.html",
            "status": 200,
            "content_type": "text/html",
            "record_keys": ["r1"],
        }]),
        encoding="utf-8",
    )
    manifest = build_evidence_manifest([])
    (instance / "inputs/evidence_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (instance / "inputs/candidates.json").write_text(
        json.dumps([proposal("r1")]),
        encoding="utf-8",
    )
    (instance / "inputs/statuses.json").write_text("{}", encoding="utf-8")
    packet = prepare_review_packet([proposal("r1")])
    (instance / "inputs/review_packet.json").write_text(
        json.dumps(packet),
        encoding="utf-8",
    )
    (instance / "inputs/groups.json").write_text("[]", encoding="utf-8")
    (instance / "inputs/batches.json").write_text("[]", encoding="utf-8")

    common = ("--instance-root", str(instance))
    result = command(
        *common,
        "identity-web-evidence-discover",
        "--input",
        "inputs/discovery_input.json",
        "--output",
        "outputs/discovered_urls.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-web-evidence-collect",
        "--responses",
        "inputs/responses.json",
        "--synthetic-responses",
        "--output-root",
        "outputs/collected",
        "--manifest",
        "outputs/web_evidence_manifest.json",
        "--source-tier",
        "A1",
        "--source-kind",
        "registration_notice",
        "--extracted-at",
        "2026-09-18T00:00:00Z",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-web-evidence-enrich",
        "--candidates",
        "inputs/candidates.json",
        "--evidence-manifest",
        "inputs/evidence_manifest.json",
        "--search-statuses",
        "inputs/statuses.json",
        "--output-candidates",
        "outputs/enriched.json",
        "--output-conflicts",
        "outputs/conflicts.json",
        "--output-summary",
        "outputs/summary.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-review-workbook-export",
        "--packet",
        "inputs/review_packet.json",
        "--evidence-manifest",
        "inputs/evidence_manifest.json",
        "--groups",
        "inputs/groups.json",
        "--batches",
        "inputs/batches.json",
        "--output",
        "outputs/review_packet_workbook.xlsx",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-review-workbook-import",
        "--packet",
        "inputs/review_packet.json",
        "--evidence-manifest",
        "inputs/evidence_manifest.json",
        "--groups",
        "inputs/groups.json",
        "--batches",
        "inputs/batches.json",
        "--workbook",
        "outputs/review_packet_workbook.xlsx",
        "--output-csv",
        "outputs/review_projection.csv",
    )
    assert result.returncode == 0, result.stderr
    assert (instance / "outputs/review_projection.csv").is_file()


def test_cli_web_evidence_path_boundary(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    (instance / "inputs").mkdir()
    (instance / "inputs/discovery_input.json").write_text(
        json.dumps([{
            "query": "q",
            "discovered_url": "https://www.cbaleague.com/player/1",
            "discovery_provider": "search_engine",
            "discovered_at": "2026-09-18T00:00:00Z",
            "title_hint": None,
        }]),
        encoding="utf-8",
    )
    outside = tmp_path / "outside.json"
    result = command(
        "--instance-root",
        str(instance),
        "identity-web-evidence-discover",
        "--input",
        "inputs/discovery_input.json",
        "--output",
        str(outside),
    )
    assert result.returncode == 1
    assert "IDENTITY_WEB_EVIDENCE_PATH_OUTSIDE_PRIVATE_INSTANCE" in result.stderr
