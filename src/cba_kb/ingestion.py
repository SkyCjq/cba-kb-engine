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


def _child_hashes(children):
    if children is None:
        return []
    items = children.items() if isinstance(children, dict) else children
    hashes = {}
    for item in items:
        if isinstance(children, dict):
            article_id, raw = item
            child_sha = content_sha256(raw)
        else:
            if not isinstance(item, dict):
                raise ValueError("CHILD_SNAPSHOT_OBJECT_REQUIRED")
            article_id = item.get("article_id")
            child_sha = item.get("content_sha256")
            if not child_sha and item.get("raw") is not None:
                child_sha = content_sha256(item["raw"])
        if not isinstance(article_id, str) or not article_id:
            raise ValueError("CHILD_ARTICLE_ID_REQUIRED")
        if not isinstance(child_sha, str) or len(child_sha) != 64:
            raise ValueError("CHILD_CONTENT_SHA256_REQUIRED")
        if article_id in hashes:
            raise ValueError("CHILD_ARTICLE_ID_DUPLICATE")
        hashes[article_id] = child_sha
    return [
        {"article_id": article_id, "content_sha256": hashes[article_id]}
        for article_id in sorted(hashes)
    ]


def _snapshot_identity(source, index_sha, children, revision):
    payload = {
        "source_contract_revision": revision,
        "season": source["season"],
        "index_article_id": source["article_id"],
        "index_content_sha256": index_sha,
        "children": _child_hashes(children),
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


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


def build_snapshot(source, raw, rows, *, key_fields, children=None,
                   source_contract_revision="legacy-single-response"):
    sha = content_sha256(raw)
    child_hashes = _child_hashes(children)
    identity = _snapshot_identity(
        source, sha, child_hashes, source_contract_revision,
    )
    ordered = [row for _, row in sorted(_indexed(rows, key_fields).items())]
    return {
        "schema_version": 1,
        "source_id": source["source_id"],
        "season": source["season"],
        "article_id": source["article_id"],
        "index_article_id": source["article_id"],
        "source_contract_revision": source_contract_revision,
        "content_sha256": sha,
        "index_content_sha256": sha,
        "children": child_hashes,
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


def schema_drift(source, reason, *, raw=b"", children=None,
                 source_contract_revision="legacy-single-response"):
    sha = content_sha256(raw)
    child_hashes = _child_hashes(children)
    snapshot = {
        "schema_version": 1,
        "source_id": source["source_id"],
        "season": source["season"],
        "article_id": source["article_id"],
        "index_article_id": source["article_id"],
        "source_contract_revision": source_contract_revision,
        "content_sha256": sha,
        "index_content_sha256": sha,
        "children": child_hashes,
        "snapshot_identity": _snapshot_identity(
            source, sha, child_hashes, source_contract_revision,
        ),
        "row_key_fields": ["season", "child_article_id", "row_index"],
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


def write_evidence(output, *, raw, snapshot, diff, candidate, validation,
                   publish, run_report, raw_files=None):
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
    for name, data in sorted((raw_files or {}).items()):
        target = Path(name)
        if target.is_absolute() or ".." in target.parts or not target.parts:
            raise ValueError("EVIDENCE_RAW_PATH_INVALID")
        artifacts[name] = data
    for name, data in artifacts.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {name: content_sha256(data) for name, data in sorted(artifacts.items())}
