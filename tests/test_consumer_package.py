import pytest

from cba_kb.consumer_package import (
    CONSUMER_TARGETS,
    ConsumerPackageError,
    build_consumer_payload,
    build_target_package,
    build_target_packages,
    validate_package,
)
from cba_kb.master import HEADERS
from cba_kb.player_profile import build_profile


MASTER_SHA = "a" * 64
GENERATOR_SHA = "b" * 64


def profile():
    row = {key: None for key in HEADERS}
    row.update({
        "record_key": "synthetic-r1",
        "season": "2026-2027",
        "club_id": "synthetic-club",
        "player": "SYNTHETIC PLAYER",
        "source_file_id": "synthetic-source",
        "source_url": "https://example.test/source",
        "verification_level": "machine_validated",
    })
    return build_profile(
        [row],
        record_keys=["synthetic-r1"],
        release_id="v1.7.0-synthetic",
        as_of="2026-09-15T00:00:00Z",
        source_master_sha256=MASTER_SHA,
        generator_sha=GENERATOR_SHA,
    )


def document(doc_id, rights="public"):
    return {
        "doc_id": doc_id,
        "rights": rights,
        "title": f"title-{doc_id}",
        "body": f"body-{doc_id}",
        "body_status": "AVAILABLE",
        "source_ref": f"source-{doc_id}",
        "public_export_allowed": rights == "public",
    }


def payload():
    return build_consumer_payload(
        release_scope={
            "release_id": "v1.7.0-synthetic",
            "as_of": "2026-09-15T00:00:00Z",
            "provenance": "synthetic-fixture",
        },
        profile=profile(),
        documents=[
            document("doc-public"),
            document("doc-private", "private"),
            document("doc-copyright", "copyrighted"),
        ],
        sources=[
            {"source_id": "source-public", "type": "document"},
            {"source_id": "source-master", "type": "master"},
        ],
        event_spec={
            "season": "2026-2027",
            "team": "Synthetic Club",
            "events": [{"event_id": "event-1", "resolved": True}],
            "sources": [{"source_id": "event-source", "status": "PASS"}],
            "as_of": "2026-09-15T00:00:00Z",
        },
    )


def authorization(doc_id, target=CONSUMER_TARGETS[0]):
    return {
        "doc_id": doc_id,
        "target": target,
        "allowed_scope": "package",
        "authorization_basis": "synthetic-user-authorization",
        "frozen_at": "2026-09-15T00:00:00Z",
    }


def test_all_targets_reference_one_canonical_payload_and_block_unauthorized_items():
    value = payload()
    packages = build_target_packages(
        value,
        authorizations=[authorization("doc-copyright")],
    )
    assert set(packages) == set(CONSUMER_TARGETS)
    assert {
        package["consumer_payload_sha256"]
        for package in packages.values()
    } == {value["consumer_payload_sha256"]}
    chatgpt = packages["ChatGPT"]
    assert "documents/doc-public.json" in chatgpt["files"]
    assert "documents/doc-copyright.json" in chatgpt["files"]
    assert "documents/doc-private.json" not in chatgpt["files"]
    assert chatgpt["manifest"]["coverage"]["explicitly_blocked_items"] == 1
    validate_package(chatgpt)


def test_limits_shard_documents_without_truncation():
    value = payload()
    package = build_target_package(
        value,
        CONSUMER_TARGETS[0],
        authorizations=[
            authorization("doc-private"),
            authorization("doc-copyright"),
        ],
        limits={"max_files": 8},
    )
    assert "documents/shard-000.json" in package["files"]
    assert package["manifest"]["coverage"]["missing"] == 0
    assert package["manifest"]["coverage"]["silent_truncation"] == 0
    assert package["manifest"]["coverage"]["declared_items"] == (
        package["manifest"]["coverage"]["packaged_items"]
        + package["manifest"]["coverage"]["explicitly_blocked_items"]
    )


def test_package_limits_fail_closed_instead_of_dropping_items():
    with pytest.raises(ConsumerPackageError, match="FILE_LIMIT"):
        build_target_package(
            payload(),
            CONSUMER_TARGETS[0],
            limits={"max_files": 7},
        )


def test_authorization_is_bound_to_exact_document_and_target():
    value = payload()
    package = build_target_package(
        value,
        "Gemini Notebook",
        authorizations=[authorization("doc-private", "ChatGPT")],
    )
    blocked = package["manifest"]["coverage"]["blocked"]
    assert {item["doc_id"] for item in blocked} == {
        "doc-private", "doc-copyright",
    }
