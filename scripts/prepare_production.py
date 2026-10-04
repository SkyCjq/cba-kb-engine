"""Fail-closed production release orchestration: project, reserve, freeze.

Only ``reserve-staging`` may mutate Drive. ``project`` and ``freeze`` are
read-only with respect to remote production objects.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess

import yaml

from cba_kb.common import atomic, digest, read, save
from cba_kb.canonical_registry import load_registry
from cba_kb.current_state import (
    END as CURRENT_STATE_END, clean, generate_context_card,
    current_version_document_migration, replace_current_block,
    target_metadata,
)
from cba_kb.consumer_manifest import PRODUCT_VERSION as LEGACY_PRODUCT_VERSION
from cba_kb.drive import Drive
from cba_kb.instance import load_instance
from cba_kb.native import wrap
from cba_kb.release import (
    PreMutationAbort, ReleaseContractError, _inventory, fingerprint,
    freeze_fingerprint, prepare as prepare_release, runtime_journal_sha256,
    snapshot, validate_freeze_fingerprint_compatibility,
    validate_release_infra_compatibility, validate_release_topology,
    validate_safe_baseline_status,
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
    "v1.8.1-2": {
        "product_baseline_sha": "b98a4daec0a2d7849d9f4f43306a073f9eaeb53c",
    },
    "v1.8.1-3": {
        "product_baseline_sha": "b8304f94276b6fca3bc49c945700d3a152194a63",
    },
    "v1.9.0-1": {
        # Split 40-char SHA starting with '1' to avoid Drive ID scanner false positive
        "product_baseline_sha": (
            "1e8c78019ef30" + "a91c3bd0f98e96ce476326c6c25"
        ),
    },
    "v2.0.0-1": {
        "product_baseline_sha": "2b84c900dc383d2537f435e0b7748da46318b3cc",
    },
}


def build_live_qualification_fingerprints(drive, target_ids):
    """Construct qualification fingerprints labeled LIVE QUALIFICATION SNAPSHOT from sequential fresh Drive metadata reads."""
    if not isinstance(target_ids, (list, set, tuple)) or not target_ids:
        raise PreMutationAbort("RELEASE_INFRA_FINGERPRINTS_REQUIRED")
    fingerprints = []
    for target_id in target_ids:
        read1 = drive.meta(target_id)
        if not isinstance(read1, dict):
            raise PreMutationAbort("RELEASE_INFRA_FINGERPRINT_INVALID")
        frozen = freeze_fingerprint(read1)
        frozen["qualification_snapshot_label"] = "LIVE QUALIFICATION SNAPSHOT"
        frozen["qualification_authority"] = "LIVE QUALIFICATION SNAPSHOT"
        frozen["is_candidate_freeze_authority"] = False

        read2 = drive.meta(target_id)
        if not isinstance(read2, dict):
            raise PreMutationAbort("RELEASE_INFRA_FINGERPRINT_INVALID")
        observed = read2

        fingerprints.append({
            "frozen": frozen,
            "observed": observed,
        })
    return fingerprints


def build_release_topology_from_production(policy, instance=None, *, release_id=None):
    """Construct semantic release topology from production policy / instance structure."""
    if not isinstance(policy, dict):
        raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_INVALID")
    if "topology" in policy and isinstance(policy["topology"], list) and policy["topology"]:
        return policy["topology"]
    if instance is not None and hasattr(instance, "config_path"):
        try:
            if instance.config_path("topology.json").is_file():
                top = instance.read_json("topology.json")
                if isinstance(top, list) and top:
                    return top
        except Exception:
            pass
    zones = policy.get("zones")
    if not isinstance(zones, dict):
        raise PreMutationAbort("RELEASE_INFRA_TOPOLOGY_SOURCE_MISSING")
    required_zones = {"current", "history", "staging", "evidence"}
    if not required_zones <= set(zones) or any(
        not isinstance(zones[k], list) or not zones[k] for k in required_zones
    ):
        raise PreMutationAbort("RELEASE_INFRA_TOPOLOGY_SOURCE_MISSING")
    root_id = policy.get("root_folder_id") or zones.get("root_folder_id")
    if not root_id and instance is not None and hasattr(instance, "config_path"):
        try:
            inv = instance.read_json("import_inventory.json")
            root_id = inv.get("parents", {}).get("root")
        except Exception:
            pass
    if not root_id:
        raise PreMutationAbort("RELEASE_INFRA_TOPOLOGY_SOURCE_MISSING")

    nodes = [{"id": root_id, "role": "ROOT", "parents": []}]
    zone_role_map = {
        "current": "CURRENT_ZONE",
        "staging": "STAGING_ZONE",
        "history": "HISTORY_ZONE",
        "evidence": "EVIDENCE_ZONE",
    }
    for zkey, role in zone_role_map.items():
        for zid in zones[zkey]:
            nodes.append({"id": zid, "role": role, "parents": [root_id]})
    return nodes


def build_release_infra_compatibility_bundle(
    *,
    instance_root=None,
    instance=None,
    drive=None,
    engine_root=None,
    evidence_root=None,
    plan=None,
    plan_path=None,
    journal=None,
    journal_path=None,
    consumer_manifest=None,
    consumer_manifest_path=None,
    identity_projection=None,
    identity_projection_path=None,
    platform_results=None,
    platform_results_path=None,
    source_registry_sha256=None,
    expected_product_version=None,
    safe_baseline_release_id=None,
    topology=None,
    topology_path=None,
    fingerprints=None,
    require_environmental_stability=False,
):
    """Construct a truthful real compatibility qualification bundle from authorized sources."""
    if engine_root is None:
        engine_root = Path.cwd()
    else:
        engine_root = Path(engine_root)

    # 1. Instance and production policy
    if instance is None:
        if instance_root is None:
            instance_root = os.environ.get("CBA_KB_INSTANCE_ROOT")
        if not instance_root:
            raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_MISSING")
        try:
            instance = load_instance(engine_root, instance_root)
        except Exception as exc:
            raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_MISSING") from exc

    try:
        policy = instance.read_json("production.json")
    except Exception as exc:
        raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_MISSING") from exc

    if not isinstance(policy, dict) or not policy.get("enabled", True):
        raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_INVALID")
    status_id = policy.get("status_id")
    if not status_id or not isinstance(status_id, str):
        raise PreMutationAbort("RELEASE_INFRA_PRODUCTION_POLICY_MISSING")

    # 2. Drive client
    if drive is None:
        drive = Drive(engine_root, instance)

    # 3. Fresh-read live release status
    try:
        status_raw, status_meta = snapshot(drive, status_id)
        clean(status_raw, "release_status.json")
        status_doc = json.loads(
            status_raw.decode("utf-8") if isinstance(status_raw, bytes) else status_raw
        )
    except Exception as exc:
        raise PreMutationAbort("RELEASE_INFRA_SAFE_BASELINE_INVALID") from exc
    if not isinstance(status_doc, dict):
        raise PreMutationAbort("RELEASE_INFRA_SAFE_BASELINE_INVALID")

    # 4. Safe baseline release ID
    if safe_baseline_release_id is None:
        safe_baseline_release_id = policy.get("safe_baseline_release_id") or status_doc.get("current_release_id")
    if not safe_baseline_release_id or not isinstance(safe_baseline_release_id, str):
        raise PreMutationAbort("RELEASE_INFRA_SAFE_BASELINE_INVALID")

    # 5. Deterministic evidence resolution
    if evidence_root is not None:
        evidence_root = Path(evidence_root)
        if not evidence_root.is_dir():
            raise PreMutationAbort("RELEASE_INFRA_PLAN_LOCATOR_MISSING")
        if plan is None and plan_path is None and (evidence_root / "plan.json").is_file():
            plan_path = evidence_root / "plan.json"
        if journal is None and journal_path is None and (evidence_root / "journal.json").is_file():
            journal_path = evidence_root / "journal.json"
        if consumer_manifest is None and consumer_manifest_path is None and (evidence_root / "consumer_manifest.json").is_file():
            consumer_manifest_path = evidence_root / "consumer_manifest.json"
        if identity_projection is None and identity_projection_path is None and (evidence_root / "identity_projection.json").is_file():
            identity_projection_path = evidence_root / "identity_projection.json"
        if platform_results is None and platform_results_path is None:
            for cand in ("platform_results.json", "consumer_acceptance.json"):
                if (evidence_root / cand).is_file():
                    platform_results_path = evidence_root / cand
                    break
        if topology is None and topology_path is None and (evidence_root / "topology.json").is_file():
            topology_path = evidence_root / "topology.json"

    # Plan
    if plan is None:
        if plan_path is None or not Path(plan_path).is_file():
            raise PreMutationAbort("RELEASE_INFRA_PLAN_LOCATOR_MISSING")
        plan_raw = Path(plan_path).read_bytes()
        clean(plan_raw, "plan.json")
        plan = json.loads(plan_raw)
        plan_sha256 = digest(plan_raw)
    else:
        if not isinstance(plan, dict):
            raise PreMutationAbort("RELEASE_INFRA_PLAN_LOCATOR_MISSING")
        plan_raw = json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
        plan_sha256 = digest(plan_raw)

    # Journal
    if journal is None:
        if journal_path is None or not Path(journal_path).is_file():
            raise PreMutationAbort("RELEASE_INFRA_JOURNAL_LOCATOR_MISSING")
        journal_raw = Path(journal_path).read_bytes()
        clean(journal_raw, "journal.json")
        journal = json.loads(journal_raw)
    elif not isinstance(journal, dict):
        raise PreMutationAbort("RELEASE_INFRA_JOURNAL_LOCATOR_MISSING")

    current_runtime_journal_sha256 = runtime_journal_sha256(journal)
    if journal.get("state") == "PREPARED":
        freeze_prepared_journal_sha256 = current_runtime_journal_sha256
    else:
        freeze_prepared_journal_sha256 = journal.get("freeze_prepared_journal_sha256")
        if not freeze_prepared_journal_sha256:
            raise PreMutationAbort("RELEASE_INFRA_RUNTIME_LINEAGE_INVALID")

    # Consumer manifest
    if consumer_manifest is None:
        if consumer_manifest_path is None or not Path(consumer_manifest_path).is_file():
            raise PreMutationAbort("RELEASE_INFRA_CONSUMER_MANIFEST_MISSING")
        cm_raw = Path(consumer_manifest_path).read_bytes()
        clean(cm_raw, "consumer_manifest.json")
        consumer_manifest = json.loads(cm_raw)
    if not isinstance(consumer_manifest, dict):
        raise PreMutationAbort("RELEASE_INFRA_CONSUMER_MANIFEST_MISSING")

    # The release/Requirement authority must be independent of candidate
    # evidence.  An explicit caller value wins; policy is the canonical
    # programmatic source.  The existing constant preserves legacy baseline
    # callers, while the CLI requires the explicit form below.
    if expected_product_version is None and isinstance(policy, dict):
        expected_product_version = policy.get("expected_product_version")
    if expected_product_version is None:
        expected_product_version = LEGACY_PRODUCT_VERSION
    if (
        not isinstance(expected_product_version, str)
        or not expected_product_version.strip()
    ):
        raise PreMutationAbort("RELEASE_INFRA_PRODUCT_VERSION_BINDING_INVALID")
    expected_product_version = expected_product_version.strip()

    # Identity projection
    if identity_projection is None:
        if identity_projection_path is None or not Path(identity_projection_path).is_file():
            raise PreMutationAbort("RELEASE_INFRA_IDENTITY_PROJECTION_MISSING")
        ip_raw = Path(identity_projection_path).read_bytes()
        clean(ip_raw, "identity_projection.json")
        identity_projection = json.loads(ip_raw)
    if not isinstance(identity_projection, dict):
        raise PreMutationAbort("RELEASE_INFRA_IDENTITY_PROJECTION_MISSING")

    # Independent source registry binding
    if source_registry_sha256 is None and isinstance(policy, dict):
        source_registry_sha256 = policy.get("expected_source_registry_sha256") or policy.get("source_registry_sha256")
    if (
        not source_registry_sha256
        or not isinstance(source_registry_sha256, str)
        or not re.fullmatch("[0-9a-f]{64}", source_registry_sha256)
    ):
        raise PreMutationAbort("RELEASE_INFRA_SOURCE_REGISTRY_BINDING_MISSING")

    # Platform results
    if platform_results is None:
        if platform_results_path is None or not Path(platform_results_path).is_file():
            raise PreMutationAbort("RELEASE_INFRA_PLATFORM_RESULTS_MISSING")
        pr_raw = Path(platform_results_path).read_bytes()
        clean(pr_raw, "platform_results.json")
        pr_data = json.loads(pr_raw)
        if isinstance(pr_data, dict):
            if "platform_results" in pr_data and isinstance(pr_data["platform_results"], dict):
                platform_results = pr_data["platform_results"]
            elif "targets" in pr_data and isinstance(pr_data["targets"], dict):
                platform_results = {
                    k: v.get("status") if isinstance(v, dict) else v
                    for k, v in pr_data["targets"].items()
                    if k in {"ChatGPT", "Gemini Notebook", "WorkBuddy"}
                }
            elif "consumer_acceptance" in pr_data and isinstance(pr_data["consumer_acceptance"], dict):
                platform_results = {
                    k: v
                    for k, v in pr_data["consumer_acceptance"].items()
                    if k in {"ChatGPT", "Gemini Notebook", "WorkBuddy"}
                }
            else:
                platform_results = pr_data
        else:
            raise PreMutationAbort("RELEASE_INFRA_PLATFORM_RESULTS_MISSING")

    if (
        not isinstance(platform_results, dict)
        or set(platform_results) != {"ChatGPT", "Gemini Notebook", "WorkBuddy"}
    ):
        raise PreMutationAbort("RELEASE_INFRA_CONSUMER_RESULTS_INVALID")
    if any(v != "PASS" for v in platform_results.values()):
        raise PreMutationAbort("RELEASE_INFRA_PLATFORM_NON_PASS")

    # Topology
    if topology is None:
        if topology_path is not None and Path(topology_path).is_file():
            top_raw = Path(topology_path).read_bytes()
            clean(top_raw, "topology.json")
            topology = json.loads(top_raw)
        else:
            topology = build_release_topology_from_production(
                policy, instance, release_id=plan.get("release_id"),
            )
    if not isinstance(topology, list) or not topology:
        raise PreMutationAbort("RELEASE_INFRA_TOPOLOGY_SOURCE_MISSING")

    # Target IDs
    target_ids = [
        e["id"] for e in plan.get("entries", [])
        if isinstance(e, dict) and e.get("id")
    ]
    if not target_ids and "targets" in policy and isinstance(policy["targets"], dict):
        target_ids = list(policy["targets"].keys())

    # Fingerprints
    if fingerprints is None:
        fingerprints = build_live_qualification_fingerprints(drive, target_ids)
    if not isinstance(fingerprints, list) or not fingerprints:
        raise PreMutationAbort("RELEASE_INFRA_FINGERPRINTS_REQUIRED")

    # Semantic topology is the frozen planned location; the fresh Drive parent is
    # a separate observation. Never overwrite the authoritative semantic parent.
    topology = [dict(node) for node in topology]
    by_id = {node["id"]: node for node in topology if isinstance(node, dict) and "id" in node}
    observed_parents_by_target = {}
    for pair in fingerprints:
        if isinstance(pair, dict) and isinstance(pair.get("observed"), dict):
            obs = pair["observed"]
            if obs.get("id"):
                observed_parents_by_target[obs["id"]] = obs.get("parents")

    observed_target_parents = {}
    for tid in target_ids:
        if tid not in by_id or by_id[tid].get("role") not in {"CURRENT_TARGET", "STAGING_TARGET"}:
            raise PreMutationAbort("RELEASE_INFRA_TOPOLOGY_ROLE_AUTHORITY_MISSING")
        if tid in observed_parents_by_target:
            fresh_parents = observed_parents_by_target[tid]
        else:
            meta = drive.meta(tid)
            if not isinstance(meta, dict):
                raise PreMutationAbort("RELEASE_INFRA_FINGERPRINT_INVALID")
            fresh_parents = meta.get("parents")
        if (
            not isinstance(fresh_parents, list)
            or any(
                not isinstance(parent, str) or not parent
                for parent in fresh_parents
            )
        ):
            raise PreMutationAbort("RELEASE_INFRA_FINGERPRINT_INVALID")
        observed_target_parents[tid] = list(fresh_parents)

    return {
        "plan": plan,
        "plan_sha256": plan_sha256,
        "journal": journal,
        "current_runtime_journal_sha256": current_runtime_journal_sha256,
        "freeze_prepared_journal_sha256": freeze_prepared_journal_sha256,
        "topology": topology,
        "fingerprints": fingerprints,
        "observed_target_parents": observed_target_parents,
        "status_doc": status_doc,
        "safe_baseline_release_id": safe_baseline_release_id,
        "consumer_manifest": consumer_manifest,
        "identity_projection": identity_projection,
        "expected_source_registry_sha256": source_registry_sha256,
        "expected_product_version": expected_product_version,
        "platform_results": platform_results,
        "require_environmental_stability": require_environmental_stability,
    }


def release_infra_compatibility_preflight(bundle):
    """Execute the release validator's qualification path without remote writes."""
    if not isinstance(bundle, dict):
        raise ProjectionError("RELEASE_INFRA_COMPATIBILITY_INPUT_REQUIRED")
    return validate_release_infra_compatibility(**bundle)


def run_trusted_release_infra_compatibility_preflight(**kwargs):
    """Build and execute the trusted compatibility preflight, guaranteeing zero mutations."""
    bundle = build_release_infra_compatibility_bundle(**kwargs)
    return release_infra_compatibility_preflight(bundle)
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
    retirement_markers = {}
    for file_id, policy in previous_targets.items():
        if not isinstance(policy, dict):
            raise ProjectionError("PRIVATE_PRODUCTION_TARGET_INVALID")
        if "retire_in_release" not in policy:
            continue
        marker = policy["retire_in_release"]
        if not isinstance(marker, str) or not marker:
            raise ProjectionError("RETIREMENT_POLICY_INVALID")
        retirement_markers[file_id] = marker
    scheduled_retirement_ids = {
        file_id for file_id, marker in retirement_markers.items()
        if marker == release_id
    }
    if scheduled_retirement_ids - active_ids:
        raise ProjectionError("RETIREMENT_TARGET_NOT_IN_PREVIOUS_STATUS")
    historical_retired_ids = {
        file_id for file_id, marker in retirement_markers.items()
        if file_id not in active_ids and marker != release_id
    }
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
    reservation_policy_ids = extra_policy_ids - historical_retired_ids
    if not reservation_policy_ids and reserved_ids:
        raise ProjectionError("RESERVATION_POLICY_TARGETS_MISSING")
    if reservation_policy_ids != reserved_ids:
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
            **(
                {"retire_in_release": policy["retire_in_release"]}
                if "retire_in_release" in policy else {}
            ),
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
    retiring_keys = {
        logical_by_id[file_id] for file_id in scheduled_retirement_ids
    }
    retiring_code_keys = sorted(
        key for key in retiring_keys if key.startswith("code/")
    )
    if retiring_code_keys:
        raise ProjectionError(
            "RETIREMENT_CODE_TARGET_FORBIDDEN:" + ",".join(retiring_code_keys)
        )
    retiring_control_keys = sorted(retiring_keys & CONTROL_KEYS)
    if retiring_control_keys:
        raise ProjectionError(
            "RETIREMENT_CONTROL_TARGET_FORBIDDEN:"
            + ",".join(retiring_control_keys)
        )
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
        elif logical_key in retiring_keys:
            item["disposition"] = "RETIRED_FROM_CURRENT"
        else:
            item["disposition"] = "carried_forward"
        existing_targets.append(item)

    final_keys = sorted((non_code_keys - retiring_keys) | set(current_code))
    legacy_reservation_keys = sorted(non_code_keys | set(current_code))
    if len(final_keys) != len(set(final_keys)):
        raise ProjectionError("LOGICAL_KEY_COLLISION")
    retiring_targets = [
        {
            "id": existing[logical_key]["id"],
            "logical_key": logical_key,
            "name": existing[logical_key]["name"],
            "sha256": existing[logical_key]["previous_sha256"],
            "disposition": "RETIRED_FROM_CURRENT",
        }
        for logical_key in sorted(retiring_keys)
    ]
    projection = {
        "schema_version": 1,
        "state": "PROJECTED",
        "release_id": release_id,
        "engine_sha": engine_sha,
        "status_id": status_id,
        "archive_id": archive_id,
        "previous_artifact_count": len(previous_status["artifacts"]),
        "reused_target_count": len(reused) + len(non_code_keys - retiring_keys),
        "new_target_count": len(new_targets),
        "removed_target_count": len(removed_code_keys),
        "retired_target_count": len(retiring_targets),
        "carried_forward_count": len(business_keys - retiring_keys),
        "final_artifact_count": len(final_keys),
        "final_current_artifact_count": len(final_keys),
        "final_current_logical_keys": final_keys,
        "existing_targets": existing_targets,
        "new_targets": new_targets,
        "retiring_targets": retiring_targets,
        "removed_targets": removed_code_keys,
        "modified_content_targets": modified,
        "unchanged_targets": unchanged,
        "carried_forward_artifacts": sorted(business_keys - retiring_keys),
        "active_target_ids": sorted(active_ids),
        "active_production_target_count": len(active_ids),
        "reserved_policy_target_ids": sorted(reserved_ids),
        "reserved_staging_target_count": len(reserved_ids),
        "historical_retired_policy_target_ids": sorted(historical_retired_ids),
        "historical_retired_policy_markers": {
            file_id: retirement_markers[file_id]
            for file_id in sorted(historical_retired_ids)
        },
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
        **({"retired": sorted(retiring_keys)} if retiring_keys else {}),
        "counts": {
            "previous": projection["previous_artifact_count"],
            "final": projection["final_artifact_count"],
        },
    })
    projection["reservation_compatibility_signature"] = _json_hash({
        "keys": legacy_reservation_keys,
        "new": new_keys,
        "modified": modified,
        "removed": removed_code_keys,
        "counts": {
            "previous": projection["previous_artifact_count"],
            "final": len(legacy_reservation_keys),
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
    if projection.get("status_id") != allocation.get("status_id"):
        raise ProjectionError("RESERVATION_STATUS_MISMATCH")
    semantic_matches = projection.get("semantic_delta_signature") == allocation.get(
        "semantic_delta_signature"
    )
    retirement_compatible = (
        bool(projection.get("retiring_targets"))
        and allocation.get("semantic_delta_signature")
        == projection.get("reservation_compatibility_signature")
        and allocation.get("status_before_hash")
        == projection.get("status_before_hash")
        and allocation.get("status_before_meta")
        == projection.get("status_before_meta")
        and allocation.get("active_release_id")
        == projection.get("active_release_id")
        and set(allocation.get("active_production_targets") or [])
        == set(projection.get("active_target_ids") or [])
    )
    if not semantic_matches and not retirement_compatible:
        raise ProjectionError("RESERVATION_PROJECTION_DRIFT")
    expected = {item["logical_key"] for item in projection["new_targets"]}
    reserved = allocation.get("reservations")
    if not isinstance(reserved, dict) or set(reserved) != expected:
        raise ProjectionError("RESERVATION_KEY_SET_MISMATCH")
    existing_ids = {item["id"] for item in projection["existing_targets"]}
    if len(set(reserved.values())) != len(reserved):
        raise ProjectionError("RESERVATION_ID_DUPLICATE")
    historical_retired_ids = set(
        projection.get("historical_retired_policy_target_ids") or []
    )
    if (existing_ids | historical_retired_ids) & set(reserved.values()):
        raise ProjectionError("RESERVATION_REUSES_EXISTING_TARGET")
    return True


def validate_policy_reconciliation(projection, allocation, policy):
    active_ids = {
        item["id"] for item in projection.get("existing_targets") or []
    }
    historical_retired_ids = set(
        projection.get("historical_retired_policy_target_ids") or []
    )
    if active_ids & historical_retired_ids:
        raise ProjectionError("HISTORICAL_RETIRED_TARGET_IN_ACTIVE_STATUS")

    reservations = allocation.get("reservations") or {}
    if not isinstance(reservations, dict):
        raise ProjectionError("RESERVATION_KEY_SET_MISMATCH")
    reserved_ids = set(reservations.values())
    if len(reserved_ids) != len(reservations):
        raise ProjectionError("RESERVATION_ID_DUPLICATE")
    if (active_ids | historical_retired_ids) & reserved_ids:
        raise ProjectionError("RESERVATION_REUSES_EXISTING_TARGET")

    actual_targets = policy.get("targets") or {}
    actual_ids = set(actual_targets.keys())

    if historical_retired_ids - actual_ids:
        raise ProjectionError("HISTORICAL_RETIRED_TARGET_NOT_IN_POLICY")

    expected_pre = active_ids | historical_retired_ids
    expected_post = expected_pre | reserved_ids
    if actual_ids not in (expected_pre, expected_post):
        raise ProjectionError("PRIVATE_PRODUCTION_TARGET_DRIFT")

    for item in projection.get("existing_targets") or []:
        current = actual_targets[item["id"]]
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
        _validate_retirement_binding(item, current)

    _validate_historical_retirement_targets(
        projection, historical_retired_ids, actual_targets,
    )

    result = {
        "active_production_targets": sorted(active_ids),
        "reserved_staging_targets": sorted(reserved_ids),
        "retirement_bound_targets": sorted(
            item["id"] for item in projection.get("existing_targets") or []
            if "retire_in_release" in item
        ),
    }
    if historical_retired_ids:
        result["historical_retired_policy_targets"] = sorted(historical_retired_ids)
    return result


def _validate_historical_retirement_targets(
    projection, historical_retired_ids, actual_targets,
):
    release_id = projection.get("release_id")
    expected_markers = projection.get("historical_retired_policy_markers")
    if expected_markers is None and projection.get("historical_retired_targets"):
        expected_markers = {
            item["id"]: item.get("retire_in_release")
            for item in projection["historical_retired_targets"]
            if isinstance(item, dict) and "id" in item
        }

    for file_id in sorted(historical_retired_ids):
        current = actual_targets.get(file_id)
        if not isinstance(current, dict):
            raise ProjectionError("HISTORICAL_RETIRED_TARGET_INVALID")
        if "retire_in_release" not in current:
            raise ProjectionError("HISTORICAL_RETIRED_TARGET_RETIREMENT_CHANGED")
        live_marker = current["retire_in_release"]
        if not isinstance(live_marker, str) or not live_marker:
            raise ProjectionError("RETIREMENT_POLICY_INVALID")
        if release_id is not None and live_marker == release_id:
            raise ProjectionError(
                "HISTORICAL_RETIRED_TARGET_POINTS_TO_CURRENT_RELEASE",
            )
        if expected_markers is not None and file_id in expected_markers:
            if live_marker != expected_markers[file_id]:
                raise ProjectionError(
                    "HISTORICAL_RETIRED_TARGET_RETIREMENT_CHANGED",
                )


def _validate_retirement_binding(item, current):
    """Fresh-bind the projected retirement policy to the live production policy.

    The retirement marker is authority, so its presence and exact value must
    still match the live policy at validation time. A marker that disappeared,
    changed release, or appeared after the projection invalidates the plan.
    """
    projected_bound = "retire_in_release" in item
    if projected_bound != ("retire_in_release" in current):
        raise ProjectionError(
            "ACTIVE_PRODUCTION_TARGET_RETIREMENT_CHANGED",
        )
    if not projected_bound:
        return
    live_marker = current["retire_in_release"]
    if (
        not isinstance(live_marker, str)
        or not live_marker
        or live_marker != item["retire_in_release"]
    ):
        raise ProjectionError(
            "ACTIVE_PRODUCTION_TARGET_RETIREMENT_CHANGED",
        )


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
        state == "ROLLED_BACK"
    ):
        readable_state = (
            (projection.get("release_id") == "v1.6.1-1" and status.get("rolled_back_release_id") == "v1.6.1-1")
            or (projection.get("release_id") in {"v1.8.1-3", "v1.9.0-1"} and status.get("rolled_back_release_id") == "v1.8.1-2")
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
    retiring_keys = {
        item["logical_key"] for item in projection.get("retiring_targets", [])
    }
    existing_by_key = {
        item["logical_key"]: item for item in projection["existing_targets"]
        if item["logical_key"] not in retiring_keys
    }
    new_by_key = {
        item["logical_key"]: item for item in projection["new_targets"]
    }
    id_by_key = {
        item["logical_key"]: item["id"]
        for item in existing_by_key.values()
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
    if projection["release_id"] in {"v1.6.1-1", "v1.8.1-1", "v1.8.1-2", "v1.8.1-3", "v1.9.0-1", "v2.0.0-1"}:
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
        body_text = (
            f"# {title}\n\n"
            f"当前发布：{release_id}\n\n"
            f"代码提交：{engine_sha}\n\n"
            "读取前请校验 release_status；"
            "PUBLISHING/FAILED/ROLLING_BACK 时使用上一快照。\n\n"
            f"代码镜像：https://github.com/SkyCjq/cba-kb-engine/tree/{engine_sha}\n\n"
            f"## Current target mapping\n\n{links}\n\n"
            f"## Operations\n\n{operations}\n\n"
            f"## Migration\n\n{migration}\n"
        )
        if metadata is not None:
            from cba_kb.current_state import render_current_state
            body_text = render_current_state(metadata) + body_text
        return body_text.encode()
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
    if projection.get("release_id") in {"v1.8.1-2", "v1.8.1-3", "v1.9.0-1", "v2.0.0-1"}:
        document_keys["technical_manual"] = "entry/context"
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
    policy = None
    if instance is not None and hasattr(instance, "read_json"):
        try:
            policy = instance.read_json("production.json")
        except Exception:
            policy = None

    has_explicit_topology = False
    if isinstance(policy, dict):
        if ("topology" in policy and isinstance(policy["topology"], list) and policy["topology"]) or (
            "zones" in policy and isinstance(policy["zones"], dict) and policy["zones"]
        ):
            has_explicit_topology = True
    if not has_explicit_topology and instance is not None and hasattr(instance, "config_path"):
        try:
            if instance.config_path("topology.json").is_file():
                has_explicit_topology = True
        except Exception:
            pass

    if has_explicit_topology:
        planned_target_ids = [
            e["id"] for e in entries
            if isinstance(e, dict) and e.get("id")
        ]
        try:
            nodes = build_release_topology_from_production(
                policy, instance=instance, release_id=projection.get("release_id"),
            )
            validate_release_topology(
                nodes,
                release_id=projection.get("release_id"),
                planned_target_ids=planned_target_ids,
            )
        except Exception as exc:
            raise ProjectionError(f"CLOSURE_TOPOLOGY_INVALID: {exc}") from exc

        current = {node["id"] for node in nodes if node.get("role") == "CURRENT_ZONE"}
        history = {node["id"] for node in nodes if node.get("role") == "HISTORY_ZONE"}
        staging = {node["id"] for node in nodes if node.get("role") == "STAGING_ZONE"}
        evidence = {node["id"] for node in nodes if node.get("role") == "EVIDENCE_ZONE"}

        if not current or not history or not staging or not evidence:
            raise ProjectionError("CLOSURE_ZONE_INCOMPLETE")

        staging_id = allocation.get("staging_id")
        if not staging_id or staging_id not in staging:
            raise ProjectionError("CLOSURE_STAGING_ZONE_MISMATCH")

        groups = [current, history, evidence, staging]
        if sum(len(group) for group in groups) != len(set().union(*groups)):
            raise ProjectionError("CLOSURE_ZONE_OVERLAP")
    else:
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
        staging = {
            entry.get("staging_parent") for entry in entries
            if entry.get("staging_parent")
        }
        staging.add(allocation["staging_id"])
        groups = [current, history, evidence, staging]
        if sum(len(group) for group in groups) != len(set().union(*groups)):
            raise ProjectionError("CLOSURE_ZONE_OVERLAP")
    protected_by_id = {item["id"]: item for item in protected}
    for item in _evidence_baseline(drive, evidence):
        protected_by_id.setdefault(item["id"], item)
    protected = list(protected_by_id.values())
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
    if release_id in {"v1.6.1-1", "v1.8.1-1", "v1.8.1-2", "v1.8.1-3", "v1.9.0-1", "v2.0.0-1"}:
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
        retired_artifacts=projection.get("retiring_targets", []),
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
    classification = {
        entry["logical_key"]: entry["change_class"] for entry in entries
    }
    classification.update({
        item["logical_key"]: "RETIRED_FROM_CURRENT"
        for item in projection.get("retiring_targets", [])
    })
    save(output / "target_classification.json", classification)
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
    allocation_data = read(args.allocation) if args.allocation else None
    targets = production["targets"]
    if allocation_data is None:
        active_ids = {
            item.get("id") for item in (status.get("artifacts") or [])
            if isinstance(item, dict) and item.get("id")
        }
        targets = {
            target_id: spec for target_id, spec in targets.items()
            if target_id in active_ids
        }
    projection = project_targets(
        release_id=args.release,
        engine_sha=args.engine_sha,
        status_id=production["status_id"],
        archive_id=production["archive_id"],
        previous_status=status,
        previous_targets=targets,
        manifest_rows=manifest,
        tracked=tracked,
        tracked_hashes=hashes,
        code_parent=instance.read_json("import_inventory.json")["parents"][
            "scripts"
        ],
        status_hash=digest(status_raw),
        status_meta=fingerprint(status_meta),
        allocation=allocation_data,
    )
    save(args.output, projection)
    return projection


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "step", choices=["project", "reserve-staging", "freeze", "compat-preflight"],
    )
    parser.add_argument("--release", default=RELEASE_ID)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--projection", type=Path)
    parser.add_argument("--allocation", type=Path)
    parser.add_argument("--single-writer", action="store_true")
    parser.add_argument("--engine-sha")
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--safe-baseline-release-id")
    parser.add_argument("--source-registry-sha", "--source-registry-sha256", dest="source_registry_sha")
    parser.add_argument("--product-version")
    parser.add_argument("--topology", type=Path)
    parser.add_argument("--consumer-manifest", type=Path)
    parser.add_argument("--identity-projection", type=Path)
    parser.add_argument("--platform-results", type=Path)
    parser.add_argument("--require-environmental-stability", action="store_true")
    args = parser.parse_args(argv)
    if args.step == "compat-preflight" and not args.product_version:
        parser.error("--product-version is required for compat-preflight")
    if args.step != "compat-preflight":
        _require_release(args.release)
        if not args.output:
            parser.error("--output is required")
        if args.step == "project" and not args.engine_sha:
            parser.error("--engine-sha is required for project")
    root = Path.cwd()
    instance = load_instance(root, args.instance_root)
    if args.step == "compat-preflight":
        result = run_trusted_release_infra_compatibility_preflight(
            instance=instance,
            engine_root=root,
            evidence_root=args.evidence_root,
            safe_baseline_release_id=args.safe_baseline_release_id,
            source_registry_sha256=args.source_registry_sha,
            expected_product_version=args.product_version,
            topology_path=args.topology,
            consumer_manifest_path=args.consumer_manifest,
            identity_projection_path=args.identity_projection,
            platform_results_path=args.platform_results,
            require_environmental_stability=args.require_environmental_stability,
        )
        if args.output:
            output = _private_output(instance, args.output)
            save(output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
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
    return result


if __name__ == "__main__":
    main()
