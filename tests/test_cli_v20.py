"""CLI tests for v2.0 Stats subcommands (stats-ingest, stats-validate, research-view)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest


import os

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "cba_kb.cli", *args]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_cli_stats_validate_valid(tmp_path: Path):
    sample = {
        "record_id": "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118",
        "semantic_grain": "PLAYER_SEASON_STATS",
        "schema_version": 1,
        "player_uid": None,
        "identity_status": "REVIEW_REQUIRED",
        "identity_resolution": {
            "status": "REVIEW_REQUIRED",
            "candidate_player_uids": ["pid_0000000000000001"],
            "resolution_method": "CANDIDATE_NAME_MATCH",
            "reason": "CANDIDATE_NAME_MATCH",
        },
        "provider": "CBA_OFFICIAL",
        "provider_player_id": "100098118",
        "player_name": "萨姆纳",
        "season": "2024",
        "competition": "CBA",
        "match_type": "1",
        "match_type_label": "REGULAR_SEASON",
        "scope": "WHOLE_SEASON",
        "team_id": "29127",
        "team_name": "四川丰谷酒业",
        "metrics": {
            "games_played": 26.0,
            "games_started": 26,
            "points_per_game": 36.0,
            "field_goals_percentage": "44.8%",
            "field_goals_percentage_rate": 0.4482,
        },
        "raw_metrics": {"points": "36.0"},
        "provenance": {
            "source_uri": "https://data-server.cbaleague.com/api/player-base-list",
            "endpoint": "/api/player-base-list",
            "request_contract": {},
            "season": "2024",
            "match_type": "1",
            "raw_response_sha256": "a" * 64,
            "decoded_sha256": "b" * 64,
            "captured_at": "2026-10-03T10:00:00Z",
        },
        "rights": {
            "classification": "UNKNOWN_FOR_REDISTRIBUTION",
            "public_export_allowed": False,
            "private_ai_consumption": "TARGET_SPECIFIC",
            "materialization_authorized": False,
        },
    }
    input_file = tmp_path / "valid_stats.json"
    input_file.write_text(json.dumps([sample]), encoding="utf-8")
    output_file = tmp_path / "result.json"

    proc = run_cli("stats-validate", "--input", str(input_file), "--output", str(output_file))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["status"] == "VALID"
    assert data["validated_count"] == 1


def test_cli_stats_validate_invalid(tmp_path: Path):
    invalid_sample = {
        "record_id": "bad_record",
        "semantic_grain": "CANONICAL_FACT",  # Invalid grain
    }
    input_file = tmp_path / "invalid_stats.json"
    input_file.write_text(json.dumps([invalid_sample]), encoding="utf-8")

    proc = run_cli("stats-validate", "--input", str(input_file))
    assert proc.returncode != 0
    assert "StatsValidationError" in proc.stderr or "MISSING_REQUIRED_KEYS" in proc.stderr


def test_cli_stats_ingest_offline_payload(tmp_path: Path):
    raw_record = {
        "season": 2024,
        "playerId": 100098118,
        "cnAlias": "萨姆纳",
        "teamId": 29127,
        "teamCnAlias": "四川丰谷酒业",
        "gameStartNum": 26,
        "playerTimes": 26.0,
        "minutes": "35.9",
        "seconds": "2156.4",
        "points": "36.0",
        "fieldGoals": "10.7",
        "fieldGoalsAttempted": "23.8",
        "fieldGoalsPercentage": "44.8%",
        "fieldGoalsPercentageSort": 0.4482,
        "threePointGoals": "2.5",
        "threePointAttempted": "7.8",
        "threePointPercentage": "31.5%",
        "threePointPercentageSort": 0.3153,
        "freeThrows": "12.3",
        "freeThrowsAttempted": "14.0",
        "freeThrowsPercentage": "87.6%",
        "freeThrowsPercentageSort": 0.8764,
        "rebounds": "6.6",
        "assists": "6.8",
        "steals": "2.1",
        "blocked": "0.5",
        "turnovers": "4.1",
        "fouls": "2.8",
    }
    offline_file = tmp_path / "offline_records.json"
    offline_file.write_text(json.dumps({"records": [raw_record]}), encoding="utf-8")
    output_file = tmp_path / "canonical_stats.json"

    proc = run_cli(
        "stats-ingest",
        "--season", "2024",
        "--offline-payload", str(offline_file),
        "--output", str(output_file),
    )
    assert proc.returncode == 0, proc.stderr

    records = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["semantic_grain"] == "PLAYER_SEASON_STATS"
    assert records[0]["player_name"] == "萨姆纳"
    assert records[0]["metrics"]["points_per_game"] == 36.0


def test_cli_research_view_with_stats(tmp_path: Path):
    stats_record = {
        "record_id": "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118",
        "semantic_grain": "PLAYER_SEASON_STATS",
        "schema_version": 1,
        "player_uid": "pid_0000000000000002",
        "identity_status": "RESOLVED",
        "identity_resolution": {
            "status": "RESOLVED",
            "candidate_player_uids": ["pid_0000000000000002"],
            "resolution_method": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:100098118",
            "reason": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:100098118",
        },
        "provider": "CBA_OFFICIAL",
        "provider_player_id": "100098118",
        "player_name": "萨姆纳",
        "season": "2024",
        "competition": "CBA",
        "match_type": "1",
        "match_type_label": "REGULAR_SEASON",
        "scope": "WHOLE_SEASON",
        "team_id": "29127",
        "team_name": "四川丰谷酒业",
        "metrics": {
            "games_played": 26.0,
            "games_started": 26,
            "points_per_game": 36.0,
            "field_goals_percentage": "44.8%",
            "field_goals_percentage_rate": 0.4482,
        },
        "raw_metrics": {"points": "36.0"},
        "provenance": {
            "source_uri": "https://data-server.cbaleague.com/api/player-base-list",
            "endpoint": "/api/player-base-list",
            "request_contract": {},
            "season": "2024",
            "match_type": "1",
            "raw_response_sha256": "a" * 64,
            "decoded_sha256": "b" * 64,
            "captured_at": "2026-10-03T10:00:00Z",
        },
        "rights": {
            "classification": "UNKNOWN_FOR_REDISTRIBUTION",
            "public_export_allowed": False,
            "private_ai_consumption": "TARGET_SPECIFIC",
            "materialization_authorized": False,
        },
    }
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps([stats_record]), encoding="utf-8")
    output_md = tmp_path / "view.md"

    proc = run_cli(
        "research-view",
        "--name", "萨姆纳",
        "--stats", str(stats_file),
        "--format", "markdown",
        "--output", str(output_md),
    )
    assert proc.returncode == 0, proc.stderr
    md_content = output_md.read_text(encoding="utf-8")
    assert "## 8. 比赛数据表现层 (STATS)" in md_content
    assert "萨姆纳" in md_content
    assert "36.0" in md_content
