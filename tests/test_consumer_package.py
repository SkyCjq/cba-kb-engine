import json

import pytest

from cba_kb.common import digest
from cba_kb.consumer_package import (
    CONSUMER_TARGETS,
    ConsumerPackageError,
    build_consumer_payload,
    build_target_package,
    build_target_packages,
    validate_package,
)
from cba_kb.consumer_projection import project_document
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


def authorized_evidence(doc_id, *, target=None, body=None, sha256=None):
    body = body or f"SYNTHETIC AUTHORIZED EVIDENCE {doc_id}"
    return {
        "doc_id": doc_id,
        "target": target,
        "body": body,
        "sha256": sha256 or digest(body.encode("utf-8")),
        "source_ref": f"private-source-{doc_id}",
    }


def test_all_targets_reference_one_canonical_payload_and_block_unauthorized_items():
    value = payload()
    packages = build_target_packages(value)
    assert set(packages) == set(CONSUMER_TARGETS)
    assert {
        package["consumer_payload_sha256"]
        for package in packages.values()
    } == {value["consumer_payload_sha256"]}
    chatgpt = packages["ChatGPT"]
    assert "documents/doc-public.json" in chatgpt["files"]
    assert "documents/doc-copyright.json" not in chatgpt["files"]
    assert "documents/doc-private.json" not in chatgpt["files"]
    assert chatgpt["manifest"]["coverage"]["explicitly_blocked_items"] == 2
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
        authorized_evidence=[
            authorized_evidence("doc-private"),
            authorized_evidence("doc-copyright"),
        ],
        limits={"max_files": 10},
    )
    assert "documents/shard-000.json" in package["files"]
    assert any(
        path.startswith("authorized-evidence/")
        for path in package["files"]
    )
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


def test_v1_projection_remains_redacted_without_authorized_evidence():
    value = payload()
    scope = {
        "release_id": "v1.7.0-synthetic",
        "as_of": "2026-09-15T00:00:00Z",
        "provenance": "synthetic-fixture",
    }
    copyrighted = project_document(document("doc-copyright", "copyrighted"), scope)
    private = project_document(document("doc-private", "private"), scope)
    assert copyrighted["body"] is None
    assert copyrighted["availability"] == "METADATA_ONLY"
    assert private["body"] is None
    assert private["title"] is None
    assert private["metadata"] == {}
    assert private["availability"] == "PRIVATE"

    package = build_target_package(value, CONSUMER_TARGETS[0])
    canonical = json.loads(
        package["files"]["canonical_consumer_payload.json"],
    )
    projections = {
        item["doc_id"]: item
        for item in canonical["selected_document_projections"]
    }
    private_projection = projections["doc-private"]
    assert private_projection["body"] is None
    assert not any(
        path.startswith("authorized-evidence/")
        for path in package["files"]
    )


def test_copyrighted_authorized_evidence_is_target_only_and_unchanged_rights():
    value = payload()
    evidence = authorized_evidence("doc-copyright")
    package = build_target_package(
        value,
        "ChatGPT",
        authorizations=[authorization("doc-copyright")],
        authorized_evidence=[evidence],
    )
    path = "authorized-evidence/doc-copyright.json"
    assert path in package["files"]
    artifact = json.loads(package["files"][path])
    assert artifact["body"] == evidence["body"]
    assert artifact["rights"] == "copyrighted"
    assert artifact["public_export_allowed"] is False
    assert evidence["body"] not in package["files"][
        "canonical_consumer_payload.json"
    ]
    validate_package(package)


def test_private_authorized_evidence_is_target_only():
    value = payload()
    evidence = authorized_evidence("doc-private")
    package = build_target_package(
        value,
        "ChatGPT",
        authorizations=[authorization("doc-private")],
        authorized_evidence=[evidence],
    )
    artifact = json.loads(
        package["files"]["authorized-evidence/doc-private.json"],
    )
    assert artifact["body"] == evidence["body"]
    assert artifact["rights"] == "private"
    assert artifact["public_export_allowed"] is False
    validate_package(package)


def test_target_authorization_does_not_cross_targets():
    value = payload()
    packages = build_target_packages(
        value,
        authorizations=[authorization("doc-private", "ChatGPT")],
        authorized_evidence=[authorized_evidence("doc-private")],
    )
    assert "authorized-evidence/doc-private.json" in packages["ChatGPT"]["files"]
    for target in ("Gemini Notebook", "WorkBuddy"):
        assert "authorized-evidence/doc-private.json" not in packages[target]["files"]
        assert "doc-private" in {
            item["doc_id"]
            for item in packages[target]["manifest"]["coverage"]["blocked"]
        }
    assert len({
        package["consumer_payload_sha256"]
        for package in packages.values()
    }) == 1


@pytest.mark.parametrize("case", [
    "hash",
    "missing",
    "out_of_scope",
    "duplicate",
    "target_mismatch",
])
def test_authorized_evidence_fails_closed(case):
    value = payload()
    auth = [authorization("doc-private")]
    if case == "hash":
        evidence = [authorized_evidence("doc-private", sha256="0" * 64)]
    elif case == "missing":
        evidence = []
    elif case == "out_of_scope":
        evidence = [authorized_evidence("doc-out-of-scope")]
    elif case == "duplicate":
        evidence = [
            authorized_evidence("doc-private"),
            authorized_evidence("doc-private"),
        ]
    else:
        evidence = [authorized_evidence("doc-private", target="Gemini Notebook")]
    with pytest.raises(ConsumerPackageError):
        build_target_package(
            value,
            "ChatGPT",
            authorizations=auth,
            authorized_evidence=evidence,
        )


def test_authorized_evidence_cannot_exist_without_target_authorization():
    with pytest.raises(
        ConsumerPackageError,
        match="WITHOUT_AUTHORIZATION",
    ):
        build_target_package(
            payload(),
            "ChatGPT",
            authorized_evidence=[authorized_evidence("doc-private")],
        )


def test_two_authorized_sources_support_cross_source_synthesis_without_truncation():
    value = payload()
    package = build_target_package(
        value,
        "ChatGPT",
        authorizations=[
            authorization("doc-private"),
            authorization("doc-copyright"),
        ],
        authorized_evidence=[
            authorized_evidence(
                "doc-private",
                body="PRIVATE SYNTHESIS SOURCE BODY",
            ),
            authorized_evidence(
                "doc-copyright",
                body="COPYRIGHTED SYNTHESIS SOURCE BODY",
            ),
        ],
    )
    bodies = {
        json.loads(
            package["files"][f"authorized-evidence/{doc_id}.json"],
        )["body"]
        for doc_id in ("doc-private", "doc-copyright")
    }
    assert bodies == {
        "PRIVATE SYNTHESIS SOURCE BODY",
        "COPYRIGHTED SYNTHESIS SOURCE BODY",
    }
    coverage = package["manifest"]["coverage"]
    assert coverage["declared_items"] == (
        coverage["packaged_items"] + coverage["explicitly_blocked_items"]
    )
    assert coverage["missing"] == 0
    assert coverage["unexpected"] == 0
    assert coverage["duplicate"] == 0
    assert coverage["silent_truncation"] == 0
    validate_package(package)
