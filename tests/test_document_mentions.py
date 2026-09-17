import copy

import pytest

from cba_kb.document_mentions import (
    DocumentMentionError,
    build_mention_artifact,
    discover_document_mentions,
    mention_summary,
    serialize_mention_artifact,
    validate_mention,
    validate_mention_artifact,
)
from cba_kb.player_identity import new_registry


UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"
UID_C = "pid_0000000000000003"
UID_D = "pid_0000000000000004"
UID_E = "pid_0000000000000005"

DOC_PUBLIC = "doc_000000000000000000000001"
DOC_COPYRIGHT = "doc_000000000000000000000002"
DOC_PRIVATE = "doc_000000000000000000000003"
DOC_UNKNOWN = "doc_000000000000000000000004"


def player(uid, name):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "canonical_name": name,
        "status": "ACTIVE",
        "redirect_to": None,
    }


def alias(uid, value):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "alias": value,
        "alias_type": "NAME_VARIANT",
        "evidence_refs": ["synthetic-alias-evidence"],
    }


def mention(
    doc_id,
    uid,
    *,
    status="undecided",
    role="mentioned",
    method="exact_name",
    confidence="UNKNOWN",
    evidence="synthetic-evidence",
):
    return {
        "schema_version": 1,
        "doc_id": doc_id,
        "player_uid": uid,
        "mention_status": status,
        "mention_role": role,
        "mention_method": method,
        "mention_confidence": confidence,
        "evidence_ref": evidence,
    }


def document(doc_id, rights, public=False, authorized_targets=None):
    return {
        "doc_id": doc_id,
        "rights": {
            "classification": rights,
            "public_export_allowed": public,
            "evidence": [],
        },
        "authorized_targets": authorized_targets or [],
    }


def authorization(doc_id, target):
    return {
        "doc_id": doc_id,
        "target": target,
        "allowed_scope": "private_acceptance",
        "authorization_basis": "synthetic-authorization",
        "frozen_at": "2026-09-17T00:00:00Z",
    }


def test_mention_schema_rejects_automatic_same_and_duplicate_pairs():
    with pytest.raises(
        DocumentMentionError,
        match="MENTION_AUTOMATIC_SAME_FORBIDDEN",
    ):
        validate_mention(mention(
            DOC_PUBLIC,
            UID_A,
            status="same",
            method="exact_name",
        ))
    with pytest.raises(
        DocumentMentionError,
        match="MENTION_AUTOMATIC_SAME_FORBIDDEN",
    ):
        validate_mention(mention(
            DOC_PUBLIC,
            UID_A,
            status="same",
            method="alias",
        ))
    assert validate_mention(mention(
        DOC_PUBLIC,
        UID_A,
        status="same",
        method="manual",
    ))["mention_status"] == "same"
    assert validate_mention(mention(
        DOC_PUBLIC,
        UID_A,
        status="same",
        method="external_id",
    ))["mention_method"] == "external_id"
    with pytest.raises(
        DocumentMentionError,
        match="DOCUMENT_PLAYER_MENTION_DUPLICATE",
    ):
        build_mention_artifact(
            [document(DOC_PUBLIC, "public", True)],
            [mention(DOC_PUBLIC, UID_A), mention(DOC_PUBLIC, UID_A)],
        )


def test_one_document_can_link_multiple_players():
    artifact = build_mention_artifact(
        [document(DOC_PUBLIC, "public", True)],
        [
            mention(DOC_PUBLIC, UID_A, role="subject"),
            mention(DOC_PUBLIC, UID_B, role="mentioned"),
        ],
    )
    assert len(artifact["mentions"]) == 2
    assert {
        item["player_uid"] for item in artifact["mentions"]
    } == {UID_A, UID_B}
    assert artifact["coverage"]["undecided"] == 2
    assert artifact["coverage"]["silent_drop_count"] == 0
    validate_mention_artifact(artifact)


def test_exact_name_and_alias_discovery_is_candidate_only_and_ambiguous():
    registry = new_registry(
        [
            player(UID_A, "Synthetic Player"),
            player(UID_B, "Synthetic Player"),
            player(UID_C, "Synthetic Other"),
        ],
        aliases=[alias(UID_C, "Synthetic Alias")],
    )
    result = discover_document_mentions(
        "Synthetic Player appears with Synthetic Alias.",
        registry,
        doc_id=DOC_PUBLIC,
        roles={UID_A: "subject"},
    )
    assert {item["player_uid"] for item in result} == {
        UID_A,
        UID_B,
        UID_C,
    }
    assert all(item["mention_status"] == "undecided" for item in result)
    assert all(item["mention_method"] != "manual" for item in result)
    by_uid = {item["player_uid"]: item for item in result}
    assert by_uid[UID_A]["mention_role"] == "subject"
    assert by_uid[UID_B]["mention_role"] == "mentioned"
    assert by_uid[UID_C]["mention_method"] == "alias"


def test_ambiguous_alias_returns_multiple_undecided_candidates():
    registry = new_registry(
        [player(UID_A, "Synthetic A"), player(UID_B, "Synthetic B")],
        aliases=[
            alias(UID_A, "Shared Alias"),
            alias(UID_B, "Shared Alias"),
        ],
    )
    result = discover_document_mentions(
        "Shared Alias",
        registry,
        doc_id=DOC_PUBLIC,
    )
    assert {item["player_uid"] for item in result} == {UID_A, UID_B}
    assert all(item["mention_status"] == "undecided" for item in result)
    assert all(item["mention_method"] == "alias" for item in result)


def test_coverage_exposes_confirmed_undecided_unauthorized_and_blocked():
    documents = [
        document(DOC_PUBLIC, "public", True),
        document(DOC_COPYRIGHT, "copyrighted", False),
        document(DOC_PRIVATE, "private", False),
        document(DOC_UNKNOWN, "unknown", False),
    ]
    mentions = [
        mention(DOC_PUBLIC, UID_A, status="same", method="manual"),
        mention(DOC_COPYRIGHT, UID_B, status="same", method="manual"),
        mention(DOC_PRIVATE, UID_C, status="undecided"),
        mention(DOC_UNKNOWN, UID_D, status="same", method="manual"),
    ]
    artifact = build_mention_artifact(
        documents,
        mentions,
        authorizations=[
            authorization(DOC_COPYRIGHT, "ChatGPT"),
            authorization(DOC_PRIVATE, "WorkBuddy"),
            authorization(DOC_UNKNOWN, "ChatGPT"),
        ],
    )
    chatgpt = artifact["coverage"]["by_target"]["ChatGPT"]
    workbuddy = artifact["coverage"]["by_target"]["WorkBuddy"]
    assert [DOC_PUBLIC, DOC_COPYRIGHT, DOC_PRIVATE, DOC_UNKNOWN] == (
        chatgpt["document_level_reachable"]
    )
    assert [DOC_PUBLIC, DOC_COPYRIGHT] == sorted(
        pair[0] for pair in chatgpt["confirmed_player_centric"]
    )
    assert chatgpt["undecided_document_level"] == [[DOC_PRIVATE, UID_C]]
    assert [DOC_PRIVATE, UID_C] in chatgpt["unauthorized"]
    assert [DOC_UNKNOWN, UID_D] in chatgpt["blocked"]
    assert [DOC_COPYRIGHT, UID_B] in workbuddy["unauthorized"]
    assert [DOC_PRIVATE, UID_C] in workbuddy["undecided_document_level"]
    assert artifact["coverage"]["confirmed"] == 3
    assert artifact["coverage"]["undecided"] == 1
    assert artifact["coverage"]["silent_drop_count"] == 0
    assert artifact["documents"][1]["public_export_allowed"] is False
    assert artifact["documents"][1]["canonical_projection"] == "METADATA_ONLY"
    assert artifact["documents"][2]["canonical_projection"] == "PRIVATE"
    assert artifact["documents"][3]["canonical_projection"] == "BLOCKED"


def test_public_export_never_upgrades_private_or_copyrighted_rights():
    with pytest.raises(
        DocumentMentionError,
        match="PUBLIC_EXPORT_RIGHTS_CONFLICT",
    ):
        build_mention_artifact(
            [document(DOC_PRIVATE, "private", True)],
            [],
        )
    artifact = build_mention_artifact(
        [document(DOC_PRIVATE, "private", False)],
        [],
        authorizations=[authorization(DOC_PRIVATE, "ChatGPT")],
    )
    assert artifact["documents"][0]["public_export_allowed"] is False
    assert artifact["documents"][0]["canonical_projection"] == "PRIVATE"


def test_artifact_ordering_hash_and_serialization_are_deterministic():
    documents = [
        document(DOC_PUBLIC, "public", True),
        document(DOC_PRIVATE, "private", False),
    ]
    mentions = [
        mention(DOC_PRIVATE, UID_C),
        mention(DOC_PUBLIC, UID_B),
        mention(DOC_PUBLIC, UID_A),
    ]
    first = build_mention_artifact(documents, mentions)
    second = build_mention_artifact(
        list(reversed(copy.deepcopy(documents))),
        list(reversed(copy.deepcopy(mentions))),
    )
    assert first == second
    assert serialize_mention_artifact(first) == serialize_mention_artifact(second)
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert mention_summary(first)["silent_drop_count"] == 0


def test_artifact_validation_recomputes_coverage_and_hash():
    artifact = build_mention_artifact(
        [document(DOC_PUBLIC, "public", True)],
        [mention(DOC_PUBLIC, UID_A)],
    )
    tampered = copy.deepcopy(artifact)
    tampered["coverage"]["undecided"] = 0
    with pytest.raises(DocumentMentionError, match="MENTION_COVERAGE_MISMATCH"):
        validate_mention_artifact(tampered)
    tampered = copy.deepcopy(artifact)
    tampered["mentions"][0]["mention_status"] = "same"
    with pytest.raises(
        DocumentMentionError,
        match="MENTION_AUTOMATIC_SAME_FORBIDDEN",
    ):
        validate_mention_artifact(tampered)
