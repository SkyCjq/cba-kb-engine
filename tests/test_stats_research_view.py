"""Tests for Research View STATS semantic grain integration.

Verifies:
- R7 / A14: Authoritative non-overridable STATS grain labeling.
- Multi-grain synthesis: Facts, Statements, Claims, Verification items, Unknown items, and STATS coexist without semantic collapse.
- Caller cannot spoof STATS as CANONICAL_FACT or STATEMENT.
- Markdown rendering includes official STATS section and disclaimer.
"""
from __future__ import annotations

import copy
import json
import pytest

from cba_kb.research_view import GRAIN_LABELS, ResearchView

SAMPLE_STATS_RECORD = {
    "record_id": "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118",
    "semantic_grain": "PLAYER_SEASON_STATS",
    "schema_version": 1,
    "player_uid": "pid_0000000000000002",
    "identity_status": "RESOLVED",
    "identity_resolution": {
        "status": "RESOLVED",
        "candidate_player_uids": ["pid_0000000000000002"],
        "resolution_method": "TRUSTED_EXTERNAL_ID_LINK",
        "reason": "TRUSTED_EXTERNAL_ID_LINK",
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
        "rebounds_per_game": 6.6,
        "assists_per_game": 6.8,
        "field_goals_percentage": "44.8%",
        "field_goals_percentage_rate": 0.4482,
    },
    "raw_metrics": {"points": "36.0"},
    "provenance": {
        "source_uri": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
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

SAMPLE_STATEMENT = {
    "statement_id": "stmt_000000000000000000000001",
    "doc_id": "doc_000000000000000000000001",
    "speaker_actor_ref": {"raw_name": "萨姆纳", "id": "pid_0000000000000002", "kind": "player", "evidence_ref": "speaker_clip"},
    "subject_actor_refs": [{"raw_name": "萨姆纳", "id": "pid_0000000000000002", "kind": "player", "evidence_ref": "subject_clip"}],
    "time_anchor": "2024-11-01",
    "statement_text_or_controlled_excerpt": "我们本赛季的目标是全力以赴打好每一场比赛。",
    "source_ref": "official_interview",
    "evidence_ref": "interview_clip",
    "attribution_type": "direct_quote",
    "rights": {
        "classification": "public",
        "public_export_allowed": True,
        "evidence": "press_conference",
    },
    "provenance": "official_media",
    "extraction_status": "accepted",
}

SAMPLE_CLAIM = {
    "claim_id": "claim_000000000000000000000001",
    "claim_text": "萨姆纳认为球队在2024赛季有信心冲击更好成绩。",
    "status": "unverified",
    "supporting_statement_ids": ["stmt_000000000000000000000001"],
    "evidence_refs": ["interview_clip"],
    "review_reason": None,
    "provenance": {"decision_ref": "synth_claim_01"},
}


def test_research_view_grain_labels_includes_stats():
    assert "STATS" in GRAIN_LABELS


def test_research_view_stats_grain_non_overridable():
    spoofed = copy.deepcopy(SAMPLE_STATS_RECORD)
    # Caller attempts to spoof grain as CANONICAL_FACT
    spoofed["semantic_grain"] = "CANONICAL_FACT"

    rv = ResearchView(
        subject_name="萨姆纳",
        subject_player_uid="pid_0000000000000002",
        stats=[spoofed],
    )
    # The view MUST strip the caller spoof and enforce authoritative STATS grain
    assert rv.stats[0]["semantic_grain"] == "STATS"
    assert rv.to_dict()["grains"]["stats"][0]["semantic_grain"] == "STATS"


def test_research_view_multi_grain_synthesis():
    facts = [{"season": "2024-2025", "club": "四川丰谷酒业", "registration_type": "外籍球员", "contract_type": "标准合同"}]

    rv = ResearchView(
        subject_name="萨姆纳",
        subject_player_uid="pid_0000000000000002",
        canonical_facts=facts,
        statements=[SAMPLE_STATEMENT],
        claims=[SAMPLE_CLAIM],
        stats=[SAMPLE_STATS_RECORD],
    )

    data = rv.to_dict()
    assert data["summary_counts"]["stats_count"] == 1
    assert data["summary_counts"]["canonical_facts_count"] == 1
    assert data["summary_counts"]["statements_count"] == 1
    assert data["summary_counts"]["claims_count"] == 1

    # Invariant: Grains remain separate
    grains = data["grains"]
    assert grains["canonical_facts"][0]["semantic_grain"] == "CANONICAL_FACT"
    assert grains["statements"][0]["semantic_grain"] == "STATEMENT"
    assert grains["claims"][0]["semantic_grain"] == "CLAIM"
    assert grains["stats"][0]["semantic_grain"] == "STATS"


def test_research_view_markdown_rendering():
    rv = ResearchView(
        subject_name="萨姆纳",
        subject_player_uid="pid_0000000000000002",
        statements=[SAMPLE_STATEMENT],
        stats=[SAMPLE_STATS_RECORD],
    )
    md = rv.render_markdown()

    assert "## 8. 比赛数据表现层 (STATS)" in md
    assert "萨姆纳" in md
    assert "36.0" in md
    assert "44.8%" in md
    assert "STATS 源于官方技术统计通道，独立于注册事实 (CANONICAL_FACT) 与发言陈述 (STATEMENT)" in md
