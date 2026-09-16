from copy import deepcopy

import pytest

from cba_kb.consumer_acceptance import (
    CONSUMERS,
    ConsumerAcceptanceError,
    build_acceptance_template,
    evaluate_consumer_acceptance,
    validate_golden_v2,
    validate_usage_gate,
)


CATEGORY_COUNTS = [
    ("deterministic_exact", 4),
    ("source_tracing", 2),
    ("cross_source_synthesis", 2),
    ("identity_boundary", 1),
    ("unknown_gap", 1),
]


def golden():
    questions = []
    index = 0
    for category, count in CATEGORY_COUNTS:
        for _ in range(count):
            index += 1
            questions.append({
                "id": f"q{index:02d}",
                "category": category,
                "question": f"Synthetic question {index}",
                "expected_answer": f"answer-{index}",
                "expected_schema": {"type": "string"},
                "oracle_validation": {
                    "status": "VALIDATED",
                    "evidence_refs": [f"synthetic-oracle-{index}"],
                },
                "frozen": True,
            })
    return {
        "schema_version": 2,
        "version": "v2",
        "frozen_at": "2026-09-15T00:00:00Z",
        "questions": questions,
    }


def oracle(value):
    return {
        question["id"]: {
            "expected_answer": question["expected_answer"],
            "evidence_refs": [f"independent-oracle-{question['id']}"],
        }
        for question in value["questions"]
    }


def intakes():
    return [{
        "target": consumer,
        "run_date": "2026-09-15",
        "package_sha256": "c" * 64,
        "expected_files": 3,
        "accepted_files": 3,
        "rejected_files": [],
        "observed_constraints": {"max_files": 10},
        "search_retrieval_ready": True,
        "warning_observed": False,
        "truncation_observed": False,
        "evidence_ref": f"synthetic-intake-{consumer}",
    } for consumer in CONSUMERS]


def matrix_pass(value):
    matrix = build_acceptance_template(value)
    for cell in matrix["cells"]:
        cell.update({
            "status": "PASS",
            "failure_layer": None,
            "evidence_ref": f"synthetic-evidence-{cell['consumer']}-{cell['question_id']}",
            "raw_answer_ref": f"synthetic-answer-{cell['consumer']}-{cell['question_id']}",
        })
        cell.pop("reason", None)
    return matrix


def test_v2_golden_requires_exact_distribution_and_independent_oracle():
    value = golden()
    summary = validate_golden_v2(value, oracle(value))
    assert summary["coverage"] == {
        "deterministic_exact": 4,
        "source_tracing": 2,
        "cross_source_synthesis": 2,
        "identity_boundary": 1,
        "unknown_gap": 1,
    }
    broken = deepcopy(oracle(value))
    broken["q01"]["expected_answer"] = "wrong"
    with pytest.raises(ConsumerAcceptanceError, match="ORACLE_ANSWER_MISMATCH"):
        validate_golden_v2(value, broken)


def test_acceptance_requires_all_hard_gates_and_emits_usage_gate_open():
    value = golden()
    result = evaluate_consumer_acceptance(
        golden=value,
        oracle=oracle(value),
        intakes=intakes(),
        matrix=matrix_pass(value),
    )
    assert result["status"] == "PASS"
    assert result["gates"]["aggregate_pass"] == 30
    assert result["mcp_trigger"]["required"] is False
    assert result["usage_gate"] == "USAGE_GATE_OPEN"


def test_only_one_synthesis_miss_is_tolerated_per_consumer():
    value = golden()
    matrix = matrix_pass(value)
    for cell in matrix["cells"]:
        if (
            cell["consumer"] == "ChatGPT"
            and cell["question_id"] == "q07"
        ):
            cell.update({
                "status": "FAIL",
                "failure_layer": "projection",
                "evidence_ref": "synthetic-projection-failure",
                "raw_answer_ref": "synthetic-projection-answer",
            })
    result = evaluate_consumer_acceptance(
        golden=value,
        oracle=oracle(value),
        intakes=intakes(),
        matrix=matrix,
    )
    assert result["status"] == "PASS"
    assert result["gates"]["aggregate_pass"] == 29
    assert result["mcp_trigger"]["required"] is False


def test_exact_retrieval_failure_triggers_mcp_and_does_not_pass():
    value = golden()
    matrix = matrix_pass(value)
    for cell in matrix["cells"]:
        if (
            cell["consumer"] == "WorkBuddy"
            and cell["question_id"] == "q01"
        ):
            cell.update({
                "status": "FAIL",
                "failure_layer": "retrieval",
                "evidence_ref": "synthetic-retrieval-failure",
                "raw_answer_ref": "synthetic-retrieval-answer",
            })
    result = evaluate_consumer_acceptance(
        golden=value,
        oracle=oracle(value),
        intakes=intakes(),
        matrix=matrix,
    )
    assert result["status"] == "FAIL"
    assert result["mcp_trigger"]["status"] == "MCP_TRIGGER_REQUIRED"
    assert result["usage_gate"] == "USAGE_GATE_CLOSED"


def test_usage_gate_requires_ten_non_acceptance_queries_and_decision():
    records = [{
        "consumer": consumer,
        "question": f"Synthetic real query {index}",
        **({"answer": "Synthetic answer"} if index % 2 else {
            "result": "Synthetic result",
        }),
        "evidence_refs": [f"synthetic-usage-{index}"],
        "failure_domain": "Other",
        "manual_minutes": 1,
        "observed_at": "2026-09-15T00:00:00Z",
    } for index, consumer in enumerate(
        [CONSUMERS[index % len(CONSUMERS)] for index in range(10)],
    )]
    assert validate_usage_gate(records[:9])["state"] == (
        "EXIT_EVIDENCE_PENDING"
    )
    assert validate_usage_gate(records)["state"] == (
        "READY_FOR_NEXT_STEP_DECISION"
    )
    closed = validate_usage_gate(
        records,
        next_step_decision="HOLD_AND_KEEP_USING",
    )
    assert closed["state"] == "CLOSED"
