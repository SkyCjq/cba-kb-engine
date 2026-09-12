"""Fail-closed v1.5.5 release orchestration: project, reserve, freeze.

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
from cba_kb.drive import Drive
from cba_kb.instance import load_instance
from cba_kb.native import wrap
from cba_kb.release import fingerprint, prepare as prepare_release, snapshot


RELEASE_ID = "v1.5.5-1"
PRODUCT_BASELINE_SHA = "c14a0f2579fcc86e2dc114f0b15d00dd54e9f55e"
FOLDER = "application/vnd.google-apps.folder"
FORBIDDEN_CODE_PREFIXES = ("workspace/", ".credentials/", ".venv/")
CONTROL_KEYS = frozenset({
    "control/drive_map.yaml",
    "entry/code",
    "entry/README",
    "entry/context",
    "derived/INDEX.md",
})


class ProjectionError(RuntimeError):
    pass


def _json_hash(value):
    return digest(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode())


def _require_release(release_id):
    if release_id != RELEASE_ID:
        raise ProjectionError(f"RELEASE_ID_FORBIDDEN:{release_id}")


def _private_output(instance, output):
    output = Path(output).resolve()
    try:
        output.relative_to(instance.data_root)
    except ValueError as exc:
        raise ProjectionError("OUTPUT_MUST_BE_PRIVATE_INSTANCE_DATA") from exc
    return output


def verify_execution_sha(engine_root, engine_sha):
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
            PRODUCT_BASELINE_SHA, engine_sha,
        ],
        cwd=engine_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise ProjectionError("CODE_PROVENANCE_MISMATCH")
    return {
        "head": head,
        "clean": True,
        "product_baseline_sha": PRODUCT_BASELINE_SHA,
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
    if set(by_id) != set(targets):
        raise ProjectionError("PREVIOUS_STATUS_TARGET_MISMATCH")
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
):
    """Project exact target semantics without any remote mutation."""
    _require_release(release_id)
    status_by_id = _status_rows(previous_status, previous_targets)
    logical_by_id = _manifest_rows(manifest_rows)
    if set(status_by_id) - set(logical_by_id):
        raise ProjectionError("MANIFEST_MAPPING_MISSING")

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
    removed_code_keys = sorted(existing_code_keys - set(current_code))
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
            f"reserve:{release_id}:{key}",
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
        "release_id": release_id,
        "status_id": projection["status_id"],
        "archive_id": projection["archive_id"],
        "staging_id": staging_id,
        "semantic_delta_signature": projection["semantic_delta_signature"],
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
    for index, logical_key in enumerate(ordered):
        item = existing_by_key.get(logical_key) or new_by_key[logical_key]
        candidate = candidates / str(index)
        generated = None
        if logical_key.startswith("code/"):
            relative = logical_key.removeprefix("code/")
            data = (Path(engine_root) / relative).read_bytes()
        else:
            previous, _ = snapshot(
                drive, item["id"], item.get("mode", "binary"),
            )
            generated = _control_candidate(
                logical_key, previous, projection, allocation, id_by_key,
                code_hashes,
            )
            data = generated if generated is not None else previous
        if generated is not None and item.get("mode") == "managed_doc":
            data = _managed_candidate(data)
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


def _managed_candidate(data):
    return wrap(data.decode()).encode()


def _control_candidate(logical_key, previous, projection, allocation,
                       id_by_key, code_hashes):
    release_id = projection["release_id"]
    engine_sha = projection["engine_sha"]
    status_id = allocation["status_id"]
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
    if logical_key in {"entry/README", "entry/context", "derived/INDEX.md"}:
        title = {
            "entry/README": "CBA-KB 发布入口",
            "entry/context": "CBA-KB 当前状态",
            "derived/INDEX.md": "CBA-KB INDEX",
        }[logical_key]
        return (
            f"# {title}\n\n"
            f"当前发布：{release_id}\n\n"
            f"代码提交：{engine_sha}\n\n"
            "读取前请校验 release_status；"
            "PUBLISHING/FAILED/ROLLING_BACK 时使用上一快照。\n"
        ).encode()
    return None


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
    _require_release(release_id)
    verify_execution_sha(engine_root, projection["engine_sha"])
    validate_reservation(projection, allocation)
    output = Path(output)
    if output.exists():
        raise ProjectionError("FREEZE_OUTPUT_MUST_BE_NEW")
    entries = _candidate_inputs(
        drive, engine_root, projection, allocation, output,
    )
    runtime = instance.read_json("runtime.json")
    dependencies = []
    for name in ("MASTER.xlsx", "source_registry.csv"):
        item = runtime["inputs"][name]
        data, meta = snapshot(drive, item["id"])
        dependencies.append({
            "id": item["id"],
            "sha256": digest(data),
            "meta": fingerprint(meta),
        })
    outbox = output / "outbox"
    plan = prepare_release(
        drive,
        outbox,
        release_id,
        entries,
        allocation["archive_id"],
        allocation["status_id"],
        dependencies,
        carry_forward_artifacts=True,
        environment="production",
        code_commit=projection["engine_sha"],
    )
    save(output / "allocation.json", allocation)
    save(output / "entries.json", plan["entries"])
    save(output / "target_classification.json", {
        entry["logical_key"]: entry["change_class"] for entry in entries
    })
    save(output / "dependencies.json", dependencies)
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


def _load_manifest(raw):
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def _project_command(args, instance):
    verify_execution_sha(Path.cwd(), args.engine_sha)
    production = instance.read_json("production.json")
    runtime = instance.read_json("runtime.json")
    drive = Drive(Path.cwd(), instance)
    status = json.loads(drive.get(production["status_id"]))
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
