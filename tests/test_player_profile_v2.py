import copy
import json

import pytest

from cba_kb.common import digest
from cba_kb.document_mentions import build_mention_artifact
from cba_kb.evidence_ledger import canonical_bytes
from cba_kb.master import HEADERS
from cba_kb.player_identity import new_registry
from cba_kb.player_profile import (
    PlayerProfileError,
    build_player_record_view,
    build_profile_v2,
    validate_profile_v2,
)


MASTER_SHA = "a" * 64
GENERATOR_SHA = "b" * 64
UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"
DOC_1 = "doc_000000000000000000000001"
DOC_2 = "doc_000000000000000000000002"
DOC_3 = "doc_000000000000000000000003"


def row(record_key, *, season="2026-2027", club="club-a", name="Synthetic"):
    value = {key: None for key in HEADERS}
    value.update({
        "record_key": record_key,
        "season": season,
        "club_id": club,
        "club_official": "Synthetic Club",
        "player": name,
        "source_file_id": "synthetic-source",
        "source_url": "https://example.test/source",
        "verification_level": "machine_validated",
    })
    return value


def player(uid, name):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "canonical_name": name,
        "status": "ACTIVE",
        "redirect_to": None,
    }


def record_link(record_key, uid, status):
    return {
        "schema_version": 1,
        "record_key": record_key,
        "player_uid": uid,
        "link_status": status,
        "method": (
            "MANUAL_REVIEW"
            if status in {"same", "not_same"}
            else "REVIEW_PENDING"
        ),
        "confidence": "HIGH" if status == "same" else "UNKNOWN",
        "evidence_refs": (
            ["synthetic-identity-evidence"]
            if status in {"same", "not_same"} else []
        ),
    }


def mention(doc_id, uid, status):
    return {
        "schema_version": 1,
        "doc_id": doc_id,
        "player_uid": uid,
        "mention_status": status,
        "mention_role": "mentioned",
        "mention_method": (
            "manual" if status == "same" else "exact_name"
        ),
        "mention_confidence": "HIGH" if status == "same" else "UNKNOWN",
        "evidence_ref": f"synthetic-{status}-evidence",
    }


def document(doc_id):
    return {
        "doc_id": doc_id,
        "rights": {
            "classification": "public",
            "public_export_allowed": False,
            "evidence": [],
        },
    }


def mention_artifact():
    return build_mention_artifact(
        [document(DOC_1), document(DOC_2), document(DOC_3)],
        [
            mention(DOC_1, UID_A, "same"),
            mention(DOC_2, UID_A, "undecided"),
            mention(DOC_3, UID_A, "not_same"),
        ],
    )


def registry(*, include_same=True, include_undecided=True):
    links = []
    if include_same:
        links.append(record_link("r1", UID_A, "same"))
    if include_undecided:
        links.append(record_link("r2", UID_A, "undecided"))
    links.extend([
        record_link("r3", UID_A, "not_same"),
        record_link("r4", UID_B, "same"),
    ])
    return new_registry(
        [player(UID_A, "Synthetic A"), player(UID_B, "Synthetic B")],
        record_links=links,
    )


def event_spec():
    return {
        "season": "2026-2027",
        "team": "Synthetic Club",
        "events": [
            {"event_id": "event-covered", "resolved": True},
            {"event_id": "event-gap", "resolved": False},
        ],
        "sources": [{"source_id": "event-source", "status": "PASS"}],
        "as_of": "2026-09-17T00:00:00Z",
    }


def profile(rows=None, registry_value=None, artifact=None):
    return build_profile_v2(
        rows or [
            row("r1", season="2025-2026"),
            row("r2", season="2026-2027"),
            row("r3", season="2026-2027"),
            row("r4", season="2026-2027", name="Synthetic B"),
            row("r5", season="2026-2027", name="Synthetic Unlinked"),
        ],
        player_uid=UID_A,
        identity_registry=registry_value or registry(),
        mention_artifact=artifact or mention_artifact(),
        release_id="v1.8.0-synthetic",
        as_of="2026-09-17T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
        event_spec=event_spec(),
    )


def test_player_record_view_preserves_all_master_rows_and_link_states():
    value = build_player_record_view(
        [
            row("r4"),
            row("r1"),
            row("r2"),
            row("r3"),
            row("r5", name="Synthetic Unlinked"),
        ],
        identity_registry=registry(),
    )
    assert value["view_name"] == "PLAYER_RECORD_VIEW"
    assert value["view_name"] != "MASTER_WITH_PLAYER_UID"
    by_key = {item["record_key"]: item for item in value["rows"]}
    assert set(by_key) == {"r1", "r2", "r3", "r4", "r5"}
    assert by_key["r1"]["confirmed_player_uid"] == UID_A
    assert by_key["r1"]["identity_link_status"] == "same"
    assert by_key["r2"]["confirmed_player_uid"] is None
    assert by_key["r2"]["identity_link_status"] == "undecided"
    assert by_key["r3"]["identity_link_status"] == "not_same"
    assert by_key["r4"]["identity_link_status"] == "same"
    assert by_key["r5"]["identity_link_status"] == "UNLINKED"
    assert by_key["r5"]["confirmed_player_uid"] is None
    assert all(key in by_key["r1"] for key in HEADERS)


def test_profile_v2_uses_player_uid_and_exposes_unresolved_states():
    value = profile()
    assert value["profile_version"] == "v2.0"
    assert value["identity_selector"] == "player_uid"
    assert value["player_uid"] == UID_A
    assert value["record_keys"] == ["r1"]
    assert [item["record_key"] for item in value["registration_history"]] == [
        "r1"
    ]
    assert value["unresolved_identity_links"][0]["record_key"] == "r2"
    assert value["identity_coverage"]["confirmed_record_keys"] == ["r1"]
    assert value["identity_coverage"]["undecided_record_keys"] == ["r2"]
    assert value["identity_coverage"]["not_same_record_keys"] == ["r3"]
    assert value["identity_coverage"]["unlinked_record_keys"] == ["r5"]
    assert value["identity_coverage"]["full_history_coverage_complete"] is False
    assert [item["doc_id"] for item in value["confirmed_documents"]] == [
        DOC_1
    ]
    assert [item["doc_id"] for item in value["unresolved_mentions"]] == [
        DOC_2
    ]
    assert [item["doc_id"] for item in value["not_same_mentions"]] == [
        DOC_3
    ]
    assert value["event_coverage"]["selected"] is True
    assert value["unresolved_gaps"] == ["event-gap"]
    assert value["status"] == "REVIEW_REQUIRED"
    validate_profile_v2(value)


def test_same_name_does_not_extend_profile_history():
    rows = [
        row("r1", name="Synthetic Same Name"),
        row("r2", name="Synthetic Same Name"),
    ]
    registry_value = new_registry(
        [
            player(UID_A, "Synthetic Same Name"),
            player(UID_B, "Synthetic Same Name"),
        ],
        record_links=[
            record_link("r1", UID_A, "same"),
            record_link("r2", UID_B, "same"),
        ],
    )
    value = build_profile_v2(
        rows,
        player_uid=UID_A,
        identity_registry=registry_value,
        mention_artifact=build_mention_artifact([], []),
        release_id="v1.8.0-synthetic",
        as_of="2026-09-17T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
    )
    assert value["record_keys"] == ["r1"]
    assert "r2" not in value["registration_history"][0]["record_key"]


def test_registration_history_uses_master_order_for_multiple_same_links():
    rows = [
        row("r2", season="2026-2027", club="club-b"),
        row("r1", season="2025-2026", club="club-a"),
    ]
    registry_value = new_registry(
        [player(UID_A, "Synthetic Player")],
        record_links=[
            record_link("r2", UID_A, "same"),
            record_link("r1", UID_A, "same"),
        ],
    )
    value = build_profile_v2(
        rows,
        player_uid=UID_A,
        identity_registry=registry_value,
        mention_artifact=build_mention_artifact([], []),
        release_id="v1.8.0-synthetic",
        as_of="2026-09-17T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
    )
    assert value["record_keys"] == ["r1", "r2"]
    assert [
        item["record_key"] for item in value["registration_history"]
    ] == ["r1", "r2"]


def test_profile_v2_is_deterministic_and_does_not_mutate_inputs():
    rows = [
        row("r1", season="2025-2026"),
        row("r2"),
        row("r3"),
        row("r4", name="Synthetic B"),
        row("r5", name="Synthetic Unlinked"),
    ]
    registry_value = registry()
    artifact = mention_artifact()
    before = (
        copy.deepcopy(rows),
        copy.deepcopy(registry_value),
        copy.deepcopy(artifact),
    )
    first = build_profile_v2(
        rows,
        player_uid=UID_A,
        identity_registry=registry_value,
        mention_artifact=artifact,
        release_id="v1.8.0-synthetic",
        as_of="2026-09-17T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
        event_spec=event_spec(),
    )
    second = build_profile_v2(
        list(reversed(rows)),
        player_uid=UID_A,
        identity_registry=registry_value,
        mention_artifact=artifact,
        release_id="v1.8.0-synthetic",
        as_of="2026-09-17T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
        event_spec=event_spec(),
    )
    assert first == second
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["profile_sha256"] == digest(canonical_bytes({
        key: value for key, value in first.items()
        if key != "profile_sha256"
    }))
    assert (rows, registry_value, artifact) == before


def test_profile_v2_fails_closed_for_invalid_inputs():
    with pytest.raises(PlayerProfileError, match="PLAYER_UID"):
        build_profile_v2(
            [row("r1")],
            player_uid="bad",
            identity_registry=registry(),
            mention_artifact=mention_artifact(),
            release_id="v1.8.0-synthetic",
            as_of="2026-09-17T00:00:00Z",
            source_master_sha256=MASTER_SHA,
            generator_sha=GENERATOR_SHA,
        )
    malformed = registry()
    malformed["registry_sha256"] = "0" * 64
    with pytest.raises(PlayerProfileError, match="HASH_MISMATCH"):
        build_profile_v2(
            [row("r1")],
            player_uid=UID_A,
            identity_registry=malformed,
            mention_artifact=mention_artifact(),
            release_id="v1.8.0-synthetic",
            as_of="2026-09-17T00:00:00Z",
            source_master_sha256=MASTER_SHA,
            generator_sha=GENERATOR_SHA,
        )
    malformed_artifact = mention_artifact()
    malformed_artifact["artifact_sha256"] = "0" * 64
    with pytest.raises(PlayerProfileError, match="HASH_MISMATCH"):
        build_profile_v2(
            [row("r1")],
            player_uid=UID_A,
            identity_registry=registry(),
            mention_artifact=malformed_artifact,
            release_id="v1.8.0-synthetic",
            as_of="2026-09-17T00:00:00Z",
            source_master_sha256=MASTER_SHA,
            generator_sha=GENERATOR_SHA,
        )
