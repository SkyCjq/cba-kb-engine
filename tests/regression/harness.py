"""Deterministic scorer for REQ-202-REGRESSION-BASELINE-01.

The golden set is immutable input.  This module verifies its raw-byte SHA-256
before it parses questions, loads responses, calculates a score, or writes a
report.  A hash mismatch therefore cannot produce a score accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


FROZEN_GOLDEN_SHA256 = "78d68240d00db797b67f8a3219cf4e3cc711f9647dcf8def4f5811601449112d"
SCHEMA_VERSION = "cba-kb.regression-report.v1"
RESPONSE_SCHEMA_VERSION = "cba-kb.regression-responses.v1"
DEFAULT_GOLDEN = Path(__file__).with_name("golden_set_r1.json")

EXPECTED_CATEGORY_COUNTS = {
    "Fact": 2,
    "Identity": 2,
    "Document": 2,
    "Statement": 2,
    "Claim": 2,
    "Player-season Stats": 2,
    "Unknown": 2,
    "跨粒度": 2,
    "missing vs zero": 2,
    "unresolved": 1,
    "rights": 1,
    "duplicate / revision": 2,
    "coverage 局限": 1,
    "因果陷阱": 2,
    "Stats 与媒体数字冲突": 2,
}

HARD_GATE_CATEGORIES = {
    "identity_fail_safe": {"Identity", "Unknown", "unresolved"},
    "missing_vs_zero": {"missing vs zero"},
    "stats_statement_separation": {
        "Statement",
        "Player-season Stats",
        "Stats 与媒体数字冲突",
    },
    "rights_boundary": {"rights"},
}


class FrozenGoldenError(RuntimeError):
    """Raised before scoring when frozen golden bytes are not trustworthy."""


class ResponseError(ValueError):
    """Raised when response input is incomplete or malformed."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_frozen_golden(path: Path = DEFAULT_GOLDEN) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    actual_hash = sha256_bytes(raw)
    if actual_hash != FROZEN_GOLDEN_SHA256:
        raise FrozenGoldenError(
            "FROZEN_GOLDEN_HASH_MISMATCH: refusing to parse or score; "
            f"expected={FROZEN_GOLDEN_SHA256} actual={actual_hash}"
        )

    value = json.loads(raw)
    if not isinstance(value, list) or len(value) != 27:
        raise FrozenGoldenError("FROZEN_GOLDEN_SHAPE_INVALID: expected exactly 27 questions")

    required = {"id", "category", "question", "expected_answer", "source", "verification"}
    ids: set[str] = set()
    counts: dict[str, int] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != required:
            raise FrozenGoldenError(f"FROZEN_GOLDEN_ITEM_INVALID: index={index}")
        if any(not isinstance(item[key], str) or not item[key].strip() for key in required):
            raise FrozenGoldenError(f"FROZEN_GOLDEN_ITEM_EMPTY: index={index}")
        if item["id"] in ids:
            raise FrozenGoldenError(f"FROZEN_GOLDEN_DUPLICATE_ID: {item['id']}")
        ids.add(item["id"])
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    if counts != EXPECTED_CATEGORY_COUNTS:
        raise FrozenGoldenError(
            "FROZEN_GOLDEN_CATEGORY_COVERAGE_INVALID: "
            f"expected={EXPECTED_CATEGORY_COUNTS!r} actual={counts!r}"
        )
    return value, actual_hash


def normalize_answer(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def load_responses(path: Path, golden: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise ResponseError(f"response schema_version must be {RESPONSE_SCHEMA_VERSION}")
    items = value.get("items")
    if not isinstance(items, list):
        raise ResponseError("responses.items must be a list")

    golden_ids = {item["id"] for item in golden}
    response_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ResponseError("every response item must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, str) or item_id not in golden_ids or item_id in response_ids:
            raise ResponseError(f"invalid or duplicate response id: {item_id!r}")
        response_ids.add(item_id)
        if not isinstance(item.get("actual_answer"), str):
            raise ResponseError(f"{item_id}: actual_answer must be a string")
        if not isinstance(item.get("false_fact_promotion"), bool):
            raise ResponseError(f"{item_id}: false_fact_promotion must be a boolean")
        if next(q for q in golden if q["id"] == item_id)["category"] == "Statement":
            if not isinstance(item.get("attribution_correct"), bool):
                raise ResponseError(f"{item_id}: Statement responses require attribution_correct")

    missing = golden_ids - response_ids
    extra = response_ids - golden_ids
    if missing or extra:
        raise ResponseError(f"responses must cover the frozen set exactly; missing={sorted(missing)} extra={sorted(extra)}")
    return value, sha256_bytes(raw)


def reference_responses(golden: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    value = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "mode": "FROZEN_REFERENCE_REPLAY",
        "system_under_test": "frozen_expected_answers",
        "items": [
            {
                "id": item["id"],
                "actual_answer": item["expected_answer"],
                "false_fact_promotion": False,
                **({"attribution_correct": True} if item["category"] == "Statement" else {}),
            }
            for item in golden
        ],
    }
    return value, sha256_bytes(canonical_json_bytes(value))


def score(
    golden: list[dict[str, Any]],
    golden_hash: str,
    responses: dict[str, Any],
    responses_hash: str,
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in responses["items"]}
    results: list[dict[str, Any]] = []
    category_summary: dict[str, dict[str, int]] = {
        category: {"passed": 0, "total": total}
        for category, total in EXPECTED_CATEGORY_COUNTS.items()
    }

    for question in golden:
        response = by_id[question["id"]]
        answer_correct = normalize_answer(response["actual_answer"]) == normalize_answer(
            question["expected_answer"]
        )
        result = {
            "id": question["id"],
            "category": question["category"],
            "answer_correct": answer_correct,
            "false_fact_promotion": response["false_fact_promotion"],
        }
        if question["category"] == "Statement":
            result["attribution_correct"] = response["attribution_correct"]
        results.append(result)
        if answer_correct:
            category_summary[question["category"]]["passed"] += 1

    false_fact_promotions = sum(item["false_fact_promotion"] for item in results)
    hard_gates = {
        gate: {
            "status": "PASS"
            if all(
                item["answer_correct"] and not item["false_fact_promotion"]
                for item in results
                if item["category"] in categories
            )
            else "FAIL"
        }
        for gate, categories in HARD_GATE_CATEGORIES.items()
    }
    hard_gates["false_fact_promotion"] = {
        "status": "PASS" if false_fact_promotions == 0 else "FAIL",
        "count": false_fact_promotions,
        "required": 0,
    }

    statement = [item for item in results if item["category"] == "Statement"]
    passed = sum(item["answer_correct"] for item in results)
    status = "PASS" if passed == len(results) and all(g["status"] == "PASS" for g in hard_gates.values()) else "FAIL"
    mode = responses.get("mode", "CANDIDATE")
    return {
        "schema_version": SCHEMA_VERSION,
        "requirement": "REQ-202-REGRESSION-BASELINE-01",
        "status": status,
        "evaluation_mode": mode,
        "performance_claim": mode != "FROZEN_REFERENCE_REPLAY",
        "replay": {
            "golden_path": "tests/regression/golden_set_r1.json",
            "golden_sha256_expected": FROZEN_GOLDEN_SHA256,
            "golden_sha256_actual": golden_hash,
            "responses_sha256": responses_hash,
            "answer_comparison": "Unicode NFC + whitespace collapse + exact match",
        },
        "summary": {
            "passed": passed,
            "failed": len(results) - passed,
            "total": len(results),
            "score": passed / len(results),
            "false_fact_promotions": false_fact_promotions,
            "statement_precision": sum(item["answer_correct"] for item in statement) / len(statement),
            "attribution_precision": sum(item["attribution_correct"] for item in statement) / len(statement),
        },
        "hard_gates": hard_gates,
        "categories": category_summary,
        "items": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# CBA-KB v2.0.2 Regression Baseline Report",
        "",
        f"- Requirement: `{report['requirement']}`",
        f"- Status: **{report['status']}**",
        f"- Evaluation mode: `{report['evaluation_mode']}`",
        f"- Product performance claim: `{str(report['performance_claim']).lower()}`",
        f"- Frozen golden SHA-256: `{report['replay']['golden_sha256_actual']}`",
        f"- Responses SHA-256: `{report['replay']['responses_sha256']}`",
        f"- Score: {summary['passed']}/{summary['total']} ({summary['score']:.2%})",
        f"- False fact promotions: {summary['false_fact_promotions']} (required: 0)",
        f"- Statement precision (record-only): {summary['statement_precision']:.2%}",
        f"- Attribution precision (record-only): {summary['attribution_precision']:.2%}",
        "",
    ]
    if report["evaluation_mode"] == "FROZEN_REFERENCE_REPLAY":
        lines += [
            "> This run verifies frozen-input integrity, scoring, and report reproducibility. ",
            "> It replays the frozen expected answers and is not a product-performance measurement.",
            "",
        ]
    lines += ["## Hard gates", "", "| Gate | Status |", "|---|---|"]
    lines += [f"| {name} | {value['status']} |" for name, value in report["hard_gates"].items()]
    lines += ["", "## Category coverage", "", "| Category | Passed | Total |", "|---|---:|---:|"]
    lines += [
        f"| {name} | {value['passed']} | {value['total']} |"
        for name, value in report["categories"].items()
    ]
    lines += ["", "## Replay", "", "```bash"]
    if report["evaluation_mode"] == "FROZEN_REFERENCE_REPLAY":
        lines.append(
            "python -m tests.regression.harness --reference-replay "
            "--output-json tests/regression/reports/golden_r1_report.json "
            "--output-md tests/regression/reports/golden_r1_report.md"
        )
    else:
        lines.append(
            "python -m tests.regression.harness --responses RESPONSES.json "
            "--output-json REPORT.json --output-md REPORT.md"
        )
    lines += ["```", ""]
    return "\n".join(lines)


def write_reports(report: dict[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_bytes(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
    output_md.write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--responses", type=Path)
    source.add_argument("--reference-replay", action="store_true")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)

    # This is deliberately first: no response read, score, or report before the freeze check.
    golden, golden_hash = load_frozen_golden(args.golden)
    if args.reference_replay:
        responses, responses_hash = reference_responses(golden)
    else:
        responses, responses_hash = load_responses(args.responses, golden)
    report = score(golden, golden_hash, responses, responses_hash)
    write_reports(report, args.output_json, args.output_md)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
