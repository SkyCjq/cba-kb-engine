"""Planned, fail-closed watcher for official CBA registration snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import time

from .aliases import Clubs
from .adapters import cba_registration
from .ingestion import (
    build_candidate, build_snapshot, canonical_bytes, diff_snapshots,
    publish_decision, schema_drift, validate_candidate, write_evidence,
)


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _previous(path):
    if path is None:
        return None
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("PREVIOUS_SNAPSHOT_INVALID") from exc
    if not isinstance(value, dict):
        raise RuntimeError("PREVIOUS_SNAPSHOT_OBJECT_REQUIRED")
    return value


def run_source(source, output, *, previous_snapshot=None, fetcher=None,
               started_at=None, ended_at=None, sleeper=time.sleep,
               randomizer=None, clubs=None):
    """Fetch one season discovery root plus every discovered child publication."""
    started = started_at or datetime.now(timezone.utc)
    index_raw = b"{}\n"
    child_raw = {}
    children = []
    requests = 0
    failure = None
    rows = []
    rng = randomizer or random.Random()
    clubs = clubs or Clubs(Path(__file__).resolve().parents[2])
    revision = cba_registration.SOURCE_CONTRACT_REVISION
    try:
        index_raw, attempts = (fetcher or cba_registration.fetch)(source)
        requests += attempts
        children = cba_registration.discover(index_raw, source)
        for child in children:
            sleeper(cba_registration.MIN_DELAY_SECONDS + rng.uniform(0, .5))
            raw, attempts = (fetcher or cba_registration.fetch)(child)
            requests += attempts
            child_raw[child["article_id"]] = raw
        rows = cba_registration.normalize_children(
            child_raw, children, source, clubs=clubs,
        )
        snapshot = build_snapshot(
            source, index_raw, rows, key_fields=cba_registration.ROW_KEY_FIELDS,
            children=child_raw, source_contract_revision=revision,
        )
        diff = diff_snapshots(_previous(previous_snapshot), snapshot)
    except (cba_registration.SourceSchemaDrift, ValueError) as exc:
        failure = str(exc)
        snapshot, diff = schema_drift(
            source, failure, raw=index_raw, children=child_raw,
            source_contract_revision=revision,
        )
    except cba_registration.SourceFetchClosed as exc:
        requests += exc.requests
        failure = f"fetch_fail_closed:{exc}"
        snapshot, diff = schema_drift(
            source, "fetch_fail_closed", raw=index_raw, children=child_raw,
            source_contract_revision=revision,
        )
    candidate = build_candidate(diff)
    validation = validate_candidate(candidate, diff)
    if failure:
        validation.update(result="FAIL_CLOSED", failure_reason=failure)
    publish = publish_decision(candidate, validation)
    ended = ended_at or datetime.now(timezone.utc)
    wall_clock = max(0.0, (ended - started).total_seconds())
    counts = diff["counts"]
    report = {
        "schema_version": 1,
        "run_id": "watcher-" + hashlib.sha256(
            canonical_bytes([source["source_id"], _utc(started)])
        ).hexdigest()[:16],
        "source_id": source["source_id"],
        "started_at": _utc(started),
        "ended_at": _utc(ended),
        "wall_clock_seconds": wall_clock,
        "request_count": requests,
        "provider_tokens": None,
        "snapshot_hash": snapshot["content_sha256"],
        "source_contract_revision": revision,
        "child_count": len(children),
        "canonical_team_count": len({
            row["canonical_team_id"] for row in rows
        }),
        "diff_counts": {name: counts[name] for name in sorted(counts)},
        "candidate_count": candidate["candidate_count"],
        "validation_result": validation["result"],
        "failure_reason": validation["failure_reason"],
        "manual_intervention_count": 0,
        "manual_repair_required": False,
        "release_id": None,
        "candidate_id": "candidate-" + snapshot["content_sha256"][:16],
        "canonical_publish_allowed": False,
    }
    hashes = write_evidence(
        output, raw=index_raw, snapshot=snapshot, diff=diff, candidate=candidate,
        validation=validation, publish=publish, run_report=report,
        raw_files={
            f"children/{article_id}.json": raw
            for article_id, raw in child_raw.items()
        },
    )
    return {"report": report, "artifact_sha256": hashes}


def run_planned(instance, output, *, previous_root=None, sleeper=time.sleep,
                randomizer=None, fetcher=None, clubs=None):
    """Run the three frozen sources sequentially inside private instance data."""
    output = Path(output).resolve()
    try:
        output.relative_to(instance.data_root)
    except ValueError as exc:
        raise RuntimeError("WATCHER_OUTPUT_MUST_BE_PRIVATE_INSTANCE_DATA") from exc
    if output.exists():
        raise ValueError("WATCHER_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    rng = randomizer or random.Random()
    reports = []
    sources = list(cba_registration.registry().values())
    for index, source in enumerate(sources):
        if index:
            sleeper(cba_registration.MIN_DELAY_SECONDS + rng.uniform(0, .5))
        previous = None
        if previous_root:
            previous = Path(previous_root) / source["source_id"] / "snapshot.json"
        result = run_source(
            source, output / source["source_id"],
            previous_snapshot=previous, fetcher=fetcher, sleeper=sleeper,
            randomizer=rng, clubs=clubs,
        )
        reports.append(result["report"])
    ledger = {
        "schema_version": 1,
        "cadence": "DAILY@10:00 Asia/Shanghai",
        "planned_source_count": 3,
        "runs": reports,
        "all_fail_closed_or_reviewed": all(
            report["validation_result"] in {"PASS", "REVIEW_REQUIRED", "FAIL_CLOSED"}
            and report["canonical_publish_allowed"] is False
            for report in reports
        ),
    }
    (output / "planned_run_ledger.json").write_bytes(canonical_bytes(ledger))
    return ledger
