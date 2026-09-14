"""Deterministic, facts-only player profiles built from frozen MASTER rows."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import digest
from .evidence_ledger import canonical_bytes
from .master import HEADERS, inspect


SCHEMA_VERSION = 1
PROFILE_VERSION = "v1.7"
PROFILE_STATUSES = frozenset({"READY", "REVIEW_REQUIRED"})
IDENTITY_LOOKUP = "EXACT_NAME_ONLY"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PlayerProfileError(RuntimeError):
    pass


def generator_sha256():
    return digest(Path(__file__).read_bytes())


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PlayerProfileError(f"{label}_REQUIRED")
    return value.strip()


def _required_sha256(value, label):
    value = _required_text(value, label)
    if not _SHA256.fullmatch(value):
        raise PlayerProfileError(f"{label}_SHA256_INVALID")
    return value


def _validated_rows(rows):
    if not isinstance(rows, list):
        raise PlayerProfileError("MASTER_ROWS_REQUIRED")
    validated = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PlayerProfileError(f"MASTER_ROW_OBJECT_REQUIRED:{index}")
        missing = [key for key in HEADERS if key not in row]
        if missing:
            raise PlayerProfileError(f"MASTER_ROW_SCHEMA_INVALID:{index}")
        record_key = row.get("record_key")
        if not isinstance(record_key, str) or not record_key:
            raise PlayerProfileError(f"MASTER_RECORD_KEY_REQUIRED:{index}")
        if record_key in seen:
            raise PlayerProfileError("MASTER_RECORD_KEY_DUPLICATE")
        seen.add(record_key)
        validated.append({key: row[key] for key in HEADERS})
    return validated


def normalize_record_key_selector(value):
    if isinstance(value, dict):
        value = value.get("record_keys")
    if not isinstance(value, list) or not value:
        raise PlayerProfileError("EXPLICIT_RECORD_KEY_SELECTOR_REQUIRED")
    selectors = []
    for item in value:
        selector = _required_text(item, "RECORD_KEY")
        selectors.append(selector)
    if len(set(selectors)) != len(selectors):
        raise PlayerProfileError("EXPLICIT_RECORD_KEY_SELECTOR_DUPLICATE")
    return sorted(selectors)


def load_record_key_selector(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlayerProfileError("RECORD_KEY_SELECTOR_INVALID") from exc
    return normalize_record_key_selector(value)


def discover_exact_name(rows, name):
    """Return candidate rows only; this never establishes person identity."""
    name = _required_text(name, "PLAYER_NAME")
    candidates = [
        row for row in _validated_rows(rows)
        if row.get("player") == name
    ]
    candidates.sort(
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    clubs = {row.get("club_id") for row in candidates}
    if not candidates:
        status = "NOT_FOUND"
    elif len(candidates) == 1 and len(clubs) == 1:
        status = "CANDIDATE_ONLY"
    else:
        status = "REVIEW_REQUIRED"
    return {
        "schema_version": SCHEMA_VERSION,
        "lookup": IDENTITY_LOOKUP,
        "name": name,
        "status": status,
        "candidate_count": len(candidates),
        "candidate_record_keys": [
            row["record_key"] for row in candidates
        ],
        "candidates": candidates,
        "canonical_selector_allowed": False,
        "identity_assertion": "NONE",
    }


def build_profile(
    rows,
    *,
    record_keys,
    release_id,
    as_of,
    source_master_sha256,
    generator_sha=None,
    source_master_file_id=None,
):
    rows = _validated_rows(rows)
    selectors = normalize_record_key_selector(record_keys)
    release_id = _required_text(release_id, "RELEASE_ID")
    as_of = _required_text(as_of, "AS_OF")
    source_master_sha256 = _required_sha256(
        source_master_sha256, "SOURCE_MASTER_SHA256",
    )
    generator_sha = _required_sha256(
        generator_sha or generator_sha256(), "GENERATOR_SHA",
    )
    by_key = {row["record_key"]: row for row in rows}
    missing = sorted(set(selectors) - set(by_key))
    selected = sorted(
        (by_key[key] for key in selectors if key in by_key),
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    status = "READY" if not missing else "REVIEW_REQUIRED"
    core = {
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "status": status,
        "release_id": release_id,
        "as_of": as_of,
        "source_master_sha256": source_master_sha256,
        "source_master_file_id": source_master_file_id,
        "generator_sha": generator_sha,
        "selector_source": "EXPLICIT_RECORD_KEY",
        "record_keys": selectors,
        "rows": selected,
        "coverage": {
            "selector_record_keys": len(selectors),
            "included_record_keys": len(selected),
            "missing_record_keys": missing,
            "duplicate_record_keys": 0,
            "identity_registry_used": False,
        },
    }
    return {**core, "profile_sha256": digest(canonical_bytes(core))}


def build_profile_from_master(
    master,
    *,
    record_keys,
    release_id,
    as_of,
    generator_sha=None,
    source_master_file_id=None,
):
    rows, summary = inspect(master)
    return build_profile(
        rows,
        record_keys=record_keys,
        release_id=release_id,
        as_of=as_of,
        source_master_sha256=summary["sha256"],
        generator_sha=generator_sha,
        source_master_file_id=source_master_file_id,
    )


def validate_profile(value):
    if not isinstance(value, dict):
        raise PlayerProfileError("PROFILE_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "profile_version",
        "status",
        "release_id",
        "as_of",
        "source_master_sha256",
        "generator_sha",
        "selector_source",
        "record_keys",
        "rows",
        "coverage",
        "profile_sha256",
    }
    if not required <= set(value):
        raise PlayerProfileError("PROFILE_INCOMPLETE")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["profile_version"] != PROFILE_VERSION:
        raise PlayerProfileError("PROFILE_VERSION_INVALID")
    if value["status"] not in PROFILE_STATUSES:
        raise PlayerProfileError("PROFILE_STATUS_INVALID")
    if value["selector_source"] != "EXPLICIT_RECORD_KEY":
        raise PlayerProfileError("PROFILE_SELECTOR_SOURCE_INVALID")
    normalize_record_key_selector(value["record_keys"])
    rows = _validated_rows(value["rows"])
    selected_keys = [row["record_key"] for row in rows]
    if len(selected_keys) != len(set(selected_keys)):
        raise PlayerProfileError("PROFILE_RECORD_KEY_DUPLICATE")
    if not set(selected_keys) <= set(value["record_keys"]):
        raise PlayerProfileError("PROFILE_RECORD_KEY_OUT_OF_SCOPE")
    expected_order = sorted(
        rows,
        key=lambda row: (
            str(row.get("season") or ""),
            str(row.get("club_id") or ""),
            str(row.get("record_key") or ""),
        ),
    )
    if rows != expected_order:
        raise PlayerProfileError("PROFILE_SORT_INVALID")
    if "player_uid" in value:
        raise PlayerProfileError("STABLE_PLAYER_UID_FORBIDDEN")
    digest_input = {key: value[key] for key in value if key != "profile_sha256"}
    if digest(canonical_bytes(digest_input)) != value["profile_sha256"]:
        raise PlayerProfileError("PROFILE_HASH_MISMATCH")
    return value
