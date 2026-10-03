"""Adversarial and negative tests for CBA-KB stats domain.

Verifies:
- Caller cannot spoof or override semantic_grain (R7).
- Malformed numbers, invalid scopes, out-of-range percentages fail closed.
- Identity resolution fail-closed invariants (unresolved record cannot carry player_uid).
- Private locator leakage in consumer projection triggers fail-closed behavior.
- Zero-diff invariant: stats operations cause 0 mutations to MASTER / Canonical Facts / Identity Registry.
"""
from __future__ import annotations

import copy
import json
import pytest

from cba_kb.adapters.cba_stats import CBAStatsAdapter
from cba_kb.common import digest
from cba_kb.consumer_integration import (
    CONSUMER_CAPABILITY_PLAYER_STATS,
    find_private_locator_in_object,
    project_stats_for_consumer,
)
from cba_kb.player_identity import new_registry, serialize_registry
from cba_kb.stats import (
    SEMANTIC_GRAIN,
    StatsError,
    StatsValidationError,
    validate_stats_record,
)

SAMPLE_VALID_STATS = {
    "record_id": "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118",
    "semantic_grain": "PLAYER_SEASON_STATS",
    "schema_version": 1,
    "player_uid": None,
    "identity_status": "REVIEW_REQUIRED",
    "identity_resolution": {
        "status": "REVIEW_REQUIRED",
        "candidate_player_uids": ["pid_0000000000000001"],
        "resolution_method": "CANDIDATE_NAME_MATCH_REQUIRES_MANUAL_OR_EXTERNAL_ID_REVIEW",
        "reason": "CANDIDATE_NAME_MATCH_REQUIRES_MANUAL_OR_EXTERNAL_ID_REVIEW",
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


def test_adversarial_semantic_grain_spoofing():
    spoofed = copy.deepcopy(SAMPLE_VALID_STATS)
    spoofed["semantic_grain"] = "CANONICAL_FACT"
    with pytest.raises(StatsValidationError, match="INVALID_SEMANTIC_GRAIN"):
        validate_stats_record(spoofed)

    spoofed["semantic_grain"] = "STATEMENT"
    with pytest.raises(StatsValidationError, match="INVALID_SEMANTIC_GRAIN"):
        validate_stats_record(spoofed)


def test_adversarial_unresolved_with_player_uid():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["identity_status"] = "REVIEW_REQUIRED"
    bad["player_uid"] = "pid_0000000000000001"
    with pytest.raises(StatsValidationError, match="UNRESOLVED_RECORD_CANNOT_HAVE_PLAYER_UID"):
        validate_stats_record(bad)


def test_adversarial_resolved_without_player_uid():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["identity_status"] = "RESOLVED"
    bad["identity_resolution"]["status"] = "RESOLVED"
    bad["player_uid"] = None
    with pytest.raises(StatsValidationError, match="RESOLVED_RECORD_REQUIRES_PLAYER_UID"):
        validate_stats_record(bad)


def test_adversarial_invalid_scope_and_team_split():
    bad_scope = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_scope["scope"] = "INVALID_SCOPE"
    with pytest.raises(StatsValidationError, match="INVALID_SCOPE"):
        validate_stats_record(bad_scope)

    # TEAM_SPLIT missing both team_id and team_name
    bad_split = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_split["scope"] = "TEAM_SPLIT"
    bad_split["team_id"] = None
    bad_split["team_name"] = None
    with pytest.raises(StatsValidationError, match="TEAM_SPLIT_REQUIRES_TEAM_ID_OR_NAME"):
        validate_stats_record(bad_split)


def test_adversarial_percentage_out_of_range():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["metrics"]["field_goals_percentage_rate"] = 1.25
    with pytest.raises(StatsValidationError, match="INVALID_PERCENTAGE_RATE_RANGE"):
        validate_stats_record(bad)

    bad["metrics"]["field_goals_percentage_rate"] = -0.05
    with pytest.raises(StatsValidationError, match="INVALID_PERCENTAGE_RATE_RANGE"):
        validate_stats_record(bad)

    bad["metrics"]["field_goals_percentage_rate"] = 0.5
    bad["metrics"]["field_goals_percentage"] = "invalid_pct"
    with pytest.raises(StatsValidationError, match="INVALID_PERCENTAGE_FORMAT"):
        validate_stats_record(bad)


def test_adversarial_private_locator_in_projection():
    # If a stats record has an un-sanitized local filesystem path
    bad_prov_record = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_prov_record["rights"]["classification"] = "PUBLIC"
    bad_prov_record["rights"]["public_export_allowed"] = True
    bad_prov_record["raw_metrics"]["local_debug_file"] = "/Users/example/secret_debug.log"

    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": bad_prov_record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer(
        [bad_prov_record],
        target="ChatGPT",
        authorizations=auth_list,
    )
    # Must fail closed if any private locator remains
    assert exported == []
    assert cap == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"


def test_a15_zero_diff_on_identity_registry():
    registry = new_registry(
        players=[
            {"schema_version": 1, "player_uid": "pid_0000000000000001", "canonical_name": "易建联", "status": "ACTIVE", "redirect_to": None},
            {"schema_version": 1, "player_uid": "pid_0000000000000002", "canonical_name": "萨姆纳", "status": "ACTIVE", "redirect_to": None},
        ],
        record_links=[
            {
                "schema_version": 1,
                "record_key": "cba_player_id:393433",
                "player_uid": "pid_0000000000000001",
                "link_status": "same",
                "method": "EXTERNAL_IDENTIFIER",
                "confidence": "HIGH",
                "evidence_refs": ["official_stats_link"],
            }
        ],
    )
    registry_before = serialize_registry(registry)

    # Perform stats transformations and resolution
    adapter = CBAStatsAdapter(key=b"test_key_16_byte")
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    _ = adapter.transform_provider_record(
        {
            "season": 2024,
            "playerId": 100098118,
            "cnAlias": "萨姆纳",
            "playerTimes": 26.0,
            "points": "36.0",
            "rebounds": 6.6,
            "assists": 6.8,
        },
        prov,
        registry=registry,
    )

    registry_after = serialize_registry(registry)
    # Invariant A15: Exact zero diff on identity registry
    assert registry_before == registry_after


def test_adversarial_advanced_metrics_exclusion_and_stripping():
    """Adversarial test: verify advanced rim/mid-range fields cannot be sneaked into consumer projection."""
    # Construct an adversarial record where caller injected advanced keys into metrics and raw_metrics
    sneaked_record = copy.deepcopy(SAMPLE_VALID_STATS)
    sneaked_record["rights"]["classification"] = "PUBLIC"
    sneaked_record["rights"]["public_export_allowed"] = True
    sneaked_record["metrics"]["rim_made"] = 100.0
    sneaked_record["metrics"]["mid_range_made"] = 50.0
    sneaked_record["raw_metrics"]["fieldGoalsAtRimMade"] = 100.0
    sneaked_record["raw_metrics"]["fieldGoalsMidRangeAttempted"] = 200.0

    auths = [
        {
            "target": "ChatGPT",
            "doc_id": sneaked_record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer([sneaked_record], target="ChatGPT", authorizations=auths)
    assert cap == "MATERIALIZED"
    assert len(exported) == 1
    exp = exported[0]

    out_of_scope = {
        "rim_made",
        "rim_attempted",
        "mid_range_made",
        "mid_range_attempted",
        "fieldGoalsAtRimMade",
        "fieldGoalsAtRimAttempted",
        "fieldGoalsMidRangeMade",
        "fieldGoalsMidRangeAttempted",
    }
    for k in out_of_scope:
        assert k not in exp["metrics"]
        assert k not in exp["raw_metrics"]

