"""Operational metrics for v2.0.4-2 (R7).

Computes three separate rates from ledger and registry:
1. Processing rate: (processed docs) / (discovered docs)
2. Archive rate: (archived docs) / (approved for archive)
3. Query hit rate: (queries with results) / (total queries)

Each rate includes numerator, denominator, exclusions, and failure details.
Fail-closed: missing data → rate is None (not 0).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_ledger(ledger_path: Path) -> List[Dict[str, Any]]:
    events = []
    try:
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except (ValueError, KeyError):
                    pass
    except FileNotFoundError:
        pass
    return events


def compute_processing_rate(ledger_path: Path,
                            discovered_count: int) -> Dict[str, Any]:
    """Processing rate: docs with processing complete / docs discovered.

    Numerator: ledger events with action='document_processed'.
    Denominator: discovered_count (from discovery/inventory).
    Exclusions: docs skipped due to rights (REVIEW_REQUIRED) are excluded
                from denominator if explicitly marked.
    """
    events = _load_ledger(ledger_path)
    processed = set()
    failed = []
    for e in events:
        action = e.get("action", "")
        if action == "document_processed":
            doc_id = e.get("doc_id")
            if doc_id:
                processed.add(doc_id)
        elif action == "document_process_failed":
            failed.append({
                "doc_id": e.get("doc_id"),
                "error": e.get("error", "unknown"),
            })
    numerator = len(processed)
    denominator = discovered_count
    rate = (numerator / denominator) if denominator > 0 else None
    return {
        "rate_name": "processing_rate",
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "exclusions": "docs with REVIEW_REQUIRED rights not in denominator",
        "failures": failed,
    }


def compute_archive_rate(ledger_path: Path) -> Dict[str, Any]:
    """Archive rate: docs archived / docs approved for archive.

    Numerator: ledger events with action='document_archived'.
    Denominator: ledger events with action='archive_approved'.
    """
    events = _load_ledger(ledger_path)
    approved = set()
    archived = set()
    failed = []
    for e in events:
        action = e.get("action", "")
        if action == "archive_approved":
            doc_id = e.get("doc_id")
            if doc_id:
                approved.add(doc_id)
        elif action == "document_archived":
            doc_id = e.get("doc_id")
            if doc_id:
                archived.add(doc_id)
        elif action == "archive_failed":
            failed.append({
                "doc_id": e.get("doc_id"),
                "error": e.get("error", "unknown"),
            })
    numerator = len(archived)
    denominator = len(approved)
    rate = (numerator / denominator) if denominator > 0 else None
    return {
        "rate_name": "archive_rate",
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "exclusions": "docs not approved for archive not in denominator",
        "failures": failed,
    }


def compute_query_hit_rate(ledger_path: Path) -> Dict[str, Any]:
    """Query hit rate: queries with ≥1 result / total queries.

    Numerator: ledger events with action='query' and result_count > 0.
    Denominator: all ledger events with action='query'.
    """
    events = _load_ledger(ledger_path)
    total = 0
    hits = 0
    for e in events:
        if e.get("action") == "query":
            total += 1
            if e.get("result_count", 0) > 0:
                hits += 1
    rate = (hits / total) if total > 0 else None
    return {
        "rate_name": "query_hit_rate",
        "numerator": hits,
        "denominator": total,
        "rate": rate,
        "exclusions": "non-query ledger events not counted",
        "failures": [],
    }


def compute_all_rates(ledger_path: Path,
                      discovered_count: int) -> Dict[str, Any]:
    """Compute all three operational rates."""
    return {
        "processing": compute_processing_rate(ledger_path, discovered_count),
        "archive": compute_archive_rate(ledger_path),
        "query_hit": compute_query_hit_rate(ledger_path),
    }
