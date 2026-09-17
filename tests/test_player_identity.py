import copy

import pytest

from cba_kb.player_identity import (
    IdentityStore,
    PlayerIdentityError,
    add_player,
    discover_candidates,
    identity_summary,
    merge_players,
    new_registry,
    serialize_registry,
    set_record_link,
    split_record_link,
    validate_registry,
)


UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"
UID_C = "pid_0000000000000003"


def player(uid, name, *, status="ACTIVE", redirect_to=None):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "canonical_name": name,
        "status": status,
        "redirect_to": redirect_to,
    }


def alias(uid, value, *, alias_type="NAME_VARIANT", evidence=("synthetic-alias",)):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "alias": value,
        "alias_type": alias_type,
        "evidence_refs": list(evidence),
    }


def link(
    record_key,
    uid,
    status="same",
    *,
    method="MANUAL_REVIEW",
    confidence="HIGH",
    evidence=("synthetic-link",),
):
    return {
        "schema_version": 1,
        "record_key": record_key,
        "player_uid": uid,
        "link_status": status,
        "method": method,
        "confidence": confidence,
        "evidence_refs": list(evidence),
    }


def test_registry_is_deterministic_and_serialization_is_canonical():
    players = [player(UID_B, "Synthetic B"), player(UID_A, "Synthetic A")]
    aliases = [alias(UID_A, "Synthetic A Alias")]
    links = [
        link("record-b", UID_B),
        link("record-a", UID_A),
    ]
    first = new_registry(players, aliases=aliases, record_links=links)
    second = new_registry(
        list(reversed(players)),
        aliases=list(reversed(aliases)),
        record_links=list(reversed(links)),
    )
    assert first == second
    assert serialize_registry(first) == serialize_registry(second)
    assert [item["player_uid"] for item in first["players"]] == [UID_A, UID_B]
    assert [item["record_key"] for item in first["record_links"]] == [
        "record-a",
        "record-b",
    ]
    assert len(first["registry_sha256"]) == 64
    validate_registry(first)


def test_exact_name_and_alias_lookup_only_returns_candidates():
    registry = new_registry(
        [player(UID_A, "Synthetic Player")],
        aliases=[alias(UID_A, "Synthetic Player Alias")],
    )
    result = discover_candidates(registry, "Synthetic Player")
    assert result["lookup"] == "EXACT_NAME_OR_ALIAS_ONLY"
    assert result["status"] == "CANDIDATE_ONLY"
    assert result["candidate_count"] == 1
    assert result["identity_assertion"] == "NONE"
    assert result["automatic_link_allowed"] is False
    assert result["candidates"][0]["player_uid"] == UID_A
    assert [item["match_type"] for item in result["candidates"][0]["matches"]] == [
        "CANONICAL_NAME",
    ]
    alias_result = discover_candidates(registry, "Synthetic Player Alias")
    assert alias_result["status"] == "CANDIDATE_ONLY"
    assert [item["match_type"] for item in alias_result["candidates"][0]["matches"]] == [
        "ALIAS",
    ]
    assert discover_candidates(registry, "SYNTHETIC PLAYER")["status"] == "NOT_FOUND"


def test_same_name_cross_season_fixture_requires_review_not_merge():
    registry = new_registry(
        [
            player(UID_A, "刘晓宇"),
            player(UID_B, "刘晓宇"),
        ],
        record_links=[
            link("2017-2018|beijing_shougang|刘晓宇", UID_A),
            link("2024-2025|beijing_konggu|刘晓宇", UID_B),
        ],
    )
    before = serialize_registry(registry)
    result = discover_candidates(registry, "刘晓宇")
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["candidate_count"] == 2
    assert result["identity_assertion"] == "NONE"
    assert serialize_registry(registry) == before
    assert {
        item["player_uid"] for item in registry["record_links"]
    } == {UID_A, UID_B}
    assert len(registry["record_links"]) == 2


def test_ambiguous_alias_is_review_required_and_not_merged():
    registry = new_registry(
        [player(UID_A, "Synthetic A"), player(UID_B, "Synthetic B")],
        aliases=[
            alias(UID_A, "Shared Alias"),
            alias(UID_B, "Shared Alias"),
        ],
    )
    result = discover_candidates(registry, "Shared Alias")
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["candidate_count"] == 2
    assert result["automatic_link_allowed"] is False


def test_one_same_link_per_record_key_and_duplicate_keys_fail_closed():
    with pytest.raises(
        PlayerIdentityError,
        match="RECORD_KEY_MULTIPLE_SAME_LINKS",
    ):
        new_registry(
            [player(UID_A, "Synthetic A"), player(UID_B, "Synthetic B")],
            record_links=[
                link("record-1", UID_A),
                link("record-1", UID_B),
            ],
        )
    with pytest.raises(PlayerIdentityError, match="PLAYER_RECORD_LINK_DUPLICATE"):
        new_registry(
            [player(UID_A, "Synthetic A")],
            record_links=[
                link("record-1", UID_A),
                link("record-1", UID_A),
            ],
        )
    with pytest.raises(PlayerIdentityError, match="PLAYER_ALIAS_DUPLICATE"):
        new_registry(
            [player(UID_A, "Synthetic A")],
            aliases=[alias(UID_A, "Alias"), alias(UID_A, "Alias")],
        )


def test_player_uid_is_opaque_and_cannot_equal_name_or_record_key():
    with pytest.raises(PlayerIdentityError, match="OPAQUE_FORMAT"):
        new_registry([player("short", "Synthetic A")])
    with pytest.raises(PlayerIdentityError, match="DERIVED_FROM_NAME"):
        new_registry([player("abcdefghijklmnop", "abcdefghijklmnop")])
    with pytest.raises(PlayerIdentityError, match="DERIVED_FROM_RECORD_KEY"):
        new_registry(
            [player("record-00000000000001", "Synthetic A")],
            record_links=[link("record-00000000000001", "record-00000000000001")],
        )


def test_unknown_registration_fields_are_not_inferred_into_identity():
    registry = new_registry(
        [player(UID_A, "刘晓宇")],
        record_links=[link("2017-2018|beijing_shougang|刘晓宇", UID_A)],
    )
    payload = serialize_registry(registry).decode()
    candidate = discover_candidates(registry, "刘晓宇")
    assert "registration_stage" not in payload
    assert "registration_method" not in payload
    assert "registration_stage" not in candidate
    assert "registration_method" not in candidate


def test_merge_and_split_change_relations_only_and_leave_inputs_unchanged():
    original = new_registry(
        [player(UID_A, "Source Player"), player(UID_B, "Target Player")],
        aliases=[alias(UID_A, "Source Alias")],
        record_links=[link("record-1", UID_A)],
    )
    before = copy.deepcopy(original)
    merged = merge_players(
        original,
        source_player_uid=UID_A,
        target_player_uid=UID_B,
        evidence_refs=["synthetic-merge"],
    )
    assert original == before
    assert merged["players"][0]["status"] == "REDIRECTED"
    assert merged["players"][0]["redirect_to"] == UID_B
    assert {
        item["player_uid"] for item in merged["record_links"]
    } == {UID_B}
    assert discover_candidates(merged, "Source Player")["candidates"][0][
        "player_uid"
    ] == UID_B

    split = split_record_link(
        merged,
        source_player_uid=UID_B,
        record_key="record-1",
        new_player=player(UID_C, "Split Player"),
        link=link("record-1", UID_C, evidence=("synthetic-split",)),
        evidence_refs=["synthetic-split"],
    )
    assert merged["record_links"][0]["player_uid"] == UID_B
    assert {
        (item["record_key"], item["player_uid"])
        for item in split["record_links"]
    } == {("record-1", UID_C)}
    assert "canonical_registration_facts" not in serialize_registry(split).decode()


def test_undecided_links_are_visible_and_summary_is_deterministic():
    registry = new_registry(
        [
            player(UID_A, "Synthetic A"),
            player(UID_B, "Synthetic B"),
            player(UID_C, "Synthetic C"),
        ],
        record_links=[
            link("record-1", UID_A, "same"),
            link("record-1", UID_B, "undecided", evidence=()),
            link("record-1", UID_C, "not_same"),
        ],
    )
    summary = identity_summary(registry)
    assert summary["same_links"] == 1
    assert summary["not_same_links"] == 1
    assert summary["undecided_links"] == 1
    assert summary["record_keys"] == 1
    assert discover_candidates(registry, "missing")["status"] == "NOT_FOUND"


def test_store_is_compare_and_swap_and_requires_private_instance(tmp_path):
    class Instance:
        root = tmp_path / "instance"
        data_root = root / "data"

    store = IdentityStore(Instance())
    assert store.read()["players"] == []
    registry = new_registry([player(UID_A, "Synthetic A")])
    written = store.write(registry)
    assert written["registry_sha256"] == registry["registry_sha256"]
    assert store.read() == registry
    with pytest.raises(PlayerIdentityError, match="CURRENT_SHA256_REQUIRED"):
        store.write(registry)
    with pytest.raises(PlayerIdentityError, match="HEAD_MISMATCH"):
        store.write(registry, expected_current_sha256="0" * 64)
    changed = add_player(
        registry,
        player_uid=UID_B,
        canonical_name="Synthetic B",
    )
    store.write(
        changed,
        expected_current_sha256=registry["registry_sha256"],
    )
    assert store.read() == changed


def test_set_record_link_can_preserve_undecided_candidates():
    registry = new_registry([player(UID_A, "Synthetic A")])
    registry = set_record_link(
        registry,
        record_key="record-1",
        player_uid=UID_A,
        link_status="undecided",
        method="REVIEW_PENDING",
        confidence="UNKNOWN",
        evidence_refs=[],
    )
    assert registry["record_links"][0]["link_status"] == "undecided"
    assert registry["record_links"][0]["evidence_refs"] == []
