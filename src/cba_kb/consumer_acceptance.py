"""v1.7 consumer acceptance contract v2 and Usage Gate validation."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .common import digest
from .consumer_projection import FAILURE_LAYERS, RESULT_STATUSES
from .evidence_ledger import canonical_bytes


SCHEMA_VERSION = 2
GOLDEN_VERSION = "v2"
CONSUMERS = ("ChatGPT", "Gemini Notebook", "WorkBuddy")
GOLDEN_CATEGORY_COUNTS = {
    "deterministic_exact": 4,
    "source_tracing": 2,
    "cross_source_synthesis": 2,
    "identity_boundary": 1,
    "unknown_gap": 1,
}
HARD_GATE_CATEGORIES = {
    "deterministic_exact": 4,
    "source_tracing": 2,
    "identity_boundary": 1,
    "unknown_gap": 1,
}
MCP_ROOT_CAUSES = frozenset({"retrieval", "aggregation", "counting"})
USAGE_FAILURE_DOMAINS = frozenset({
    "Document",
    "Event",
    "Identity",
    "Stats",
    "Consumer Tool",
    "Other",
})
NEXT_STEP_DECISIONS = frozenset({
    "CONTINUE_V1_8",
    "SPLIT_NEW_REQUIREMENT",
    "HOLD_AND_KEEP_USING",
})


class ConsumerAcceptanceError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ConsumerAcceptanceError(f"{label}_REQUIRED")
    return value.strip()


def _questions(value):
    if not isinstance(value, dict):
        raise ConsumerAcceptanceError("GOLDEN_QUESTIONS_OBJECT_REQUIRED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("version") != GOLDEN_VERSION
    ):
        raise ConsumerAcceptanceError("GOLDEN_QUESTIONS_VERSION_INVALID")
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) != 10:
        raise ConsumerAcceptanceError("GOLDEN_QUESTIONS_COUNT_INVALID")
    return questions


def _oracle_record(question):
    record = question.get("oracle_validation")
    if not isinstance(record, dict):
        raise ConsumerAcceptanceError("ORACLE_VALIDATION_REQUIRED")
    if record.get("status") != "VALIDATED":
        raise ConsumerAcceptanceError("ORACLE_NOT_INDEPENDENTLY_VALIDATED")
    evidence_refs = record.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise ConsumerAcceptanceError("ORACLE_EVIDENCE_REQUIRED")
    if not all(isinstance(item, str) and item for item in evidence_refs):
        raise ConsumerAcceptanceError("ORACLE_EVIDENCE_INVALID")
    return record


def validate_golden_v2(value, oracle=None):
    questions = _questions(value)
    ids = set()
    counts = {}
    for question in questions:
        if not isinstance(question, dict):
            raise ConsumerAcceptanceError("GOLDEN_QUESTION_INVALID")
        question_id = _required_text(question.get("id"), "QUESTION_ID")
        category = question.get("category")
        if question_id in ids:
            raise ConsumerAcceptanceError("GOLDEN_QUESTION_ID_DUPLICATE")
        if category not in GOLDEN_CATEGORY_COUNTS:
            raise ConsumerAcceptanceError("GOLDEN_QUESTION_CATEGORY_INVALID")
        if (
            not question.get("question")
            or "expected_answer" not in question
            or not isinstance(question.get("expected_schema"), dict)
            or question.get("frozen") is not True
        ):
            raise ConsumerAcceptanceError("GOLDEN_QUESTION_INVALID")
        _oracle_record(question)
        ids.add(question_id)
        counts[category] = counts.get(category, 0) + 1
    if counts != GOLDEN_CATEGORY_COUNTS:
        raise ConsumerAcceptanceError("GOLDEN_QUESTION_COVERAGE_INVALID")
    oracle_summary = {
        "status": "VALIDATED",
        "questions": len(questions),
        "source": "EMBEDDED",
    }
    if oracle is not None:
        if not isinstance(oracle, dict) or set(oracle) != ids:
            raise ConsumerAcceptanceError("ORACLE_SET_MISMATCH")
        for question in questions:
            expected = oracle[question["id"]]
            if not isinstance(expected, dict) or "expected_answer" not in expected:
                raise ConsumerAcceptanceError("ORACLE_ITEM_INVALID")
            if canonical_bytes(expected["expected_answer"]) != canonical_bytes(
                question["expected_answer"],
            ):
                raise ConsumerAcceptanceError("ORACLE_ANSWER_MISMATCH")
            refs = expected.get("evidence_refs")
            if not isinstance(refs, list) or not refs:
                raise ConsumerAcceptanceError("ORACLE_EVIDENCE_REQUIRED")
        oracle_summary["source"] = "SEPARATE_FROZEN_ORACLE"
    return {
        "status": "PASS",
        "version": GOLDEN_VERSION,
        "questions": len(questions),
        "coverage": counts,
        "oracle": oracle_summary,
        "sha256": digest(canonical_bytes(value)),
    }


def load_golden_v2(path, oracle_path=None):
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConsumerAcceptanceError("GOLDEN_QUESTIONS_INVALID") from exc
    oracle = None
    if oracle_path is not None:
        try:
            oracle = json.loads(Path(oracle_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConsumerAcceptanceError("ORACLE_FILE_INVALID") from exc
    validate_golden_v2(value, oracle)
    return value


def validate_intake_precheck(value):
    if isinstance(value, dict):
        value = value.get("receipts")
    if not isinstance(value, list) or len(value) != len(CONSUMERS):
        raise ConsumerAcceptanceError("CONSUMER_INTAKE_RECEIPTS_REQUIRED")
    receipts = {}
    for item in value:
        if not isinstance(item, dict):
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_RECEIPT_INVALID")
        target = item.get("target")
        if target not in CONSUMERS or target in receipts:
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_TARGET_INVALID")
        required_text = (
            "run_date",
            "package_sha256",
            "evidence_ref",
        )
        for key in required_text:
            _required_text(item.get(key), f"INTAKE_{key.upper()}")
        expected_files = item.get("expected_files")
        accepted_files = item.get("accepted_files")
        rejected_files = item.get("rejected_files")
        if (
            not isinstance(expected_files, int)
            or expected_files <= 0
            or not isinstance(accepted_files, int)
            or accepted_files < 0
            or not isinstance(rejected_files, list)
        ):
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_FILE_COUNTS_INVALID")
        if accepted_files + len(rejected_files) != expected_files:
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_FILE_COVERAGE_MISMATCH")
        constraints = item.get("observed_constraints")
        if not isinstance(constraints, dict):
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_CONSTRAINTS_REQUIRED")
        if item.get("search_retrieval_ready") is not True:
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_NOT_READY")
        if item.get("truncation_observed") is not False:
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_TRUNCATION_OBSERVED")
        if not isinstance(item.get("warning_observed"), bool):
            raise ConsumerAcceptanceError("CONSUMER_INTAKE_WARNING_FLAG_REQUIRED")
        receipts[target] = dict(item)
    if set(receipts) != set(CONSUMERS):
        raise ConsumerAcceptanceError("CONSUMER_INTAKE_TARGET_COVERAGE_INVALID")
    return {
        "status": "PASS",
        "targets": list(CONSUMERS),
        "receipts": receipts,
    }


def build_acceptance_template(golden):
    questions = _questions(golden)
    return {
        "schema_version": SCHEMA_VERSION,
        "version": GOLDEN_VERSION,
        "cells": [
            {
                "consumer": consumer,
                "question_id": question["id"],
                "status": "NOT_TESTED",
                "failure_layer": None,
                "evidence_ref": None,
                "raw_answer_ref": None,
                "reason": "NOT_EXECUTED",
            }
            for consumer in CONSUMERS
            for question in questions
        ],
    }


def validate_result_matrix(value, golden):
    if isinstance(value, dict):
        value = value.get("cells")
    if not isinstance(value, list) or len(value) != 30:
        raise ConsumerAcceptanceError("CONSUMER_RESULT_MATRIX_SIZE_INVALID")
    questions = {
        item["id"]: item for item in _questions(golden)
    }
    cells = {}
    for item in value:
        if not isinstance(item, dict):
            raise ConsumerAcceptanceError("CONSUMER_RESULT_CELL_INVALID")
        consumer = item.get("consumer")
        question_id = item.get("question_id")
        if consumer not in CONSUMERS or question_id not in questions:
            raise ConsumerAcceptanceError("CONSUMER_RESULT_CELL_SCOPE_INVALID")
        key = (consumer, question_id)
        if key in cells:
            raise ConsumerAcceptanceError("CONSUMER_RESULT_CELL_DUPLICATE")
        status = item.get("status")
        if status not in RESULT_STATUSES:
            raise ConsumerAcceptanceError("CONSUMER_RESULT_STATUS_INVALID")
        failure_layer = item.get("failure_layer")
        if status == "FAIL":
            if failure_layer not in FAILURE_LAYERS:
                raise ConsumerAcceptanceError("CONSUMER_FAILURE_LAYER_REQUIRED")
            _required_text(item.get("evidence_ref"), "FAIL_EVIDENCE")
            _required_text(item.get("raw_answer_ref"), "FAIL_RAW_ANSWER")
        elif failure_layer is not None:
            raise ConsumerAcceptanceError("CONSUMER_FAILURE_LAYER_INVALID")
        if status == "PASS":
            _required_text(item.get("evidence_ref"), "PASS_EVIDENCE")
            _required_text(item.get("raw_answer_ref"), "PASS_RAW_ANSWER")
        if status == "NOT_TESTED":
            _required_text(item.get("reason"), "NOT_TESTED_REASON")
        cells[key] = dict(item)
    expected = {
        (consumer, question_id)
        for consumer in CONSUMERS
        for question_id in questions
    }
    if set(cells) != expected:
        raise ConsumerAcceptanceError("CONSUMER_RESULT_CELL_COVERAGE_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "version": GOLDEN_VERSION,
        "cells": cells,
    }


def _gate_summary(cells, questions):
    consumers = {}
    aggregate_pass = 0
    for consumer in CONSUMERS:
        category_counts = {
            category: {"PASS": 0, "FAIL": 0, "NOT_TESTED": 0}
            for category in GOLDEN_CATEGORY_COUNTS
        }
        for question_id, question in questions.items():
            cell = cells[(consumer, question_id)]
            category_counts[question["category"]][cell["status"]] += 1
        total_pass = sum(
            counts["PASS"] for counts in category_counts.values()
        )
        gates = {
            category: (
                category_counts[category]["PASS"] == required
                and category_counts[category]["FAIL"] == 0
                and category_counts[category]["NOT_TESTED"] == 0
            )
            for category, required in HARD_GATE_CATEGORIES.items()
        }
        gates["overall_min_pass"] = total_pass >= 9
        consumers[consumer] = {
            "category_counts": category_counts,
            "total_pass": total_pass,
            "hard_gates": gates,
            "pass": all(gates.values()),
        }
        aggregate_pass += total_pass
    return {
        "consumers": consumers,
        "aggregate_pass": aggregate_pass,
        "aggregate_required": 27,
        "aggregate_pass_gate": aggregate_pass >= 27,
        "pass": (
            all(item["pass"] for item in consumers.values())
            and aggregate_pass >= 27
        ),
    }


def evaluate_consumer_acceptance(*, golden, oracle, intakes, matrix):
    golden_summary = validate_golden_v2(golden, oracle)
    intake_summary = validate_intake_precheck(intakes)
    validated_matrix = validate_result_matrix(matrix, golden)
    questions = {
        item["id"]: item for item in _questions(golden)
    }
    gates = _gate_summary(validated_matrix["cells"], questions)
    exact_failures = []
    for (consumer, question_id), cell in validated_matrix["cells"].items():
        if (
            questions[question_id]["category"] == "deterministic_exact"
            and cell["status"] == "FAIL"
        ):
            exact_failures.append({
                "consumer": consumer,
                "question_id": question_id,
                "failure_layer": cell["failure_layer"],
                "evidence_ref": cell["evidence_ref"],
            })
    mcp_causes = [
        item for item in exact_failures
        if item["failure_layer"] in MCP_ROOT_CAUSES
    ]
    accepted = gates["pass"]
    return {
        "schema_version": SCHEMA_VERSION,
        "version": GOLDEN_VERSION,
        "status": "PASS" if accepted else "FAIL",
        "golden": golden_summary,
        "intake": intake_summary,
        "gates": gates,
        "exact_failures": exact_failures,
        "mcp_trigger": {
            "required": bool(mcp_causes),
            "status": "MCP_TRIGGER_REQUIRED" if mcp_causes else "NOT_REQUIRED",
            "follow_up_requirement_id": "TBD_NEW_REQ" if mcp_causes else None,
            "causes": mcp_causes,
        },
        "feature_acceptance": "PASS" if accepted else "FAIL",
        "usage_gate": "USAGE_GATE_OPEN" if accepted else "USAGE_GATE_CLOSED",
        "project_closeout_state": (
            "EXIT_EVIDENCE_PENDING" if accepted else "OPEN"
        ),
    }


def validate_usage_gate(
    records,
    *,
    minimum=10,
    next_step_decision=None,
):
    if not isinstance(minimum, int) or minimum <= 0:
        raise ConsumerAcceptanceError("USAGE_MINIMUM_INVALID")
    if records is None:
        records = []
    if isinstance(records, dict):
        records = records.get("queries")
    if not isinstance(records, list):
        raise ConsumerAcceptanceError("USAGE_RECORDS_REQUIRED")
    normalized = []
    for record in records:
        if not isinstance(record, dict):
            raise ConsumerAcceptanceError("USAGE_RECORD_INVALID")
        required = {
            "consumer",
            "question",
            "evidence_refs",
            "failure_domain",
            "manual_minutes",
            "observed_at",
        }
        if not required <= set(record):
            raise ConsumerAcceptanceError("USAGE_RECORD_INCOMPLETE")
        if record["consumer"] not in CONSUMERS:
            raise ConsumerAcceptanceError("USAGE_CONSUMER_INVALID")
        _required_text(record["question"], "USAGE_QUESTION")
        if record.get("answer") is None and record.get("result") is None:
            raise ConsumerAcceptanceError("USAGE_RESULT_REQUIRED")
        refs = record["evidence_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(item, str) and item for item in refs)
        ):
            raise ConsumerAcceptanceError("USAGE_EVIDENCE_REQUIRED")
        if record["failure_domain"] not in USAGE_FAILURE_DOMAINS:
            raise ConsumerAcceptanceError("USAGE_FAILURE_DOMAIN_INVALID")
        minutes = record["manual_minutes"]
        if (
            not isinstance(minutes, (int, float))
            or isinstance(minutes, bool)
            or minutes < 0
        ):
            raise ConsumerAcceptanceError("USAGE_MANUAL_MINUTES_INVALID")
        _required_text(record["observed_at"], "USAGE_OBSERVED_AT")
        if record.get("acceptance_question_id") is not None:
            raise ConsumerAcceptanceError("USAGE_RECORD_MUST_NOT_BE_ACCEPTANCE")
        normalized.append(dict(record))
    if next_step_decision is not None:
        if next_step_decision not in NEXT_STEP_DECISIONS:
            raise ConsumerAcceptanceError("NEXT_STEP_DECISION_INVALID")
    if len(normalized) < minimum:
        state = "EXIT_EVIDENCE_PENDING"
        decision = None
    elif next_step_decision is None:
        state = "READY_FOR_NEXT_STEP_DECISION"
        decision = None
    else:
        state = "CLOSED"
        decision = next_step_decision
    return {
        "schema_version": SCHEMA_VERSION,
        "count": len(normalized),
        "minimum": minimum,
        "state": state,
        "next_step_decision": decision,
    }


validate_golden_questions = validate_golden_v2
validate_golden_questions_v2 = validate_golden_v2
