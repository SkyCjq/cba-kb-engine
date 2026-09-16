import copy

import pytest

from cba_kb.master import HEADERS
from cba_kb.player_profile import (
    PlayerProfileError,
    build_profile,
    discover_exact_name,
    normalize_record_key_selector,
    validate_profile,
)


MASTER_SHA = "a" * 64
GENERATOR_SHA = "b" * 64


def row(record_key, *, season="2026-2027", club="club-a", player="SYNTHETIC PLAYER"):
    value = {key: None for key in HEADERS}
    value.update({
        "record_key": record_key,
        "season": season,
        "club_id": club,
        "club_official": "Synthetic Club",
        "player": player,
        "source_file_id": "synthetic-source",
        "source_url": "https://example.test/source",
        "verification_level": "machine_validated",
    })
    return value


def profile(rows, selectors):
    return build_profile(
        rows,
        record_keys=selectors,
        release_id="v1.7.0-synthetic",
        as_of="2026-09-15T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
    )


def test_profile_requires_explicit_selector_and_preserves_null_fields():
    rows = [row("r2"), row("r1", season="2025-2026")]
    with pytest.raises(PlayerProfileError, match="EXPLICIT_RECORD_KEY_SELECTOR"):
        normalize_record_key_selector([])
    value = profile(list(reversed(rows)), ["r2", "r1"])
    assert [item["record_key"] for item in value["rows"]] == ["r1", "r2"]
    assert value["rows"][0]["contract_term_official"] is None
    assert value["rows"][0]["source_file_id"] == "synthetic-source"
    assert value["rows"][0]["source_url"] == "https://example.test/source"
    assert value["rows"][0]["verification_level"] == "machine_validated"
    assert "player_uid" not in value
    validate_profile(value)


def test_profile_is_deterministic_and_reports_missing_selector_keys():
    rows = [row("r2", season="2025-2026"), row("r1")]
    first = profile(rows, ["r1", "r2"])
    second = profile(copy.deepcopy(list(reversed(rows))), ["r2", "r1"])
    assert first == second
    missing = profile(rows, ["r1", "missing"])
    assert missing["status"] == "REVIEW_REQUIRED"
    assert missing["coverage"]["missing_record_keys"] == ["missing"]


def test_exact_name_is_candidate_discovery_only_and_ambiguity_fails_closed():
    rows = [
        row("r1", club="club-a"),
        row("r2", club="club-b"),
    ]
    candidates = discover_exact_name(rows, "SYNTHETIC PLAYER")
    assert candidates["lookup"] == "EXACT_NAME_ONLY"
    assert candidates["status"] == "REVIEW_REQUIRED"
    assert candidates["candidate_record_keys"] == ["r1", "r2"]
    assert candidates["canonical_selector_allowed"] is False
    assert candidates["identity_assertion"] == "NONE"


def test_profile_rejects_stable_uid_extension():
    value = profile([row("r1")], ["r1"])
    value["player_uid"] = "forbidden"
    with pytest.raises(PlayerProfileError, match="PLAYER_UID"):
        validate_profile(value)
