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

    F-05 fix: If ledger is missing/empty and discovered_count > 0,
    return None (unknown), not 0.0. 0.0 implies "processed none",
    None implies "no data".
    """
    events = _load_ledger(ledger_path)
    # F-05: Distinguish "no ledger data" from "zero processed".
    if not events and discovered_count > 0:
        return {
            "rate_name": "processing_rate",
            "numerator": 0,
            "denominator": discovered_count,
            "rate": None,
            "exclusions": "docs with REVIEW_REQUIRED rights not in denominator",
            "failures": [],
            "note": "No ledger data; rate unknown (not 0.0)",
        }
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

    Numerator: ledger events with action='document_archived' WHERE the
               doc_id was in the approved set.
    Denominator: ledger events with action='archive_approved'.

    F-05 fix: Only count archived IDs that were approved. This prevents
    the rate from exceeding 100% if an unapproved doc was archived
    (which would indicate a policy violation, not a high success rate).
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
    # F-05: Only count archived that were approved.
    valid_archived = archived & approved
    unapproved_archived = archived - approved
    numerator = len(valid_archived)
    denominator = len(approved)
    rate = (numerator / denominator) if denominator > 0 else None
    result = {
        "rate_name": "archive_rate",
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "exclusions": "docs not approved for archive not in denominator",
        "failures": failed,
    }
    if unapproved_archived:
        result["policy_violations"] = sorted(unapproved_archived)
        result["note"] = (f"{len(unapproved_archived)} docs archived "
                          f"without approval (policy violation)")
    return result


def compute_query_hit_rate(ledger_path: Path) -> Dict[str, Any]:
    """Query hit rate: queries with ≥1 result / total queries.

    Numerator: ledger events with action='query' and result_count > 0.
    Denominator: all ledger events with action='query'.

    F-05 fix: Queries missing result_count are excluded from both
    numerator and denominator (unknown, not zero). This prevents
    undercounting hits due to incomplete logging.
    """
    events = _load_ledger(ledger_path)
    total = 0
    hits = 0
    missing_count = 0
    for e in events:
        if e.get("action") == "query":
            if "result_count" not in e:
                missing_count += 1
                continue
            total += 1
            if e.get("result_count", 0) > 0:
                hits += 1
    rate = (hits / total) if total > 0 else None
    result = {
        "rate_name": "query_hit_rate",
        "numerator": hits,
        "denominator": total,
        "rate": rate,
        "exclusions": "non-query ledger events not counted; queries "
                      "missing result_count excluded",
        "failures": [],
    }
    if missing_count:
        result["incomplete_queries"] = missing_count
    return result


def compute_all_rates(ledger_path: Path,
                      discovered_count: int) -> Dict[str, Any]:
    """Compute all three operational rates."""
    return {
        "processing": compute_processing_rate(ledger_path, discovered_count),
        "archive": compute_archive_rate(ledger_path),
        "query_hit": compute_query_hit_rate(ledger_path),
    }
