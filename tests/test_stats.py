"""Comprehensive unit and oracle tests for CBA-KB canonical Stats domain and adapter.

Covers Acceptance Matrix A1-A13, A16, A17 from REQ-200-PLAYER-STATS-01:
- A1: Normal single-team player-season stats.
- A2: 2023/2024 sampled season contract parity.
- A3: True zero vs missing/null.
- A4: Duplicate provider row / deterministic key behavior.
- A5: Total vs per-game / rate / percentage unit semantics.
- A6: Mid-season transfer / whole-season vs team-split separation.
- A7: Existing provider external-id link -> same existing player_uid.
- A8: Same-name/birth/team without canonical link -> REVIEW_REQUIRED/UNRESOLVED, never automatic SAME.
- A9: Unknown player -> UNRESOLVED.
- A10: Provider envelope/key/schema mismatch -> fail closed.
- A11: Provider correction/supersession reproducibility.
- A12: Provenance includes source URI, request contract, raw hash, decoded hash.
- A13: Rights default public_export_allowed=False without target authorization.
- A16: Deterministic replay: identical bound source -> identical canonical output.
- A17: No private runtime paths, cookies, tokens or credentials in public output.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import pytest

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from cba_kb.adapters.cba_stats import (
    CBAStatsAdapter,
    CBAStatsEnvelopeError,
    KeyDiscoveryError,
    StatsProviderContractError,
    decrypt_cba_envelope,
    discover_client_key_from_bundle,
    discover_key_from_portal,
)
from cba_kb.common import digest
from cba_kb.consumer_integration import (
    CONSUMER_CAPABILITY_PLAYER_STATS,
    find_private_locator_in_object,
    is_private_locator,
    project_stats_for_consumer,
)
from cba_kb.consumer_manifest import (
    ConsumerManifestError,
    build_consumer_manifest,
    validate_consumer_manifest,
)
from cba_kb.player_identity import new_registry
from cba_kb.stats import (
    DEFAULT_COMPETITION,
    PROVIDER_CBA_OFFICIAL,
    SEMANTIC_GRAIN,
    StatsError,
    StatsProviderDriftError,
    StatsValidationError,
    build_record_id,
    deduplicate_and_reconcile_stats,
    resolve_stats_player_identity,
    validate_stats_record,
)

SAMPLE_AES_KEY = b"synth_key_16_byt"

RAW_2024_RECORD = {
    "season": 2024,
    "birthDate": "1995-12-31 00:00:00",
    "rankSort": None,
    "playerId": 100098118,
    "cnAlias": "萨姆纳",
    "teamId": 29127,
    "age": 29,
    "number": "4",
    "teamCnAlias": "四川丰谷酒业",
    "gameStartNum": 26,
    "playerTimes": 26.0,
    "playerTimes2": None,
    "seconds": "2156.4",
    "minutes": "35.9",
    "nationality": "美国",
    "points": "36.0",
    "fieldGoalsAttempted": "23.8",
    "fieldGoals": "10.7",
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
    "reboundsDefensive": "5.5",
    "reboundsOffensive": "1.1",
    "rebounds": "6.6",
    "assists": "6.8",
    "steals": "2.1",
    "blocked": "0.5",
    "turnovers": "4.1",
    "fouls": "2.8",
    "scheduleId": None,
    "position": "得分后卫",
    "positionNum": 2.0,
    "foulsDefensive": 67.0,
    "fieldGoalsAtRimAttempted": 280.0,
    "fieldGoalsAtRimMade": 171.0,
    "fieldGoalsMidRangeAttempted": 135.0,
    "fieldGoalsMidRangeMade": 42.0,
}

RAW_2023_RECORD = {
    "season": 2023,
    "birthDate": "1987-10-27 00:00:00",
    "rankSort": None,
    "playerId": 393433,
    "cnAlias": "易建联",
    "teamId": 29120,
    "age": 36,
    "number": "9",
    "teamCnAlias": "广东东莞大益",
    "gameStartNum": 30,
    "playerTimes": 32.0,
    "playerTimes2": None,
    "seconds": "1500.0",
    "minutes": "25.0",
    "nationality": "中国",
    "points": "12.5",
    "fieldGoalsAttempted": "8.5",
    "fieldGoals": "4.5",
    "fieldGoalsPercentage": "52.9%",
    "fieldGoalsPercentageSort": 0.5294,
    "threePointGoals": "1.0",
    "threePointAttempted": "2.5",
    "threePointPercentage": "40.0%",
    "threePointPercentageSort": 0.4000,
    "freeThrows": "2.5",
    "freeThrowsAttempted": "3.0",
    "freeThrowsPercentage": "83.3%",
    "freeThrowsPercentageSort": 0.8333,
    "reboundsDefensive": "5.0",
    "reboundsOffensive": "1.5",
    "rebounds": "6.5",
    "assists": "1.2",
    "steals": "0.8",
    "blocked": "1.1",
    "turnovers": "1.0",
    "fouls": "2.0",
    "scheduleId": None,
    "position": "中锋/大前锋",
    "positionNum": 5.0,
    "foulsDefensive": 45.0,
    "fieldGoalsAtRimAttempted": 150.0,
    "fieldGoalsAtRimMade": 90.0,
    "fieldGoalsMidRangeAttempted": 60.0,
    "fieldGoalsMidRangeMade": 30.0,
}


def encrypt_payload(data: Any, key: bytes = SAMPLE_AES_KEY) -> bytes:
    """Helper to produce standard CBA encrypted payload."""
    raw_json = json.dumps(data, ensure_ascii=False).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw_json) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    b64_str = base64.b64encode(ciphertext).decode("utf-8")
    return json.dumps(b64_str).encode("utf-8")


def test_a1_normal_player_season_stats():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov_base = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    rec = adapter.transform_provider_record(RAW_2024_RECORD, prov_base)

    assert rec["semantic_grain"] == "PLAYER_SEASON_STATS"
    assert rec["record_id"] == "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118"
    assert rec["player_name"] == "萨姆纳"
    assert rec["metrics"]["points_per_game"] == 36.0
    assert rec["metrics"]["games_played"] == 26.0
    assert rec["metrics"]["field_goals_percentage"] == "44.8%"
    assert rec["metrics"]["field_goals_percentage_rate"] == 0.4482
    assert rec["rights"]["public_export_allowed"] is False
    validate_stats_record(rec)


def test_a2_two_season_schema_parity():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov_2024 = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "1" * 64,
        "decoded_sha256": "2" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    prov_2023 = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "3" * 64,
        "decoded_sha256": "4" * 64,
        "season": "2023",
        "match_type_id": "1",
    }

    rec_2024 = adapter.transform_provider_record(RAW_2024_RECORD, prov_2024)
    rec_2023 = adapter.transform_provider_record(RAW_2023_RECORD, prov_2023)

    assert set(rec_2024["metrics"].keys()) == set(rec_2023["metrics"].keys())
    assert rec_2024["season"] == "2024"
    assert rec_2023["season"] == "2023"


def test_a3_true_zero_vs_missing_null():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "e" * 64,
        "decoded_sha256": "f" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    raw_with_zeros = dict(RAW_2024_RECORD, points="0.0", blocked="0.0", assists=None, rebounds=None)
    rec = adapter.transform_provider_record(raw_with_zeros, prov)

    # Invariant: 0.0 is explicitly preserved as a numeric zero
    assert rec["metrics"]["points_per_game"] == 0.0
    assert rec["metrics"]["blocks_per_game"] == 0.0

    # Invariant: missing/null is preserved as None, NOT converted to 0
    assert rec["metrics"]["assists_per_game"] is None
    assert rec["metrics"]["rebounds_per_game"] is None
    assert rec["metrics"]["assists_per_game"] != 0.0


def test_a4_duplicate_rows_deduplicate_or_fail_closed():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
        "captured_at": "2026-10-03T10:00:00Z",
    }
    rec1 = adapter.transform_provider_record(RAW_2024_RECORD, prov)
    rec2 = copy.deepcopy(rec1)

    # Identical duplicates deduplicate idempotently
    deduped = deduplicate_and_reconcile_stats([rec1, rec2])
    assert len(deduped) == 1

    # Conflicting duplicate without timestamp order fails closed
    rec_conflicted = copy.deepcopy(rec1)
    rec_conflicted["metrics"]["points_per_game"] = 40.0
    with pytest.raises(StatsError, match="AMBIGUOUS_CONFLICTING_STATS_DUPLICATE"):
        deduplicate_and_reconcile_stats([rec1, rec_conflicted])


def test_a5_unit_semantics_and_formatting():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    rec = adapter.transform_provider_record(RAW_2024_RECORD, prov)

    # Percentage string vs float rate
    assert rec["metrics"]["field_goals_percentage"] == "44.8%"
    assert rec["metrics"]["field_goals_percentage_rate"] == 0.4482
    assert isinstance(rec["metrics"]["field_goals_percentage_rate"], float)
    assert 0.0 <= rec["metrics"]["field_goals_percentage_rate"] <= 1.0


def test_a6_midseason_transfer_and_scope_separation():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    # Whole season record
    rec_whole = adapter.transform_provider_record(RAW_2024_RECORD, prov, scope="WHOLE_SEASON")
    # Split record for team A
    raw_team_a = dict(RAW_2024_RECORD, teamId=29127, teamCnAlias="四川丰谷酒业", playerTimes=15.0)
    rec_team_a = adapter.transform_provider_record(raw_team_a, prov, scope="TEAM_SPLIT")
    # Split record for team B
    raw_team_b = dict(RAW_2024_RECORD, teamId=29130, teamCnAlias="北京北汽", playerTimes=11.0)
    rec_team_b = adapter.transform_provider_record(raw_team_b, prov, scope="TEAM_SPLIT")

    assert rec_whole["record_id"] == "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118"
    assert rec_team_a["record_id"] == "stats:CBA_OFFICIAL:2024:1:TEAM_SPLIT:100098118:29127"
    assert rec_team_b["record_id"] == "stats:CBA_OFFICIAL:2024:1:TEAM_SPLIT:100098118:29130"

    # Distinct record IDs guarantee no duplicate whole-season truth
    records = deduplicate_and_reconcile_stats([rec_whole, rec_team_a, rec_team_b])
    assert len(records) == 3


def test_a7_trusted_external_id_link_resolves_player_uid():
    registry = new_registry(
        players=[{"schema_version": 1, "player_uid": "pid_yijianlian_001", "canonical_name": "易建联", "status": "ACTIVE", "redirect_to": None}],
        record_links=[
            {
                "schema_version": 1,
                "record_key": "cba_player_id:393433",
                "player_uid": "pid_yijianlian_001",
                "link_status": "same",
                "method": "EXTERNAL_IDENTIFIER",
                "confidence": "HIGH",
                "evidence_refs": ["official_stats_link"],
            }
        ],
    )
    status, uid, candidates, reason = resolve_stats_player_identity(
        provider_player_id="393433",
        player_name="易建联",
        registry=registry,
    )
    assert status == "RESOLVED"
    assert uid == "pid_yijianlian_001"
    assert candidates == ["pid_yijianlian_001"]


def test_a8_same_name_birth_team_without_canonical_link_is_review_required():
    registry = new_registry(
        players=[{"schema_version": 1, "player_uid": "pid_0000000000000002", "canonical_name": "萨姆纳", "status": "ACTIVE", "redirect_to": None}],
        record_links=[],
    )
    # Name matches, but NO trusted external-identifier link exists
    status, uid, candidates, reason = resolve_stats_player_identity(
        provider_player_id="100098118",
        player_name="萨姆纳",
        registry=registry,
        candidate_signals={"birth_date": "1995-12-31", "team_name": "四川丰谷酒业"},
    )
    # MUST fail closed to REVIEW_REQUIRED; player_uid MUST remain None
    assert status == "REVIEW_REQUIRED"
    assert uid is None
    assert candidates == ["pid_0000000000000002"]


def test_a9_unknown_player_is_unresolved():
    registry = new_registry(
        players=[{"schema_version": 1, "player_uid": "pid_0000000000000003", "canonical_name": "郭艾伦", "status": "ACTIVE", "redirect_to": None}],
        record_links=[],
    )
    status, uid, candidates, reason = resolve_stats_player_identity(
        provider_player_id="999999999",
        player_name="完全未知球员",
        registry=registry,
    )
    assert status == "UNRESOLVED"
    assert uid is None
    assert candidates == []


def test_a10_envelope_key_drift_fails_closed():
    correct_data = {"records": [RAW_2024_RECORD]}
    raw_encrypted = encrypt_payload(correct_data, key=SAMPLE_AES_KEY)

    # Decryption with correct key succeeds
    decoded, sha = decrypt_cba_envelope(raw_encrypted, SAMPLE_AES_KEY)
    assert "records" in decoded

    # Decryption with wrong key fails closed
    wrong_key = b"wrong_key_123456"
    with pytest.raises(CBAStatsEnvelopeError):
        decrypt_cba_envelope(raw_encrypted, wrong_key)

    # Corrupted ciphertext fails closed
    corrupted = json.dumps(base64.b64encode(b"not_16_byte_aligned_and_corrupt").decode("utf-8")).encode("utf-8")
    with pytest.raises(CBAStatsEnvelopeError):
        decrypt_cba_envelope(corrupted, SAMPLE_AES_KEY)


def test_a11_correction_supersession_reproducibility():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov_v1 = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "1" * 64,
        "decoded_sha256": "2" * 64,
        "season": "2024",
        "match_type_id": "1",
        "captured_at": "2026-10-03T10:00:00Z",
    }
    prov_v2 = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "3" * 64,
        "decoded_sha256": "4" * 64,
        "season": "2024",
        "match_type_id": "1",
        "captured_at": "2026-10-03T12:00:00Z",  # later timestamp
    }
    rec1 = adapter.transform_provider_record(RAW_2024_RECORD, prov_v1)
    corrected_raw = dict(RAW_2024_RECORD, points="36.5")  # official score correction
    rec2 = adapter.transform_provider_record(corrected_raw, prov_v2)

    # Reconciled result chooses the later observation deterministically
    reconciled = deduplicate_and_reconcile_stats([rec1, rec2])
    assert len(reconciled) == 1
    assert reconciled[0]["metrics"]["points_per_game"] == 36.5


def test_a12_provenance_binding():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "request_payload": {"season": 2024, "matchTypeId": 1},
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
        "captured_at": "2026-10-03T08:30:00Z",
    }
    rec = adapter.transform_provider_record(RAW_2024_RECORD, prov)
    p = rec["provenance"]
    assert p["source_uri"] == "https://data-server.cbaleague.com/api/player-base-list"
    assert p["raw_response_sha256"] == "a" * 64
    assert p["decoded_sha256"] == "b" * 64
    assert p["captured_at"] == "2026-10-03T08:30:00Z"


def test_a13_rights_fail_closed_without_target_authorization():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    rec = adapter.transform_provider_record(RAW_2024_RECORD, prov)

    # Default rights state: public_export_allowed is False
    assert rec["rights"]["public_export_allowed"] is False

    # Projection with no authorizations fails closed
    exported, status = project_stats_for_consumer([rec], target="ChatGPT", authorizations=None)
    assert exported == []
    assert "NO_TARGET_AUTHORIZATIONS_PROVIDED" in status

    # Projection with un-authorized target records fails closed
    auth_list = [
        {
            "target": "ChatGPT",
            "doc_id": "stats:CBA_OFFICIAL:2024:1:WHOLE_SEASON:100098118",
            "allowed_scope": CONSUMER_CAPABILITY_PLAYER_STATS,
            "authorization_basis": "PUBLIC",
            "frozen_at": "2026-10-03T10:00:00Z",
        }
    ]
    # Because rec rights has public_export_allowed=False, it must NOT be exported
    exported2, status2 = project_stats_for_consumer([rec], target="ChatGPT", authorizations=auth_list)
    assert exported2 == []
    assert "NO_AUTHORIZED_PUBLIC_STATS_EVIDENCE" in status2


def test_a16_deterministic_replay():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
        "captured_at": "2026-10-03T08:30:00Z",
    }
    rec1 = adapter.transform_provider_record(RAW_2024_RECORD, prov)
    rec2 = adapter.transform_provider_record(RAW_2024_RECORD, prov)

    # Identical bound source generates identical canonical output byte-for-byte
    assert json.dumps(rec1, sort_keys=True) == json.dumps(rec2, sort_keys=True)


def test_a17_no_private_locator_leakage():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }
    rec = adapter.transform_provider_record(RAW_2024_RECORD, prov)

    # Canonical record must not contain local absolute filesystem paths
    assert find_private_locator_in_object(rec) is None


def test_provider_contract_missing_or_renamed_identifiers():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }

    # Missing playerId
    bad_record = copy.deepcopy(RAW_2024_RECORD)
    del bad_record["playerId"]
    with pytest.raises(StatsProviderContractError, match="MISSING_IDENTIFIER:playerId"):
        adapter.transform_provider_record(bad_record, prov)

    # Renamed playerId -> player_id
    bad_record_renamed = copy.deepcopy(RAW_2024_RECORD)
    bad_record_renamed["player_id"] = bad_record_renamed.pop("playerId")
    with pytest.raises(StatsProviderContractError, match="MISSING_IDENTIFIER:playerId"):
        adapter.transform_provider_record(bad_record_renamed, prov)

    # Missing cnAlias
    bad_name = copy.deepcopy(RAW_2024_RECORD)
    del bad_name["cnAlias"]
    with pytest.raises(StatsProviderContractError, match="MISSING_IDENTIFIER:cnAlias"):
        adapter.transform_provider_record(bad_name, prov)

    # Renamed cnAlias -> name
    bad_name_renamed = copy.deepcopy(RAW_2024_RECORD)
    bad_name_renamed["name"] = bad_name_renamed.pop("cnAlias")
    with pytest.raises(StatsProviderContractError, match="MISSING_IDENTIFIER:cnAlias"):
        adapter.transform_provider_record(bad_name_renamed, prov)

    # Missing season in both record and prov
    bad_season = copy.deepcopy(RAW_2024_RECORD)
    del bad_season["season"]
    prov_no_season = copy.deepcopy(prov)
    del prov_no_season["season"]
    with pytest.raises(StatsProviderContractError, match="MISSING_IDENTIFIER:season"):
        adapter.transform_provider_record(bad_season, prov_no_season)


def test_provider_contract_missing_or_renamed_core_metrics():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }

    # Missing points
    bad_points = copy.deepcopy(RAW_2024_RECORD)
    del bad_points["points"]
    with pytest.raises(StatsProviderContractError, match="MISSING_CORE_METRIC:points"):
        adapter.transform_provider_record(bad_points, prov)

    # Renamed points -> pts
    bad_pts = copy.deepcopy(RAW_2024_RECORD)
    bad_pts["pts"] = bad_pts.pop("points")
    with pytest.raises(StatsProviderContractError, match="MISSING_CORE_METRIC:points"):
        adapter.transform_provider_record(bad_pts, prov)

    # Missing playerTimes (games played)
    bad_gp = copy.deepcopy(RAW_2024_RECORD)
    del bad_gp["playerTimes"]
    with pytest.raises(StatsProviderContractError, match="MISSING_CORE_METRIC:playerTimes"):
        adapter.transform_provider_record(bad_gp, prov)

    # Missing rebounds
    bad_reb = copy.deepcopy(RAW_2024_RECORD)
    del bad_reb["rebounds"]
    with pytest.raises(StatsProviderContractError, match="MISSING_CORE_METRIC:rebounds"):
        adapter.transform_provider_record(bad_reb, prov)

    # Missing assists
    bad_ast = copy.deepcopy(RAW_2024_RECORD)
    del bad_ast["assists"]
    with pytest.raises(StatsProviderContractError, match="MISSING_CORE_METRIC:assists"):
        adapter.transform_provider_record(bad_ast, prov)


def test_provider_contract_incompatible_types():
    adapter = CBAStatsAdapter(key=SAMPLE_AES_KEY)
    prov = {
        "url": "https://data-server.cbaleague.com/api/player-base-list",
        "endpoint": "/api/player-base-list",
        "raw_sha256": "a" * 64,
        "decoded_sha256": "b" * 64,
        "season": "2024",
        "match_type_id": "1",
    }

    # points as non-numeric string
    bad_type1 = copy.deepcopy(RAW_2024_RECORD)
    bad_type1["points"] = "N/A_NOT_NUMERIC"
    with pytest.raises(StatsProviderContractError, match="INCOMPATIBLE_TYPE:points"):
        adapter.transform_provider_record(bad_type1, prov)

    # points as dict
    bad_type2 = copy.deepcopy(RAW_2024_RECORD)
    bad_type2["points"] = {"pts": 36.0}
    with pytest.raises(StatsProviderContractError, match="INCOMPATIBLE_TYPE:points"):
        adapter.transform_provider_record(bad_type2, prov)

    # secondary metric (steals) as incompatible type
    bad_type3 = copy.deepcopy(RAW_2024_RECORD)
    bad_type3["steals"] = [2.1]
    with pytest.raises(StatsProviderContractError, match="INCOMPATIBLE_TYPE:steals"):
        adapter.transform_provider_record(bad_type3, prov)


def test_fail_closed_key_discovery():
    # Bundle without key candidate returns None
    bundle_empty = "function main() { console.log('no key'); }"
    assert discover_client_key_from_bundle(bundle_empty) is None

    # Multiple conflicting candidates returns None
    bundle_ambiguous = 'const k1 = "1234567890123456"; const k2 = "abcdefghijklmnop";'
    assert discover_client_key_from_bundle(bundle_ambiguous) is None

    # Adapter without key and unable to reach portal fails closed
    bad_adapter = CBAStatsAdapter(portal_url="https://non-existent-domain-fail-closed-999.com", key=None)
    with pytest.raises(KeyDiscoveryError):
        bad_adapter.get_encryption_key()


def test_validate_consumer_manifest_with_stats_surface():
    control_keys = ["release_status", "readme", "index", "technical_manual", "context_card", "current_version_doc"]
    control_entries = {
        k: {
            "id": f"ctrl_{k}",
            "name": f"{k}.json",
            "sha256": "1" * 64,
            "mime": "application/json",
            "authority": "control",
            "rights": "public",
        }
        for k in control_keys
    }
    fact_entries = {
        "master": {
            "id": "fact_master",
            "name": "MASTER.xlsx",
            "sha256": "2" * 64,
            "mime": "application/json",
            "authority": "canonical",
            "rights": "public",
        }
    }
    identity_entry = {
        "id": "ident_id",
        "name": "player_identity_projection.json",
        "sha256": "3" * 64,
        "mime": "application/json",
        "authority": "derived",
        "rights": "public",
        "source_registry_sha256": "5" * 64,
    }
    stats_entries = {
        "player_stats_projection": {
            "id": "stats_art_id",
            "name": "player_stats_projection.json",
            "sha256": "4" * 64,
            "mime": "application/json",
            "authority": "canonical",
            "rights": "public",
        }
    }

    manifest = build_consumer_manifest(
        release_id="REL-20261003-01",
        code_commit="c" * 40,
        surfaces=control_entries,
        facts=fact_entries,
        identity_projection=identity_entry,
        stats=stats_entries,
    )

    # Valid validation without resolver
    res = validate_consumer_manifest(manifest, expected_release_id="REL-20261003-01")
    assert res["status"] == "PASS"
    assert res["entries_validated"] == len(control_keys) + 1 + 1 + 1  # control + facts + identity + stats

    # Invalid stats authority (e.g. derived instead of canonical) fails validation
    bad_stats_manifest = copy.deepcopy(manifest)
    bad_stats_manifest["consumer_surfaces"]["stats"]["player_stats_projection"]["authority"] = "derived"
    without_hash = {k: v for k, v in bad_stats_manifest.items() if k != "manifest_sha256"}
    from cba_kb.evidence_ledger import canonical_bytes
    bad_stats_manifest["manifest_sha256"] = digest(canonical_bytes(without_hash))
    with pytest.raises(ConsumerManifestError, match="STATS_AUTHORITY_INVALID:player_stats_projection"):
        validate_consumer_manifest(bad_stats_manifest)

    # Valid validation with artifact resolver
    store = {f"ctrl_{k}": b"fake_ctrl" for k in control_keys}
    store["fact_master"] = b"fake_fact"
    store["ident_id"] = b"fake_ident"
    store["stats_art_id"] = b"fake_stats"

    for k in control_keys:
        control_entries[k]["sha256"] = digest(store[f"ctrl_{k}"])
    fact_entries["master"]["sha256"] = digest(store["fact_master"])
    identity_entry["sha256"] = digest(store["ident_id"])
    stats_entries["player_stats_projection"]["sha256"] = digest(store["stats_art_id"])

    manifest_with_resolver = build_consumer_manifest(
        release_id="REL-20261003-01",
        code_commit="c" * 40,
        surfaces=control_entries,
        facts=fact_entries,
        identity_projection=identity_entry,
        stats=stats_entries,
    )
    res_resolver = validate_consumer_manifest(
        manifest_with_resolver,
        expected_release_id="REL-20261003-01",
        artifact_resolver=lambda art_id: store[art_id],
    )
    assert res_resolver["status"] == "PASS"

    # Mismatch sha in stats artifact resolver raises ConsumerManifestError
    bad_store = dict(store)
    bad_store["stats_art_id"] = b"corrupted_stats_payload"
    with pytest.raises(ConsumerManifestError, match="REQUIRED_ARTIFACT_HASH_MISMATCH:player_stats_projection"):
        validate_consumer_manifest(
            manifest_with_resolver,
            expected_release_id="REL-20261003-01",
            artifact_resolver=lambda art_id: bad_store[art_id],
        )

    # Unreadable stats artifact raises ConsumerManifestError
    def unreadable_resolver(art_id: str):
        if art_id == "stats_art_id":
            raise IOError("Disk read error")
        return store[art_id]

    with pytest.raises(ConsumerManifestError, match="UNREADABLE_REQUIRED_ARTIFACT:player_stats_projection"):
        validate_consumer_manifest(
            manifest_with_resolver,
            expected_release_id="REL-20261003-01",
            artifact_resolver=unreadable_resolver,
        )
