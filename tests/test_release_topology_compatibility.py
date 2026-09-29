import json

import pytest

from cba_kb.common import digest
from cba_kb.consumer_manifest import build_consumer_manifest
from cba_kb.consumer_projection import build_player_identity_consumer_projection
from cba_kb.release import (
    PreMutationAbort,
    ReleaseContractError,
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
    journal = {"state": "PREPARED", "uploaded": {}, "inflight": None}
    prepared_sha = runtime_journal_sha256(journal)
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
        "journal": journal,
        "current_runtime_journal_sha256": prepared_sha,
        "freeze_prepared_journal_sha256": prepared_sha,
        "topology": topology(),
        "fingerprints": [{"frozen": freeze_fingerprint(frozen_meta), "observed": observed}],
        "status_doc": {"state": "COMPLETE", "current_release_id": RELEASE_ID,
                       "code_commit": SHA},
        "safe_baseline_release_id": RELEASE_ID,
        "consumer_manifest": manifest,
        "identity_projection": projection,
        "expected_source_registry_sha256": projection["source_registry_sha256"],
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
        proj = json.loads((evidence_root / "identity_projection.json").read_text())
        proj.pop("source_registry_sha256")
        (evidence_root / "identity_projection.json").write_text(json.dumps(proj))
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
        "--output", str(out_file),
    ])

    assert result["status"] == "PASS"
    assert result["classification"] == "RELEASE_INFRA_COMPATIBILITY_PREFLIGHT_PASS"
    assert result["production_mutation_count"] == 0
    assert result["release_status_mutation_count"] == 0
    assert out_file.is_file()
    saved = json.loads(out_file.read_text())
    assert saved["status"] == "PASS"

