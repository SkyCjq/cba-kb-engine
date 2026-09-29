from copy import deepcopy
from pathlib import Path

import pytest

from cba_kb.consumer_projection import (
    build_player_identity_consumer_projection,
    ConsumerProjectionError,
    consumer_baseline_template,
    event_coverage,
    load_golden_questions,
    project_document,
    project_documents,
    usage_failure_layer_schema,
    validate_consumer_baseline,
    validate_consumer_result,
    validate_player_identity_consumer_projection,
)
from cba_kb.common import digest
from cba_kb.evidence_ledger import canonical_bytes
from cba_kb.player_identity import new_registry


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/consumer_golden_questions_v1.yaml"
SCOPE = {
    "release_id": "v1.6.1-1",
    "as_of": "2026-09-13T00:00:00Z",
    "provenance": "source_registry:fixture",
}


def document(**overrides):
    value = {
        "doc_id": "doc-1",
        "rights": "public",
        "title": "Public title",
        "body": "Public body",
        "body_status": "AVAILABLE",
        "source_ref": "source-1",
        "public_export_allowed": True,
    }
    value.update(overrides)
    return value


def test_golden_questions_are_versioned_and_frozen():
    value = load_golden_questions(CONFIG)
    assert len(value["questions"]) == 10
    assert value["version"] == "v1"
    assert all(question["frozen"] is True for question in value["questions"])


def test_rights_aware_projection_fails_closed():
    public = project_document(document(), SCOPE)
    assert public["availability"] == "AVAILABLE"
    assert public["body"] == "Public body"

    private = project_document(document(rights="private"), SCOPE)
    assert private["availability"] == "PRIVATE"
    assert private["title"] is None and private["body"] is None
    assert private["body_status"] == "BODY_UNAVAILABLE"

    unknown = project_document(document(rights="unknown"), SCOPE)
    assert unknown["availability"] == "BLOCKED"
    assert unknown["body_status"] == "BODY_UNAVAILABLE"

    copyrighted = project_document(
        document(rights="copyrighted", public_export_allowed=False), SCOPE,
    )
    assert copyrighted["availability"] == "METADATA_ONLY"
    assert copyrighted["body"] is None
    assert copyrighted["body_status"] == "BODY_UNAVAILABLE"


def test_projection_statuses_and_order_are_deterministic():
    documents = [
        document(doc_id="b", body_status="REVIEW_REQUIRED"),
        document(doc_id="a", body_status="OCR_REQUIRED"),
    ]
    first = project_documents(documents, SCOPE)
    second = project_documents(list(reversed(documents)), SCOPE)
    assert first == second
    assert [item["doc_id"] for item in first] == ["a", "b"]
    assert first[0]["body_status"] == "OCR_REQUIRED"
    assert first[1]["body_status"] == "REVIEW_REQUIRED"


def test_event_coverage_is_deterministic_and_exposes_gaps():
    value = event_coverage(
        season="2026-2027",
        team="北京首钢",
        events=[
            {"event_id": "e2", "resolved": True},
            {"event_id": "e1", "resolved": False},
        ],
        sources=[
            {"source_id": "s1", "status": "PASS"},
            {"source_id": "s2", "status": "PENDING"},
        ],
        as_of="2026-09-13T00:00:00Z",
    )
    assert value["covered"] == ["e2"]
    assert value["unresolved"] == ["e1"]
    assert value["critical_gap"] is True


def test_consumer_result_schema_and_external_baseline_are_not_faked():
    assert validate_consumer_result({"status": "PASS"})["status"] == "PASS"
    assert validate_consumer_result({
        "status": "FAIL", "failure_layer": "retrieval",
    })["failure_layer"] == "retrieval"
    with pytest.raises(ConsumerProjectionError, match="FAILURE_LAYER"):
        validate_consumer_result({"status": "FAIL"})
    baseline = consumer_baseline_template()
    assert all(
        result["status"] == "NOT_TESTED"
        for result in validate_consumer_baseline(baseline)["consumers"].values()
    )
    assert "failure_layer" in usage_failure_layer_schema()["required"]


def identity_registry(evidence_refs=None):
    return new_registry([{
        "schema_version": 1,
        "player_uid": "pid_00000000000000000000000000000001",
        "canonical_name": "测试球员",
        "status": "ACTIVE",
        "redirect_to": None,
    }], record_links=[{
        "schema_version": 1,
        "record_key": "2025-2026|club|测试球员",
        "player_uid": "pid_00000000000000000000000000000001",
        "link_status": "same",
        "confidence": "HIGH",
        "method": "MANUAL_REVIEW",
        "evidence_refs": evidence_refs or [
            "doc:consumer-safe", "private:secret", "/Users/private/file",
            "unclassified-secret",
        ],
    }])


def identity_projection():
    return build_player_identity_consumer_projection(
        identity_registry(), release_id="v1.8.1-2",
        code_commit="a" * 40,
    )


def rehash_projection(value):
    value["projection_sha256"] = digest(canonical_bytes({
        key: item for key, item in value.items()
        if key != "projection_sha256"
    }))


def test_identity_projection_uses_positive_safe_provenance_and_source_binding():
    projection = identity_projection()
    assert projection["record_links"][0]["evidence_refs"] == [{
        "type": "doc", "ref": "consumer-safe",
    }]
    result = validate_player_identity_consumer_projection(
        projection,
        expected_release_id="v1.8.1-2",
        expected_code_commit="a" * 40,
        expected_source_registry_sha256=projection["source_registry_sha256"],
    )
    assert result["status"] == "PASS"
    with pytest.raises(ConsumerProjectionError, match="SOURCE_REGISTRY_BINDING"):
        validate_player_identity_consumer_projection(
            projection, expected_source_registry_sha256="0" * 64,
        )


@pytest.mark.parametrize("unsafe_ref", [
    "source:file:///tmp/private.db",
    "doc:/Users/example/private.txt",
    "public:file://local-secret",
    "web:/private/path",
])
def test_identity_projection_omits_unsafe_typed_provenance_values(unsafe_ref):
    registry = identity_registry([
        "doc:consumer-safe", "private:secret", "/Users/private/file",
        "unclassified-secret", unsafe_ref,
    ])

    projection = build_player_identity_consumer_projection(
        registry, release_id="v1.8.1-2", code_commit="a" * 40,
    )

    assert projection["record_links"][0]["evidence_refs"] == [{
        "type": "doc", "ref": "consumer-safe",
    }]


@pytest.mark.parametrize(("ref_type", "unsafe_value"), [
    ("source", "file:///tmp/private.db"),
    ("doc", "/Users/example/private.txt"),
    ("public", "file://local-secret"),
    ("web", "/private/path"),
])
def test_identity_projection_rejects_injected_unsafe_typed_provenance(
    ref_type, unsafe_value,
):
    projection = identity_projection()
    projection["record_links"][0]["evidence_refs"].append({
        "type": ref_type,
        "ref": unsafe_value,
    })
    rehash_projection(projection)

    with pytest.raises(ConsumerProjectionError):
        validate_player_identity_consumer_projection(
            projection,
            expected_source_registry_sha256=projection[
                "source_registry_sha256"
            ],
        )


@pytest.mark.parametrize("tamper", ["relation", "summary", "duplicate"])
def test_identity_projection_semantic_tamper_fails_after_rehash(tamper):
    projection = identity_projection()
    if tamper == "relation":
        projection["record_links"][0]["relation"] = "SAMEISH"
    elif tamper == "summary":
        projection["summary"]["same_count"] = 99
    else:
        projection["record_links"].append(
            deepcopy(projection["record_links"][0])
        )
        projection["summary"]["total_record_links"] += 1
        projection["summary"]["same_count"] += 1
    rehash_projection(projection)
    with pytest.raises(ConsumerProjectionError, match=(
        "RECORD_LINK_INVALID|SUMMARY_MISMATCH|RECORD_LINK_DUPLICATE"
    )):
        validate_player_identity_consumer_projection(
            projection,
            expected_source_registry_sha256=projection[
                "source_registry_sha256"
            ],
        )
