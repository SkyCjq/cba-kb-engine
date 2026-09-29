import json

import pytest

from cba_kb.common import digest
from cba_kb.consumer_manifest import build_consumer_manifest
from cba_kb.consumer_projection import build_player_identity_consumer_projection
from cba_kb.release import (
    PreMutationAbort,
    ReleaseContractError,
    freeze_fingerprint,
    validate_freeze_fingerprint_compatibility,
    validate_release_topology,
)
from scripts.prepare_production import release_infra_compatibility_preflight
from test_canonical_registry import SHA
from test_v1_8_1_consumer_closure_repair import sample_real_identity_registry


RELEASE_ID = "v1.8.1-2"


def topology():
    return [
        {"id": "root-random", "role": "ROOT", "parents": []},
        {"id": "current-random", "role": "CURRENT_ZONE", "parents": ["root-random"]},
        {"id": "staging-random", "role": "STAGING_ZONE", "parents": ["root-random"]},
        {"id": "history-random", "role": "HISTORY_ZONE", "parents": ["root-random"]},
        {"id": "evidence-random", "role": "EVIDENCE_ZONE", "parents": ["root-random"]},
        {"id": "target-random", "role": "CURRENT_TARGET", "parents": ["current-random"]},
        {"id": "rollback-random", "role": "ROLLBACK_SNAPSHOT", "parents": ["history-random"],
         "release_id": RELEASE_ID, "target_id": "target-random"},
        {"id": "recovery-random", "role": "RECOVERY_CHECKPOINT", "parents": ["history-random"],
         "release_id": RELEASE_ID, "target_id": "target-random"},
        {"id": "superseded-random", "role": "SUPERSEDED_AUTHORITY", "parents": ["evidence-random"],
         "release_id": RELEASE_ID, "governance": "HUMAN_WEB",
         "predecessor_sha": "1" * 40, "successor_sha": "2" * 40},
    ]


def consumer_bundle():
    projection = build_player_identity_consumer_projection(
        sample_real_identity_registry(), release_id=RELEASE_ID,
        product_version="v1.8.1", code_commit=SHA,
    )
    surfaces = {
        key: {"id": f"id-{key}", "name": key, "sha256": str(index) * 64,
              "mime": "application/json", "authority": "control", "rights": "public"}
        for index, key in enumerate((
            "release_status", "readme", "index", "technical_manual",
            "context_card", "current_version_doc",
        ))
    }
    manifest = build_consumer_manifest(
        release_id=RELEASE_ID,
        product_version="v1.8.1",
        code_commit=SHA,
        surfaces=surfaces,
        facts={"master": {
            "id": "id-master", "name": "master", "sha256": "6" * 64,
            "mime": "application/octet-stream", "authority": "canonical",
            "rights": "public",
        }},
        identity_projection={
            "id": "id-projection", "name": "identity", "sha256": projection["projection_sha256"],
            "mime": "application/json", "authority": "derived", "rights": "public",
            "source_registry_sha256": projection["source_registry_sha256"],
        },
    )
    return manifest, projection


def preflight_bundle():
    plan = {
        "release_id": RELEASE_ID,
        "entries": [{"id": "target-random", "logical_key": "target",
                     "after_hash": "a" * 64}],
    }
    plan_sha = digest(json.dumps(plan, sort_keys=True).encode())
    prepared_sha = "c045ce56126ba52a5b942b0a196e9ee4749b1199314161f2005f869d777f079c"
    observed = {
        "id": "target-random", "mimeType": "application/json",
        "version": "2", "modifiedTime": "2026-09-29T00:00:01Z",
        "parents": ["current-random"],
    }
    frozen_meta = dict(observed, version="1", modifiedTime="2026-09-29T00:00:00Z")
    manifest, projection = consumer_bundle()
    return {
        "plan": plan,
        "plan_sha256": plan_sha,
        "journal": {"state": "PREPARED", "uploaded": {}, "inflight": None},
        "current_runtime_journal_sha256": prepared_sha,
        "freeze_prepared_journal_sha256": prepared_sha,
        "topology": topology(),
        "fingerprints": [{"frozen": freeze_fingerprint(frozen_meta), "observed": observed}],
        "status_doc": {"state": "COMPLETE", "current_release_id": RELEASE_ID,
                       "code_commit": SHA},
        "safe_baseline_release_id": RELEASE_ID,
        "consumer_manifest": manifest,
        "identity_projection": projection,
        "platform_results": {"ChatGPT": "PASS", "Gemini Notebook": "PASS", "WorkBuddy": "PASS"},
    }


def test_topology_validates_semantic_roles_and_ancestors_not_observed_ids():
    result = validate_release_topology(topology(), release_id=RELEASE_ID)
    assert result["status"] == "PASS"
    assert "ROLLBACK_SNAPSHOT" in result["semantic_roles"]
    assert "RECOVERY_CHECKPOINT" in result["semantic_roles"]
    assert "SUPERSEDED_AUTHORITY" in result["semantic_roles"]


@pytest.mark.parametrize("tamper", ["ancestor", "rollback", "superseded"])
def test_topology_contradictions_fail_closed(tamper):
    nodes = topology()
    if tamper == "ancestor":
        next(node for node in nodes if node["role"] == "CURRENT_TARGET")["parents"] = ["history-random"]
    elif tamper == "rollback":
        next(node for node in nodes if node["role"] == "ROLLBACK_SNAPSHOT")["target_id"] = "missing"
    else:
        next(node for node in nodes if node["role"] == "SUPERSEDED_AUTHORITY")["governance"] = "AUTOMATION"
    with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY"):
        validate_release_topology(nodes, release_id=RELEASE_ID)


def test_freeze_fingerprint_separates_identity_from_environment_observation():
    frozen_meta = {"id": "x", "mimeType": "application/json", "version": "1",
                   "modifiedTime": "t1", "parents": ["staging"]}
    frozen = freeze_fingerprint(frozen_meta)
    result = validate_freeze_fingerprint_compatibility(
        frozen, dict(frozen_meta, version="2", modifiedTime="t2", parents=["current"]),
    )
    assert result["immutable_release_identity"] == "EXACT"
    assert result["observed_environment_state"] == "CHANGED"
    with pytest.raises(ReleaseContractError, match="IDENTITY_MISMATCH"):
        validate_freeze_fingerprint_compatibility(
            frozen, dict(frozen_meta, id="other"),
        )


def test_real_path_compatibility_preflight_is_read_only_and_complete():
    bundle = preflight_bundle()
    before = json.loads(json.dumps(bundle))
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"
    assert result["classification"] == "RELEASE_INFRA_COMPATIBILITY_PREFLIGHT_PASS"
    assert result["production_mutation_count"] == 0
    assert result["release_status_mutation_count"] == 0
    assert bundle == before


def test_compatibility_preflight_fails_before_mutation_on_contradiction():
    bundle = preflight_bundle()
    before = json.loads(json.dumps(bundle))
    bundle["platform_results"]["WorkBuddy"] = None
    expected = json.loads(json.dumps(bundle))
    with pytest.raises(PreMutationAbort, match="CONSUMER_ACCEPTANCE_INVALID"):
        release_infra_compatibility_preflight(bundle)
    assert bundle == expected
    assert before["status_doc"] == bundle["status_doc"]
