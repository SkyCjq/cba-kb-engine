import hashlib
import json
from pathlib import Path

import pytest

from tests.regression import harness


GOLDEN = Path(__file__).with_name("golden_set_r1.json")


def test_golden_frozen():
    raw = GOLDEN.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == harness.FROZEN_GOLDEN_SHA256

    questions, actual_hash = harness.load_frozen_golden(GOLDEN)
    assert actual_hash == harness.FROZEN_GOLDEN_SHA256
    assert len(questions) == 27
    assert {item["category"] for item in questions} == set(harness.EXPECTED_CATEGORY_COUNTS)


def test_hash_mismatch_refuses_to_score_or_write_reports(tmp_path, monkeypatch):
    tampered = tmp_path / "golden.json"
    tampered.write_bytes(GOLDEN.read_bytes() + b" ")
    output_json = tmp_path / "report.json"
    output_md = tmp_path / "report.md"

    score_called = False

    def forbidden_score(*args, **kwargs):
        nonlocal score_called
        score_called = True
        raise AssertionError("score must not run")

    monkeypatch.setattr(harness, "score", forbidden_score)
    with pytest.raises(harness.FrozenGoldenError, match="refusing to parse or score"):
        harness.main(
            [
                "--golden",
                str(tampered),
                "--reference-replay",
                "--output-json",
                str(output_json),
                "--output-md",
                str(output_md),
            ]
        )

    assert not score_called
    assert not output_json.exists()
    assert not output_md.exists()


def test_reference_replay_is_deterministic_and_not_a_performance_claim(tmp_path):
    first_json = tmp_path / "first.json"
    first_md = tmp_path / "first.md"
    second_json = tmp_path / "second.json"
    second_md = tmp_path / "second.md"

    for output_json, output_md in ((first_json, first_md), (second_json, second_md)):
        assert harness.main(
            [
                "--reference-replay",
                "--output-json",
                str(output_json),
                "--output-md",
                str(output_md),
            ]
        ) == 0

    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_md.read_bytes() == second_md.read_bytes()
    report = json.loads(first_json.read_text())
    assert report["evaluation_mode"] == "FROZEN_REFERENCE_REPLAY"
    assert report["performance_claim"] is False
    assert report["summary"]["false_fact_promotions"] == 0
    assert all(gate["status"] == "PASS" for gate in report["hard_gates"].values())


def test_candidate_failure_is_scored_without_changing_golden(tmp_path):
    golden, golden_hash = harness.load_frozen_golden()
    responses, responses_hash = harness.reference_responses(golden)
    responses["mode"] = "CANDIDATE"
    responses["items"][0]["actual_answer"] = "错误答案"
    responses["items"][0]["false_fact_promotion"] = True

    report = harness.score(golden, golden_hash, responses, responses_hash)

    assert report["status"] == "FAIL"
    assert report["summary"]["passed"] == 26
    assert report["summary"]["false_fact_promotions"] == 1
    assert report["hard_gates"]["false_fact_promotion"]["status"] == "FAIL"
