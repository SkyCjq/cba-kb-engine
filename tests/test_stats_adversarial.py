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

import cba_kb.consumer_integration as consumer_integration
from cba_kb.adapters.cba_stats import CBAStatsAdapter
from cba_kb.common import digest
from cba_kb.consumer_integration import (
    CONSUMER_CAPABILITY_PLAYER_STATS,
    STATS_CONSUMER_PROVENANCE_FIELDS,
    STATS_CONSUMER_REQUEST_CONTRACT_FIELDS,
    find_private_locator_in_object,
    project_stats_for_consumer,
)
from cba_kb.player_identity import new_registry, serialize_registry
from cba_kb.stats import (
    ALLOWED_TOP_LEVEL_FIELDS,
    SEMANTIC_GRAIN,
    StatsError,
    StatsValidationError,
    build_record_id,
    deduplicate_and_reconcile_stats,
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
        "request_contract": {"season": 2024, "matchTypeId": 1},
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

    # TEAM_SPLIT missing team_id
    bad_split = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_split["scope"] = "TEAM_SPLIT"
    bad_split["team_id"] = None
    with pytest.raises(StatsValidationError, match="TEAM_SPLIT_REQUIRES_TEAM_ID"):
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
    bad_prov_record["team_name"] = "/Users/example/secret_debug.log"

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


def test_adversarial_unknown_canonical_metric_fail_closed():
    """Assert arbitrary unknown canonical metric keys fail closed at canonical validation."""
    # Arbitrary unknown advanced metric (e.g. usage_rate)
    bad_record = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_record["metrics"]["usage_rate"] = 0.285
    with pytest.raises(StatsValidationError, match="UNKNOWN_CANONICAL_METRIC_KEYS:usage_rate"):
        validate_stats_record(bad_record)

    # Known rim/mid-range advanced metric (e.g. rim_made, mid_range_attempted)
    bad_record2 = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_record2["metrics"]["rim_made"] = 100.0
    bad_record2["metrics"]["mid_range_attempted"] = 200.0
    with pytest.raises(StatsValidationError, match="UNKNOWN_CANONICAL_METRIC_KEYS:mid_range_attempted,rim_made"):
        validate_stats_record(bad_record2)


def test_adversarial_unknown_raw_provider_key_fail_closed():
    """Assert arbitrary unknown raw provider keys fail closed at canonical validation."""
    # Arbitrary unknown provider key (e.g. foulsDefensive)
    bad_record = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_record["raw_metrics"]["foulsDefensive"] = 45.0
    with pytest.raises(StatsValidationError, match="UNKNOWN_RAW_PROVIDER_KEYS:foulsDefensive"):
        validate_stats_record(bad_record)

    # Known rim/mid-range provider key (e.g. fieldGoalsAtRimMade)
    bad_record2 = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_record2["raw_metrics"]["fieldGoalsAtRimMade"] = 171.0
    with pytest.raises(StatsValidationError, match="UNKNOWN_RAW_PROVIDER_KEYS:fieldGoalsAtRimMade"):
        validate_stats_record(bad_record2)

    # Non-dict raw_metrics
    bad_record3 = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_record3["raw_metrics"] = ["not_a_dict"]
    with pytest.raises(StatsValidationError, match="RAW_METRICS_DICT_REQUIRED"):
        validate_stats_record(bad_record3)


def test_adversarial_advanced_metrics_exclusion_and_stripping():
    """Adversarial test: verify advanced/unknown fields fail validation and consumer projection only projects allowlist."""
    # Construct an adversarial record where caller injected advanced keys into metrics and raw_metrics
    sneaked_record = copy.deepcopy(SAMPLE_VALID_STATS)
    sneaked_record["rights"]["classification"] = "PUBLIC"
    sneaked_record["rights"]["public_export_allowed"] = True
    sneaked_record["metrics"]["rim_made"] = 100.0

    auths = [
        {
            "target": "ChatGPT",
            "doc_id": sneaked_record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    # Fails closed at validation when entering consumer projection
    with pytest.raises(StatsValidationError, match="UNKNOWN_CANONICAL_METRIC_KEYS:rim_made"):
        project_stats_for_consumer([sneaked_record], target="ChatGPT", authorizations=auths)

    # Clean valid record passes and projects only allowlisted fields
    valid_record = copy.deepcopy(SAMPLE_VALID_STATS)
    valid_record["rights"]["classification"] = "PUBLIC"
    valid_record["rights"]["public_export_allowed"] = True
    exported, cap = project_stats_for_consumer([valid_record], target="ChatGPT", authorizations=auths)
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
        "usage_rate",
        "foulsDefensive",
    }
    for k in out_of_scope:
        assert k not in exp["metrics"]
        assert k not in exp["raw_metrics"]


def test_adversarial_record_id_mismatch():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["record_id"] = "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:999999999"
    with pytest.raises(StatsValidationError, match="RECORD_ID_MISMATCH"):
        validate_stats_record(bad)


def test_adversarial_invalid_provider():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["provider"] = "NBA_OFFICIAL"
    with pytest.raises(StatsValidationError, match="INVALID_PROVIDER:NBA_OFFICIAL"):
        validate_stats_record(bad)


def test_adversarial_invalid_competition():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["competition"] = "WCBA"
    with pytest.raises(StatsValidationError, match="INVALID_COMPETITION:WCBA"):
        validate_stats_record(bad)


def test_adversarial_spoofed_resolved_without_trusted_method():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["identity_status"] = "RESOLVED"
    bad["player_uid"] = "pid_0000000000000001"
    bad["identity_resolution"] = {
        "status": "RESOLVED",
        "candidate_player_uids": ["pid_0000000000000001"],
        "resolution_method": "CANDIDATE_NAME_MATCH_FUZZY",
        "reason": "CANDIDATE_NAME_MATCH_FUZZY",
    }
    with pytest.raises(StatsValidationError, match="RESOLVED_IDENTITY_REQUIRES_TRUSTED_LINK_METHOD"):
        validate_stats_record(bad)


def test_adversarial_resolved_player_uid_candidate_mismatch():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["identity_status"] = "RESOLVED"
    bad["player_uid"] = "pid_0000000000000002"
    bad["identity_resolution"] = {
        "status": "RESOLVED",
        "candidate_player_uids": ["pid_0000000000000001"],
        "resolution_method": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:100098118",
        "reason": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:100098118",
    }
    with pytest.raises(StatsValidationError, match="RESOLVED_PLAYER_UID_NOT_IN_CANDIDATE_UIDS"):
        validate_stats_record(bad)


def test_adversarial_metric_type_checks():
    # Boolean value in numeric metric rejected (bool is int subclass in python)
    bad_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_bool["metrics"]["points_per_game"] = True
    with pytest.raises(StatsValidationError, match="INVALID_METRIC_TYPE:points_per_game=True"):
        validate_stats_record(bad_bool)

    # Negative value rejected
    bad_neg = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_neg["metrics"]["points_per_game"] = -5.0
    with pytest.raises(StatsValidationError, match="NEGATIVE_METRIC_VALUE:points_per_game=-5.0"):
        validate_stats_record(bad_neg)

    # String value for numeric metric rejected
    bad_str = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_str["metrics"]["points_per_game"] = "36.0"
    with pytest.raises(StatsValidationError, match="INVALID_METRIC_TYPE:points_per_game='36.0'"):
        validate_stats_record(bad_str)

    # Negative games_started rejected
    bad_gs = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_gs["metrics"]["games_started"] = -1
    with pytest.raises(StatsValidationError, match="INVALID_GAMES_STARTED"):
        validate_stats_record(bad_gs)

    # Non-integer games_started rejected
    bad_gs_float = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_gs_float["metrics"]["games_started"] = 1.5
    with pytest.raises(StatsValidationError, match="INVALID_GAMES_STARTED"):
        validate_stats_record(bad_gs_float)


def test_adversarial_unknown_top_level_field():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["unauthorized_top_level_key"] = "malicious_payload"
    with pytest.raises(StatsValidationError, match="UNKNOWN_TOP_LEVEL_FIELDS:unauthorized_top_level_key"):
        validate_stats_record(bad)


def test_adversarial_raw_metrics_nested_value_rejected():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["raw_metrics"]["points"] = {"nested_obj": 36.0}
    with pytest.raises(StatsValidationError, match="RAW_METRICS_NON_SCALAR_VALUE:points=dict"):
        validate_stats_record(bad)

    bad_list = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_list["raw_metrics"]["points"] = [36.0, 37.0]
    with pytest.raises(StatsValidationError, match="RAW_METRICS_NON_SCALAR_VALUE:points=list"):
        validate_stats_record(bad_list)


def test_temporal_supersession_timezone_handling():
    rec1 = copy.deepcopy(SAMPLE_VALID_STATS)
    rec1["metrics"]["points_per_game"] = 30.0
    rec1["provenance"]["captured_at"] = "2026-10-03T18:00:00+08:00"  # 10:00:00 UTC
    rec1["provenance"]["season"] = "2024"
    rec1["provenance"]["match_type"] = "1"

    rec2 = copy.deepcopy(SAMPLE_VALID_STATS)
    rec2["metrics"]["points_per_game"] = 35.0
    rec2["provenance"]["captured_at"] = "2026-10-03T11:00:00Z"  # 11:00:00 UTC (1 hour later than rec1)
    rec2["provenance"]["season"] = "2024"
    rec2["provenance"]["match_type"] = "1"

    # Note: Lexically, "2026-10-03T18:00:00+08:00" > "2026-10-03T11:00:00Z"
    # But temporally, rec2 is newer than rec1. True instant comparison must pick rec2.
    reconciled = deduplicate_and_reconcile_stats([rec1, rec2])
    assert len(reconciled) == 1
    assert reconciled[0]["metrics"]["points_per_game"] == 35.0


def test_deterministic_replay_without_captured_at():
    adapter = CBAStatsAdapter(key=b"test_aes_key_16b")
    prov_no_ts = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "c" * 64,
        "decoded_sha256": "d" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    raw_data = {
        "season": 2024,
        "playerId": 100098118,
        "cnAlias": "萨姆纳",
        "playerTimes": 26.0,
        "points": "36.0",
        "rebounds": 6.6,
        "assists": 6.8,
    }
    rec1 = adapter.transform_provider_record(raw_data, prov_no_ts)
    rec2 = adapter.transform_provider_record(raw_data, prov_no_ts)

    # Must be byte-for-byte identical (no fabricated datetime.now())
    assert json.dumps(rec1, sort_keys=True) == json.dumps(rec2, sort_keys=True)
    assert "captured_at" not in rec1["provenance"]
    assert "captured_at" not in rec2["provenance"]


def test_scope_exact_case_rejected():
    bad_lower = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_lower["scope"] = "whole_season"
    with pytest.raises(StatsValidationError, match="INVALID_SCOPE:whole_season"):
        validate_stats_record(bad_lower)

    bad_mixed = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_mixed["scope"] = "Team_Split"
    with pytest.raises(StatsValidationError, match="INVALID_SCOPE:Team_Split"):
        validate_stats_record(bad_mixed)

    bad_space = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_space["scope"] = " WHOLE_SEASON "
    with pytest.raises(StatsValidationError, match="INVALID_SCOPE: WHOLE_SEASON "):
        validate_stats_record(bad_space)


def test_optional_metadata_types_rejected():
    bad_mtl_dict = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_mtl_dict["match_type_label"] = {"nested": "dict"}
    with pytest.raises(StatsValidationError, match="INVALID_MATCH_TYPE_LABEL"):
        validate_stats_record(bad_mtl_dict)

    bad_mtl_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_mtl_bool["match_type_label"] = True
    with pytest.raises(StatsValidationError, match="INVALID_MATCH_TYPE_LABEL"):
        validate_stats_record(bad_mtl_bool)

    bad_tid_list = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_tid_list["team_id"] = [123]
    with pytest.raises(StatsValidationError, match="INVALID_TEAM_ID"):
        validate_stats_record(bad_tid_list)

    bad_tid_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_tid_bool["team_id"] = False
    with pytest.raises(StatsValidationError, match="INVALID_TEAM_ID"):
        validate_stats_record(bad_tid_bool)

    bad_tname_dict = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_tname_dict["team_name"] = {"cn": "北京首钢"}
    with pytest.raises(StatsValidationError, match="INVALID_TEAM_NAME"):
        validate_stats_record(bad_tname_dict)

    bad_tname_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_tname_bool["team_name"] = True
    with pytest.raises(StatsValidationError, match="INVALID_TEAM_NAME"):
        validate_stats_record(bad_tname_bool)


def test_fabricated_trusted_method_substring_rejected():
    bad = copy.deepcopy(SAMPLE_VALID_STATS)
    bad["identity_status"] = "RESOLVED"
    bad["player_uid"] = "pid_0000000000000001"
    bad["identity_resolution"] = {
        "status": "RESOLVED",
        "candidate_player_uids": ["pid_0000000000000001"],
        "resolution_method": "UNTRUSTED_PREFIX_WITH_TRUSTED_EXTERNAL_ID_LINK_SUBSTRING",
        "reason": "UNTRUSTED_PREFIX_WITH_TRUSTED_EXTERNAL_ID_LINK_SUBSTRING",
    }
    with pytest.raises(StatsValidationError, match="RESOLVED_IDENTITY_REQUIRES_TRUSTED_LINK_METHOD"):
        validate_stats_record(bad)

    # Different provider_player_id in method
    bad_diff_id = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_diff_id["identity_status"] = "RESOLVED"
    bad_diff_id["player_uid"] = "pid_0000000000000001"
    bad_diff_id["identity_resolution"] = {
        "status": "RESOLVED",
        "candidate_player_uids": ["pid_0000000000000001"],
        "resolution_method": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:999999999",
        "reason": "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:999999999",
    }
    with pytest.raises(StatsValidationError, match="RESOLVED_IDENTITY_REQUIRES_TRUSTED_LINK_METHOD"):
        validate_stats_record(bad_diff_id)


def test_trusted_resolver_external_id_syntax_valid():
    valid_methods = [
        "TRUSTED_EXTERNAL_ID_LINK:cba_player_id:100098118",
        "TRUSTED_EXTERNAL_ID_LINK:cba_player:100098118",
        "TRUSTED_EXTERNAL_ID_LINK:cba_official:100098118",
        "TRUSTED_EXTERNAL_ID_LINK:100098118",
    ]
    for method in valid_methods:
        rec = copy.deepcopy(SAMPLE_VALID_STATS)
        rec["identity_status"] = "RESOLVED"
        rec["player_uid"] = "pid_0000000000000001"
        rec["identity_resolution"] = {
            "status": "RESOLVED",
            "candidate_player_uids": ["pid_0000000000000001"],
            "resolution_method": method,
            "reason": method,
        }
        validated = validate_stats_record(rec)
        assert validated["identity_status"] == "RESOLVED"
        assert validated["player_uid"] == "pid_0000000000000001"


def test_nan_and_infinity_canonical_metrics_rejected():
    bad_nan = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_nan["metrics"]["points_per_game"] = float("nan")
    with pytest.raises(StatsValidationError, match="INVALID_METRIC_TYPE:points_per_game=nan"):
        validate_stats_record(bad_nan)

    bad_inf = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_inf["metrics"]["points_per_game"] = float("inf")
    with pytest.raises(StatsValidationError, match="INVALID_METRIC_TYPE:points_per_game=inf"):
        validate_stats_record(bad_inf)

    bad_neginf = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_neginf["metrics"]["points_per_game"] = float("-inf")
    with pytest.raises(StatsValidationError, match="INVALID_METRIC_TYPE:points_per_game=-inf"):
        validate_stats_record(bad_neginf)

    bad_rate_nan = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_rate_nan["metrics"]["field_goals_percentage_rate"] = float("nan")
    with pytest.raises(StatsValidationError, match="INVALID_PERCENTAGE_RATE_RANGE"):
        validate_stats_record(bad_rate_nan)

    bad_rate_inf = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_rate_inf["metrics"]["field_goals_percentage_rate"] = float("inf")
    with pytest.raises(StatsValidationError, match="INVALID_PERCENTAGE_RATE_RANGE"):
        validate_stats_record(bad_rate_inf)


def test_raw_metrics_type_and_finite_checks():
    bad_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_bool["raw_metrics"]["points"] = True
    with pytest.raises(StatsValidationError, match="RAW_METRICS_INVALID_TYPE:points=True\\(bool\\)"):
        validate_stats_record(bad_bool)

    bad_nan = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_nan["raw_metrics"]["points"] = float("nan")
    with pytest.raises(StatsValidationError, match="RAW_METRICS_NON_FINITE_VALUE:points=nan"):
        validate_stats_record(bad_nan)

    bad_inf = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_inf["raw_metrics"]["points"] = float("inf")
    with pytest.raises(StatsValidationError, match="RAW_METRICS_NON_FINITE_VALUE:points=inf"):
        validate_stats_record(bad_inf)

    bad_non_num_str = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_non_num_str["raw_metrics"]["points"] = "not_a_valid_number"
    with pytest.raises(StatsValidationError, match="RAW_METRICS_NON_NUMERIC_STRING:points='not_a_valid_number'"):
        validate_stats_record(bad_non_num_str)

    bad_id_bool = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_id_bool["raw_metrics"]["playerId"] = False
    with pytest.raises(StatsValidationError, match="RAW_METRICS_INVALID_TYPE:playerId=False\\(bool\\)"):
        validate_stats_record(bad_id_bool)

    bad_pct_format = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_pct_format["raw_metrics"]["fieldGoalsPercentage"] = "45%invalid"
    with pytest.raises(StatsValidationError, match="RAW_METRICS_INVALID_PERCENTAGE:fieldGoalsPercentage='45%invalid'"):
        validate_stats_record(bad_pct_format)


def test_provenance_mandatory_bindings_rejected():
    bad_no_ep = copy.deepcopy(SAMPLE_VALID_STATS)
    del bad_no_ep["provenance"]["endpoint"]
    with pytest.raises(StatsValidationError, match="PROVENANCE_ENDPOINT_REQUIRED"):
        validate_stats_record(bad_no_ep)

    bad_no_req = copy.deepcopy(SAMPLE_VALID_STATS)
    del bad_no_req["provenance"]["request_contract"]
    with pytest.raises(StatsValidationError, match="PROVENANCE_REQUEST_CONTRACT_DICT_REQUIRED"):
        validate_stats_record(bad_no_req)

    bad_no_season = copy.deepcopy(SAMPLE_VALID_STATS)
    del bad_no_season["provenance"]["season"]
    with pytest.raises(StatsValidationError, match="PROVENANCE_SEASON_MISMATCH"):
        validate_stats_record(bad_no_season)

    bad_mismatch_season = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_mismatch_season["provenance"]["season"] = "2023"
    with pytest.raises(StatsValidationError, match="PROVENANCE_SEASON_MISMATCH"):
        validate_stats_record(bad_mismatch_season)

    bad_no_mt = copy.deepcopy(SAMPLE_VALID_STATS)
    del bad_no_mt["provenance"]["match_type"]
    with pytest.raises(StatsValidationError, match="PROVENANCE_MATCH_TYPE_MISMATCH"):
        validate_stats_record(bad_no_mt)

    bad_mismatch_mt = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_mismatch_mt["provenance"]["match_type"] = "2"
    with pytest.raises(StatsValidationError, match="PROVENANCE_MATCH_TYPE_MISMATCH"):
        validate_stats_record(bad_mismatch_mt)


def test_provenance_naive_vs_timezone_aware_captured_at():
    bad_naive = copy.deepcopy(SAMPLE_VALID_STATS)
    bad_naive["provenance"]["captured_at"] = "2026-10-03T10:00:00"
    with pytest.raises(StatsValidationError, match="PROVENANCE_CAPTURED_AT_TIMEZONE_REQUIRED"):
        validate_stats_record(bad_naive)

    good_utc = copy.deepcopy(SAMPLE_VALID_STATS)
    good_utc["provenance"]["captured_at"] = "2026-10-03T10:00:00Z"
    assert validate_stats_record(good_utc)["provenance"]["captured_at"] == "2026-10-03T10:00:00Z"

    good_offset = copy.deepcopy(SAMPLE_VALID_STATS)
    good_offset["provenance"]["captured_at"] = "2026-10-03T18:00:00+08:00"
    assert validate_stats_record(good_offset)["provenance"]["captured_at"] == "2026-10-03T18:00:00+08:00"


def test_deduplicate_same_metrics_different_provenance_not_idempotent():
    rec1 = copy.deepcopy(SAMPLE_VALID_STATS)
    rec1["provenance"]["captured_at"] = "2026-10-03T10:00:00Z"
    rec1["provenance"]["raw_response_sha256"] = "1" * 64
    rec1["provenance"]["decoded_sha256"] = "2" * 64

    # rec2 has IDENTICAL metrics but different provenance (later timestamp)
    rec2 = copy.deepcopy(rec1)
    rec2["provenance"]["captured_at"] = "2026-10-03T12:00:00Z"
    rec2["provenance"]["raw_response_sha256"] = "3" * 64
    rec2["provenance"]["decoded_sha256"] = "4" * 64

    # Must NOT be treated as an idempotent duplicate; newer timestamp supersedes
    reconciled = deduplicate_and_reconcile_stats([rec1, rec2])
    assert len(reconciled) == 1
    assert reconciled[0]["provenance"]["decoded_sha256"] == "4" * 64
    assert reconciled[0]["provenance"]["captured_at"] == "2026-10-03T12:00:00Z"

    # Conflicting provenance with equal timestamps fails closed
    rec_equal_ts = copy.deepcopy(rec2)
    rec_equal_ts["provenance"]["captured_at"] = "2026-10-03T10:00:00Z"
    with pytest.raises(StatsError, match="equal instants with differing canonical content"):
        deduplicate_and_reconcile_stats([rec1, rec_equal_ts])

    # Conflicting provenance missing captured_at fails closed
    rec_no_ts = copy.deepcopy(rec2)
    del rec_no_ts["provenance"]["captured_at"]
    with pytest.raises(StatsError, match="conflicting canonical records without temporal supersession timestamp"):
        deduplicate_and_reconcile_stats([rec1, rec_no_ts])


def test_q2_rerun_reproducer_arbitrary_request_runtime_metadata_is_not_projected():
    """Second-Q2 reproducer: safe-looking unknown metadata has no consumer data path.

    Verifies:
    - Request/runtime variants that need no sensitive vocabulary are omitted.
    - Approved business fields remain available.
    - Projected output passes the defense-in-depth guard as MATERIALIZED.
    """
    record = copy.deepcopy(SAMPLE_VALID_STATS)
    record["rights"]["classification"] = "PUBLIC"
    record["rights"]["public_export_allowed"] = True
    record["provenance"]["request_contract"] = {
        "season": 2024,
        "matchTypeId": 1,
        "tlsClientProfile": "desktop-v128",
        "runtime-affinity": {
            "nodeAlias": "batch-east",
            "workerPool": "stats-readers",
        },
        "transport.overrides": [
            {"retryClass": "interactive", "routeHint": "primary"},
        ],
        "execution_context": {"traceAlias": "q2-reproducer"},
    }
    record["provenance"]["runtime_metadata"] = {"workerAlias": "local-runner"}

    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer(
        [record], target="ChatGPT", authorizations=auth_list
    )
    assert cap == "MATERIALIZED"
    assert len(exported) == 1
    exp_prov = exported[0]["provenance"]
    rc = exp_prov["request_contract"]

    assert rc == {"season": 2024, "matchTypeId": 1}
    assert set(exp_prov) <= STATS_CONSUMER_PROVENANCE_FIELDS
    assert "runtime_metadata" not in exp_prov

    assert find_private_locator_in_object(exported[0]) is None


def test_stats_consumer_request_contract_unknown_key_property_matrix():
    """Varied unknown names/nesting cannot expand the explicit request allowlist."""
    unknown_stems = ("runtime", "transport", "machine", "future", "opaque")
    separators = ("_", "-", ".", "")
    unknown_entries = {}
    for stem in unknown_stems:
        for separator in separators:
            key = f"{stem}{separator}Context"
            unknown_entries[key] = {
                "mixedCaseLeaf": f"{stem}-value",
                "nested": [{"variant": separator or "camel"}],
            }

    record = copy.deepcopy(SAMPLE_VALID_STATS)
    record["rights"]["classification"] = "PUBLIC"
    record["rights"]["public_export_allowed"] = True
    record["provenance"]["request_contract"] = {
        "season": 2024,
        "matchTypeId": 1,
        **unknown_entries,
    }
    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer(
        [record], target="ChatGPT", authorizations=auth_list
    )
    assert cap == "MATERIALIZED"
    request_contract = exported[0]["provenance"]["request_contract"]
    assert set(request_contract) <= STATS_CONSUMER_REQUEST_CONTRACT_FIELDS
    assert request_contract == {"season": 2024, "matchTypeId": 1}


def test_stats_consumer_output_guard_detects_surviving_sensitive_keys():
    """Verify independent final guard find_private_locator_in_object catches sensitive keys and credentials."""
    # Dict key with cookie
    assert find_private_locator_in_object({"cookie": "<MOCK_COOKIE>"}) == "cookie"
    assert find_private_locator_in_object({"Cookie": "<MOCK_COOKIE>"}) == "Cookie"
    assert find_private_locator_in_object({"request_contract": {"session_id": "<MOCK_SESSION>"}}) == "session_id"
    assert find_private_locator_in_object({"headers": {"Authorization": "<MOCK_AUTH>"}}) == "Authorization"
    assert find_private_locator_in_object({"nested": [{"authToken": "<MOCK_TOKEN>"}]}) == "authToken"
    assert find_private_locator_in_object({"apiKey": "<MOCK_KEY>"}) == "apiKey"
    assert find_private_locator_in_object({"password": "<MOCK_PWD>"}) == "password"
    assert find_private_locator_in_object({"client_secret": "<MOCK_SECRET>"}) == "client_secret"

    # Safe objects pass cleanly
    safe_obj = {
        "season": 2024,
        "matchTypeId": 1,
        "endpoint": "/api/player-base-list",
        "nested": {"safe_code": "001", "chinese_meta": "常规赛"},
    }
    assert find_private_locator_in_object(safe_obj) is None


def test_stats_consumer_final_guard_fails_closed_after_reconstruction(monkeypatch):
    """A post-reconstruction injection is independently rejected by the final guard."""
    record = copy.deepcopy(SAMPLE_VALID_STATS)
    record["rights"]["classification"] = "PUBLIC"
    record["rights"]["public_export_allowed"] = True

    original_projector = consumer_integration._project_stats_provenance_for_consumer

    def inject_after_reconstruction(provenance):
        projected = original_projector(provenance)
        projected["unexpected_post_projection"] = {"api_key": "<MOCK_KEY>"}
        return projected

    monkeypatch.setattr(
        consumer_integration,
        "_project_stats_provenance_for_consumer",
        inject_after_reconstruction,
    )

    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer([record], target="ChatGPT", authorizations=auth_list)
    assert exported == []
    assert cap == "NOT_MATERIALIZED(PRIVATE_LOCATOR_REMAINS)"


def test_stats_safe_provenance_preservation():
    """All approved provenance and current provider request fields survive when valid."""
    record = copy.deepcopy(SAMPLE_VALID_STATS)
    record["rights"]["classification"] = "PUBLIC"
    record["rights"]["public_export_allowed"] = True
    record["provenance"]["request_contract"] = {
        "season": 2024,
        "matchTypeId": 1,
        "countRanger": 1,
        "playerRanger": 1,
        "teamId": None,
        "acrossTeamId": None,
        "type": None,
        "startTime": None,
        "endTime": None,
        "startRound": None,
        "endRound": None,
        "startMatchOrder": None,
        "endMatchOrder": None,
        "sort": 3,
        "rank": 1,
    }
    record["provenance"]["provider_version"] = "2026.10"

    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer([record], target="ChatGPT", authorizations=auth_list)
    assert cap == "MATERIALIZED"
    assert len(exported) == 1
    rc = exported[0]["provenance"]["request_contract"]
    assert set(rc) == STATS_CONSUMER_REQUEST_CONTRACT_FIELDS
    assert rc == record["provenance"]["request_contract"]
    assert exported[0]["provenance"]["endpoint"] == "/api/player-base-list"
    assert exported[0]["provenance"]["season"] == "2024"
    assert exported[0]["provenance"]["match_type"] == "1"
    assert exported[0]["provenance"]["source_uri"] == record["provenance"]["source_uri"]
    assert exported[0]["provenance"]["raw_response_sha256"] == "a" * 64
    assert exported[0]["provenance"]["decoded_sha256"] == "b" * 64
    assert exported[0]["provenance"]["captured_at"] == "2026-10-03T10:00:00Z"
    assert exported[0]["provenance"]["provider_version"] == "2026.10"


@pytest.mark.parametrize(
    "invalid_value",
    [True, "1", 1.5, {"value": 1}, [1], (1,)],
)
def test_stats_consumer_request_contract_omits_non_integer_non_null_values(invalid_value):
    record = copy.deepcopy(SAMPLE_VALID_STATS)
    record["rights"]["classification"] = "PUBLIC"
    record["rights"]["public_export_allowed"] = True
    record["provenance"]["request_contract"] = {
        "season": 2024,
        "matchTypeId": invalid_value,
        "teamId": None,
    }
    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": record["record_id"],
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]

    exported, cap = project_stats_for_consumer([record], target="ChatGPT", authorizations=auth_list)
    assert cap == "MATERIALIZED"
    assert exported[0]["provenance"]["request_contract"] == {
        "season": 2024,
        "teamId": None,
    }



