"""Machine-readable release-scoped Consumer Manifest schema and validators."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import digest
from .evidence_ledger import canonical_bytes

SCHEMA = "cba-kb.consumer-manifest.v1"
SCHEMA_VERSION = 1
PRODUCT_VERSION = "v1.8.1"
V200_PRODUCT_VERSION = "v2.0.0"
REQUIRED_CONTROL_KEYS = frozenset({
    "release_status",
    "readme",
    "index",
    "technical_manual",
    "context_card",
    "current_version_doc",
})
REQUIRED_FACT_KEYS = frozenset({"master"})
REQUIRED_IDENTITY_KEYS = frozenset({"player_identity_projection"})


class ConsumerManifestError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ConsumerManifestError(f"{label}_REQUIRED")
    return value.strip()


def _required_sha256(value, label):
    value = _required_text(value, label)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ConsumerManifestError(f"{label}_INVALID_SHA256")
    return value


def _required_git_sha(value, label):
    value = _required_text(value, label)
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ConsumerManifestError(f"{label}_INVALID_SHA")
    return value


def _requires_independent_candidate(release_id, product_version):
    return (
        product_version == V200_PRODUCT_VERSION
        and re.fullmatch(re.escape(product_version) + r"-\d+", release_id) is not None
    )


def _validate_entry(key, entry, *, category):
    if not isinstance(entry, dict):
        raise ConsumerManifestError(f"MANIFEST_ENTRY_OBJECT_REQUIRED:{key}")
    required_fields = {"id", "name", "sha256", "mime", "authority", "rights"}
    if not required_fields <= set(entry):
        raise ConsumerManifestError(f"MANIFEST_ENTRY_INCOMPLETE:{key}")
    _required_text(entry["id"], f"{key}_ID")
    _required_text(entry["name"], f"{key}_NAME")
    _required_sha256(entry["sha256"], f"{key}_SHA256")
    _required_text(entry["mime"], f"{key}_MIME")
    authority = _required_text(entry["authority"], f"{key}_AUTHORITY")
    rights = _required_text(entry["rights"], f"{key}_RIGHTS")
    expected_authority = {
        "control": "control",
        "facts": "canonical",
        "identity": "derived",
        "stats": "canonical",
    }.get(category)
    if expected_authority is None or authority != expected_authority:
        raise ConsumerManifestError(
            f"{category.upper()}_AUTHORITY_INVALID:{key}"
        )
    if category == "identity":
        _required_sha256(
            entry.get("source_registry_sha256"),
            f"{key}_SOURCE_REGISTRY",
        )
    if rights not in {"public", "copyrighted", "private"}:
        raise ConsumerManifestError(f"ENTRY_RIGHTS_INVALID:{key}")
    return dict(entry)


def build_consumer_manifest(
    *,
    release_id,
    product_version,
    code_commit,
    product_candidate_sha=None,
    surfaces,
    facts=None,
    identity_projection=None,
    consumer_packages=None,
    stats=None,
):
    """Build a deterministic, release-scoped Consumer Manifest."""
    release_id = _required_text(release_id, "RELEASE_ID")
    product_version = _required_text(product_version, "PRODUCT_VERSION")
    code_commit = _required_git_sha(code_commit, "CODE_COMMIT")
    if product_candidate_sha is None:
        # Preserve the v1 manifest-builder API while making the published v2
        # candidate binding explicit.  The v2 release must never inherit the
        # execution commit merely because it was the only SHA supplied.
        if _requires_independent_candidate(release_id, product_version):
            raise ConsumerManifestError("PRODUCT_CANDIDATE_SHA_REQUIRED")
        product_candidate_sha = code_commit
    product_candidate_sha = _required_git_sha(
        product_candidate_sha, "PRODUCT_CANDIDATE_SHA",
    )
    if (_requires_independent_candidate(release_id, product_version)
            and product_candidate_sha == code_commit):
        raise ConsumerManifestError("PRODUCT_CANDIDATE_CODE_COMMIT_COLLAPSED")

    if not isinstance(surfaces, dict):
        raise ConsumerManifestError("SURFACES_DICT_REQUIRED")
    control_entries = {}
    for key in sorted(REQUIRED_CONTROL_KEYS):
        if key not in surfaces:
            raise ConsumerManifestError(f"MISSING_CONTROL_SURFACE:{key}")
        control_entries[key] = _validate_entry(
            key, surfaces[key], category="control",
        )

    facts = facts or {}
    fact_entries = {}
    for key in sorted(facts):
        fact_entries[key] = _validate_entry(key, facts[key], category="facts")
    if not REQUIRED_FACT_KEYS <= set(fact_entries):
        missing = sorted(REQUIRED_FACT_KEYS - set(fact_entries))
        raise ConsumerManifestError(f"MISSING_REQUIRED_FACT:{','.join(missing)}")

    stats = stats or {}
    stats_entries = {}
    for key in sorted(stats):
        stats_entries[key] = _validate_entry(key, stats[key], category="stats")

    if identity_projection is None or not isinstance(identity_projection, dict):
        raise ConsumerManifestError("IDENTITY_PROJECTION_ENTRY_REQUIRED")
    identity_entry = _validate_entry(
        "player_identity_projection", identity_projection, category="identity",
    )

    consumer_surfaces_dict = {
        "control": control_entries,
        "facts": fact_entries,
        "identity": {
            "player_identity_projection": identity_entry,
        },
        "consumer_packages": consumer_packages or {
            "ChatGPT": {"target": "ChatGPT", "status": "AVAILABLE"},
            "Gemini Notebook": {"target": "Gemini Notebook", "status": "AVAILABLE"},
            "WorkBuddy": {"target": "WorkBuddy", "status": "AVAILABLE"},
        },
    }
    if stats_entries:
        consumer_surfaces_dict["stats"] = stats_entries

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "product_version": product_version,
        "product_candidate_sha": product_candidate_sha,
        "code_commit": code_commit,
        "manifest_version": 1,
        "consumer_surfaces": consumer_surfaces_dict,
        "navigation": {
            "bootstrap_rule": [
                "1. Fresh-read live release_status to discover current_release_id and state.",
                "2. Discover exact Consumer Manifest (cba-kb.consumer-manifest.v1).",
                "3. Follow manifest-bound file IDs and hashes for control, facts, and identity.",
            ],
            "discovery_precedence": "LIVE_RELEASE_STATUS_THEN_CONSUMER_MANIFEST",
            "stale_external_snapshot_policy": "LIVE_MANIFEST_OVERRIDES_INDEXED_SNAPSHOTS",
        },
        "rights_profile": {
            "private_registry_exposed": False,
            "private_registry_leakage": 0,
            "default_rights": "public",
        },
    }
    manifest["manifest_sha256"] = digest(canonical_bytes(manifest))
    return manifest


def validate_consumer_manifest(
    manifest_data,
    *,
    expected_release_id=None,
    expected_product_version=PRODUCT_VERSION,
    expected_product_candidate_sha=None,
    expected_code_commit=None,
    artifact_resolver=None,
):
    """Validate a Consumer Manifest for completeness, bindings, and content hashes."""
    if not isinstance(manifest_data, dict):
        raise ConsumerManifestError("CONSUMER_MANIFEST_OBJECT_REQUIRED")
    if manifest_data.get("schema") != SCHEMA:
        raise ConsumerManifestError("CONSUMER_MANIFEST_SCHEMA_INVALID")
    if manifest_data.get("schema_version") != SCHEMA_VERSION:
        raise ConsumerManifestError("CONSUMER_MANIFEST_SCHEMA_VERSION_INVALID")

    release_id = manifest_data.get("release_id")
    if not release_id or not isinstance(release_id, str):
        raise ConsumerManifestError("CONSUMER_MANIFEST_RELEASE_ID_REQUIRED")
    if expected_release_id is not None and release_id != expected_release_id:
        raise ConsumerManifestError(
            f"CONSUMER_MANIFEST_RELEASE_MISMATCH:{release_id}!={expected_release_id}"
        )

    product_version = manifest_data.get("product_version")
    if expected_product_version is not None and product_version != expected_product_version:
        raise ConsumerManifestError(
            f"CONSUMER_MANIFEST_PRODUCT_VERSION_MISMATCH:{product_version}!={expected_product_version}"
        )

    product_candidate_sha = _required_git_sha(
        manifest_data.get("product_candidate_sha"),
        "CONSUMER_MANIFEST_PRODUCT_CANDIDATE_SHA",
    )
    code_commit = _required_git_sha(
        manifest_data.get("code_commit"),
        "CONSUMER_MANIFEST_CODE_COMMIT",
    )
    if (_requires_independent_candidate(release_id, product_version)
            and product_candidate_sha == code_commit):
        raise ConsumerManifestError(
            "CONSUMER_MANIFEST_PRODUCT_CANDIDATE_CODE_COMMIT_COLLAPSED"
        )
    if (expected_product_candidate_sha is not None
            and product_candidate_sha != expected_product_candidate_sha):
        raise ConsumerManifestError(
            "CONSUMER_MANIFEST_PRODUCT_CANDIDATE_SHA_MISMATCH:"
            f"{product_candidate_sha}!={expected_product_candidate_sha}"
        )
    if expected_code_commit is not None and code_commit != expected_code_commit:
        raise ConsumerManifestError(
            f"CONSUMER_MANIFEST_CODE_COMMIT_MISMATCH:{code_commit}!={expected_code_commit}"
        )

    rights_profile = manifest_data.get("rights_profile", {})
    if (
        rights_profile.get("private_registry_exposed") is not False
        or rights_profile.get("private_registry_leakage") != 0
    ):
        raise ConsumerManifestError("CONSUMER_MANIFEST_RIGHTS_VIOLATION")

    surfaces = manifest_data.get("consumer_surfaces")
    if not isinstance(surfaces, dict):
        raise ConsumerManifestError("CONSUMER_MANIFEST_SURFACES_REQUIRED")

    control = surfaces.get("control")
    if not isinstance(control, dict) or not REQUIRED_CONTROL_KEYS <= set(control):
        missing = sorted(REQUIRED_CONTROL_KEYS - set(control or {}))
        raise ConsumerManifestError(f"CONSUMER_MANIFEST_MISSING_CONTROL:{','.join(missing)}")

    facts = surfaces.get("facts")
    if not isinstance(facts, dict) or not REQUIRED_FACT_KEYS <= set(facts):
        missing = sorted(REQUIRED_FACT_KEYS - set(facts or {}))
        raise ConsumerManifestError(f"CONSUMER_MANIFEST_MISSING_FACT:{','.join(missing)}")

    identity = surfaces.get("identity")
    if not isinstance(identity, dict) or not REQUIRED_IDENTITY_KEYS <= set(identity):
        missing = sorted(REQUIRED_IDENTITY_KEYS - set(identity or {}))
        raise ConsumerManifestError(f"CONSUMER_MANIFEST_MISSING_IDENTITY:{','.join(missing)}")

    stats = surfaces.get("stats")
    if stats is not None and not isinstance(stats, dict):
        raise ConsumerManifestError("CONSUMER_MANIFEST_STATS_INVALID")

    # Verify self-hash
    stored_sha = manifest_data.get("manifest_sha256")
    without_hash = {k: v for k, v in manifest_data.items() if k != "manifest_sha256"}
    computed_sha = digest(canonical_bytes(manifest_data))
    computed_without = digest(canonical_bytes(without_hash))
    if stored_sha not in {computed_sha, computed_without}:
        raise ConsumerManifestError("CONSUMER_MANIFEST_HASH_MISMATCH")

    categories = [("control", control), ("facts", facts), ("identity", identity)]
    if stats:
        categories.append(("stats", stats))

    all_entries = {}
    for cat_name, cat_dict in categories:
        for key, entry in cat_dict.items():
            _validate_entry(key, entry, category=cat_name)
            all_entries[key] = entry

    # Exact-read verification if resolver supplied
    if artifact_resolver is not None:
        for key, entry in all_entries.items():
            try:
                raw_bytes = artifact_resolver(entry["id"])
            except Exception as exc:
                raise ConsumerManifestError(
                    f"UNREADABLE_REQUIRED_ARTIFACT:{key}:{entry['id']}"
                ) from exc
            actual_sha = digest(raw_bytes)
            if actual_sha != entry["sha256"]:
                raise ConsumerManifestError(
                    f"REQUIRED_ARTIFACT_HASH_MISMATCH:{key}:expected={entry['sha256']},actual={actual_sha}"
                )

    return {
        "status": "PASS",
        "release_id": release_id,
        "product_version": product_version,
        "product_candidate_sha": product_candidate_sha,
        "code_commit": code_commit,
        "entries_validated": len(all_entries),
        "manifest_sha256": stored_sha,
    }
