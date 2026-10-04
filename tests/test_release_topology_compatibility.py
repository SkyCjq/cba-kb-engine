import json

import pytest

from cba_kb.common import digest
from cba_kb.consumer_manifest import ConsumerManifestError, build_consumer_manifest
from cba_kb.consumer_projection import build_player_identity_consumer_projection
from cba_kb.release import (
    PreMutationAbort,
    ReleaseContractError,
    classify_planned_target_relocation,
    freeze_fingerprint,
    runtime_journal_sha256,
    validate_freeze_fingerprint_compatibility,
    validate_release_topology,
    validate_safe_baseline_status,
)
from scripts.prepare_production import (
    build_live_qualification_fingerprints,
    build_release_infra_compatibility_bundle,
    build_release_topology_from_production,
    main as prepare_production_main,
    release_infra_compatibility_preflight,
    run_trusted_release_infra_compatibility_preflight,
)
from test_canonical_registry import SHA
from test_v1_8_1_consumer_closure_repair import sample_real_identity_registry


RELEASE_ID = "v1.8.1-2"


def topology(release_id=RELEASE_ID):
    return [
        {"id": "root-random", "role": "ROOT", "parents": []},
        {"id": "current-random", "role": "CURRENT_ZONE", "parents": ["root-random"]},
        {"id": "staging-random", "role": "STAGING_ZONE", "parents": ["root-random"]},
        {"id": "history-random", "role": "HISTORY_ZONE", "parents": ["root-random"]},
        {"id": "evidence-random", "role": "EVIDENCE_ZONE", "parents": ["root-random"]},
        {"id": "target-random", "role": "CURRENT_TARGET", "parents": ["current-random"]},
        {"id": "rollback-random", "role": "ROLLBACK_SNAPSHOT", "parents": ["history-random"],
         "release_id": release_id, "target_id": "target-random"},
        {"id": "recovery-random", "role": "RECOVERY_CHECKPOINT", "parents": ["history-random"],
         "release_id": release_id, "target_id": "target-random"},
        {"id": "superseded-random", "role": "SUPERSEDED_AUTHORITY", "parents": ["evidence-random"],
         "release_id": release_id, "governance": "HUMAN_WEB",
         "predecessor_sha": "1" * 40, "successor_sha": "2" * 40},
    ]


def mixed_planned_topology(release_id=RELEASE_ID):
    nodes = topology(release_id)
    nodes.extend([
        {"id": "history-target", "role": "HISTORY_TARGET",
         "parents": ["history-random"]},
        {"id": "evidence-target", "role": "EVIDENCE_TARGET",
         "parents": ["evidence-random"]},
    ])
    return nodes


def consumer_bundle(product_version="v1.8.1", release_id=RELEASE_ID):
    projection = build_player_identity_consumer_projection(
        sample_real_identity_registry(), release_id=release_id,
        product_version=product_version, code_commit=SHA,
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
        release_id=release_id,
        product_version=product_version,
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


def preflight_bundle(product_version="v1.8.1", release_id=RELEASE_ID):
    plan = {
        "release_id": release_id,
        "entries": [{"id": "target-random", "logical_key": "target",
                     "after_hash": "a" * 64}],
    }
    plan_sha = digest(json.dumps(plan, sort_keys=True).encode())
    journal = {"state": "PREPARED", "uploaded": {}, "inflight": None}
    prepared_sha = runtime_journal_sha256(journal)
    observed = {
        "id": "target-random", "mimeType": "application/json",
        "version": "2", "modifiedTime": "2026-09-29T00:00:01Z",
        "parents": ["current-random"],
    }
    frozen_meta = dict(observed, version="1", modifiedTime="2026-09-29T00:00:00Z")
    manifest, projection = consumer_bundle(product_version, release_id)
    return {
        "plan": plan,
        "plan_sha256": plan_sha,
        "journal": journal,
        "current_runtime_journal_sha256": prepared_sha,
        "freeze_prepared_journal_sha256": prepared_sha,
        "topology": topology(release_id),
        "observed_target_parents": {"target-random": ["current-random"]},
        "fingerprints": [{"frozen": freeze_fingerprint(frozen_meta), "observed": observed}],
        "status_doc": {"state": "COMPLETE", "current_release_id": release_id,
                       "code_commit": SHA},
        "safe_baseline_release_id": release_id,
        "consumer_manifest": manifest,
        "identity_projection": projection,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
        "expected_product_version": product_version,
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


def test_v200_candidate_manifest_generation_and_independent_preflight_binding():
    first_manifest, _ = consumer_bundle("v2.0.0")
    second_manifest, _ = consumer_bundle("v2.0.0")
    assert first_manifest["product_version"] == "v2.0.0"
    assert first_manifest["manifest_sha256"] == second_manifest["manifest_sha256"]

    result = release_infra_compatibility_preflight(preflight_bundle("v2.0.0"))
    assert result["status"] == "PASS"
    assert result["consumer_manifest"]["product_version"] == "v2.0.0"
    assert result["identity_projection"]["product_version"] == "v2.0.0"


@pytest.mark.parametrize("wrong_version", ["v1.8.1", "v1.9.0"])
def test_v200_candidate_preflight_rejects_forged_or_stale_manifest(wrong_version):
    bundle = preflight_bundle("v2.0.0")
    wrong_manifest, _ = consumer_bundle(wrong_version)
    bundle["consumer_manifest"] = wrong_manifest

    with pytest.raises(
        ConsumerManifestError,
        match=f"CONSUMER_MANIFEST_PRODUCT_VERSION_MISMATCH:{wrong_version}!=v2.0.0",
    ):
        release_infra_compatibility_preflight(bundle)


def test_v190_safe_baseline_is_not_reinterpreted_as_v200():
    bundle = preflight_bundle("v1.9.0", "v1.9.0-2")
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"
    assert result["consumer_manifest"]["product_version"] == "v1.9.0"


def test_candidate_preflight_requires_independent_product_version_authority():
    bundle = preflight_bundle("v2.0.0")
    bundle.pop("expected_product_version")
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_PRODUCT_VERSION_BINDING_INVALID",
    ):
        release_infra_compatibility_preflight(bundle)


def test_compatibility_preflight_fails_before_mutation_on_contradiction():
    bundle = preflight_bundle()
    before = json.loads(json.dumps(bundle))
    bundle["platform_results"]["WorkBuddy"] = None
    expected = json.loads(json.dumps(bundle))
    with pytest.raises(PreMutationAbort, match="CONSUMER_ACCEPTANCE_INVALID"):
        release_infra_compatibility_preflight(bundle)
    assert bundle == expected
    assert before["status_doc"] == bundle["status_doc"]


def test_compatibility_preflight_requires_independent_source_binding():
    bundle = preflight_bundle()
    bundle.pop("expected_source_registry_sha256")
    with pytest.raises(
        PreMutationAbort, match="SOURCE_REGISTRY_BINDING_INVALID",
    ):
        release_infra_compatibility_preflight(bundle)


def test_compatibility_preflight_rejects_unbound_advanced_runtime_sha():
    from cba_kb.release import _runtime_lineage_event, _runtime_transaction_binding

    bundle = preflight_bundle()
    transaction = _runtime_transaction_binding(
        bundle["plan"], bundle["plan_sha256"],
    )
    lineage = []
    previous_sha = bundle["freeze_prepared_journal_sha256"]
    previous_state = "PREPARED"
    for state in (
        "ARCHIVING", "ARCHIVE_COMPLETE", "PUBLISHING", "VERIFYING", "COMPLETE",
    ):
        event = _runtime_lineage_event(
            len(lineage) + 1, previous_sha, previous_state, state, transaction,
        )
        lineage.append(event)
        bundle["journal"] = {
            "state": state,
            "freeze_prepared_journal_sha256": bundle[
                "freeze_prepared_journal_sha256"
            ],
            "runtime_transaction": transaction,
            "runtime_lineage": list(lineage),
        }
        bundle["current_runtime_journal_sha256"] = runtime_journal_sha256(
            bundle["journal"]
        )
        assert bundle["current_runtime_journal_sha256"] != bundle[
            "freeze_prepared_journal_sha256"
        ]
        assert release_infra_compatibility_preflight(bundle)["status"] == "PASS"
        previous_sha, previous_state = event["event_sha256"], state

    bundle["current_runtime_journal_sha256"] = "0" * 64
    with pytest.raises(
        PreMutationAbort, match="CURRENT_RUNTIME_JOURNAL_SHA_INVALID",
    ):
        release_infra_compatibility_preflight(bundle)

    stale_sha = runtime_journal_sha256(bundle["journal"])
    bundle["journal"]["inflight"] = "drift"
    bundle["current_runtime_journal_sha256"] = stale_sha
    with pytest.raises(
        PreMutationAbort, match="CURRENT_RUNTIME_JOURNAL_SHA_INVALID",
    ):
        release_infra_compatibility_preflight(bundle)


class MockDrive:
    def __init__(self, files=None, metas=None):
        self.files = files or {}
        self.metas = metas or {}
        self.puts = []

    def get(self, file_id):
        item = self.files[file_id]
        return item if isinstance(item, bytes) else item.encode("utf-8")

    def meta(self, file_id):
        if file_id in self.metas:
            return dict(self.metas[file_id])
        return {
            "id": file_id,
            "version": "1",
            "modifiedTime": "2026-09-29T00:00:00Z",
            "mimeType": "application/json",
            "parents": ["current-random"],
        }

    def put(self, file_id, data, mime):
        self.puts.append((file_id, data, mime))


class MockInstance:
    def __init__(self, configs=None, root=None):
        self.configs = configs or {}
        self.root = root

    def read_json(self, name):
        if name in self.configs:
            return self.configs[name]
        raise RuntimeError(f"CONFIG_MISSING: {name}")

    def config_path(self, name):
        if self.root is not None:
            return Path(self.root) / "config" / name
        return Path("/mock/config") / name


def test_safe_baseline_status_complete_positive_and_negative():
    status = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    assert validate_safe_baseline_status(status, RELEASE_ID) is True

    # Mismatched current_release_id
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SAFE_BASELINE_INVALID"):
        validate_safe_baseline_status(status, "other-release")

    # Pending release state present
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SAFE_BASELINE_INVALID"):
        validate_safe_baseline_status(
            dict(status, pending_release_id="pending"), RELEASE_ID,
        )


def test_safe_baseline_status_exact_v181_rollback_fixture():
    status = {
        "state": "ROLLED_BACK",
        "current_release_id": "v1.8.1-1",
        "rolled_back_release_id": "v1.8.1-2",
        "code_commit": SHA,
    }
    assert validate_safe_baseline_status(status, "v1.8.1-1") is True


@pytest.mark.parametrize("case", [
    "missing_rolled_back",
    "arbitrary_rolled_back",
    "wrong_baseline_release",
    "self_rolled_back",
    "pending_publication",
    "transient_publishing",
    "transient_verifying",
    "transient_rolling_back",
    "transient_failed",
])
def test_safe_baseline_status_rejects_arbitrary_and_inconsistent_rollbacks(case):
    status = {
        "state": "ROLLED_BACK",
        "current_release_id": "v1.8.1-1",
        "rolled_back_release_id": "v1.8.1-2",
    }
    safe_baseline = "v1.8.1-1"
    if case == "missing_rolled_back":
        status.pop("rolled_back_release_id")
    elif case == "arbitrary_rolled_back":
        status["rolled_back_release_id"] = "arbitrary"
    elif case == "wrong_baseline_release":
        status["current_release_id"] = "v1.8.0-1"
    elif case == "self_rolled_back":
        status["rolled_back_release_id"] = "v1.8.1-1"
    elif case == "pending_publication":
        status["pending_release_id"] = "v1.9.0-1"
    elif case == "transient_publishing":
        status["state"] = "PUBLISHING"
    elif case == "transient_verifying":
        status["state"] = "VERIFYING"
    elif case == "transient_rolling_back":
        status["state"] = "ROLLING_BACK"
    elif case == "transient_failed":
        status["state"] = "FAILED"

    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SAFE_BASELINE_INVALID"):
        validate_safe_baseline_status(status, safe_baseline)


def _setup_evidence_root(tmp_path, release_id=RELEASE_ID, current_release_id=RELEASE_ID):
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    plan = {
        "release_id": release_id,
        "entries": [{"id": "target-random", "logical_key": "target", "after_hash": "a" * 64}],
    }
    journal = {"state": "PREPARED", "uploaded": {}, "inflight": None}
    projection = build_player_identity_consumer_projection(
        sample_real_identity_registry(), release_id=current_release_id,
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
        release_id=current_release_id,
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
    platform_results = {"ChatGPT": "PASS", "Gemini Notebook": "PASS", "WorkBuddy": "PASS"}

    (evidence_root / "plan.json").write_text(json.dumps(plan))
    (evidence_root / "journal.json").write_text(json.dumps(journal))
    (evidence_root / "consumer_manifest.json").write_text(json.dumps(manifest))
    (evidence_root / "identity_projection.json").write_text(json.dumps(projection))
    (evidence_root / "platform_results.json").write_text(json.dumps(platform_results))
    (evidence_root / "topology.json").write_text(json.dumps(topology(release_id=release_id)))
    (evidence_root / "source_registry_sha256.txt").write_text(projection["source_registry_sha256"])
    return evidence_root, plan, journal, manifest, projection, platform_results


def test_trusted_bundle_builder_real_path_and_qualification_fingerprints(tmp_path):
    evidence_root, plan, journal, manifest, projection, _ = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID, "code_commit": SHA}
    status_raw = json.dumps(status_doc).encode("utf-8")

    drive = MockDrive(
        files={"status-drive-id": status_raw},
        metas={
            "status-drive-id": {
                "id": "status-drive-id", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["root-random"],
            },
            "target-random": {
                "id": "target-random", "version": "2", "modifiedTime": "2026-09-29T00:00:01Z",
                "mimeType": "application/json", "parents": ["current-random"],
            },
        },
    )
    instance = MockInstance(
        configs={
            "production.json": {
                "enabled": True,
                "status_id": "status-drive-id",
                "safe_baseline_release_id": RELEASE_ID,
                "targets": {"target-random": {"mime": "application/json"}},
            },
        },
    )

    bundle = build_release_infra_compatibility_bundle(
        instance=instance,
        drive=drive,
        evidence_root=evidence_root,
        source_registry_sha256=projection["source_registry_sha256"],
    )

    # P6: Qualification fingerprint construction uses real metadata-shaped reads and explicitly distinguishes
    # live qualification snapshot from candidate freeze authority
    assert len(bundle["fingerprints"]) == 1
    fp_pair = bundle["fingerprints"][0]
    assert fp_pair["frozen"]["qualification_snapshot_label"] == "LIVE QUALIFICATION SNAPSHOT"
    assert fp_pair["frozen"]["is_candidate_freeze_authority"] is False
    assert fp_pair["observed"]["id"] == "target-random"

    # P4: The builder ultimately invokes the real validate_release_infra_compatibility
    # P5: Real-path preflight result proves production_mutation_count = 0, release_status_mutation_count = 0
    result = run_trusted_release_infra_compatibility_preflight(
        instance=instance,
        drive=drive,
        evidence_root=evidence_root,
        source_registry_sha256=projection["source_registry_sha256"],
    )
    assert result["status"] == "PASS"
    assert result["classification"] == "RELEASE_INFRA_COMPATIBILITY_PREFLIGHT_PASS"
    assert result["production_mutation_count"] == 0
    assert result["release_status_mutation_count"] == 0
    assert drive.puts == []


def test_trusted_bundle_builder_v181_rollback_world(tmp_path):
    # P2: Exact bounded v1.8.1 rollback-safe fixture: current_release_id = v1.8.1-1, legal rollback lineage
    evidence_root, plan, journal, manifest, projection, _ = _setup_evidence_root(
        tmp_path, release_id="v1.8.1-3", current_release_id="v1.8.1-1",
    )
    status_doc = {
        "state": "ROLLED_BACK",
        "current_release_id": "v1.8.1-1",
        "rolled_back_release_id": "v1.8.1-2",
        "code_commit": SHA,
    }
    status_raw = json.dumps(status_doc).encode("utf-8")

    drive = MockDrive(
        files={"status-drive-id": status_raw},
        metas={
            "status-drive-id": {
                "id": "status-drive-id", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["root-random"],
            },
            "target-random": {
                "id": "target-random", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["current-random"],
            },
        },
    )
    instance = MockInstance(
        configs={
            "production.json": {
                "enabled": True,
                "status_id": "status-drive-id",
                "safe_baseline_release_id": "v1.8.1-1",
                "targets": {"target-random": {"mime": "application/json"}},
            },
        },
    )

    result = run_trusted_release_infra_compatibility_preflight(
        instance=instance,
        drive=drive,
        evidence_root=evidence_root,
        safe_baseline_release_id="v1.8.1-1",
        source_registry_sha256=projection["source_registry_sha256"],
    )
    assert result["status"] == "PASS"
    assert result["safe_baseline_release_id"] == "v1.8.1-1"
    assert result["production_mutation_count"] == 0
    assert result["release_status_mutation_count"] == 0
    assert drive.puts == []


@pytest.mark.parametrize("missing_kind", [
    "production_policy",
    "policy_status_id",
    "topology_source",
    "consumer_manifest",
    "identity_projection",
    "source_registry_sha",
    "platform_evidence",
    "platform_non_pass",
    "plan_locator",
    "journal_locator",
])
def test_trusted_builder_source_authority_fail_closed(tmp_path, missing_kind):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    status_raw = json.dumps(status_doc).encode("utf-8")

    drive = MockDrive(
        files={"status-drive-id": status_raw},
        metas={
            "status-drive-id": {
                "id": "status-drive-id", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["root-random"],
            },
            "target-random": {
                "id": "target-random", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["current-random"],
            },
        },
    )
    policy = {
        "enabled": True,
        "status_id": "status-drive-id",
        "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
        "targets": {"target-random": {"mime": "application/json"}},
    }
    instance = MockInstance(configs={"production.json": policy})

    kwargs = {
        "instance": instance,
        "drive": drive,
        "evidence_root": evidence_root,
    }

    if missing_kind == "production_policy":
        policy["enabled"] = False
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_PRODUCTION_POLICY"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "policy_status_id":
        policy.pop("status_id")
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_PRODUCTION_POLICY"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "topology_source":
        (evidence_root / "topology.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_TOPOLOGY_SOURCE_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "consumer_manifest":
        (evidence_root / "consumer_manifest.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_CONSUMER_MANIFEST_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "identity_projection":
        (evidence_root / "identity_projection.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_IDENTITY_PROJECTION_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "source_registry_sha":
        policy.pop("expected_source_registry_sha256")
        (evidence_root / "source_registry_sha256.txt").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SOURCE_REGISTRY_BINDING_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "platform_evidence":
        (evidence_root / "platform_results.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_PLATFORM_RESULTS_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "platform_non_pass":
        non_pass = {"ChatGPT": "PASS", "Gemini Notebook": "FAIL", "WorkBuddy": "PASS"}
        (evidence_root / "platform_results.json").write_text(json.dumps(non_pass))
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_PLATFORM_NON_PASS"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "plan_locator":
        (evidence_root / "plan.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_PLAN_LOCATOR_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)
    elif missing_kind == "journal_locator":
        (evidence_root / "journal.json").unlink()
        with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_JOURNAL_LOCATOR_MISSING"):
            build_release_infra_compatibility_bundle(**kwargs)


def test_compat_preflight_cli_option_a(tmp_path, monkeypatch):
    evidence_root, plan, journal, manifest, projection, _ = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID, "code_commit": SHA}
    status_raw = json.dumps(status_doc).encode("utf-8")

    instance_root = tmp_path / "private_instance"
    (instance_root / "config").mkdir(parents=True)
    policy = {
        "enabled": True,
        "status_id": "status-drive-id",
        "safe_baseline_release_id": RELEASE_ID,
        "targets": {"target-random": {"mime": "application/json"}},
    }
    (instance_root / "config" / "production.json").write_text(json.dumps(policy))

    drive = MockDrive(
        files={"status-drive-id": status_raw},
        metas={
            "status-drive-id": {
                "id": "status-drive-id", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["root-random"],
            },
            "target-random": {
                "id": "target-random", "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
                "mimeType": "application/json", "parents": ["current-random"],
            },
        },
    )
    from scripts import prepare_production as pp_mod
    monkeypatch.setattr(pp_mod, "Drive", lambda engine_root, instance: drive)

    (instance_root / "data").mkdir(parents=True, exist_ok=True)
    out_file = instance_root / "data" / "result.json"
    result = prepare_production_main([
        "compat-preflight",
        "--instance-root", str(instance_root),
        "--evidence-root", str(evidence_root),
        "--source-registry-sha", projection["source_registry_sha256"],
        "--product-version", "v1.8.1",
        "--output", str(out_file),
    ])

    assert result["status"] == "PASS"
    assert result["classification"] == "RELEASE_INFRA_COMPATIBILITY_PREFLIGHT_PASS"
    assert result["production_mutation_count"] == 0
    assert result["release_status_mutation_count"] == 0
    assert out_file.is_file()
    saved = json.loads(out_file.read_text())
    assert saved["status"] == "PASS"


def test_c2_blocker_a_explicit_independent_expected_positive(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {"enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID}
    instance = MockInstance(configs={"production.json": policy})
    x_sha = projection["source_registry_sha256"]

    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
        source_registry_sha256=x_sha,
    )
    assert bundle["expected_source_registry_sha256"] == x_sha
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"


def test_c2_blocker_a_independent_expected_mismatch_forged_projection_manifest_fails(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {"enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID}
    instance = MockInstance(configs={"production.json": policy})

    x_sha = "1" * 64
    y_sha = "2" * 64
    projection["source_registry_sha256"] = y_sha
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
            "source_registry_sha256": y_sha,
        },
    )
    (evidence_root / "identity_projection.json").write_text(json.dumps(projection))
    (evidence_root / "consumer_manifest.json").write_text(json.dumps(manifest))

    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
        source_registry_sha256=x_sha,
    )
    from cba_kb.consumer_projection import ConsumerProjectionError
    with pytest.raises(ConsumerProjectionError, match="SOURCE_REGISTRY_BINDING_MISMATCH"):
        release_infra_compatibility_preflight(bundle)


def test_c2_blocker_a_local_source_registry_file_not_trusted_as_authority(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    # Policy has no expected_source_registry_sha256
    policy = {"enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID}
    instance = MockInstance(configs={"production.json": policy})

    y_sha = "2" * 64
    projection["source_registry_sha256"] = y_sha
    manifest["consumer_surfaces"]["identity"]["player_identity_projection"]["source_registry_sha256"] = y_sha
    (evidence_root / "identity_projection.json").write_text(json.dumps(projection))
    (evidence_root / "consumer_manifest.json").write_text(json.dumps(manifest))
    (evidence_root / "source_registry_sha256.txt").write_text(y_sha)

    # Local file exists with Y, projection has Y, manifest has Y, but NO explicit or policy independent authority
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SOURCE_REGISTRY_BINDING_MISSING"):
        build_release_infra_compatibility_bundle(
            instance=instance, drive=drive, evidence_root=evidence_root,
        )


def test_c2_blocker_a_no_independent_authority_fails_closed(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    (evidence_root / "source_registry_sha256.txt").unlink()
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {"enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID}
    instance = MockInstance(configs={"production.json": policy})

    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_SOURCE_REGISTRY_BINDING_MISSING"):
        build_release_infra_compatibility_bundle(
            instance=instance, drive=drive, evidence_root=evidence_root,
        )


def test_c2_blocker_a_independent_source_registry_binding_from_policy(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    (evidence_root / "source_registry_sha256.txt").unlink()
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {
        "enabled": True,
        "status_id": "status-drive-id",
        "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
    )
    assert bundle["expected_source_registry_sha256"] == projection["source_registry_sha256"]
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"


def test_blocker_b_semantic_target_role_current_target_positive(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
    )
    target_node = next(n for n in bundle["topology"] if n["id"] == "target-random")
    assert target_node["role"] == "CURRENT_TARGET"
    assert target_node["parents"] == ["current-random"]
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"


def test_blocker_b_semantic_target_role_staging_target_positive(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    top = topology(release_id=RELEASE_ID)
    target_node = next(n for n in top if n["id"] == "target-random")
    target_node["role"] = "STAGING_TARGET"
    target_node["parents"] = ["staging-random"]
    (evidence_root / "topology.json").write_text(json.dumps(top))

    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["staging-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
    )
    bundled_target = next(n for n in bundle["topology"] if n["id"] == "target-random")
    assert bundled_target["role"] == "STAGING_TARGET"
    assert bundled_target["parents"] == ["staging-random"]
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"


def test_blocker_b_semantic_target_role_container_only_fails_closed(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    container_only = [
        {"id": "root-random", "role": "ROOT", "parents": []},
        {"id": "current-random", "role": "CURRENT_ZONE", "parents": ["root-random"]},
        {"id": "staging-random", "role": "STAGING_ZONE", "parents": ["root-random"]},
        {"id": "history-random", "role": "HISTORY_ZONE", "parents": ["root-random"]},
        {"id": "evidence-random", "role": "EVIDENCE_ZONE", "parents": ["root-random"]},
    ]
    (evidence_root / "topology.json").write_text(json.dumps(container_only))

    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_TOPOLOGY_ROLE_AUTHORITY_MISSING"):
        build_release_infra_compatibility_bundle(
            instance=instance, drive=drive, evidence_root=evidence_root,
        )


def test_blocker_b_semantic_target_role_missing_target_fails_even_if_drive_parent_current(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    # Topology missing target-random entirely
    top = [n for n in topology(release_id=RELEASE_ID) if n["id"] != "target-random"]
    (evidence_root / "topology.json").write_text(json.dumps(top))

    # Drive says target-random parent is current-random (CURRENT_ZONE)
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    # Must fail closed: fresh parent in CURRENT_ZONE does NOT invent CURRENT_TARGET role
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_TOPOLOGY_ROLE_AUTHORITY_MISSING"):
        build_release_infra_compatibility_bundle(
            instance=instance, drive=drive, evidence_root=evidence_root,
        )


def test_blocker_b_semantic_target_role_current_target_in_history_zone_fails(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    # Authoritative topology has target-random as CURRENT_TARGET
    top = topology(release_id=RELEASE_ID)
    (evidence_root / "topology.json").write_text(json.dumps(top))

    # Fresh Drive observation says target parent is history-random
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["history-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
    )
    # Semantic role and semantic parent stay frozen, the fresh parent is separate
    bundled_target = next(n for n in bundle["topology"] if n["id"] == "target-random")
    assert bundled_target["role"] == "CURRENT_TARGET"
    assert bundled_target["parents"] == ["current-random"]
    assert bundle["observed_target_parents"]["target-random"] == ["history-random"]
    # No explicit relocation record for this entry -> fail closed
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_EXPLICIT_RELOCATION_INVALID"
    ):
        release_infra_compatibility_preflight(bundle)


def test_blocker_b_fresh_parent_observation_never_overwrites_semantic_topology(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    top = topology(release_id=RELEASE_ID)
    (evidence_root / "topology.json").write_text(json.dumps(top))
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    # Fresh Drive observation has parents = ["staging-random"]
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["staging-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
    )
    bundled_target = next(n for n in bundle["topology"] if n["id"] == "target-random")
    # Semantic role and semantic parent are authoritative and must not be overwritten
    assert bundled_target["role"] == "CURRENT_TARGET"
    assert bundled_target["parents"] == ["current-random"]
    assert bundle["observed_target_parents"]["target-random"] == ["staging-random"]
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_EXPLICIT_RELOCATION_INVALID"
    ):
        release_infra_compatibility_preflight(bundle)


@pytest.mark.parametrize("scenario", [
    "container_only",
    "missing_target",
    "unexpected_target",
    "duplicate_in_topology",
    "duplicate_in_plan",
    "wrong_role",
    "wrong_ancestor",
])
def test_topology_planned_targets_coverage_diagnostics(scenario):
    base_nodes = topology()
    release_id = RELEASE_ID
    if scenario == "container_only":
        nodes = [n for n in base_nodes if n["role"] not in {"CURRENT_TARGET", "STAGING_TARGET", "ROLLBACK_SNAPSHOT", "RECOVERY_CHECKPOINT"}]
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_CONTAINER_ONLY_FORBIDDEN"):
            validate_release_topology(nodes, release_id=release_id, planned_target_ids=["target-random"])
    elif scenario == "missing_target":
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_TARGET_MISSING"):
            validate_release_topology(base_nodes, release_id=release_id, planned_target_ids=["target-random", "target-other"])
    elif scenario == "unexpected_target":
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_TARGET_UNEXPECTED"):
            validate_release_topology(base_nodes, release_id=release_id, planned_target_ids=[])
    elif scenario == "duplicate_in_topology":
        dup_nodes = list(base_nodes) + [{"id": "target-random", "role": "CURRENT_TARGET", "parents": ["current-random"]}]
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_TARGET_DUPLICATE"):
            validate_release_topology(dup_nodes, release_id=release_id, planned_target_ids=["target-random"])
    elif scenario == "duplicate_in_plan":
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_TARGET_DUPLICATE"):
            validate_release_topology(base_nodes, release_id=release_id, planned_target_ids=["target-random", "target-random"])
    elif scenario == "wrong_role":
        nodes = [dict(n) for n in base_nodes if n["role"] not in {"ROLLBACK_SNAPSHOT", "RECOVERY_CHECKPOINT"}]
        t = next(n for n in nodes if n["id"] == "target-random")
        t["role"] = "CURRENT_ZONE"
        t["parents"] = ["root-random"]
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_ROLE_INVALID"):
            validate_release_topology(nodes, release_id=release_id, planned_target_ids=["target-random"])
    elif scenario == "wrong_ancestor":
        nodes = [dict(n) for n in base_nodes]
        t = next(n for n in nodes if n["id"] == "target-random")
        t["parents"] = ["history-random"]
        with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_ANCESTOR_INVALID"):
            validate_release_topology(nodes, release_id=release_id, planned_target_ids=["target-random"])


def test_r2_mixed_planned_roles_pass_exact_set_validation():
    result = validate_release_topology(
        mixed_planned_topology(), release_id=RELEASE_ID,
        planned_target_ids=["target-random", "history-target", "evidence-target"],
    )
    assert result["status"] == "PASS"
    assert {"CURRENT_TARGET", "HISTORY_TARGET", "EVIDENCE_TARGET"} <= set(
        result["semantic_roles"]
    )


@pytest.mark.parametrize(("role", "wrong_parent"), [
    ("HISTORY_TARGET", "current-random"),
    ("EVIDENCE_TARGET", "history-random"),
])
def test_r2_noncurrent_planned_roles_require_exact_zone(role, wrong_parent):
    nodes = mixed_planned_topology()
    node = next(item for item in nodes if item["role"] == role)
    node["parents"] = [wrong_parent]
    with pytest.raises(
        ReleaseContractError, match="RELEASE_TOPOLOGY_ANCESTOR_INVALID"
    ):
        validate_release_topology(
            nodes, release_id=RELEASE_ID,
            planned_target_ids=["target-random", "history-target", "evidence-target"],
        )


@pytest.mark.parametrize(
    "special_id", ["rollback-random", "recovery-random", "superseded-random"]
)
def test_r2_special_roles_cannot_satisfy_planned_target_coverage(special_id):
    with pytest.raises(ReleaseContractError, match="RELEASE_TOPOLOGY_ROLE_INVALID"):
        validate_release_topology(
            topology(), release_id=RELEASE_ID,
            planned_target_ids=[special_id],
        )


def test_retired_target_remaining_in_planned_topology_fails_exact_set_validation():
    with pytest.raises(
        ReleaseContractError, match="RELEASE_TOPOLOGY_TARGET_UNEXPECTED"
    ):
        validate_release_topology(
            topology(), release_id=RELEASE_ID, planned_target_ids=[]
        )


def _relocation_target_drive(fresh_parent):
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    return MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": [fresh_parent]},
        },
    )


def test_bundle_builder_preserves_semantic_parent_and_accepts_explicit_relocation(tmp_path):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    plan["entries"][0].update({
        "staging_parent": "staging-random",
        "publish_parent": "current-random",
    })
    (evidence_root / "plan.json").write_text(json.dumps(plan))
    drive = _relocation_target_drive("staging-random")
    policy = {"enabled": True, "status_id": "status-drive-id", "safe_baseline_release_id": RELEASE_ID}
    instance = MockInstance(configs={"production.json": policy})

    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
        source_registry_sha256=projection["source_registry_sha256"],
    )
    target_in_bundle = next(n for n in bundle["topology"] if n["id"] == "target-random")
    # The semantic parent is never overwritten by the fresh observation
    assert target_in_bundle["parents"] == ["current-random"]
    assert bundle["observed_target_parents"]["target-random"] == ["staging-random"]
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"
    assert result["relocations"]["unchanged"] == 0
    assert result["relocations"]["explicit_relocation"] == 1
    assert result["relocations"]["targets"] == [{
        "status": "PASS",
        "classification": "EXPLICIT_RELOCATION",
        "target_id": "target-random",
        "semantic_parents": ["current-random"],
        "observed_parents": ["staging-random"],
        "staging_parent": "staging-random",
        "publish_parent": "current-random",
    }]


@pytest.mark.parametrize(("role", "parent"), [
    ("HISTORY_TARGET", "history-random"),
    ("EVIDENCE_TARGET", "evidence-random"),
])
def test_r2_bundle_builder_accepts_noncurrent_planned_role_authority(
        tmp_path, role, parent):
    evidence_root, _, _, _, projection, _ = _setup_evidence_root(tmp_path)
    top = [
        node for node in topology()
        if node["role"] not in {"ROLLBACK_SNAPSHOT", "RECOVERY_CHECKPOINT"}
    ]
    target = next(node for node in top if node["id"] == "target-random")
    target.update(role=role, parents=[parent])
    (evidence_root / "topology.json").write_text(json.dumps(top))
    status_doc = {"state": "COMPLETE", "current_release_id": RELEASE_ID}
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {
                "id": "status-drive-id", "version": "1",
                "modifiedTime": "t0", "mimeType": "application/json",
                "parents": ["root-random"],
            },
            "target-random": {
                "id": "target-random", "version": "1",
                "modifiedTime": "t0", "mimeType": "application/json",
                "parents": [parent],
            },
        },
    )
    instance = MockInstance(configs={
        "production.json": {
            "enabled": True, "status_id": "status-drive-id",
            "safe_baseline_release_id": RELEASE_ID,
        },
    })
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
        source_registry_sha256=projection["source_registry_sha256"],
    )
    assert next(
        node for node in bundle["topology"] if node["id"] == "target-random"
    )["role"] == role
    result = release_infra_compatibility_preflight(bundle)
    assert result["status"] == "PASS"
    assert result["relocations"]["unchanged"] == 1


@pytest.mark.parametrize("spoofed_parent", [
    "staging-random",
    "history-random",
])
def test_spoofed_semantic_topology_parent_fails_closed(tmp_path, spoofed_parent):
    evidence_root, plan, journal, manifest, projection, platform_results = _setup_evidence_root(tmp_path)
    plan["entries"][0].update({
        "staging_parent": "staging-random",
        "publish_parent": "current-random",
    })
    (evidence_root / "plan.json").write_text(json.dumps(plan))
    spoofed_topology = topology()
    target_node = next(n for n in spoofed_topology if n["id"] == "target-random")
    target_node["parents"] = [spoofed_parent]
    (evidence_root / "topology.json").write_text(json.dumps(spoofed_topology))

    instance = MockInstance(configs={
        "production.json": {
            "enabled": True, "status_id": "status-drive-id",
            "safe_baseline_release_id": RELEASE_ID,
        },
    })
    bundle = build_release_infra_compatibility_bundle(
        instance=instance, drive=_relocation_target_drive("staging-random"),
        evidence_root=evidence_root,
        source_registry_sha256=projection["source_registry_sha256"],
    )
    target_in_bundle = next(n for n in bundle["topology"] if n["id"] == "target-random")
    assert target_in_bundle["parents"] == [spoofed_parent]
    with pytest.raises(ReleaseContractError):
        release_infra_compatibility_preflight(bundle)


def _relocation_verdict(*, node=None, entry=None, observed=None, index=None):
    default_node = {
        "id": "target-random", "role": "CURRENT_TARGET",
        "parents": ["current-random"],
    }
    default_entry = {
        "id": "target-random", "staging_parent": "staging-random",
        "publish_parent": "current-random",
    }
    default_index = {
        "target-random": default_node,
        "current-random": {
            "id": "current-random", "role": "CURRENT_ZONE",
            "parents": ["root-random"],
        },
        "staging-random": {
            "id": "staging-random", "role": "STAGING_ZONE",
            "parents": ["root-random"],
        },
    }
    return classify_planned_target_relocation(
        default_node if node is None else node,
        default_entry if entry is None else entry,
        ["staging-random"] if observed is None else observed,
        topology_index=default_index if index is None else index,
    )


def test_section18_positive_oracles_unchanged_and_explicit_relocation():
    unchanged = _relocation_verdict(observed=["current-random"])
    assert unchanged["classification"] == "UNCHANGED"
    assert unchanged["semantic_parents"] == ["current-random"]
    relocation = _relocation_verdict()
    assert relocation["classification"] == "EXPLICIT_RELOCATION"
    assert relocation["staging_parent"] == "staging-random"
    assert relocation["publish_parent"] == "current-random"


@pytest.mark.parametrize(("role", "zone_role", "parent"), [
    ("HISTORY_TARGET", "HISTORY_ZONE", "history-random"),
    ("EVIDENCE_TARGET", "EVIDENCE_ZONE", "evidence-random"),
])
def test_r2_noncurrent_planned_relocation_unchanged_and_explicit(
        role, zone_role, parent):
    node = {"id": "target-random", "role": role, "parents": [parent]}
    index = {
        "target-random": node,
        parent: {"id": parent, "role": zone_role, "parents": ["root-random"]},
        "staging-random": {
            "id": "staging-random", "role": "STAGING_ZONE",
            "parents": ["root-random"],
        },
    }
    entry = {
        "id": "target-random", "staging_parent": "staging-random",
        "publish_parent": parent,
    }
    unchanged = classify_planned_target_relocation(
        node, entry, [parent], topology_index=index,
    )
    assert unchanged["classification"] == "UNCHANGED"
    relocated = classify_planned_target_relocation(
        node, entry, ["staging-random"], topology_index=index,
    )
    assert relocated["classification"] == "EXPLICIT_RELOCATION"

    wrong_zone_index = dict(index)
    wrong_zone_index[parent] = dict(index[parent], role="CURRENT_ZONE")
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_EXPLICIT_RELOCATION_INVALID"
    ):
        classify_planned_target_relocation(
            node, entry, ["staging-random"], topology_index=wrong_zone_index,
        )


def test_r2_staging_target_mismatch_remains_fail_closed():
    node = {
        "id": "target-random", "role": "STAGING_TARGET",
        "parents": ["staging-random"],
    }
    entry = {
        "id": "target-random", "staging_parent": "other-staging",
        "publish_parent": "staging-random",
    }
    index = {
        "target-random": node,
        "staging-random": {
            "id": "staging-random", "role": "STAGING_ZONE",
            "parents": ["root-random"],
        },
    }
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_EXPLICIT_RELOCATION_INVALID"
    ):
        classify_planned_target_relocation(
            node, entry, ["other-staging"], topology_index=index,
        )


@pytest.mark.parametrize("case", [
    "two_observed_parents",
    "no_observed_parent",
    "missing_staging_parent",
    "missing_publish_parent",
    "observed_not_staging_parent",
    "semantic_not_publish_parent",
    "staging_equals_publish",
    "publish_parent_not_in_topology",
    "publish_parent_not_current_zone",
    "node_role_not_current_target",
])
def test_section18_negative_oracles_fail_closed(case):
    node = {
        "id": "target-random", "role": "CURRENT_TARGET",
        "parents": ["current-random"],
    }
    entry = {
        "id": "target-random", "staging_parent": "staging-random",
        "publish_parent": "current-random",
    }
    observed = ["staging-random"]
    index = {
        "target-random": node,
        "current-random": {
            "id": "current-random", "role": "CURRENT_ZONE",
            "parents": ["root-random"],
        },
        "staging-random": {
            "id": "staging-random", "role": "STAGING_ZONE",
            "parents": ["root-random"],
        },
    }
    if case == "two_observed_parents":
        observed = ["staging-random", "current-random"]
    elif case == "no_observed_parent":
        observed = []
    elif case == "missing_staging_parent":
        entry = {"id": "target-random", "publish_parent": "current-random"}
    elif case == "missing_publish_parent":
        entry = {"id": "target-random", "staging_parent": "staging-random"}
    elif case == "observed_not_staging_parent":
        observed = ["history-random"]
    elif case == "semantic_not_publish_parent":
        node = dict(node, parents=["other-random"])
    elif case == "staging_equals_publish":
        entry = {
            "id": "target-random", "staging_parent": "current-random",
            "publish_parent": "current-random",
        }
    elif case == "publish_parent_not_in_topology":
        entry = {
            "id": "target-random", "staging_parent": "staging-random",
            "publish_parent": "missing-zone",
        }
    elif case == "publish_parent_not_current_zone":
        index = dict(index, **{
            "current-random": {
                "id": "current-random", "role": "HISTORY_ZONE",
                "parents": ["root-random"],
            },
        })
    elif case == "node_role_not_current_target":
        node = dict(node, role="STAGING_TARGET")
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_EXPLICIT_RELOCATION_INVALID"
    ):
        classify_planned_target_relocation(
            node, entry, observed, topology_index=index,
        )


def test_preflight_requires_fresh_parent_observation_for_every_planned_target():
    bundle = preflight_bundle()
    missing = dict(bundle)
    missing["observed_target_parents"] = {}
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_TARGET_PARENT_OBSERVATION_REQUIRED"
    ):
        release_infra_compatibility_preflight(missing)
    absent = dict(bundle)
    absent.pop("observed_target_parents")
    with pytest.raises(
        PreMutationAbort, match="RELEASE_INFRA_TARGET_PARENT_OBSERVATION_REQUIRED"
    ):
        release_infra_compatibility_preflight(absent)


class SequentialMockDrive:
    def __init__(self, metas_sequence, files=None):
        self.metas_sequence = {k: list(v) for k, v in metas_sequence.items()}
        self.files = files or {}
        self.puts = []

    def get(self, file_id):
        item = self.files[file_id]
        return item if isinstance(item, bytes) else item.encode("utf-8")

    def meta(self, file_id):
        if file_id in self.metas_sequence and self.metas_sequence[file_id]:
            return dict(self.metas_sequence[file_id].pop(0))
        return {
            "id": file_id, "version": "1", "modifiedTime": "2026-09-29T00:00:00Z",
            "mimeType": "application/json", "parents": ["current-random"],
        }


def test_qualification_fingerprint_two_read_sequential_stability_and_drift():
    target_id = "target-random"
    read1 = {"id": target_id, "version": "1", "modifiedTime": "2026-09-29T00:00:00Z", "mimeType": "application/json", "parents": ["current-random"]}
    read2_identical = dict(read1)
    read2_drift_version = dict(read1, version="2", modifiedTime="2026-09-29T00:00:01Z")
    read2_drift_parent = dict(read1, parents=["staging-random"])
    read2_mismatch_mime = dict(read1, mimeType="application/octet-stream")

    # Case 1: Identical sequential reads -> UNCHANGED
    drive_stable = SequentialMockDrive({target_id: [read1, read2_identical]})
    fps = build_live_qualification_fingerprints(drive_stable, [target_id])
    assert len(fps) == 1
    assert fps[0]["frozen"]["qualification_snapshot_label"] == "LIVE QUALIFICATION SNAPSHOT"
    assert fps[0]["frozen"]["is_candidate_freeze_authority"] is False
    check = validate_freeze_fingerprint_compatibility(fps[0]["frozen"], fps[0]["observed"])
    assert check["observed_environment_state"] == "UNCHANGED"

    # Case 2: Mutable drift -> CHANGED. PASS without require_environmental_stability, FAIL with it.
    drive_drift = SequentialMockDrive({target_id: [read1, read2_drift_version]})
    fps_drift = build_live_qualification_fingerprints(drive_drift, [target_id])
    check_drift = validate_freeze_fingerprint_compatibility(fps_drift[0]["frozen"], fps_drift[0]["observed"])
    assert check_drift["observed_environment_state"] == "CHANGED"

    bundle = preflight_bundle()
    bundle["fingerprints"] = fps_drift
    res = release_infra_compatibility_preflight(bundle)
    assert res["status"] == "PASS"

    bundle["require_environmental_stability"] = True
    with pytest.raises(PreMutationAbort, match="RELEASE_INFRA_ENVIRONMENT_DRIFT"):
        release_infra_compatibility_preflight(bundle)

    # Case 3: Parent drift -> CHANGED
    drive_parent = SequentialMockDrive({target_id: [read1, read2_drift_parent]})
    fps_parent = build_live_qualification_fingerprints(drive_parent, [target_id])
    check_parent = validate_freeze_fingerprint_compatibility(fps_parent[0]["frozen"], fps_parent[0]["observed"])
    assert check_parent["observed_environment_state"] == "CHANGED"

    # Case 4: Immutable mismatch -> IDENTITY_MISMATCH
    drive_bad_id = SequentialMockDrive({target_id: [read1, read2_mismatch_mime]})
    fps_bad = build_live_qualification_fingerprints(drive_bad_id, [target_id])
    with pytest.raises(ReleaseContractError, match="FREEZE_FINGERPRINT_IDENTITY_MISMATCH"):
        validate_freeze_fingerprint_compatibility(fps_bad[0]["frozen"], fps_bad[0]["observed"])


def test_rollback_acceptance_normalization_reporting(tmp_path):
    bundle = preflight_bundle()
    res_complete = release_infra_compatibility_preflight(bundle)
    assert res_complete["safe_baseline_live_state"] == "COMPLETE"
    assert res_complete["acceptance_status_normalization"] == "NONE"

    evidence_root, plan, journal, manifest, projection, _ = _setup_evidence_root(
        tmp_path, release_id="v1.8.1-3", current_release_id="v1.8.1-1",
    )
    status_doc = {
        "state": "ROLLED_BACK",
        "current_release_id": "v1.8.1-1",
        "rolled_back_release_id": "v1.8.1-2",
        "code_commit": SHA,
    }
    drive = MockDrive(
        files={"status-drive-id": json.dumps(status_doc).encode("utf-8")},
        metas={
            "status-drive-id": {"id": "status-drive-id", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["root-random"]},
            "target-random": {"id": "target-random", "version": "1", "modifiedTime": "t0", "mimeType": "application/json", "parents": ["current-random"]},
        },
    )
    policy = {
        "enabled": True, "status_id": "status-drive-id",
        "safe_baseline_release_id": "v1.8.1-1",
        "targets": {"target-random": {"mime": "application/json"}},
    }
    instance = MockInstance(configs={"production.json": policy})
    bundle_rb = build_release_infra_compatibility_bundle(
        instance=instance, drive=drive, evidence_root=evidence_root,
        safe_baseline_release_id="v1.8.1-1",
        source_registry_sha256=projection["source_registry_sha256"],
    )
    before_status_doc = dict(bundle_rb["status_doc"])
    res_rb = release_infra_compatibility_preflight(bundle_rb)
    assert res_rb["safe_baseline_live_state"] == "ROLLED_BACK"
    assert res_rb["acceptance_status_normalization"] == (
        "ROLLED_BACK_SAFE_BASELINE_AS_COMPLETE_FOR_BASELINE_ACCEPTANCE_ONLY"
    )
    assert bundle_rb["status_doc"] == before_status_doc
