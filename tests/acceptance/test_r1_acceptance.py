"""2.0.3-2 R1 验收检查（Web 出题，Q1 规格 §6）。

来源：ChatGPT Web 独立出题（2026-10-10，GPT-6.1 Sol/Medium），Q1 冻结规格
`2.0.3-2-R1-Q1` §6 全文。验收断言逻辑与出题原文逐行一致，未做任何改动。

落盘适配（仅此一处，与验收逻辑无关）：
出题原文为顶层顺序执行脚本（``python acceptance_r1.py <schema> <manifest>
<report> <oracle>``）；为避免 pytest 采集该文件时直接执行顶层代码，
将执行体包入 ``main()`` 并加 ``if __name__ == "__main__"`` 守卫。
断言、字段集合、锚点算法无任何改动。freeze 绑定的是本落盘文件的哈希。

调用形式（独立验收方手工执行）：
    python tests/acceptance/test_r1_acceptance.py \\
        tests/acceptance/r1_manifest.schema.json \\
        reports/<run_id>.manifest.json \\
        reports/<run_id>.report.md \\
        oracle.json
"""

import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

FIELDS = {
    "file_id", "name", "mime_type", "sha256", "parent_ids",
    "source_id", "document_id", "processing_status",
    "disposition", "final_parent", "evidence_reference",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )


def index_unique(rows, key):
    result = {}
    for row in rows:
        require(row[key] not in result, f"Duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def anchor(file_id):
    return "obj-" + hashlib.sha256(file_id.encode("utf-8")).hexdigest()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    schema_path, manifest_path, report_path, oracle_path = argv
    schema = read_json(schema_path)
    manifest = read_json(manifest_path)
    oracle = read_json(oracle_path)
    report = Path(report_path).read_text(encoding="utf-8")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema, format_checker=FormatChecker()
    ).validate(manifest)

    # Common gate: independent review, not scanner self-attestation.
    review = oracle["runtime_review"]
    require(oracle["run_id"] == manifest["run_id"], "Wrong acceptance run")
    require(bool(review["reviewer"]), "Missing independent reviewer")
    require(bool(review["audit_reference"]), "Missing runtime audit evidence")
    for key in (
        "live_run_verified", "bindings_verified", "enumeration_verified",
        "stable_window_verified", "report_file_verified",
    ):
        require(review[key] is True, f"Runtime review failed: {key}")
    require(review["unauthorized_writes"] == [], "Read-only boundary violated")
    require(manifest["scope_complete"] is True, "Incomplete scope")
    require(manifest["scan_errors"] == [], "Scope enumeration errors")

    objects = index_unique(manifest["objects"], "file_id")
    expected = index_unique(oracle["expected_objects"], "file_id")
    evidence = index_unique(manifest["evidence"], "evidence_id")
    verified = index_unique(oracle["verified_evidence"], "evidence_id")

    # A1: set equality, exactly 11 fields, and independently verified values.
    require(set(objects) == set(expected), "A1: missing or unexpected objects")
    require(evidence == verified, "A1: evidence not independently verified")

    for file_id, obj in objects.items():
        require(set(obj) == FIELDS, f"A1: wrong fields: {file_id}")
        truth = expected[file_id]
        require(set(truth) == FIELDS, f"Invalid oracle fields: {file_id}")
        for field in FIELDS:
            left, right = obj[field], truth[field]
            if field in {"parent_ids", "evidence_reference"}:
                left, right = sorted(left), sorted(right)
            require(left == right, f"A1: wrong {field}: {file_id}")

        require(set(obj["parent_ids"]) <= set(objects), "A1: unresolved parent")
        refs = obj["evidence_reference"]
        require(set(refs) <= set(evidence), "A1: unresolved evidence")
        require(all(evidence[r]["file_id"] == file_id for r in refs),
                "A1: evidence belongs to another object")
        kinds = {evidence[r]["kind"] for r in refs}
        require("OBJECT" in kinds, "A1: missing object locator evidence")
        if obj["source_id"] is not None:
            require("SOURCE" in kinds, "A1: missing Source evidence")
        if obj["document_id"] is not None:
            require("DOCUMENT" in kinds, "A1: missing Document evidence")
        if obj["disposition"] == "PROCESSED":
            require({"PROCESS", "FINAL_LOCATION"} <= kinds,
                    "A1: incomplete completed-disposition evidence")
        if obj["disposition"] == "UNPROCESSED":
            require("PROCESS" in kinds, "A1: missing pending-status evidence")
        if obj["disposition"] == "REVIEW_REQUIRED":
            require("REVIEW_REASON" in kinds, "A1: missing review reason")

    require(all(e["file_id"] in objects for e in evidence.values()),
            "A1: orphan evidence")

    # A2: actual report anchors plus independent semantic report checks.
    checks = oracle["report_checks"]
    require(set(checks["object_row_counts"]) == set(objects),
            "A2: report object set differs")
    require(all(n == 1 for n in checks["object_row_counts"].values()),
            "A2: duplicate or missing report object rows")
    require(checks["summary_counts_correct"] is True, "A2: wrong summary counts")
    require(checks["all_object_rows_match_manifest"] is True,
            "A2: report and manifest disagree")

    for file_id in objects:
        marker = f'<a id="{anchor(file_id)}"></a>'
        require(report.count(marker) == 1, f"A2: missing/duplicate anchor: {file_id}")

    review_ids = {
        fid for fid, obj in objects.items()
        if obj["disposition"] == "REVIEW_REQUIRED"
    }
    entries = index_unique(checks["review_entries"], "file_id")
    require(set(entries) == review_ids, "A2: review entry set differs")
    for fid, entry in entries.items():
        require(entry["anchor"] == anchor(fid), "A2: wrong review anchor")
        for key in ("locator", "reason_code", "detail", "next_check"):
            require(isinstance(entry[key], str) and entry[key].strip(),
                    f"A2: missing {key}: {fid}")
        require(entry["matches_manifest_and_evidence"] is True,
                "A2: review content not verified")
        require(entry["locator_checked"] is True, "A2: locator not checked")

    # A6: at least 10 distinct Human-reviewed objects, covering present values.
    samples = index_unique(oracle["human_samples"], "file_id")
    require(len(samples) >= 10, "A6: fewer than 10 distinct samples")
    require(set(samples) <= set(objects), "A6: sample outside scope")
    require(
        {objects[f]["disposition"] for f in samples}
        == {o["disposition"] for o in objects.values()},
        "A6: disposition coverage incomplete",
    )
    require(
        {objects[f]["processing_status"] for f in samples}
        == {o["processing_status"] for o in objects.values()},
        "A6: status coverage incomplete",
    )
    for fid, sample in samples.items():
        require(bool(sample["reviewer"]) and bool(sample["reviewed_at"]),
                "A6: missing Human review identity/time")
        require(sample["run_id"] == manifest["run_id"], "A6: stale review")
        require(sample["verdict"] == "PASS", f"A6: unresolved difference: {fid}")
        for step in ("source", "process", "final_location"):
            item = sample[step]
            require(item["result"] in {"VERIFIED", "NOT_APPLICABLE", "MISSING"},
                    f"A6: invalid trace result: {fid}/{step}")
            require(bool(item["detail"]) and bool(item["evidence_reference"]),
                    f"A6: missing trace explanation/evidence: {fid}/{step}")

    print("A1/A2/A6 artifact checks satisfied; retain the independent evidence.")


if __name__ == "__main__":
    main()
