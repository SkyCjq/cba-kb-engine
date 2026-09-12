"""Deterministic, transport-neutral ingestion kernel.

The kernel creates review evidence only. It has no Drive dependency and cannot
promote an observed source change into a canonical business event.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


CHANGE_TYPES = ("add", "delete", "modify", "schema_drift")


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def content_sha256(data):
    if not isinstance(data, bytes):
        raise TypeError("Snapshot content must be bytes")
    return hashlib.sha256(data).hexdigest()


def _indexed(rows, key_fields):
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("NORMALIZED_ROW_OBJECT_REQUIRED")
        key = "|".join(str(row.get(field) or "") for field in key_fields)
        if not all(row.get(field) not in (None, "") for field in key_fields):
            raise ValueError("NORMALIZED_ROW_KEY_REQUIRED")
        if key in result:
            raise ValueError("NORMALIZED_ROW_KEY_COLLISION")
        result[key] = row
    return result


def build_snapshot(source, raw, rows, *, key_fields):
    sha = content_sha256(raw)
    identity = "|".join((source["season"], source["article_id"], sha))
    ordered = [row for _, row in sorted(_indexed(rows, key_fields).items())]
    return {
        "schema_version": 1,
        "source_id": source["source_id"],
        "season": source["season"],
        "article_id": source["article_id"],
        "content_sha256": sha,
        "snapshot_identity": identity,
        "row_key_fields": list(key_fields),
        "rows": ordered,
    }


def diff_snapshots(previous, current):
    if previous is None:
        old = {}
    else:
        if previous.get("source_id") != current.get("source_id"):
            raise ValueError("SNAPSHOT_SOURCE_MISMATCH")
        if previous.get("row_key_fields") != current.get("row_key_fields"):
            return _schema_drift(current, "row_key_fields_changed")
        old = _indexed(previous.get("rows", []), current["row_key_fields"])
    new = _indexed(current.get("rows", []), current["row_key_fields"])
    changes = []
    for key in sorted(old.keys() | new.keys()):
        if key not in old:
            changes.append({"change_type": "add", "row_key": key, "after": new[key]})
        elif key not in new:
            changes.append({"change_type": "delete", "row_key": key, "before": old[key]})
        elif old[key] != new[key]:
            changes.append({
                "change_type": "modify", "row_key": key,
                "before": old[key], "after": new[key],
            })
    counts = Counter(change["change_type"] for change in changes)
    return {
        "schema_version": 1,
        "source_id": current["source_id"],
        "previous_snapshot_identity": previous and previous.get("snapshot_identity"),
        "current_snapshot_identity": current["snapshot_identity"],
        "counts": {name: counts.get(name, 0) for name in CHANGE_TYPES},
        "changes": changes,
    }


def _schema_drift(snapshot, reason):
    change = {"change_type": "schema_drift", "reason": reason}
    return {
        "schema_version": 1,
        "source_id": snapshot["source_id"],
        "previous_snapshot_identity": None,
        "current_snapshot_identity": snapshot.get("snapshot_identity"),
        "counts": {name: int(name == "schema_drift") for name in CHANGE_TYPES},
        "changes": [change],
    }


def schema_drift(source, reason, *, raw=b""):
    snapshot = {
        "schema_version": 1,
        "source_id": source["source_id"],
        "season": source["season"],
        "article_id": source["article_id"],
        "content_sha256": content_sha256(raw),
        "snapshot_identity": "|".join(
            (source["season"], source["article_id"], content_sha256(raw))
        ),
        "row_key_fields": ["season", "article_id", "raw_player_name"],
        "rows": [],
    }
    return snapshot, _schema_drift(snapshot, reason)


def build_candidate(diff):
    changes = []
    for change in diff["changes"]:
        changes.append({
            **change,
            "disposition": "review_required",
            "canonical_write_allowed": False,
            "business_event": None,
            "business_event_date": None,
        })
    return {
        "schema_version": 1,
        "source_id": diff["source_id"],
        "observed_change_is_business_event": False,
        "candidate_count": len(changes),
        "changes": changes,
    }


def validate_candidate(candidate, diff):
    counts = diff["counts"]
    blocked = any(counts[name] for name in ("delete", "modify", "schema_drift"))
    changed = bool(candidate["candidate_count"])
    if any(item.get("canonical_write_allowed") for item in candidate["changes"]):
        raise ValueError("OBSERVED_CHANGE_PROMOTION_FORBIDDEN")
    if any(item.get("business_event_date") for item in candidate["changes"]):
        raise ValueError("UNKNOWN_EVENT_DATE_INFERENCE_FORBIDDEN")
    return {
        "schema_version": 1,
        "source_id": candidate["source_id"],
        "result": "FAIL_CLOSED" if blocked else ("REVIEW_REQUIRED" if changed else "PASS"),
        "canonical_publish_allowed": False,
        "failure_reason": "destructive_or_schema_change" if blocked else None,
        "manual_intervention_count": 0,
    }


def publish_decision(candidate, validation):
    """Close the publish stage without promoting watcher observations."""
    if validation.get("canonical_publish_allowed") is not False:
        raise ValueError("WATCHER_PUBLISH_POLICY_REQUIRED")
    return {
        "schema_version": 1,
        "source_id": candidate["source_id"],
        "result": "NOT_PUBLISHED",
        "publish_attempted": False,
        "canonical_write_performed": False,
        "reason": "observed_change_requires_separate_human_approved_fact_workflow",
        "required_controls": [
            "single_writer", "before_snapshot", "readback", "verify", "rollback",
        ],
    }


def write_evidence(output, *, raw, snapshot, diff, candidate, validation, publish, run_report):
    output = Path(output)
    if output.exists():
        raise ValueError("WATCHER_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    artifacts = {
        "raw.json": raw,
        "snapshot.json": canonical_bytes(snapshot),
        "diff.json": canonical_bytes(diff),
        "candidate.json": canonical_bytes(candidate),
        "validation.json": canonical_bytes(validation),
        "publish.json": canonical_bytes(publish),
        "run_report.json": canonical_bytes(run_report),
    }
    for name, data in artifacts.items():
        (output / name).write_bytes(data)
    return {name: content_sha256(data) for name, data in sorted(artifacts.items())}
