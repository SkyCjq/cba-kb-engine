"""Tests for operational metrics (R7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cba_kb import ops_metrics as om


def _write_ledger(path: Path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_processing_rate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, [
        {"action": "document_processed", "doc_id": "d1"},
        {"action": "document_processed", "doc_id": "d2"},
        {"action": "document_process_failed", "doc_id": "d3",
         "error": "timeout"},
    ])
    result = om.compute_processing_rate(ledger, discovered_count=4)
    assert result["numerator"] == 2
    assert result["denominator"] == 4
    assert result["rate"] == 0.5
    assert len(result["failures"]) == 1


def test_processing_rate_empty(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("")
    result = om.compute_processing_rate(ledger, discovered_count=0)
    assert result["rate"] is None  # Fail-closed, not 0.


def test_archive_rate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, [
        {"action": "archive_approved", "doc_id": "d1"},
        {"action": "archive_approved", "doc_id": "d2"},
        {"action": "document_archived", "doc_id": "d1"},
    ])
    result = om.compute_archive_rate(ledger)
    assert result["numerator"] == 1
    assert result["denominator"] == 2
    assert result["rate"] == 0.5


def test_query_hit_rate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, [
        {"action": "query", "result_count": 5},
        {"action": "query", "result_count": 0},
        {"action": "query", "result_count": 3},
    ])
    result = om.compute_query_hit_rate(ledger)
    assert result["numerator"] == 2
    assert result["denominator"] == 3
    assert abs(result["rate"] - 0.6667) < 0.001


def test_all_rates(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_ledger(ledger, [
        {"action": "document_processed", "doc_id": "d1"},
        {"action": "query", "result_count": 1},
    ])
    result = om.compute_all_rates(ledger, discovered_count=2)
    assert "processing" in result
    assert "archive" in result
    assert "query_hit" in result


def test_processing_rate_no_ledger_returns_none(tmp_path):
    """F-05: Missing ledger + discovered>0 → None (not 0.0)."""
    ledger = tmp_path / "missing.jsonl"
    result = om.compute_processing_rate(ledger, discovered_count=5)
    assert result["rate"] is None
    assert "unknown" in result.get("note", "").lower()


def test_archive_rate_excludes_unapproved(tmp_path):
    """F-05: Unapproved archived docs don't inflate the rate."""
    ledger = tmp_path / "ledger.jsonl"
    with open(ledger, "w") as f:
        import json
        f.write(json.dumps({"action": "archive_approved", "doc_id": "d1"}) + "\n")
        f.write(json.dumps({"action": "document_archived", "doc_id": "d1"}) + "\n")
        f.write(json.dumps({"action": "document_archived", "doc_id": "d2"}) + "\n")  # Not approved!
    result = om.compute_archive_rate(ledger)
    assert result["numerator"] == 1  # Only d1
    assert result["denominator"] == 1
    assert result["rate"] == 1.0  # Not 2.0!
    assert "d2" in result.get("policy_violations", [])


def test_query_hit_rate_missing_count_excluded(tmp_path):
    """F-05: Queries missing result_count are excluded."""
    ledger = tmp_path / "ledger.jsonl"
    with open(ledger, "w") as f:
        import json
        f.write(json.dumps({"action": "query", "result_count": 5}) + "\n")
        f.write(json.dumps({"action": "query"}) + "\n")  # Missing!
        f.write(json.dumps({"action": "query", "result_count": 0}) + "\n")
    result = om.compute_query_hit_rate(ledger)
    assert result["numerator"] == 1
    assert result["denominator"] == 2  # Missing excluded
    assert result["incomplete_queries"] == 1
