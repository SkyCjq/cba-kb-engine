"""Fail-closed production release orchestration: project, reserve, freeze.

Only ``reserve-staging`` may mutate Drive. ``project`` and ``freeze`` are
read-only with respect to remote production objects.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import subprocess

import yaml

from cba_kb.common import atomic, digest, read, save
from cba_kb.canonical_registry import load_registry
from cba_kb.current_state import (
    END as CURRENT_STATE_END, generate_context_card,
    current_version_document_migration, replace_current_block,
    target_metadata,
)
from cba_kb.drive import Drive
from cba_kb.instance import load_instance
from cba_kb.native import wrap
from cba_kb.release import (
    _inventory, fingerprint, prepare as prepare_release, snapshot,
)


RELEASE_ID = "v1.5.5-1"
PRODUCT_BASELINE_SHA = "c14a0f2579fcc86e2dc114f0b15d00dd54e9f55e"
RELEASE_SPECS = {
    RELEASE_ID: {
        "product_baseline_sha": PRODUCT_BASELINE_SHA,
    },
    "v1.6.0-1": {
        "product_baseline_sha": "0b9c6e062616fc8d4349304ea483afdd917ce181",
    },
    "v1.6.1-1": {
        "product_baseline_sha": "4fab3d0e8eedc594fae982f12a507fef88958f15",
    },
    "v1.8.0-1": {
        "product_baseline_sha": "81bd581fafbccb602f9ecaf9aaefca4533be69a4",
    },
    "v1.8.1-1": {
        "product_baseline_sha": "9cd5dab298012eadaf8345f3f9d2709a2b5c2288",
    },
}
FOLDER = "application/vnd.google-apps.folder"
NATIVE_DOCUMENT = "application/vnd.google-apps.document"
REGISTRY_KEY = "config/canonical_products.yaml"
MANIFEST_KEY = "input/manifest.csv"
CONTEXT_CARD_KEY = "ai/CONTEXT_CARD.md"
FORBIDDEN_CODE_PREFIXES = ("workspace/", ".credentials/", ".venv/")
CONTROL_KEYS = frozenset({
    "control/drive_map.yaml",
    "entry/code",
    "entry/README",
    "entry/context",
    "ai/CONTEXT_CARD.md",
    "CURRENT_VERSION_DOC",
    "derived/INDEX.md",
    "input/manifest.csv",
    "config/canonical_products.yaml",
})


class ProjectionError(RuntimeError):
    pass


def _json_hash(value):
    return digest(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode())


def _release_spec(release_id):
    try:
        return RELEASE_SPECS[release_id]
    except KeyError:
        raise ProjectionError(f"RELEASE_ID_FORBIDDEN:{release_id}") from None


def _require_release(release_id):
    return _release_spec(release_id)


def _private_output(instance, output):
    output = Path(output).resolve()
    try:
        output.relative_to(instance.data_root)
    except ValueError as exc:
        raise ProjectionError("OUTPUT_MUST_BE_PRIVATE_INSTANCE_DATA") from exc
    return output


def verify_execution_sha(
    engine_root,
    engine_sha,
    product_baseline_sha=PRODUCT_BASELINE_SHA,
):
    if not isinstance(engine_sha, str) or not re.fullmatch(
        "[0-9a-f]{40}", engine_sha,
    ):
        raise ProjectionError("CODE_PROVENANCE_MISMATCH")
    engine_root = Path(engine_root)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=engine_root, text=True,
    ).strip()
    if head != engine_sha:
        raise ProjectionError("CODE_PROVENANCE_MISMATCH")
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=engine_root, text=True,
    ).strip():
        raise ProjectionError("CODE_PROVENANCE_MISMATCH")
    if subprocess.run(
        [
            "git", "merge-base", "--is-ancestor",
            product_baseline_sha, engine_sha,
        ],
        cwd=engine_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise ProjectionError("CODE_PROVENANCE_MISMATCH")
    return {
        "head": head,
        "clean": True,
        "product_baseline_sha": product_baseline_sha,
        "baseline_is_ancestor": True,
    }


def _status_rows(status, targets):
    artifacts = status.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ProjectionError("PREVIOUS_STATUS_ARTIFACTS_REQUIRED")
    by_id = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise ProjectionError("PREVIOUS_STATUS_ARTIFACT_INVALID")
        file_id = item.get("id")
        name = item.get("name")
        sha = item.get("sha256")
        if not file_id or not name or not sha:
            raise ProjectionError("PREVIOUS_STATUS_ARTIFACT_INCOMPLETE")
        if file_id in by_id:
            raise ProjectionError("PREVIOUS_STATUS_ARTIFACT_DUPLICATE")
        if file_id not in targets:
            raise ProjectionError("PREVIOUS_STATUS_TARGET_MISMATCH")
        by_id[file_id] = item
    return by_id


def _manifest_rows(rows):
    by_id = {}
    for row in rows:
        file_id = row.get("drive_file_id")
        logical_key = row.get("uid") or row.get("local_path")
        if not file_id or not logical_key:
            raise ProjectionError("MANIFEST_ROW_INCOMPLETE")
        if file_id in by_id:
            raise ProjectionError("MANIFEST_TARGET_DUPLICATE")
        by_id[file_id] = logical_key
    return by_id


def project_targets(
    *,
    release_id,
    engine_sha,
    status_id,
    archive_id,
    previous_status,
    previous_targets,
    manifest_rows,
    tracked,
    tracked_hashes,
    code_parent,
    status_hash=None,
    status_meta=None,
    allocation=None,
):
    """Project exact target semantics without any remote mutation."""
    _require_release(release_id)
    migration = current_version_document_migration(manifest_rows, release_id)
    manifest_rows = migration["manifest"]
    status_by_id = _status_rows(previous_status, previous_targets)
    logical_by_id = _manifest_rows(manifest_rows)
    if set(status_by_id) - set(logical_by_id):
        raise ProjectionError("MANIFEST_MAPPING_MISSING")
    active_ids = set(status_by_id)
    reserved_ids = set()
    reserved_keys = {}
    if allocation is not None:
        if allocation.get("release_id") != release_id:
            raise ProjectionError("RESERVATION_RELEASE_MISMATCH")
        if allocation.get("status_id") != status_id:
            raise ProjectionError("RESERVATION_STATUS_MISMATCH")
        reserved_keys = dict(allocation.get("reservations") or {})
        reserved_ids = set(reserved_keys.values())
        if len(reserved_ids) != len(reserved_keys):
            raise ProjectionError("RESERVATION_ID_DUPLICATE")
    policy_ids = set(previous_targets)
    extra_policy_ids = policy_ids - active_ids
    if not extra_policy_ids and reserved_ids:
        raise ProjectionError("RESERVATION_POLICY_TARGETS_MISSING")
    if extra_policy_ids != reserved_ids:
        raise ProjectionError("UNEXPLAINED_RESERVED_POLICY_TARGET")
    reserved_by_id = {file_id: key for key, file_id in reserved_keys.items()}

    existing = {}
    logical_seen = set()
    for file_id, item in status_by_id.items():
        logical_key = logical_by_id[file_id]
        if logical_key in logical_seen:
            raise ProjectionError("LOGICAL_KEY_DUPLICATE")
        logical_seen.add(logical_key)
        policy = previous_targets[file_id]
        existing[logical_key] = {
            "logical_key": logical_key,
            "id": file_id,
            "name": item["name"],
            "previous_sha256": item["sha256"],
            "mime": policy["mime"],
            "mode": policy.get("mode", "binary"),
            "allowed_parents": list(policy.get("allowed_parents") or []),
            "publish_parent": policy.get("publish_parent"),
            "staging_parent": policy.get("staging_parent"),
        }
    if set(reserved_by_id) & set(existing):
        raise ProjectionError("RESERVATION_REUSES_EXISTING_TARGET")
    logical_by_id.update(reserved_by_id)

    tracked = sorted(set(tracked))
    for relative in tracked:
        if relative.startswith(FORBIDDEN_CODE_PREFIXES):
            raise ProjectionError(f"FORBIDDEN_CODE_MIRROR:{relative}")
        if relative not in tracked_hashes:
            raise ProjectionError(f"TRACKED_HASH_MISSING:{relative}")

    current_code = {
        f"code/{relative}": {
            "relative": relative,
            "sha256": tracked_hashes[relative],
        }
        for relative in tracked
    }
    existing_code_keys = {
        key for key in existing if key.startswith("code/")
    }
    non_code_keys = set(existing) - existing_code_keys
    control_keys = non_code_keys & CONTROL_KEYS
    business_keys = non_code_keys - CONTROL_KEYS
    removed_code_keys = sorted(
        key for key in existing_code_keys if key not in current_code
    )
    if removed_code_keys:
        raise ProjectionError(
            "REMOVED_TARGET_POLICY_REQUIRED:" + ",".join(removed_code_keys)
        )

    unchanged = []
    modified = []
    reused = []
    for logical_key in sorted(existing_code_keys & set(current_code)):
        item = existing[logical_key]
        reused.append(logical_key)
        if item["previous_sha256"] == current_code[logical_key]["sha256"]:
            unchanged.append(logical_key)
        else:
            modified.append(logical_key)

    new_keys = sorted(set(current_code) - existing_code_keys)
    if allocation is not None and set(new_keys) != set(reserved_keys):
        raise ProjectionError("RESERVATION_KEY_SET_MISMATCH")
    if not code_parent:
        raise ProjectionError("CODE_PARENT_REQUIRED")
    new_targets = [
        {
            "logical_key": logical_key,
            "name": current_code[logical_key]["relative"].replace("/", "__"),
            "mime": "text/plain",
            "mode": "binary",
            "publish_parent": code_parent,
            "sha256": current_code[logical_key]["sha256"],
            "relative": current_code[logical_key]["relative"],
            **(
                {"id": reserved_keys[logical_key]}
                if logical_key in reserved_keys else {}
            ),
        }
        for logical_key in new_keys
    ]
    existing_targets = []
    for logical_key in sorted(existing):
        item = dict(existing[logical_key])
        if logical_key in current_code:
            item["current_sha256"] = current_code[logical_key]["sha256"]
            item["disposition"] = (
                "modified" if logical_key in modified else "unchanged"
            )
        elif logical_key in control_keys:
            item["disposition"] = "metadata_update"
        else:
            item["disposition"] = "carried_forward"
        existing_targets.append(item)

    final_keys = sorted(non_code_keys | set(current_code))
    if len(final_keys) != len(set(final_keys)):
        raise ProjectionError("LOGICAL_KEY_COLLISION")
    projection = {
        "schema_version": 1,
        "state": "PROJECTED",
        "release_id": release_id,
        "engine_sha": engine_sha,
        "status_id": status_id,
        "archive_id": archive_id,
        "previous_artifact_count": len(previous_status["artifacts"]),
        "reused_target_count": len(reused) + len(non_code_keys),
        "new_target_count": len(new_targets),
        "removed_target_count": len(removed_code_keys),
        "carried_forward_count": len(business_keys),
        "final_artifact_count": len(final_keys),
        "existing_targets": existing_targets,
        "new_targets": new_targets,
        "removed_targets": removed_code_keys,
        "modified_content_targets": modified,
        "unchanged_targets": unchanged,
        "carried_forward_artifacts": sorted(business_keys),
        "active_target_ids": sorted(active_ids),
        "active_production_target_count": len(active_ids),
        "reserved_policy_target_ids": sorted(reserved_ids),
        "reserved_staging_target_count": len(reserved_ids),
        "release_status_unchanged": True,
        "control_target_migrations": (
            [] if migration["report"]["status"] == "NOT_APPLICABLE"
            else [migration["report"]]
        ),
        "status_before_hash": status_hash,
        "status_before_meta": status_meta,
        "active_release_id": previous_status.get("current_release_id"),
    }
    projection["semantic_delta_signature"] = _json_hash({
        "keys": final_keys,
        "new": new_keys,
        "modified": modified,
        "removed": removed_code_keys,
        "counts": {
            "previous": projection["previous_artifact_count"],
            "final": projection["final_artifact_count"],
        },
    })
    return projection


def reproject_targets(projection, allocation):
    """Invalidate a pre-reservation projection and bind it to the reservation."""
    validate_reservation(projection, allocation)
    result = dict(projection)
    result.update({
        "state": "REPROJECTED",
        "reservation_state": "RESERVED",
        "reservation_semantic_delta_signature": allocation.get(
            "semantic_delta_signature",
        ),
        "reserved_target_ids": sorted(
            set((allocation.get("reservations") or {}).values()),
        ),
    })
    return result


def validate_manifest_coverage(planned_targets, resolved_targets,
                               manifest_targets, publish_targets):
    from cba_kb.release import manifest_coverage
    return manifest_coverage(
        planned_targets, resolved_targets, manifest_targets, publish_targets,
    )


def validate_reservation(projection, allocation):
    """Prove a reservation only covers projected NEW keys."""
    if projection.get("release_id") != allocation.get("release_id"):
        raise ProjectionError("RESERVATION_RELEASE_MISMATCH")
    if projection.get("semantic_delta_signature") != allocation.get(
        "semantic_delta_signature",
    ):
        raise ProjectionError("RESERVATION_PROJECTION_DRIFT")
    expected = {item["logical_key"] for item in projection["new_targets"]}
    reserved = allocation.get("reservations")
    if not isinstance(reserved, dict) or set(reserved) != expected:
        raise ProjectionError("RESERVATION_KEY_SET_MISMATCH")
    existing_ids = {item["id"] for item in projection["existing_targets"]}
    if len(set(reserved.values())) != len(reserved):
        raise ProjectionError("RESERVATION_ID_DUPLICATE")
    if existing_ids & set(reserved.values()):
        raise ProjectionError("RESERVATION_REUSES_EXISTING_TARGET")
    return True


def validate_policy_reconciliation(projection, allocation, policy):
    active_ids = {
        item["id"] for item in projection["existing_targets"]
    }
    reserved_ids = set(allocation["reservations"].values())
    actual_ids = set((policy.get("targets") or {}).keys())
    if actual_ids not in (active_ids, active_ids | reserved_ids):
        raise ProjectionError("PRIVATE_PRODUCTION_TARGET_DRIFT")
    for item in projection["existing_targets"]:
        current = (policy.get("targets") or {})[item["id"]]
        expected = {
            "mime": item["mime"],
            "mode": item["mode"],
            "allowed_parents": item["allowed_parents"],
        }
        if item.get("publish_parent") is not None:
            expected["publish_parent"] = item["publish_parent"]
        if item.get("staging_parent") is not None:
            expected["staging_parent"] = item["staging_parent"]
        if any(current.get(key) != value for key, value in expected.items()):
            raise ProjectionError("ACTIVE_PRODUCTION_TARGET_CHANGED")
    return {
        "active_production_targets": sorted(active_ids),
        "reserved_staging_targets": sorted(reserved_ids),
    }


def validate_release_state(drive, instance, projection, allocation):
    try:
        validate_reservation(projection, allocation)
        status_raw, status_meta = snapshot(
            drive, allocation["status_id"],
        )
        status = json.loads(status_raw)
    except Exception as exc:
        raise ProjectionError("STAGE4_RELEASE_STATE_DRIFT") from exc
    if digest(status_raw) != allocation.get("status_before_hash"):
        raise ProjectionError("STAGE4_RELEASE_STATE_DRIFT")
    if fingerprint(status_meta) != allocation.get("status_before_meta"):
        raise ProjectionError("STAGE4_RELEASE_STATE_DRIFT")
    active_ids = {
        item["id"] for item in projection["existing_targets"]
    }
    status_ids = {
        item.get("id") for item in (status.get("artifacts") or [])
    }
    state = status.get("state")
    readable_state = state == "COMPLETE"
    if (
        projection.get("release_id") == "v1.6.1-1"
        and state == "ROLLED_BACK"
    ):
        readable_state = (
            status.get("rolled_back_release_id") == "v1.6.1-1"
        )
    if (
        not readable_state
        or status.get("current_release_id") != allocation.get("active_release_id")
        or status_ids != active_ids
    ):
        raise ProjectionError("STAGE4_RELEASE_STATE_DRIFT")
    policy = instance.read_json("production.json")
    reconciliation = validate_policy_reconciliation(
        projection, allocation, policy,
    )
    return {
        "result": "PASS",
        "state": state,
        "rolled_back_release_id": status.get("rolled_back_release_id"),
        "status_sha256": digest(status_raw),
        "status_meta": fingerprint(status_meta),
        "active_production_target_count": len(active_ids),
        "reserved_staging_target_count": len(allocation["reservations"]),
        "status": status,
        **reconciliation,
    }


def reserve_staging(
    drive,
    instance,
    *,
    release_id,
    projection,
    output,
    single_writer,
):
    """Create only projected NEW keys below the release staging folder."""
    _require_release(release_id)
    if not single_writer:
        raise ProjectionError("SINGLE_WRITER_REQUIRED")
    if projection.get("release_id") != release_id:
        raise ProjectionError("RESERVATION_RELEASE_MISMATCH")
    output = Path(output)
    allocation_path = output / "allocation.json"
    if allocation_path.is_file():
        existing = read(allocation_path)
        staging_id = existing.get("staging_id")
        reservations = dict(existing.get("reservations") or {})
    else:
        staging_id = None
        reservations = {}
    if not staging_id:
        staging_id = drive.ensure(
            projection["archive_id"],
            f"{release_id}-staging",
            f"{release_id}_staging",
            FOLDER,
        )
    for item in sorted(
        projection["new_targets"], key=lambda value: value["logical_key"],
    ):
        key = item["logical_key"]
        file_id = drive.ensure(
            staging_id,
            _reservation_key(release_id, key),
            item["name"],
            item["mime"],
            b"",
        )
        meta = drive.meta(file_id)
        if meta.get("mimeType") != item["mime"]:
            raise ProjectionError("RESERVED_TARGET_MIME_MISMATCH")
        if staging_id not in (meta.get("parents") or []):
            raise ProjectionError("RESERVED_TARGET_NOT_IN_STAGING")
        reservations[key] = file_id
        output.mkdir(parents=True, exist_ok=True)
        save(output / "allocation-progress.json", {
            "release_id": release_id,
            "staging_id": staging_id,
            "reservations": reservations,
        })
    allocation = {
        "schema_version": 1,
        "state": "RESERVED",
        "release_id": release_id,
        "status_id": projection["status_id"],
        "archive_id": projection["archive_id"],
        "staging_id": staging_id,
        "semantic_delta_signature": projection["semantic_delta_signature"],
        "status_before_hash": projection.get("status_before_hash"),
        "status_before_meta": projection.get("status_before_meta"),
        "active_release_id": projection.get("active_release_id"),
        "active_production_targets": sorted(
            item["id"] for item in projection["existing_targets"]
        ),
        "reserved_staging_targets": sorted(reservations.values()),
        "reservations": reservations,
    }
    validated = validate_reservation(projection, allocation)
    policy = instance.read_json("production.json")
    validate_policy_reconciliation(projection, allocation, policy)
    for item in projection["new_targets"]:
        file_id = allocation["reservations"][item["logical_key"]]
        policy["targets"][file_id] = {
            "mime": item["mime"],
            "mode": item["mode"],
            "allowed_parents": [
                item["publish_parent"], allocation["staging_id"],
            ],
            "staging_parent": allocation["staging_id"],
            "publish_parent": item["publish_parent"],
        }
    save(instance.config_path("production.json"), policy)
    save(allocation_path, allocation)
    return {"allocation": allocation, "validated": validated}


def _reservation_key(release_id, logical_key):
    """Keep existing keys unless Drive's 124-byte app-property limit requires a digest."""
    key = f"reserve:{release_id}:{logical_key}"
    if len(("cba_key" + key).encode("utf-8")) <= 124:
        return key
    return f"reserve:{release_id}:sha256:{digest(logical_key.encode('utf-8'))}"


def _candidate_inputs(drive, engine_root, projection, allocation, output):
    output.mkdir(parents=True, exist_ok=False)
    candidates = output / "candidate-inputs"
    candidates.mkdir()
    entries = []
    existing_by_key = {
        item["logical_key"]: item for item in projection["existing_targets"]
    }
    new_by_key = {
        item["logical_key"]: item for item in projection["new_targets"]
    }
    id_by_key = {
        item["logical_key"]: item["id"]
        for item in projection["existing_targets"]
    }
    id_by_key.update({
        key: file_id for key, file_id in allocation["reservations"].items()
    })
    code_hashes = {
        item["logical_key"]: item.get("current_sha256") or item["sha256"]
        for item in projection["existing_targets"]
        if item["logical_key"].startswith("code/")
    }
    code_hashes.update({
        item["logical_key"]: item["sha256"]
        for item in projection["new_targets"]
    })
    ordered = sorted(set(existing_by_key) | set(new_by_key))
    item_by_key = {
        key: existing_by_key.get(key) or new_by_key[key] for key in ordered
    }
    registry = None
    registry_bytes = None
    manifest_previous = None
    if projection["release_id"] in {"v1.6.1-1", "v1.8.1-1"}:
        if REGISTRY_KEY not in item_by_key or MANIFEST_KEY not in item_by_key:
            raise ProjectionError("CLOSURE_CONTROL_TARGET_MISSING")
        registry_previous, _ = snapshot(
            drive, item_by_key[REGISTRY_KEY]["id"],
            item_by_key[REGISTRY_KEY].get("mode", "binary"),
        )
        registry = load_registry(registry_previous)
        registry["registry_release_id"] = projection["release_id"]
        registry_bytes = yaml.safe_dump(
            registry, allow_unicode=True, sort_keys=False,
        ).encode()
        manifest_previous, _ = snapshot(
            drive, item_by_key[MANIFEST_KEY]["id"],
            item_by_key[MANIFEST_KEY].get("mode", "binary"),
        )
    data_by_key = {}
    previous_by_key = {}
    for logical_key in ordered:
        if logical_key == "input/manifest.csv":
            continue
        item = item_by_key[logical_key]
        if logical_key.startswith("code/"):
            relative = logical_key.removeprefix("code/")
            data = (Path(engine_root) / relative).read_bytes()
            generated = True
        else:
            previous, _ = snapshot(
                drive, item["id"], item.get("mode", "binary"),
            )
            previous_by_key[logical_key] = previous
            generated_data = _control_candidate(
                logical_key, previous, projection, allocation, id_by_key,
                code_hashes, engine_root, registry, registry_bytes,
                manifest_previous,
            )
            generated = generated_data is not None
            data = generated_data if generated else previous
        if generated and item.get("mode") == "managed_doc":
            data = _managed_candidate(data)
        data_by_key[logical_key] = data
    if MANIFEST_KEY in item_by_key:
        if manifest_previous is None:
            manifest_previous, _ = snapshot(
                drive, item_by_key[MANIFEST_KEY]["id"],
                item_by_key[MANIFEST_KEY].get("mode", "binary"),
            )
        previous_by_key[MANIFEST_KEY] = manifest_previous
        data_by_key[MANIFEST_KEY] = _manifest_candidate(
            manifest_previous, projection, allocation, id_by_key,
            data_by_key, item_by_key,
        )
    for index, logical_key in enumerate(ordered):
        item = item_by_key[logical_key]
        candidate = candidates / str(index)
        data = data_by_key[logical_key]
        atomic(candidate, data)
        if logical_key in new_by_key:
            change_class = "NEW_CODE_MIRROR"
        elif logical_key in CONTROL_KEYS:
            change_class = "CONTROL_METADATA_UPDATE"
        elif logical_key.startswith("code/"):
            change_class = "CODE_MIRROR_UPDATE"
        else:
            change_class = "BUSINESS_FACT_CARRY_FORWARD"
        entry = {
            "logical_key": logical_key,
            "id": item.get("id") or allocation["reservations"][logical_key],
            "name": item["name"],
            "mime": item["mime"],
            "mode": item.get("mode", "binary"),
            "path": str(candidate),
            "change_class": change_class,
            "before_hash": (
                digest(previous_by_key[logical_key])
                if logical_key in previous_by_key else None
            ),
        }
        if logical_key in new_by_key:
            entry.update({
                "allowed_parents": [
                    new_by_key[logical_key]["publish_parent"],
                    allocation["staging_id"],
                ],
                "staging_parent": allocation["staging_id"],
                "publish_parent": new_by_key[logical_key]["publish_parent"],
            })
        else:
            entry["allowed_parents"] = item.get("allowed_parents") or []
            if item.get("staging_parent"):
                entry["staging_parent"] = item["staging_parent"]
            if item.get("publish_parent"):
                entry["publish_parent"] = item["publish_parent"]
        entries.append(entry)
    return entries


def _manifest_candidate(previous, projection, allocation, id_by_key,
                        data_by_key, item_by_key):
    reader = csv.DictReader(io.StringIO(previous.decode("utf-8-sig")))
    if not reader.fieldnames:
        raise ProjectionError("MANIFEST_SCHEMA_REQUIRED")
    columns = list(reader.fieldnames)
    for name in ("published_release", "hash_scope"):
        if name not in columns:
            columns.append(name)
    rows = list(reader)
    by_id = {}
    by_key = {}
    for row in rows:
        file_id = row.get("drive_file_id")
        logical_key = row.get("uid") or row.get("local_path")
        if not file_id or not logical_key:
            raise ProjectionError("MANIFEST_ROW_INCOMPLETE")
        if file_id in by_id or logical_key in by_key:
            raise ProjectionError("MANIFEST_DUPLICATE")
        by_id[file_id] = row
        by_key[logical_key] = row
    final_keys = sorted(id_by_key)
    if set(final_keys) != set(item_by_key):
        raise ProjectionError("MANIFEST_COVERAGE_MISMATCH")
    output_rows = []
    for logical_key in final_keys:
        file_id = id_by_key[logical_key]
        row = dict(by_key.get(logical_key) or by_id.get(file_id) or {})
        row["drive_file_id"] = file_id
        row["uid"] = logical_key
        row["published_release"] = projection["release_id"]
        mode = item_by_key[logical_key].get("mode", "binary")
        row["hash_scope"] = "managed_prefix" if mode == "managed_doc" else "bytes"
        if logical_key == "input/manifest.csv":
            row["content_hash"] = ""
        elif logical_key in data_by_key:
            row["content_hash"] = digest(data_by_key[logical_key])
        output_rows.append({name: row.get(name, "") for name in columns})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=columns, lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(output_rows)
    return stream.getvalue().encode("utf-8-sig")


def _managed_candidate(data):
    return wrap(data.decode()).encode()


def _control_candidate(logical_key, previous, projection, allocation,
                       id_by_key, code_hashes, engine_root, registry,
                       registry_bytes, manifest_bytes):
    release_id = projection["release_id"]
    engine_sha = projection["engine_sha"]
    status_id = allocation["status_id"]
    if logical_key == REGISTRY_KEY:
        return registry_bytes if registry_bytes is not None else None
    if logical_key == "control/drive_map.yaml":
        mapping = yaml.safe_load(previous.decode())
        mapping["v1_5_release"] = {
            "release_id": release_id,
            "status_id": status_id,
            "code_commit": engine_sha,
            "targets": id_by_key,
        }
        return yaml.safe_dump(
            mapping, allow_unicode=True, sort_keys=False,
        ).encode()
    if logical_key == "entry/code":
        lines = [
            "# 当前代码镜像",
            f"GitHub: https://github.com/SkyCjq/cba-kb-engine/tree/{engine_sha}",
            f"commit: {engine_sha}",
            "legacy/ 是历史脚本，禁止用旧 uploader/wechat/sync 向生产写入。",
        ]
        lines.extend(
            f"- {key} | SHA256 {code_hashes[key]}"
            for key in sorted(code_hashes)
        )
        return ("\n".join(lines) + "\n").encode()
    if logical_key == CONTEXT_CARD_KEY:
        if registry is None or manifest_bytes is None:
            return None
        metadata = (
        target_metadata(release_id, engine_sha, registry)
        if registry is not None else None
    )
        return generate_context_card(
            metadata, registry, manifest_bytes,
        ).encode()
    if logical_key in {
        "entry/README", "entry/context", "CURRENT_VERSION_DOC",
        "derived/INDEX.md",
    }:
        return _content_preserving_candidate(
            logical_key, previous, projection, allocation, id_by_key,
            engine_root, registry,
        )
    return None


def _managed_body(data):
    text = data.decode(errors="replace")
    if text.startswith("[CBA-KB CURRENT RELEASE BEGIN]"):
        start = text.find("\n") + 1
        end = text.find("[CBA-KB CURRENT RELEASE END]")
        text = text[start:end if end >= 0 else None]
    history = "以下为迁移前的历史阅读内容；当前回答请以上方发布内容及 MASTER 为准。"
    text = text.replace(history, "").strip()
    if text.endswith(CURRENT_STATE_END.rstrip("\n")):
        text += "\n"
    return text


def _content_preserving_candidate(logical_key, previous, projection, allocation,
                                  id_by_key, engine_root, registry=None):
    release_id = projection["release_id"]
    engine_sha = projection["engine_sha"]
    metadata = (
        target_metadata(release_id, engine_sha, registry)
        if registry is not None else None
    )
    title = {
        "entry/README": "CBA-KB 发布入口",
        "entry/context": "CBA-KB 当前状态",
        "CURRENT_VERSION_DOC": "CBA-KB 当前版本",
        "derived/INDEX.md": "CBA-KB INDEX",
    }[logical_key]
    links = "\n".join(
        f"- {key}: {file_id}" for key, file_id in sorted(id_by_key.items())
    )
    if logical_key == "entry/context":
        operations = (
            Path(engine_root) / "docs/OPERATIONS_V1_5.md"
        ).read_text()
        migration = (
            Path(engine_root) / "docs/MASTER_MIGRATION_NOTE.md"
        ).read_text()
        return (
            f"# {title}\n\n"
            f"当前发布：{release_id}\n\n"
            f"代码提交：{engine_sha}\n\n"
            "读取前请校验 release_status；"
            "PUBLISHING/FAILED/ROLLING_BACK 时使用上一快照。\n\n"
            f"代码镜像：https://github.com/SkyCjq/cba-kb-engine/tree/{engine_sha}\n\n"
            f"## Current target mapping\n\n{links}\n\n"
            f"## Operations\n\n{operations}\n\n"
            f"## Migration\n\n{migration}\n"
        ).encode()
    preserved = _managed_body(previous)
    if metadata is not None:
        preserved = replace_current_block(preserved, metadata)
    historical_releases = {
        projection.get("active_release_id"),
        "v1.5.4-1",
    }
    for previous_release in sorted(
        release for release in historical_releases if release
    ):
        preserved = preserved.replace(
            f"当前发布：{previous_release}",
            f"历史发布：{previous_release}",
        )
        preserved = preserved.replace(
            f"{previous_release} / COMPLETE",
            f"{previous_release} / 历史发布",
        )
        preserved = preserved.replace(
            f"current release: {previous_release}",
            f"historical release: {previous_release}",
        )
    return (
        f"# {title}\n\n"
        f"当前发布：{release_id}\n\n"
        f"代码提交：{engine_sha}\n\n"
        "读取前请校验 release_status；"
        "PUBLISHING/FAILED/ROLLING_BACK 时使用上一快照。\n\n"
        f"代码镜像：https://github.com/SkyCjq/cba-kb-engine/tree/{engine_sha}\n\n"
        f"## Current target mapping\n\n{links}\n\n"
        f"## Previous current navigation\n\n{preserved}\n"
    ).encode()


def _evidence_baseline(drive, roots):
    roots = sorted({root for root in roots if root})
    if not roots:
        return []
    items, _ = _inventory(drive, {
        "current": [],
        "history": [],
        "staging": [],
        "evidence": roots,
    })
    protected = []
    for item in sorted(
        (
            item for item in items
            if item.get("mimeType") != FOLDER
        ),
        key=lambda item: item["id"],
    ):
        mode = (
            "managed_doc"
            if item.get("mimeType") == NATIVE_DOCUMENT else "binary"
        )
        data, _ = snapshot(drive, item["id"], mode)
        protected.append({
            "id": item["id"],
            "name": item["name"],
            "sha256": digest(data),
            "mode": mode,
            "kind": "evidence",
        })
    return protected


def _build_closure_contract(*, drive, instance, projection, allocation,
                            entries, state):
    """Build the production closure from actual candidate targets and zones."""
    by_key = {entry["logical_key"]: entry for entry in entries}
    document_keys = {
        "readme": "entry/README",
        "index": "derived/INDEX.md",
        "context_card": CONTEXT_CARD_KEY,
        "current_version_doc": "CURRENT_VERSION_DOC",
    }
    required = {REGISTRY_KEY, MANIFEST_KEY, *document_keys.values()}
    if not required <= set(by_key):
        raise ProjectionError("CLOSURE_CONTROL_TARGET_MISSING")
    status = state["status"]
    previous_code_commit = (
        status.get("code_commit") or status.get("published_code_commit")
    )
    if not isinstance(previous_code_commit, str) or not re.fullmatch(
        "[0-9a-f]{40}", previous_code_commit,
    ):
        raise ProjectionError("CLOSURE_PREVIOUS_CODE_REQUIRED")
    protected_specs = (
        ("input/MASTER.xlsx", "master"),
        ("facts/CBA_注册领域_六表.xlsx", "six_table"),
        ("facts/CBA_球员注册_EVENTS.xlsx", "six_table"),
        ("facts/CBA_外籍球员注册_SNAPSHOTS.xlsx", "six_table"),
        ("input/source_registry.csv", "source_registry"),
        ("evidence/bayi_legacy_context.md", "evidence"),
    )
    protected = []
    for logical_key, kind in protected_specs:
        try:
            entry = by_key[logical_key]
        except KeyError:
            raise ProjectionError("CLOSURE_PROTECTED_TARGET_MISSING") from None
        before_hash = entry.get("before_hash")
        if (
            not isinstance(before_hash, str)
            or not re.fullmatch("[0-9a-f]{64}", before_hash)
        ):
            raise ProjectionError("CLOSURE_PROTECTED_HASH_REQUIRED")
        protected.append({
            "id": entry["id"],
            "name": entry["name"],
            "sha256": before_hash,
            "mode": entry.get("mode", "binary"),
            "kind": kind,
        })
    parents = instance.read_json("import_inventory.json")["parents"]
    current = {
        parents["root"], parents["ai"], parents["scripts"],
        parents["config"], parents["data"],
    }
    evidence_keys = {
        logical_key for logical_key, kind in protected_specs
        if kind == "evidence"
    }
    for logical_key in required | (
        {item[0] for item in protected_specs} - evidence_keys
    ):
        parent = by_key[logical_key].get("publish_parent")
        if parent and parent != parents["archive"]:
            current.add(parent)
    history = {parents["archive"]}
    evidence = {
        by_key[logical_key].get("publish_parent")
        for logical_key in evidence_keys
        if by_key[logical_key].get("publish_parent")
    }
    if not evidence:
        raise ProjectionError("CLOSURE_EVIDENCE_ZONE_MISSING")
    protected_by_id = {item["id"]: item for item in protected}
    for item in _evidence_baseline(drive, evidence):
        protected_by_id.setdefault(item["id"], item)
    protected = list(protected_by_id.values())
    staging = {
        entry.get("staging_parent") for entry in entries
        if entry.get("staging_parent")
    }
    staging.add(allocation["staging_id"])
    groups = [current, history, evidence, staging]
    if sum(len(group) for group in groups) != len(set().union(*groups)):
        raise ProjectionError("CLOSURE_ZONE_OVERLAP")
    return {
        "code_commit": projection["engine_sha"],
        "previous_code_commit": previous_code_commit,
        "baseline_release_id": status.get("current_release_id"),
        "registry_key": REGISTRY_KEY,
        "manifest_key": MANIFEST_KEY,
        "documents": document_keys,
        "zones": {
            "current": sorted(current),
            "history": sorted(history),
            "staging": sorted(staging),
            "evidence": sorted(evidence),
        },
        "protected": protected,
    }


def freeze_plan(
    drive,
    *,
    instance,
    engine_root,
    release_id,
    projection,
    allocation,
    output,
):
    """Freeze all candidate bytes and a remote-read-only release plan."""
    release_spec = _require_release(release_id)
    verify_execution_sha(
        engine_root,
        projection["engine_sha"],
        release_spec["product_baseline_sha"],
    )
    projection = reproject_targets(projection, allocation)
    state = validate_release_state(drive, instance, projection, allocation)
    output = Path(output)
    if output.exists():
        raise ProjectionError("FREEZE_OUTPUT_MUST_BE_NEW")
    entries = _candidate_inputs(
        drive, engine_root, projection, allocation, output,
    )
    policy = instance.read_json("production.json")
    dependencies = load_production_dependencies(drive, policy)
    outbox = output / "outbox"
    closure = None
    if release_id in {"v1.6.1-1", "v1.8.1-1"}:
        closure = _build_closure_contract(
            drive=drive,
            instance=instance,
            projection=projection,
            allocation=allocation,
            entries=entries,
            state=state,
        )
    plan = prepare_release(
        drive,
        outbox,
        release_id,
        entries,
        allocation["archive_id"],
        allocation["status_id"],
        dependencies,
        carry_forward_artifacts=True,
        **({"closure": closure} if closure is not None else {}),
        environment="production",
        code_commit=projection["engine_sha"],
    )
    if {item["id"] for item in plan["dependencies"]} != {
        item["id"] for item in dependencies
    }:
        raise ProjectionError("PRODUCTION_DEPENDENCY_BINDING_MISMATCH")
    save(output / "allocation.json", allocation)
    if closure is not None:
        save(output / "closure.json", closure)
    save(output / "entries.json", plan["entries"])
    save(output / "target_classification.json", {
        entry["logical_key"]: entry["change_class"] for entry in entries
    })
    save(output / "dependencies.json", dependencies)
    save(output / "release_state_reconciliation.json", state)
    rollback = {
        "release_id": release_id,
        "actions": [
            {
                "id": item["id"],
                "name": item["name"],
                "before_hash": item["before_hash"],
                "restore": item["before"],
                "validation": "digest_equals_before_hash",
            }
            for item in plan["entries"]
        ],
    }
    verification = {
        "release_id": release_id,
        "checks": [
            {
                "id": item["id"],
                "name": item["name"],
                "mime": item["mime"],
                "after_hash": item["after_hash"],
                "parent": item.get("publish_parent"),
            }
            for item in plan["entries"]
        ],
    }
    save(output / "rollback_manifest.json", rollback)
    save(output / "verification_manifest.json", verification)
    return {
        "state": "FROZEN_PLAN",
        "plan": str(outbox / "plan.json"),
        "journal": str(outbox / "journal.json"),
        "entries": len(plan["entries"]),
        "rollback_actions": len(rollback["actions"]),
        "verification_checks": len(verification["checks"]),
        "rollback_manifest_sha256": digest(
            (output / "rollback_manifest.json").read_bytes()
        ),
        "verification_manifest_sha256": digest(
            (output / "verification_manifest.json").read_bytes()
        ),
    }


def load_production_dependencies(drive, policy):
    dependency_ids = policy.get("dependency_ids")
    if (
        not isinstance(dependency_ids, list)
        or not dependency_ids
        or not all(isinstance(item, str) and item for item in dependency_ids)
        or len(dependency_ids) != len(set(dependency_ids))
    ):
        raise ProjectionError("PRODUCTION_DEPENDENCY_BINDING_MISMATCH")
    dependencies = []
    for file_id in sorted(dependency_ids):
        try:
            data, meta = snapshot(drive, file_id)
        except Exception as exc:
            raise ProjectionError(
                "PRODUCTION_DEPENDENCY_BINDING_MISMATCH"
            ) from exc
        dependencies.append({
            "id": file_id,
            "sha256": digest(data),
            "meta": fingerprint(meta),
            "mode": "binary",
        })
    return dependencies


def _load_manifest(raw):
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def _project_command(args, instance):
    release_spec = _require_release(args.release)
    verify_execution_sha(
        Path.cwd(),
        args.engine_sha,
        release_spec["product_baseline_sha"],
    )
    production = instance.read_json("production.json")
    runtime = instance.read_json("runtime.json")
    drive = Drive(Path.cwd(), instance)
    status_raw = drive.get(production["status_id"])
    status_meta = drive.meta(production["status_id"])
    status = json.loads(status_raw)
    manifest = _load_manifest(
        drive.get(runtime["inputs"]["manifest.csv"]["id"])
    )
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=Path.cwd(),
    ).decode().split("\0")
    tracked = [path for path in tracked if path]
    hashes = {
        path: digest((Path.cwd() / path).read_bytes()) for path in tracked
    }
    projection = project_targets(
        release_id=args.release,
        engine_sha=args.engine_sha,
        status_id=production["status_id"],
        archive_id=production["archive_id"],
        previous_status=status,
        previous_targets=production["targets"],
        manifest_rows=manifest,
        tracked=tracked,
        tracked_hashes=hashes,
        code_parent=instance.read_json("import_inventory.json")["parents"][
            "scripts"
        ],
        status_hash=digest(status_raw),
        status_meta=fingerprint(status_meta),
        allocation=read(args.allocation) if args.allocation else None,
    )
    save(args.output, projection)
    return projection


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "step", choices=["project", "reserve-staging", "freeze"],
    )
    parser.add_argument("--release", default=RELEASE_ID)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--projection", type=Path)
    parser.add_argument("--allocation", type=Path)
    parser.add_argument("--single-writer", action="store_true")
    parser.add_argument("--engine-sha")
    args = parser.parse_args(argv)
    _require_release(args.release)
    if args.step == "project" and not args.engine_sha:
        parser.error("--engine-sha is required for project")
    root = Path.cwd()
    instance = load_instance(root, args.instance_root)
    output = _private_output(instance, args.output)
    if args.step == "project":
        result = _project_command(args, instance)
    else:
        if not args.projection:
            parser.error("--projection is required")
        projection = read(args.projection)
        if args.step == "reserve-staging":
            result = reserve_staging(
                Drive(root, instance),
                instance,
                release_id=args.release,
                projection=projection,
                output=output,
                single_writer=args.single_writer,
            )
        else:
            if not args.allocation:
                parser.error("--allocation is required")
            allocation = read(args.allocation)
            result = freeze_plan(
                Drive(root, instance),
                instance=instance,
                engine_root=root,
                release_id=args.release,
                projection=projection,
                allocation=allocation,
                output=output,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
