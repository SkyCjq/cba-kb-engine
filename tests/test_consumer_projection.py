from pathlib import Path

import pytest

from cba_kb.consumer_projection import (
    ConsumerProjectionError,
    consumer_baseline_template,
    event_coverage,
    load_golden_questions,
    project_document,
    project_documents,
    usage_failure_layer_schema,
    validate_consumer_baseline,
    validate_consumer_result,
)


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
