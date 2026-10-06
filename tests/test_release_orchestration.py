import json
from pathlib import Path
import subprocess

import pytest

from cba_kb.common import digest
from cba_kb.current_state import (
    read_current_block, render_current_state, target_metadata,
    validate_current_state,
)
from cba_kb import release as release_module
from cba_kb.release import ReleaseContractError, publish
from scripts import prepare_production as orchestration
from test_canonical_registry import manifest as canonical_manifest, registry as canonical_registry


RELEASE = "v1.5.5-1"
ENGINE_SHA = "a" * 40
DOC = "application/vnd.google-apps.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def projection_inputs(*, tracked=None, hashes=None, manifest_rows=None):
    base_targets = {
        "code-old": {
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["scripts"],
            "publish_parent": "scripts",
        },
        "drive-map": {
            "mime": "text/yaml",
            "mode": "binary",
            "allowed_parents": ["config"],
            "publish_parent": "config",
        },
        "entry-code": {
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["scripts"],
            "publish_parent": "scripts",
        },
        "readme": {
            "mime": DOC,
            "mode": "managed_doc",
            "allowed_parents": ["root"],
            "publish_parent": "root",
        },
        "context": {
            "mime": "text/markdown",
            "mode": "binary",
            "allowed_parents": ["root"],
            "publish_parent": "root",
        },
        "index": {
            "mime": DOC,
            "mode": "managed_doc",
            "allowed_parents": ["ai"],
            "publish_parent": "ai",
        },
        "master": {
            "mime": XLSX,
            "mode": "binary",
            "allowed_parents": ["data"],
            "publish_parent": "data",
        },
        "manifest": {
            "mime": "text/csv",
            "mode": "binary",
            "allowed_parents": ["config"],
            "publish_parent": "config",
        },
    }
    status = {
        "state": "COMPLETE",
        "current_release_id": "v1.5.4-1",
        "artifacts": [
            {"id": "code-old", "name": "Makefile", "sha256": "old-code-sha"},
            {"id": "drive-map", "name": "drive_map.yaml", "sha256": "old-map-sha"},
            {"id": "entry-code", "name": "CODE_MANIFEST.md", "sha256": "old-code-doc"},
            {"id": "readme", "name": "00_README", "sha256": "old-readme"},
            {"id": "context", "name": "context.md", "sha256": "old-context"},
            {"id": "index", "name": "INDEX", "sha256": "old-index-sha"},
            {"id": "master", "name": "MASTER.xlsx", "sha256": "old-master"},
            {"id": "manifest", "name": "manifest.csv", "sha256": "old-manifest"},
        ],
    }
    targets = base_targets
    rows = manifest_rows or [
        {"drive_file_id": "code-old", "uid": "code/Makefile"},
        {"drive_file_id": "drive-map", "uid": "control/drive_map.yaml"},
        {"drive_file_id": "entry-code", "uid": "entry/code"},
        {"drive_file_id": "readme", "uid": "entry/README"},
        {"drive_file_id": "context", "uid": "entry/context"},
        {"drive_file_id": "index", "uid": "derived/INDEX.md"},
        {"drive_file_id": "master", "uid": "input/MASTER.xlsx"},
        {"drive_file_id": "manifest", "uid": "input/manifest.csv"},
    ]
    tracked = tracked or ["Makefile", "src/new.py"]
    hashes = hashes or {
        "Makefile": "old-code-sha",
        "src/new.py": "new-file-sha",
    }
    return {
        "release_id": RELEASE,
        "engine_sha": ENGINE_SHA,
        "status_id": "status",
        "archive_id": "archive",
        "previous_status": status,
        "previous_targets": targets,
        "manifest_rows": rows,
        "tracked": tracked,
        "tracked_hashes": hashes,
        "code_parent": "scripts",
        "status_hash": digest(json.dumps(
            status, sort_keys=True, separators=(",", ":"),
        ).encode()),
        "status_meta": {
            "id": "status", "version": "1", "modifiedTime": "t",
            "mimeType": "application/json", "parents": ["config"],
        },
    }


def projection():
    return orchestration.project_targets(**projection_inputs())


def retirement_projection_inputs(marker=RELEASE):
    inputs = projection_inputs()
    inputs["previous_targets"] = {
        file_id: dict(policy)
        for file_id, policy in inputs["previous_targets"].items()
    }
    inputs["previous_targets"]["master"]["retire_in_release"] = marker
    return inputs


def allocation_for(projected):
    reservations = {
        item["logical_key"]: f"reserved-{index}"
        for index, item in enumerate(projected["new_targets"])
    }
    return {
        "release_id": projected["release_id"],
        "status_id": projected["status_id"],
        "semantic_delta_signature": projected["semantic_delta_signature"],
        "status_before_hash": projected["status_before_hash"],
        "status_before_meta": projected["status_before_meta"],
        "active_release_id": projected["active_release_id"],
        "active_production_targets": projected["active_target_ids"],
        "reserved_staging_targets": sorted(reservations.values()),
        "reservations": reservations,
    }


def test_projection_is_read_only_deterministic_and_exact():
    first = orchestration.project_targets(**projection_inputs())
    second = orchestration.project_targets(**projection_inputs())
    assert first == second
    assert first["previous_artifact_count"] == 8
    assert first["reused_target_count"] == 8
    assert first["new_target_count"] == 1
    assert first["removed_target_count"] == 0
    assert first["carried_forward_count"] == 1
    assert first["final_artifact_count"] == 9
    assert first["new_targets"][0]["logical_key"] == "code/src/new.py"
    assert first["modified_content_targets"] == []
    assert first["unchanged_targets"] == ["code/Makefile"]


def test_explicit_artifact_retirement_projection_and_legacy_reservation_basis():
    legacy = projection()
    retired = orchestration.project_targets(**retirement_projection_inputs())
    assert retired["active_target_ids"] == legacy["active_target_ids"]
    assert retired["retired_target_count"] == 1
    assert retired["final_current_artifact_count"] == 8
    assert retired["retiring_targets"] == [{
        "id": "master",
        "logical_key": "input/MASTER.xlsx",
        "name": "MASTER.xlsx",
        "sha256": "old-master",
        "disposition": "RETIRED_FROM_CURRENT",
    }]
    assert "input/MASTER.xlsx" not in retired["final_current_logical_keys"]
    assert retired["reservation_compatibility_signature"] == (
        legacy["semantic_delta_signature"]
    )
    assert retired["semantic_delta_signature"] != legacy["semantic_delta_signature"]


def test_artifact_retirement_reuses_exact_existing_reservation():
    legacy = projection()
    allocation = allocation_for(legacy)
    inputs = retirement_projection_inputs()
    for item in legacy["new_targets"]:
        inputs["previous_targets"][
            allocation["reservations"][item["logical_key"]]
        ] = {
            "mime": item["mime"],
            "mode": item["mode"],
            "allowed_parents": [item["publish_parent"], "staging"],
            "staging_parent": "staging",
            "publish_parent": item["publish_parent"],
        }
    inputs["allocation"] = allocation
    retired = orchestration.project_targets(**inputs)
    assert orchestration.validate_reservation(retired, allocation)
    rebound = orchestration.reproject_targets(retired, allocation)
    assert rebound["reservation_state"] == "RESERVED"
    assert rebound["reserved_target_ids"] == sorted(
        allocation["reservations"].values()
    )


def test_historical_retirement_policy_record_is_not_a_reservation():
    inputs = projection_inputs()
    inputs["previous_targets"] = dict(inputs["previous_targets"])
    inputs["previous_targets"]["historical-retired"] = {
        "mime": "application/json",
        "mode": "binary",
        "allowed_parents": ["archive"],
        "publish_parent": "archive",
        "retire_in_release": "v1.5.4-1",
    }
    projected = orchestration.project_targets(**inputs)
    assert projected["historical_retired_policy_target_ids"] == [
        "historical-retired"
    ]
    assert projected["reserved_policy_target_ids"] == []


@pytest.mark.parametrize("scenario", [
    "different_release",
    "code",
    "control",
    "not_in_previous_status",
])
def test_artifact_retirement_policy_negative_oracles(scenario):
    if scenario == "different_release":
        projected = orchestration.project_targets(
            **retirement_projection_inputs("v1.6.0-1")
        )
        master = next(
            item for item in projected["existing_targets"]
            if item["id"] == "master"
        )
        assert master["disposition"] == "carried_forward"
        assert projected["retiring_targets"] == []
        return
    inputs = projection_inputs()
    inputs["previous_targets"] = {
        file_id: dict(policy)
        for file_id, policy in inputs["previous_targets"].items()
    }
    if scenario == "code":
        inputs["previous_targets"]["code-old"]["retire_in_release"] = RELEASE
        error = "RETIREMENT_CODE_TARGET_FORBIDDEN"
    elif scenario == "control":
        inputs["previous_targets"]["manifest"]["retire_in_release"] = RELEASE
        error = "RETIREMENT_CONTROL_TARGET_FORBIDDEN"
    else:
        inputs["previous_targets"]["not-current"] = {
            "mime": "application/json", "mode": "binary",
            "allowed_parents": ["archive"], "publish_parent": "archive",
            "retire_in_release": RELEASE,
        }
        error = "RETIREMENT_TARGET_NOT_IN_PREVIOUS_STATUS"
    with pytest.raises(orchestration.ProjectionError, match=error):
        orchestration.project_targets(**inputs)


def test_artifact_retirement_reservation_mismatch_fails_closed():
    legacy = projection()
    allocation = allocation_for(legacy)
    retired = orchestration.project_targets(**retirement_projection_inputs())

    wrong_signature = dict(allocation, semantic_delta_signature="wrong")
    with pytest.raises(
        orchestration.ProjectionError, match="RESERVATION_PROJECTION_DRIFT"
    ):
        orchestration.validate_reservation(retired, wrong_signature)

    wrong_keys = dict(allocation, reservations={"code/other.py": "reserved-0"})
    with pytest.raises(
        orchestration.ProjectionError, match="RESERVATION_KEY_SET_MISMATCH"
    ):
        orchestration.validate_reservation(retired, wrong_keys)


def test_retired_artifact_omitted_from_candidates_manifest_and_current_mapping(
        monkeypatch, tmp_path):
    projection_value = {
        "release_id": RELEASE,
        "engine_sha": ENGINE_SHA,
        "active_release_id": "v1.5.4-1",
        "existing_targets": [
            {"logical_key": "facts/keep.json", "id": "keep", "name": "keep.json",
             "mime": "application/json", "mode": "binary"},
            {"logical_key": "facts/retire.json", "id": "retire", "name": "retire.json",
             "mime": "application/json", "mode": "binary",
             "disposition": "RETIRED_FROM_CURRENT"},
            {"logical_key": "control/drive_map.yaml", "id": "drive-map",
             "name": "drive_map.yaml", "mime": "text/yaml", "mode": "binary"},
            {"logical_key": "input/manifest.csv", "id": "manifest",
             "name": "manifest.csv", "mime": "text/csv", "mode": "binary"},
        ],
        "new_targets": [],
        "retiring_targets": [{
            "id": "retire", "logical_key": "facts/retire.json",
            "name": "retire.json", "sha256": "retire-sha",
            "disposition": "RETIRED_FROM_CURRENT",
        }],
    }
    previous = {
        "keep": b"keep-bytes",
        "drive-map": b"version: 1\nroot: {}\nfolders: {}\n",
        "manifest": (
            b"drive_file_id,uid,content_hash\n"
            b"keep,facts/keep.json,old\n"
            b"retire,facts/retire.json,old\n"
            b"drive-map,control/drive_map.yaml,old\n"
            b"manifest,input/manifest.csv,old\n"
        ),
    }
    reads = []

    def fake_snapshot(_drive, file_id, _mode="binary"):
        reads.append(file_id)
        if file_id == "retire":
            raise AssertionError("retired Drive object must not be read or written")
        return previous[file_id], {"id": file_id}

    monkeypatch.setattr(orchestration, "snapshot", fake_snapshot)
    entries = orchestration._candidate_inputs(
        object(), tmp_path, projection_value,
        {"status_id": "status", "reservations": {}}, tmp_path / "candidate",
    )
    assert "retire" not in reads
    assert {entry["id"] for entry in entries} == {"keep", "drive-map", "manifest"}
    by_key = {entry["logical_key"]: Path(entry["path"]).read_bytes()
              for entry in entries}
    manifest = list(orchestration.csv.DictReader(orchestration.io.StringIO(
        by_key["input/manifest.csv"].decode("utf-8-sig")
    )))
    assert {row["drive_file_id"] for row in manifest} == {
        "keep", "drive-map", "manifest"
    }
    drive_map = orchestration.yaml.safe_load(by_key["control/drive_map.yaml"])
    assert "facts/retire.json" not in drive_map["v1_5_release"]["targets"]


def test_retirement_complete_excludes_and_rollback_restores_membership():
    class StatusDrive:
        def __init__(self):
            self.files = {"status": b""}
            self.put_ids = []

        def put(self, file_id, content, mime):
            assert mime == "application/json"
            self.put_ids.append(file_id)
            self.files[file_id] = content

        def get(self, file_id):
            return self.files[file_id]

    drive = StatusDrive()
    plan = {
        "release_id": RELEASE,
        "previous_release_id": "v1.5.4-1",
        "status_id": "status",
        "entries": [],
        "carry_forward_artifacts": [
            {"id": "keep", "name": "keep", "sha256": "keep-sha"},
        ],
        "retired_artifacts": [{
            "id": "retire", "logical_key": "facts/retire.json",
            "name": "retire", "sha256": "retire-sha",
            "disposition": "RETIRED_FROM_CURRENT",
        }],
    }
    release_module.set_status(drive, plan, "COMPLETE", [])
    complete = json.loads(drive.get("status"))
    assert {item["id"] for item in complete["artifacts"]} == {"keep"}
    release_module.set_status(drive, plan, "ROLLED_BACK", [])
    rolled_back = json.loads(drive.get("status"))
    assert {item["id"] for item in rolled_back["artifacts"]} == {
        "keep", "retire"
    }
    assert drive.put_ids == ["status", "status"]


def test_prepare_freezes_retirement_evidence_without_touching_retired_object(
        tmp_path):
    class ReadOnlyPrepareDrive:
        def __init__(self):
            self.read_ids = []
            self.data = {
                "status": json.dumps({
                    "state": "COMPLETE",
                    "current_release_id": "v1.5.4-1",
                    "artifacts": [
                        {"id": "update", "name": "update", "sha256": "old-update"},
                        {"id": "retire", "name": "retire", "sha256": "retire-sha"},
                    ],
                }).encode(),
                "update": b"old-update",
            }

        def meta(self, file_id):
            self.read_ids.append(file_id)
            return {
                "id": file_id, "version": "1", "modifiedTime": "t",
                "mimeType": "application/json" if file_id == "status" else "text/plain",
                "parents": ["current"],
            }

        def get(self, file_id):
            self.read_ids.append(file_id)
            if file_id == "retire":
                raise AssertionError("retired artifact must not be read")
            return self.data[file_id]

        def document_json(self, file_id):
            raise AssertionError(file_id)

    drive = ReadOnlyPrepareDrive()
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"new-update")
    plan = release_module.prepare(
        drive, tmp_path / "outbox", RELEASE,
        [{
            "id": "update", "name": "update", "mime": "text/plain",
            "path": str(candidate), "logical_key": "facts/update.json",
        }],
        "archive", "status", carry_forward_artifacts=True,
        retired_artifacts=[{
            "id": "retire", "logical_key": "facts/retire.json",
            "name": "retire", "sha256": "retire-sha",
            "disposition": "RETIRED_FROM_CURRENT",
        }],
    )
    assert plan["retired_artifacts"] == [{
        "id": "retire", "logical_key": "facts/retire.json",
        "name": "retire", "sha256": "retire-sha",
        "disposition": "RETIRED_FROM_CURRENT",
    }]
    assert {item["id"] for item in plan["carry_forward_artifacts"]} == {
        "update"
    }
    assert "retire" not in drive.read_ids


def test_projection_binds_exact_release_id_and_fails_closed():
    inputs = projection_inputs()
    inputs["release_id"] = "v1.5.5-2"
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN"):
        orchestration.project_targets(**inputs)


def test_v161_release_spec_and_reprojection_contract():
    spec = orchestration._release_spec("v1.6.1-1")
    assert spec["product_baseline_sha"] == (
        "4fab3d0e8eedc594fae982f12a507fef88958f15"
    )
    inputs = projection_inputs()
    version_id = "1ZebJR9YPKX37cMDdz0xznHDa45at_q65"
    inputs["previous_targets"][version_id] = {
        "mime": "text/markdown",
        "mode": "binary",
        "allowed_parents": ["ai"],
        "publish_parent": "ai",
    }
    inputs["previous_status"]["artifacts"].append({
        "id": version_id,
        "name": "CBA-KB_v1.5.3.md",
        "sha256": "version-old-sha",
    })
    inputs["manifest_rows"] = list(inputs["manifest_rows"]) + [{
        "drive_file_id": version_id,
        "uid": "ai/CBA-KB_v1.5.3.md",
        "content_hash": "version-old-sha",
    }]
    inputs["release_id"] = "v1.6.1-1"
    projected = orchestration.project_targets(**inputs)
    assert projected["state"] == "PROJECTED"
    allocation = {
        "release_id": "v1.6.1-1",
        "status_id": projected["status_id"],
        "semantic_delta_signature": projected["semantic_delta_signature"],
        "reservations": {
            item["logical_key"]: "reserved-" + str(index)
            for index, item in enumerate(projected["new_targets"])
        },
    }
    reprojected = orchestration.reproject_targets(projected, allocation)
    assert reprojected["state"] == "REPROJECTED"
    assert reprojected["reservation_state"] == "RESERVED"
    assert projected["state"] == "PROJECTED"


def test_v180_release_spec_uses_v161_production_baseline():
    spec = orchestration._release_spec("v1.8.0-1")
    assert spec["product_baseline_sha"] == (
        "81bd581fafbccb602f9ecaf9aaefca4533be69a4"
    )


def test_v181_release_spec_uses_product_merge_floor():
    baseline = "9cd5dab298012eadaf8345f3f9d2709a2b5c2288"
    assert orchestration._release_spec("v1.8.1-1") == {
        "product_baseline_sha": baseline,
    }
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN"):
        orchestration._release_spec("v1.8.2-1")
    inputs = projection_inputs()
    inputs["release_id"] = "v1.8.1-1"
    assert orchestration.project_targets(**inputs)["release_id"] == "v1.8.1-1"
    repo = Path(__file__).resolve().parents[1]
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline,
         "b30c66288fc1d44fd4dafc0a8ebcc286bcf41a9d"],
        cwd=repo, check=False,
    ).returncode == 0


def test_v181_execution_sha_requires_clean_product_descendant(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(checkout)],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
    ).strip()
    baseline = orchestration._release_spec("v1.8.1-1")["product_baseline_sha"]
    assert orchestration.verify_execution_sha(
        checkout, head, baseline,
    )["baseline_is_ancestor"]
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, "a" * 40, baseline)
    (checkout / "README.md").write_text("dirty worktree\n")
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, head, baseline)
    subprocess.run(["git", "checkout", "--quiet", "--", "README.md"],
                   cwd=checkout, check=True)
    pre_product = "81bd581fafbccb602f9ecaf9aaefca4533be69a4"
    subprocess.run(["git", "checkout", "--quiet", pre_product],
                   cwd=checkout, check=True)
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, pre_product, baseline)


def test_v190_release_spec_uses_product_merge_floor():
    baseline = "1e8c78019ef30a91c3bd0f98e96ce476326c6c25"
    assert orchestration._release_spec("v1.9.0-1") == {
        "product_baseline_sha": baseline,
    }
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN"):
        orchestration._release_spec("v1.9.0-2")
    inputs = projection_inputs()
    inputs["release_id"] = "v1.9.0-1"
    assert orchestration.project_targets(**inputs)["release_id"] == "v1.9.0-1"
    repo = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline, head],
        cwd=repo, check=False,
    ).returncode == 0


def test_v190_execution_sha_requires_clean_product_descendant(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(checkout)],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
    ).strip()
    baseline = orchestration._release_spec("v1.9.0-1")["product_baseline_sha"]
    assert orchestration.verify_execution_sha(
        checkout, head, baseline,
    )["baseline_is_ancestor"]
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, "a" * 40, baseline)
    (checkout / "README.md").write_text("dirty worktree\n")
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, head, baseline)
    subprocess.run(["git", "checkout", "--quiet", "--", "README.md"],
                   cwd=checkout, check=True)
    pre_product = "81bd581fafbccb602f9ecaf9aaefca4533be69a4"
    subprocess.run(["git", "checkout", "--quiet", pre_product],
                   cwd=checkout, check=True)
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, pre_product, baseline)


def test_v200_1_release_spec_uses_product_candidate_sha():
    baseline = "2b84c900dc383d2537f435e0b7748da46318b3cc"
    assert orchestration._release_spec("v2.0.0-1") == {
        "product_baseline_sha": baseline,
    }
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN:v2.0.0"):
        orchestration._release_spec("v2.0.0")
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN:v2.0.0-2"):
        orchestration._release_spec("v2.0.0-2")

    # Representative historical release IDs resolve unchanged
    assert orchestration._release_spec("v1.5.5-1")["product_baseline_sha"] == "c14a0f2579fcc86e2dc114f0b15d00dd54e9f55e"
    assert orchestration._release_spec("v1.6.0-1")["product_baseline_sha"] == "0b9c6e062616fc8d4349304ea483afdd917ce181"
    assert orchestration._release_spec("v1.6.1-1")["product_baseline_sha"] == "4fab3d0e8eedc594fae982f12a507fef88958f15"
    assert orchestration._release_spec("v1.8.0-1")["product_baseline_sha"] == "81bd581fafbccb602f9ecaf9aaefca4533be69a4"
    assert orchestration._release_spec("v1.8.1-1")["product_baseline_sha"] == "9cd5dab298012eadaf8345f3f9d2709a2b5c2288"
    assert orchestration._release_spec("v1.8.1-2")["product_baseline_sha"] == "b98a4daec0a2d7849d9f4f43306a073f9eaeb53c"
    assert orchestration._release_spec("v1.8.1-3")["product_baseline_sha"] == "b8304f94276b6fca3bc49c945700d3a152194a63"
    assert orchestration._release_spec("v1.9.0-1")["product_baseline_sha"] == "1e8c78019ef30a91c3bd0f98e96ce476326c6c25"

    # Unknown future release IDs remain fail closed
    for unk in ["v2.1.0-1", "v3.0.0-1", "v9.9.9"]:
        with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN"):
            orchestration._release_spec(unk)

    inputs = projection_inputs()
    inputs["release_id"] = "v2.0.0-1"
    projected = orchestration.project_targets(**inputs)
    assert projected["release_id"] == "v2.0.0-1"
    assert projected["state"] == "PROJECTED"

    repo = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline, head],
        cwd=repo, check=False,
    ).returncode == 0


def test_v201_1_is_the_only_supported_v201_release_attempt():
    baseline = "63ca7a00b70ef52ecc97a808642f821d557417b5"
    assert orchestration._release_spec("v2.0.1-1") == {
        "product_baseline_sha": baseline,
    }
    for forbidden in ["v2.0.1", "v2.0.1-2", "v2.0.1-99", "v2.0.2-1"]:
        with pytest.raises(
            orchestration.ProjectionError,
            match=f"RELEASE_ID_FORBIDDEN:{forbidden}",
        ):
            orchestration._release_spec(forbidden)

    inputs = projection_inputs()
    inputs["release_id"] = "v2.0.1-1"
    inputs["engine_sha"] = baseline
    projected = orchestration.project_targets(**inputs)
    assert projected["release_id"] == "v2.0.1-1"
    assert projected["engine_sha"] == baseline
    assert projected["state"] == "PROJECTED"


def test_v200_1_execution_sha_requires_clean_product_descendant(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(checkout)],
        check=True,
    )
    baseline = orchestration._release_spec("v2.0.0-1")["product_baseline_sha"]

    # Verify execution SHA can be a later descendant commit, not forced equal
    (checkout / "test_descendant.txt").write_text("descendant release tooling\n")
    subprocess.run(["git", "add", "test_descendant.txt"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "chore: release infra descendant"],
        cwd=checkout, check=True,
    )
    descendant_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
    ).strip()
    assert descendant_head != baseline

    desc_res = orchestration.verify_execution_sha(checkout, descendant_head, baseline)
    assert desc_res["baseline_is_ancestor"] is True
    assert desc_res["head"] == descendant_head
    assert desc_res["clean"] is True

    # Bad format / non-hex sha
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, "a" * 40, baseline)

    # Dirty worktree
    (checkout / "README.md").write_text("dirty worktree\n")
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, descendant_head, baseline)
    subprocess.run(["git", "checkout", "--quiet", "--", "README.md"],
                   cwd=checkout, check=True)

    # Non-descendant commit (parent of product baseline)
    pre_product = "b547ea386c49ed4e0dcd291bbef18664f76e3e3b"
    subprocess.run(["git", "checkout", "--quiet", pre_product],
                   cwd=checkout, check=True)
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(checkout, pre_product, baseline)


def test_v180_projection_is_accepted_and_deterministic():
    inputs = projection_inputs()
    inputs["release_id"] = "v1.8.0-1"
    first = orchestration.project_targets(**inputs)
    second = orchestration.project_targets(**inputs)
    assert first == second
    assert first["release_id"] == "v1.8.0-1"
    assert first["state"] == "PROJECTED"


def test_manifest_coverage_helper_rejects_any_set_drift():
    orchestration.validate_manifest_coverage(
        ["a", "b"], ["b", "a"], ["a", "b"], ["b", "a"],
    )
    with pytest.raises(ReleaseContractError, match="MANIFEST_COVERAGE"):
        orchestration.validate_manifest_coverage(
            ["a"], ["a"], ["a", "b"], ["a"],
        )


def test_v160_release_spec_uses_post_document_lane_baseline():
    spec = orchestration._release_spec("v1.6.0-1")
    assert spec["product_baseline_sha"] == (
        "0b9c6e062616fc8d4349304ea483afdd917ce181"
    )
    inputs = projection_inputs()
    inputs["release_id"] = "v1.6.0-1"
    value = orchestration.project_targets(**inputs)
    assert value["release_id"] == "v1.6.0-1"


def test_v160_content_preserving_candidate_marks_v155_as_historical(tmp_path):
    previous = (
        b"navigation-marker\n"
        b"current release: v1.5.5-1\n"
        b"v1.5.5-1 / COMPLETE\n"
    )
    candidate = orchestration._content_preserving_candidate(
        "entry/README",
        previous,
        {
            "release_id": "v1.6.0-1",
            "engine_sha": "a" * 40,
            "active_release_id": "v1.5.5-1",
        },
        {"status_id": "status"},
        {},
        tmp_path,
    )
    assert b"current release: v1.5.5-1" not in candidate
    assert b"historical release: v1.5.5-1" in candidate
    assert "v1.5.5-1 / 历史发布".encode() in candidate
    assert b"navigation-marker" in candidate


def test_v161_content_candidate_replaces_native_managed_current_block(tmp_path):
    from cba_kb.current_state import (
        read_current_block, render_current_state, target_metadata,
    )
    from cba_kb.native import wrap

    registry = canonical_registry()
    registry["registry_release_id"] = "v1.6.1-1"
    previous_metadata = target_metadata(
        "v1.6.1-1", "b" * 40, registry,
    )
    previous = wrap(
        "legacy payload\n" + render_current_state(previous_metadata)
    ).encode()
    candidate = orchestration._content_preserving_candidate(
        "entry/README",
        previous,
        {
            "release_id": "v1.6.1-1",
            "engine_sha": "a" * 40,
            "active_release_id": "v1.6.0-1",
        },
        {"status_id": "status"},
        {},
        tmp_path,
        registry,
    )
    assert read_current_block(candidate.decode()) == target_metadata(
        "v1.6.1-1", "a" * 40, registry,
    )
    assert b"legacy payload" in candidate


def test_release_execution_sha_provenance_fails_closed(monkeypatch):
    engine_sha = "b" * 40

    def check_output(command, **kwargs):
        if command[-1] == "HEAD":
            return engine_sha + "\n"
        if command[-2:] == ["--porcelain"]:
            return ""
        return ""

    monkeypatch.setattr(orchestration.subprocess, "check_output", check_output)
    monkeypatch.setattr(
        orchestration.subprocess, "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    assert orchestration.verify_execution_sha(".", engine_sha)["head"] == engine_sha
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(".", "c" * 40)

    monkeypatch.setattr(
        orchestration.subprocess, "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1})(),
    )
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(".", engine_sha)

    def dirty(command, **kwargs):
        if command[-1] == "HEAD":
            return engine_sha + "\n"
        return " M file\n"

    monkeypatch.setattr(orchestration.subprocess, "check_output", dirty)
    with pytest.raises(orchestration.ProjectionError, match="CODE_PROVENANCE"):
        orchestration.verify_execution_sha(".", engine_sha)


def test_manifest_generation_is_deterministic_and_covers_final_targets():
    previous = (
        b"drive_file_id,uid,content_hash\n"
        b"id-a,code/a,old-a\n"
    )
    kwargs = {
        "projection": {"release_id": RELEASE},
        "allocation": {},
        "id_by_key": {"code/a": "id-a", "code/b": "id-b"},
        "data_by_key": {"code/a": b"a", "code/b": b"b"},
        "item_by_key": {
            "code/a": {"mode": "binary"},
            "code/b": {"mode": "binary"},
        },
    }
    first = orchestration._manifest_candidate(previous, **kwargs)
    second = orchestration._manifest_candidate(previous, **kwargs)
    assert first == second
    rows = list(orchestration.csv.DictReader(
        orchestration.io.StringIO(first.decode("utf-8-sig")),
    ))
    assert [row["uid"] for row in rows] == ["code/a", "code/b"]
    assert {row["drive_file_id"] for row in rows} == {"id-a", "id-b"}
    assert all(row["published_release"] == RELEASE for row in rows)


@pytest.mark.parametrize("previous", [
    b"drive_file_id,uid\nid-a,code/a\nid-a,code/b\n",
    b"drive_file_id,uid\nid-a,code/a\nid-b,code/a\n",
])
def test_manifest_duplicate_id_or_logical_key_fails_closed(previous):
    with pytest.raises(orchestration.ProjectionError, match="MANIFEST_DUPLICATE"):
        orchestration._manifest_candidate(
            previous,
            projection={"release_id": RELEASE},
            allocation={},
            id_by_key={"code/a": "id-a", "code/b": "id-b"},
            data_by_key={"code/a": b"a", "code/b": b"b"},
            item_by_key={
                "code/a": {"mode": "binary"},
                "code/b": {"mode": "binary"},
            },
        )


def test_duplicate_logical_key_and_removed_target_fail_closed():
    rows = projection_inputs()["manifest_rows"]
    rows = [dict(row) for row in rows]
    code_row = next(row for row in rows if row["drive_file_id"] == "code-old")
    index_row = next(row for row in rows if row["drive_file_id"] == "index")
    code_row["uid"] = "code/duplicate"
    index_row["uid"] = "code/duplicate"
    duplicate = projection_inputs(manifest_rows=rows)
    with pytest.raises(orchestration.ProjectionError, match="LOGICAL_KEY_DUPLICATE"):
        orchestration.project_targets(**duplicate)

    removed = projection_inputs(tracked=["src/new.py"], hashes={
        "src/new.py": "new-file-sha",
    })
    with pytest.raises(
        orchestration.ProjectionError, match="REMOVED_TARGET_POLICY_REQUIRED",
    ):
        orchestration.project_targets(**removed)


class ReservationDrive:
    def __init__(self):
        self.files = {
            "code-old": {
                "data": b"old-code",
                "id": "code-old",
                "name": "Makefile",
                "mimeType": "text/plain",
                "parents": ["scripts"],
                "version": "1",
            },
            "status": {
                "data": b'{"state":"COMPLETE"}',
                "id": "status",
                "name": "release_status.json",
                "mimeType": "application/json",
                "parents": ["config"],
                "version": "1",
            },
        }
        self.calls = []
        self.get_calls = []
        self.put_calls = []
        self.move_calls = []

    def _next_id(self):
        return f"reserved-{len([c for c in self.calls if c[0] == 'ensure'])}"

    def ensure(self, parent, key, name, mime, content=None):
        self.calls.append(("ensure", parent, key, name, mime, content))
        for item in self.files.values():
            if item.get("app_key") == (parent, key):
                return item["id"]
        file_id = self._next_id()
        self.files[file_id] = {
            "data": content or b"",
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": [parent],
            "version": "1",
            "app_key": (parent, key),
        }
        return file_id

    def meta(self, file_id):
        return {
            key: value for key, value in self.files[file_id].items()
            if key not in ("data", "app_key")
        }

    def get(self, file_id):
        self.get_calls.append(file_id)
        if self.files[file_id]["mimeType"] == orchestration.FOLDER:
            raise ValueError("native raw download rejected")
        return self.files[file_id]["data"]

    def put(self, *args):
        self.put_calls.append(args)
        raise AssertionError("reservation must not write existing bytes")

    def move(self, *args):
        self.move_calls.append(args)
        raise AssertionError("reservation must not move objects")


class ReservationInstance:
    def __init__(self, path):
        self.path = path
        self.runtime = {
            "inputs": {
                "MASTER.xlsx": {"id": "master"},
                "source_registry.csv": {"id": "registry"},
            },
        }
        self.policy = {
            "enabled": True,
            "targets": projection_inputs()["previous_targets"],
            "dependency_ids": [
                "fact-dependency-id",
                "source-registry-dependency-id",
            ],
        }

    def read_json(self, name):
        if name == "runtime.json":
            return self.runtime
        assert name == "production.json"
        if self.path.is_file():
            return json.loads(self.path.read_text())
        return json.loads(json.dumps(self.policy))

    def config_path(self, name):
        assert name == "production.json"
        return self.path


def test_reserve_only_creates_new_keys_under_staging_and_is_retry_safe(tmp_path):
    drive = ReservationDrive()
    value = projection()
    instance = ReservationInstance(tmp_path / "production.json")
    before_status = drive.get("status")
    before_existing = drive.get("code-old")
    output = tmp_path / "allocation"

    first = orchestration.reserve_staging(
        drive, instance, release_id=RELEASE, projection=value, output=output,
        single_writer=True,
    )
    second = orchestration.reserve_staging(
        drive, instance, release_id=RELEASE, projection=value, output=output,
        single_writer=True,
    )

    assert first["validated"] is True
    assert first["allocation"]["reservations"] == second["allocation"]["reservations"]
    ensure_parents = [call[1] for call in drive.calls if call[0] == "ensure"]
    assert ensure_parents[0] == "archive"
    assert set(ensure_parents[1:]) == {second["allocation"]["staging_id"]}
    assert drive.put_calls == []
    assert drive.move_calls == []
    assert drive.get("status") == before_status
    assert drive.get("code-old") == before_existing
    assert second["allocation"]["staging_id"] not in drive.get_calls
    assert len(drive.files) == 4
    policy = json.loads(instance.path.read_text())
    assert set(policy["targets"]) == {
        "code-old", "drive-map", "entry-code", "readme", "context",
        "index", "master", "manifest", "reserved-2",
    }
    assert second["allocation"]["active_production_targets"] == sorted(
        item["id"] for item in value["existing_targets"]
    )
    assert second["allocation"]["reserved_staging_targets"] == ["reserved-2"]


def test_reserve_long_v181_logical_key_uses_bounded_stable_property(tmp_path):
    long_path = (
        'requirements/REQ-181-CONSUMER-CLOSURE-01/'
        'requirement-r2-20260924-antigravity-first-p2a-canary.md'
    )
    inputs = projection_inputs(
        tracked=['Makefile', 'src/new.py', long_path],
        hashes={'Makefile': 'old-code-sha', 'src/new.py': 'new-file-sha',
                long_path: 'long-file-sha'},
    )
    inputs['release_id'] = 'v1.8.1-1'
    value = orchestration.project_targets(**inputs)

    class LengthLimitedDrive(ReservationDrive):
        def ensure(self, parent, key, name, mime, content=None):
            assert len(('cba_key' + key).encode()) <= 124
            return super().ensure(parent, key, name, mime, content)

    drive = LengthLimitedDrive()
    instance = ReservationInstance(tmp_path / 'production.json')
    output = tmp_path / 'allocation'
    first = orchestration.reserve_staging(
        drive, instance, release_id='v1.8.1-1', projection=value,
        output=output, single_writer=True,
    )
    second = orchestration.reserve_staging(
        drive, instance, release_id='v1.8.1-1', projection=value,
        output=output, single_writer=True,
    )
    assert first['allocation']['reservations'] == second['allocation']['reservations']
    assert len(drive.files) == 5
    keys = [call[2] for call in drive.calls if call[0] == 'ensure']
    assert 'reserve:v1.8.1-1:code/src/new.py' in keys
    long_key = orchestration._reservation_key('v1.8.1-1', 'code/' + long_path)
    assert long_key.startswith('reserve:v1.8.1-1:sha256:')
    assert keys.count(long_key) == 2


def test_reserve_requires_single_writer(tmp_path):
    drive = ReservationDrive()
    with pytest.raises(orchestration.ProjectionError, match="SINGLE_WRITER"):
        orchestration.reserve_staging(
            drive, ReservationInstance(tmp_path / "production.json"),
            release_id=RELEASE, projection=projection(),
            output=tmp_path / "allocation", single_writer=False,
        )
    assert drive.calls == []


def test_post_reservation_project_accepts_exact_reserved_ids(tmp_path):
    drive = ReservationDrive()
    before = projection()
    instance = ReservationInstance(tmp_path / "production.json")
    output = tmp_path / "allocation"
    allocation = orchestration.reserve_staging(
        drive, instance, release_id=RELEASE, projection=before,
        output=output, single_writer=True,
    )["allocation"]
    policy = instance.read_json("production.json")
    inputs = projection_inputs()
    inputs["previous_targets"] = policy["targets"]
    inputs["allocation"] = allocation
    after = orchestration.project_targets(**inputs)
    assert after["semantic_delta_signature"] == before["semantic_delta_signature"]
    assert after["active_production_target_count"] == before["active_production_target_count"]
    assert after["reserved_staging_target_count"] == 1
    assert after["active_target_ids"] == before["active_target_ids"]
    assert after["new_targets"][0]["id"] == allocation["reservations"][
        "code/src/new.py"
    ]

    unexplained = projection_inputs()
    extra = dict(policy["targets"])
    extra["unexplained"] = {
        "mime": "text/plain", "mode": "binary",
        "allowed_parents": ["staging"], "staging_parent": "staging",
        "publish_parent": "scripts",
    }
    unexplained["previous_targets"] = extra
    with pytest.raises(
        orchestration.ProjectionError,
        match="UNEXPLAINED_RESERVED_POLICY_TARGET",
    ):
        orchestration.project_targets(**unexplained)


def test_publish_single_writer_contract_remains_required(tmp_path):
    with pytest.raises(RuntimeError, match="Single-writer"):
        publish(None, tmp_path)


def test_legacy_reserve_command_is_disabled(tmp_path):
    with pytest.raises(SystemExit):
        orchestration.main([
            "reserve",
            "--instance-root", str(tmp_path),
            "--output", str(tmp_path / "out"),
        ])


class Instance:
    def __init__(self):
        self.configs = {
            "runtime.json": {
                "inputs": {
                    "MASTER.xlsx": {"id": "master"},
                    "source_registry.csv": {"id": "registry"},
                },
            },
        }

    def read_json(self, name):
        return self.configs[name]


def test_freeze_uses_reserved_ids_and_does_not_mutate_remote(
        tmp_path, monkeypatch):
    drive = ReservationDrive()
    value = projection()
    instance = ReservationInstance(tmp_path / "production.json")
    allocation = orchestration.reserve_staging(
        drive, instance,
        release_id=RELEASE, projection=value,
        output=tmp_path / "allocation", single_writer=True,
    )["allocation"]
    drive.calls = []
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "Makefile").write_bytes(b"new-code")
    (root / "src/new.py").write_bytes(b"new-file")
    (root / "docs/OPERATIONS_V1_5.md").write_text(
        "operations-substantive-marker\n"
    )
    (root / "docs/MASTER_MIGRATION_NOTE.md").write_text(
        "migration-substantive-marker\n"
    )
    status = projection_inputs()["previous_status"]
    status_raw = json.dumps(
        status, sort_keys=True, separators=(",", ":"),
    ).encode()
    manifest_rows = [
        {
            "drive_file_id": item["id"],
            "uid": item["logical_key"],
            "content_hash": "old",
        }
        for item in value["existing_targets"]
    ]
    manifest_stream = orchestration.io.StringIO(newline="")
    manifest_writer = orchestration.csv.DictWriter(
        manifest_stream,
        fieldnames=["drive_file_id", "uid", "content_hash"],
        lineterminator="\n",
    )
    manifest_writer.writeheader()
    manifest_writer.writerows(manifest_rows)
    manifest_raw = manifest_stream.getvalue().encode("utf-8-sig")

    def fake_snapshot(drive, file_id, mode="binary"):
        data = {
            "status": status_raw,
            "master": b"master",
            "registry": b"registry",
            "drive-map": b"version: 1\nroot: {}\nfolders: {}\n",
            "entry-code": b"old code manifest",
            "readme": (
                b"readme-existing-navigation-marker\n"
                b"current release: v1.5.4-1\n"
            ),
            "context": b"context-substantive-marker",
            "index": (
                b"index-existing-navigation-marker\n"
                b"current release: v1.5.4-1\n"
            ),
            "manifest": manifest_raw,
            "fact-dependency-id": b"factor-dependency",
            "source-registry-dependency-id": b"registry-dependency",
        }.get(file_id, b"old")
        meta = {
            "id": file_id, "version": "1", "modifiedTime": "t",
            "mimeType": "application/json", "parents": ["ai"],
        }
        if file_id == "status":
            meta = projection_inputs()["status_meta"]
        return data, meta

    captured = {}

    def fake_prepare(drive, outbox, release_id, entries, archive, status,
                     dependencies, **options):
        captured.update({
            "entries": entries,
            "dependencies": dependencies,
            "options": options,
        })
        outbox.mkdir(parents=True)
        plan = {
            "release_id": release_id,
            "dependencies": dependencies,
            "entries": [
                {
                    **entry,
                    "before": f"before/{index}",
                    "candidate": f"candidate/{index}",
                    "before_hash": "before",
                    "after_hash": "after",
                }
                for index, entry in enumerate(entries)
            ],
        }
        (outbox / "plan.json").write_text(json.dumps(plan))
        (outbox / "journal.json").write_text(json.dumps({"state": "PREPARED"}))
        return plan

    monkeypatch.setattr(orchestration, "snapshot", fake_snapshot)
    monkeypatch.setattr(orchestration, "prepare_release", fake_prepare)
    monkeypatch.setattr(
        orchestration, "verify_execution_sha",
        lambda root, sha, baseline=None: {"head": sha, "clean": True},
    )
    result = orchestration.freeze_plan(
        drive,
        instance=instance,
        engine_root=root,
        release_id=RELEASE,
        projection=value,
        allocation=allocation,
        output=tmp_path / "freeze",
    )

    assert result["entries"] == 9
    assert drive.calls == []
    assert drive.put_calls == []
    assert drive.move_calls == []
    new_entry = next(
        item for item in captured["entries"]
        if item["logical_key"] == "code/src/new.py"
    )
    assert new_entry["id"] == allocation["reservations"]["code/src/new.py"]
    assert new_entry["allowed_parents"] == ["scripts", allocation["staging_id"]]
    assert captured["options"]["code_commit"] == ENGINE_SHA
    assert [item["id"] for item in captured["dependencies"]] == [
        "fact-dependency-id", "source-registry-dependency-id",
    ]
    assert all(
        isinstance(item.get("before_hash"), str)
        and len(item["before_hash"]) == 64
        for item in captured["entries"]
        if not item["logical_key"].startswith("code/")
    )
    assert {item["id"] for item in captured["dependencies"]} != {
        "master", "registry",
    }

    candidates = {
        entry["logical_key"]: Path(entry["path"]).read_bytes()
        for entry in captured["entries"]
    }
    assert ENGINE_SHA.encode() in candidates["entry/code"]
    drive_map = orchestration.yaml.safe_load(
        candidates["control/drive_map.yaml"].decode()
    )
    assert drive_map["v1_5_release"]["release_id"] == RELEASE
    assert drive_map["v1_5_release"]["code_commit"] == ENGINE_SHA
    assert b"current release: v1.5.4-1" not in candidates["entry/README"]
    assert b"current release: v1.5.4-1" not in candidates["derived/INDEX.md"]
    assert b"readme-existing-navigation-marker" in candidates["entry/README"]
    assert b"operations-substantive-marker" in candidates["entry/context"]
    assert b"migration-substantive-marker" in candidates["entry/context"]
    assert b"index-existing-navigation-marker" in candidates["derived/INDEX.md"]
    assert candidates["input/MASTER.xlsx"] == b"master"
    manifest = list(orchestration.csv.DictReader(
        orchestration.io.StringIO(
            candidates["input/manifest.csv"].decode("utf-8-sig"),
        ),
    ))
    assert {row["uid"] for row in manifest} == {
        item["logical_key"] for item in value["existing_targets"]
    } | {"code/src/new.py"}
    assert next(
        row for row in manifest if row["uid"] == "code/src/new.py"
    )["drive_file_id"] == allocation["reservations"]["code/src/new.py"]
    assert all(
        row["published_release"] == RELEASE for row in manifest
    )
    classification = json.loads(
        (tmp_path / "freeze/target_classification.json").read_text()
    )
    assert classification["control/drive_map.yaml"] == "CONTROL_METADATA_UPDATE"
    assert classification["input/MASTER.xlsx"] == "BUSINESS_FACT_CARRY_FORWARD"
    assert classification["code/src/new.py"] == "NEW_CODE_MIRROR"
    assert classification["input/manifest.csv"] == "CONTROL_METADATA_UPDATE"


@pytest.mark.parametrize("dependency_ids", [
    [],
    ["duplicate", "duplicate"],
    ["missing"],
])
def test_production_dependency_binding_fails_closed(
        dependency_ids, monkeypatch):
    monkeypatch.setattr(
        orchestration, "snapshot",
        lambda drive, file_id, mode="binary": (
            b"value", {
                "id": file_id, "version": "1", "modifiedTime": "t",
                "mimeType": "application/json", "parents": ["config"],
            },
        ) if file_id != "missing" else (_ for _ in ()).throw(OSError("missing")),
    )
    with pytest.raises(
        orchestration.ProjectionError,
        match="PRODUCTION_DEPENDENCY_BINDING_MISMATCH",
    ):
        orchestration.load_production_dependencies(
            object(), {"dependency_ids": dependency_ids},
        )


def test_production_dependency_binding_is_deterministic(monkeypatch):
    monkeypatch.setattr(
        orchestration, "snapshot",
        lambda drive, file_id, mode="binary": (
            file_id.encode(), {
                "id": file_id, "version": "1", "modifiedTime": "t",
                "mimeType": "application/json", "parents": ["config"],
            },
        ),
    )
    policy = {"dependency_ids": ["b", "a"]}
    first = orchestration.load_production_dependencies(object(), policy)
    second = orchestration.load_production_dependencies(object(), policy)
    assert first == second
    assert [item["id"] for item in first] == ["a", "b"]


def test_freeze_rejects_release_state_drift(tmp_path, monkeypatch):
    drive = ReservationDrive()
    value = projection()
    instance = ReservationInstance(tmp_path / "production.json")
    allocation = orchestration.reserve_staging(
        drive, instance, release_id=RELEASE, projection=value,
        output=tmp_path / "allocation", single_writer=True,
    )["allocation"]
    monkeypatch.setattr(
        orchestration, "verify_execution_sha",
        lambda root, sha, baseline=None: {"head": sha, "clean": True},
    )
    monkeypatch.setattr(
        orchestration, "snapshot",
        lambda drive, file_id, mode="binary": (
            b"changed-status", value["status_before_meta"],
        ),
    )
    with pytest.raises(
        orchestration.ProjectionError,
        match="STAGE4_RELEASE_STATE_DRIFT",
    ):
        orchestration.freeze_plan(
            drive, instance=instance, engine_root=tmp_path / "repo",
            release_id=RELEASE, projection=value, allocation=allocation,
            output=tmp_path / "freeze",
        )


def test_v161_projection_migrates_version_doc_as_existing_control_target():
    inputs = projection_inputs()
    version_id = '1ZebJR9YPKX37cMDdz0xznHDa45at_q65'
    inputs['previous_targets'][version_id] = {
        'mime': 'text/markdown',
        'mode': 'binary',
        'allowed_parents': ['ai'],
        'publish_parent': 'ai',
    }
    inputs['previous_status']['artifacts'].append({
        'id': version_id,
        'name': 'CBA-KB_v1.5.3.md',
        'sha256': 'version-old-sha',
    })
    inputs['manifest_rows'] = list(inputs['manifest_rows']) + [{
        'drive_file_id': version_id,
        'uid': 'ai/CBA-KB_v1.5.3.md',
        'content_hash': 'version-old-sha',
    }]
    inputs['release_id'] = 'v1.6.1-1'
    value = orchestration.project_targets(**inputs)
    assert value['state'] == 'PROJECTED'
    assert value['control_target_migrations'] == [{
        'status': 'MIGRATED',
        'release_id': 'v1.6.1-1',
        'logical_key': 'CURRENT_VERSION_DOC',
        'drive_file_id': version_id,
        'existing_object_reused': True,
        'source_logical_key': 'ai/CBA-KB_v1.5.3.md',
    }]
    by_key = {item['logical_key']: item for item in value['existing_targets']}
    assert by_key['CURRENT_VERSION_DOC']['id'] == version_id
    assert all(item['logical_key'] != 'CURRENT_VERSION_DOC' for item in value['new_targets'])
    ids = [item['id'] for item in value['existing_targets']]
    assert len(ids) == len(set(ids))
    target_keys = {item['logical_key'] for item in value['existing_targets']}
    target_keys.update(item['logical_key'] for item in value['new_targets'])
    assert orchestration.validate_manifest_coverage(
        target_keys, target_keys, target_keys, target_keys,
    )['missing'] == 0
    assert [item['status'] for item in value['control_target_migrations']].count('MIGRATED') == 1


def _closure_entries():
    specs = [
        ('config/canonical_products.yaml', 'config/canonical_products.yaml', 'control'),
        ('input/manifest.csv', 'manifest.csv', 'control'),
        ('entry/README', 'README', 'control'),
        ('derived/INDEX.md', 'INDEX', 'control'),
        ('ai/CONTEXT_CARD.md', 'CONTEXT_CARD.md', 'control'),
        ('CURRENT_VERSION_DOC', 'CURRENT_VERSION_DOC.md', 'control'),
        ('input/MASTER.xlsx', 'MASTER.xlsx', 'master'),
        ('facts/CBA_注册领域_六表.xlsx', 'six.xlsx', 'six_table'),
        ('facts/CBA_球员注册_EVENTS.xlsx', 'events.xlsx', 'six_table'),
        ('facts/CBA_外籍球员注册_SNAPSHOTS.xlsx', 'snapshots.xlsx', 'six_table'),
        ('input/source_registry.csv', 'source_registry.csv', 'source_registry'),
        ('evidence/bayi_legacy_context.md', 'bayi.md', 'evidence'),
    ]
    rows = []
    for index, (key, name, _kind) in enumerate(specs):
        rows.append({
            'logical_key': key, 'id': f'id-{index}', 'name': name,
            'mime': 'text/plain', 'mode': 'binary',
            'before_hash': f'{index:064x}',
            'publish_parent': (
                'source-evidence' if key == 'evidence/bayi_legacy_context.md'
                else 'root' if key not in {
                    'input/MASTER.xlsx', 'facts/CBA_注册领域_六表.xlsx',
                    'facts/CBA_球员注册_EVENTS.xlsx',
                    'facts/CBA_外籍球员注册_SNAPSHOTS.xlsx',
                }
                else 'data'
            ),
            'staging_parent': 'staging',
        })
    return rows


class ClosureInstance:
    def read_json(self, name):
        assert name == 'import_inventory.json'
        return {'parents': {
            'root': 'root', 'ai': 'ai', 'scripts': 'scripts',
            'config': 'config', 'data': 'data', 'archive': 'archive',
        }}


class ClosureDrive:
    def list(self, folder):
        return []


def test_v161_closure_uses_real_roles_protected_targets_and_zones():
    entries = _closure_entries()
    closure = orchestration._build_closure_contract(
        drive=ClosureDrive(),
        instance=ClosureInstance(),
        projection={'release_id': 'v1.6.1-1', 'engine_sha': 'a' * 40},
        allocation={'staging_id': 'staging'},
        entries=entries,
        state={'status': {
            'state': 'ROLLED_BACK', 'current_release_id': 'v1.6.0-1',
            'rolled_back_release_id': 'v1.6.1-1',
            'code_commit': 'b' * 40,
        }},
    )
    assert closure['documents'] == {
        'readme': 'entry/README',
        'index': 'derived/INDEX.md',
        'context_card': 'ai/CONTEXT_CARD.md',
        'current_version_doc': 'CURRENT_VERSION_DOC',
    }
    assert 'version' not in closure['documents']
    assert {item['kind'] for item in closure['protected']} >= {
        'master', 'six_table', 'source_registry', 'evidence',
    }
    assert closure['zones']['evidence'] == ['source-evidence']
    assert closure['previous_code_commit'] == 'b' * 40
    assert closure['baseline_release_id'] == 'v1.6.0-1'


def test_v161_candidate_identity_surfaces_are_generated():
    registry = canonical_registry()
    registry['registry_release_id'] = 'v1.6.1-1'
    registry_bytes = orchestration.yaml.safe_dump(
        registry, allow_unicode=True, sort_keys=False,
    ).encode()
    manifest_bytes = (
        b'uid,drive_file_id,content_hash\n'
        b'facts/events.jsonl,event-file,' + b'b' * 64 + b'\n'
        b'facts/old_events.jsonl,compat-file,' + b'c' * 64 + b'\n'
    )
    projection = {
        'release_id': 'v1.6.1-1', 'engine_sha': 'a' * 40,
        'active_release_id': 'v1.6.0-1',
    }
    allocation = {'status_id': 'status'}
    for key in ('entry/README', 'derived/INDEX.md', 'CURRENT_VERSION_DOC'):
        data = orchestration._content_preserving_candidate(
            key,
            ('current release: v1.5.4-1\n' if key != 'CURRENT_VERSION_DOC' else '').encode(),
            projection,
            allocation,
            {'entry/README': 'readme', 'derived/INDEX.md': 'index',
             'CURRENT_VERSION_DOC': 'version'},
            Path('.'),
            registry,
        )
        block = __import__('cba_kb.current_state', fromlist=['read_current_block']).read_current_block(
            data.decode(),
        )
        assert block['release_id'] == 'v1.6.1-1'
        assert block['code_commit'] == 'a' * 40
    context = orchestration._control_candidate(
        orchestration.CONTEXT_CARD_KEY,
        b'',
        projection,
        allocation,
        {},
        {},
        Path('.'),
        registry,
        registry_bytes,
        manifest_bytes,
    )
    assert context is not None
    block = __import__('cba_kb.current_state', fromlist=['read_current_block']).read_current_block(
        context.decode(),
    )
    assert block['release_id'] == 'v1.6.1-1'


def test_v181_candidate_repairs_drifted_current_surfaces_without_remote_writes(
        tmp_path, monkeypatch):
    registry = canonical_registry()
    registry['registry_release_id'] = 'v1.6.1-1'
    old_metadata = target_metadata('v1.6.1-1', 'b' * 40, registry)
    keys = {
        'config/canonical_products.yaml': 'registry',
        'input/manifest.csv': 'manifest',
        'entry/README': 'readme',
        'derived/INDEX.md': 'index',
        'ai/CONTEXT_CARD.md': 'card',
        'CURRENT_VERSION_DOC': 'version',
        'facts/events.jsonl': 'event-file',
        'facts/old_events.jsonl': 'compat-file',
    }
    old_block = render_current_state(old_metadata).encode()
    previous = {
        'registry': orchestration.yaml.safe_dump(
            registry, allow_unicode=True, sort_keys=False,
        ).encode(),
        'readme': old_block + b'Historical navigation retained.\n',
        'index': old_block + b'Historical index retained.\n',
        'version': old_block + b'Historical version retained.\n',
        'card': old_block + b'Code truth: private GitHub.\n',
        'event-file': b'canonical facts\n',
        'compat-file': b'compatibility facts\n',
    }
    previous['manifest'] = (
        'drive_file_id,uid,content_hash\n' + ''.join(
            f'{file_id},{key},old\n' for key, file_id in keys.items()
        )
    ).encode()
    with pytest.raises(ValueError, match='CURRENT_STATE_DRIFT'):
        validate_current_state(
            {'state': 'COMPLETE', 'current_release_id': 'v1.8.0-1',
             'code_commit': 'b' * 40},
            registry, canonical_manifest(),
            {'readme': previous['readme'].decode(),
             'index': previous['index'].decode(),
             'current_version_doc': previous['version'].decode(),
             'context_card': previous['card'].decode()},
        )
    original = dict(previous)
    snapshots = []

    def read_previous(_drive, file_id, _mode):
        snapshots.append(file_id)
        return previous[file_id], {'id': file_id}

    monkeypatch.setattr(orchestration, 'snapshot', read_previous)
    projection = {
        'release_id': 'v1.8.1-1', 'engine_sha': 'a' * 40,
        'active_release_id': 'v1.8.0-1',
        'existing_targets': [
            {'logical_key': key, 'id': file_id, 'name': key,
             'mime': 'text/plain', 'mode': 'binary'}
            for key, file_id in keys.items()
        ],
        'new_targets': [],
    }
    output = tmp_path / 'candidate'
    entries = orchestration._candidate_inputs(
        object(), tmp_path, projection, {'status_id': 'status', 'reservations': {}},
        output,
    )
    by_key = {entry['logical_key']: Path(entry['path']).read_bytes()
              for entry in entries}
    assert set(snapshots) == set(keys.values())
    assert previous == original
    candidate_registry = orchestration.yaml.safe_load(
        by_key['config/canonical_products.yaml'],
    )
    candidate_manifest = list(orchestration.csv.DictReader(
        orchestration.io.StringIO(
            by_key['input/manifest.csv'].decode('utf-8-sig'),
        ),
    ))
    documents = {
        'readme': by_key['entry/README'].decode(),
        'index': by_key['derived/INDEX.md'].decode(),
        'current_version_doc': by_key['CURRENT_VERSION_DOC'].decode(),
        'context_card': by_key['ai/CONTEXT_CARD.md'].decode(),
    }
    for text in documents.values():
        assert read_current_block(text) == target_metadata(
            'v1.8.1-1', 'a' * 40, candidate_registry,
        )
    assert 'Repository visibility: public.' in documents['context_card']
    assert 'private GitHub' not in documents['context_card']
    assert b'Historical navigation retained.' in by_key['entry/README']
    assert validate_current_state(
        {'state': 'COMPLETE', 'current_release_id': 'v1.8.1-1',
         'code_commit': 'a' * 40},
        candidate_registry, candidate_manifest, documents,
    )['status'] == 'PASS'


def _rollback_state_case():
    raw = json.dumps({
        'state': 'ROLLED_BACK', 'current_release_id': 'v1.6.0-1',
        'rolled_back_release_id': 'v1.6.1-1', 'code_commit': 'b' * 40,
        'artifacts': [{'id': 'a', 'name': 'a', 'sha256': 'x'}],
    }, sort_keys=True).encode()
    meta = {'id': 'status', 'version': '1', 'modifiedTime': 't',
            'mimeType': 'application/json', 'parents': ['root']}
    projection = {
        'release_id': 'v1.6.1-1',
        'status_id': 'status',
        'semantic_delta_signature': 'sig',
        'existing_targets': [{
            'logical_key': 'input/MASTER.xlsx', 'id': 'a',
            'mime': 'text/plain', 'mode': 'binary',
            'allowed_parents': ['data'], 'publish_parent': 'data',
            'staging_parent': None,
        }],
        'new_targets': [],
    }
    allocation = {
        'release_id': 'v1.6.1-1', 'semantic_delta_signature': 'sig',
        'status_id': 'status', 'active_release_id': 'v1.6.0-1',
        'reservations': {}, 'status_before_hash': digest(raw),
        'status_before_meta': meta,
    }
    policy = {'targets': {'a': {
        'mime': 'text/plain', 'mode': 'binary',
        'allowed_parents': ['data'], 'publish_parent': 'data',
    }}}
    return raw, meta, projection, allocation, policy


def test_rollback_baseline_reentry_validation(monkeypatch):
    raw, meta, projection, allocation, policy = _rollback_state_case()
    monkeypatch.setattr(
        orchestration, 'snapshot', lambda *args, **kwargs: (raw, meta),
    )
    result = orchestration.validate_release_state(
        object(), type('I', (), {'read_json': lambda self, name: policy})(),
        projection, allocation,
    )
    assert result['state'] == 'ROLLED_BACK'
    assert result['rolled_back_release_id'] == 'v1.6.1-1'
    for mutate in (
        lambda value: value.update(rolled_back_release_id='other'),
        lambda value: value.update(current_release_id='other'),
        lambda value: value.update(artifacts=[]),
    ):
        status = json.loads(raw)
        mutate(status)
        changed = json.dumps(status, sort_keys=True).encode()
        monkeypatch.setattr(
            orchestration, 'snapshot', lambda *args, **kwargs: (changed, meta),
        )
        with pytest.raises(orchestration.ProjectionError, match='STAGE4_RELEASE_STATE_DRIFT'):
            orchestration.validate_release_state(
                object(),
                type('I', (), {'read_json': lambda self, name: policy})(),
                projection, allocation,
            )


def _retirement_policy_case(*, projected_marker=None, live_marker=None):
    raw, meta, projection, allocation, policy = _rollback_state_case()
    if projected_marker is not None:
        projection['existing_targets'][0]['retire_in_release'] = (
            projected_marker
        )
    if live_marker is not None:
        policy['targets']['a']['retire_in_release'] = live_marker
    return raw, meta, projection, allocation, policy


def test_policy_reconciliation_fresh_binds_retirement_presence_and_value():
    raw, meta, projection, allocation, policy = _retirement_policy_case()
    assert orchestration.validate_policy_reconciliation(
        projection, allocation, policy,
    ) == {
        'active_production_targets': ['a'],
        'reserved_staging_targets': [],
        'retirement_bound_targets': [],
    }
    raw, meta, projection, allocation, policy = _retirement_policy_case(
        projected_marker=RELEASE, live_marker=RELEASE,
    )
    assert orchestration.validate_policy_reconciliation(
        projection, allocation, policy,
    ) == {
        'active_production_targets': ['a'],
        'reserved_staging_targets': [],
        'retirement_bound_targets': ['a'],
    }


@pytest.mark.parametrize('projected_marker,live_marker,scenario', [
    (RELEASE, None, 'removed_after_projection'),
    (RELEASE, 'v1.5.4-1', 'changed_after_projection'),
    (None, RELEASE, 'added_after_projection'),
    (RELEASE, '', 'invalidated_after_projection'),
])
def test_policy_reconciliation_rejects_retirement_marker_drift(
        projected_marker, live_marker, scenario):
    raw, meta, projection, allocation, policy = _retirement_policy_case(
        projected_marker=projected_marker, live_marker=live_marker,
    )
    with pytest.raises(
        orchestration.ProjectionError,
        match='ACTIVE_PRODUCTION_TARGET_RETIREMENT_CHANGED',
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, policy,
        )


def test_stage4_revalidation_fresh_binds_retirement_marker(monkeypatch):
    raw, meta, projection, allocation, policy = _retirement_policy_case(
        projected_marker=RELEASE, live_marker=RELEASE,
    )
    monkeypatch.setattr(
        orchestration, 'snapshot', lambda *args, **kwargs: (raw, meta),
    )
    result = orchestration.validate_release_state(
        object(),
        type('I', (), {'read_json': lambda self, name: policy})(),
        projection, allocation,
    )
    assert result['retirement_bound_targets'] == ['a']

    live_policy = json.loads(json.dumps(policy))
    del live_policy['targets']['a']['retire_in_release']
    with pytest.raises(
        orchestration.ProjectionError,
        match='ACTIVE_PRODUCTION_TARGET_RETIREMENT_CHANGED',
    ):
        orchestration.validate_release_state(
            object(),
            type('I', (), {'read_json': lambda self, name: live_policy})(),
            projection, allocation,
        )


def _incident_regression_fixture():
    release_id = "v2.0.0-1"
    active_ids = [f"active_{i:03d}" for i in range(1, 300)]  # 299 active
    hist_ids = ["hist_01", "hist_02"]  # 2 historical-retired
    res_keys = [f"res_key_{i:02d}" for i in range(1, 10)]  # 9 reservation keys
    res_ids = [f"res_target_{i:02d}" for i in range(1, 10)]
    reservations = dict(zip(res_keys, res_ids))

    existing_targets = [
        {
            "logical_key": f"entry/{aid}",
            "id": aid,
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["root"],
            "publish_parent": "root",
            "staging_parent": None,
        }
        for aid in active_ids
    ]
    new_targets = [
        {
            "logical_key": rkey,
            "id": rid,
            "name": f"{rkey}.txt",
            "mime": "text/plain",
            "mode": "binary",
            "publish_parent": "root",
        }
        for rkey, rid in reservations.items()
    ]
    projection = {
        "release_id": release_id,
        "status_id": "status_id",
        "semantic_delta_signature": "sig",
        "existing_targets": existing_targets,
        "new_targets": new_targets,
        "active_target_ids": sorted(active_ids),
        "historical_retired_policy_target_ids": sorted(hist_ids),
        "historical_retired_policy_markers": {
            "hist_01": "v1.9.0-1",
            "hist_02": "v1.9.0-1",
        },
    }
    allocation = {
        "release_id": release_id,
        "status_id": "status_id",
        "semantic_delta_signature": "sig",
        "reservations": reservations,
    }
    targets = {}
    for aid in active_ids:
        targets[aid] = {
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["root"],
            "publish_parent": "root",
        }
    for hid in hist_ids:
        targets[hid] = {
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["archive"],
            "publish_parent": "archive",
            "retire_in_release": "v1.9.0-1",
        }
    pre_reservation_policy = {"targets": dict(targets)}

    post_targets = dict(targets)
    for rid in res_ids:
        post_targets[rid] = {
            "mime": "text/plain",
            "mode": "binary",
            "allowed_parents": ["root", "staging"],
            "publish_parent": "root",
            "staging_parent": "staging",
        }
    post_reservation_policy = {"targets": post_targets}

    return projection, allocation, pre_reservation_policy, post_reservation_policy


def test_historical_retirement_policy_reconciliation_incident_shape():
    """Mandatory incident regression: 299 active + 2 historical-retired + 9 reserved.

    Pre-reservation policy set (301) must PASS.
    Post-reservation policy set (310) must PASS.
    311th unclassified target must FAIL CLOSED with PRIVATE_PRODUCTION_TARGET_DRIFT.
    """
    projection, allocation, pre_policy, post_policy = _incident_regression_fixture()
    assert len(projection["existing_targets"]) == 299
    assert len(projection["historical_retired_policy_target_ids"]) == 2
    assert len(allocation["reservations"]) == 9
    assert len(pre_policy["targets"]) == 301
    assert len(post_policy["targets"]) == 310

    # 1. Pre-reservation policy set = 301 must PASS
    pre_result = orchestration.validate_policy_reconciliation(
        projection, allocation, pre_policy,
    )
    assert len(pre_result["active_production_targets"]) == 299
    assert len(pre_result["reserved_staging_targets"]) == 9
    assert pre_result["historical_retired_policy_targets"] == ["hist_01", "hist_02"]

    # 2. Post-reservation policy set = 310 must PASS
    post_result = orchestration.validate_policy_reconciliation(
        projection, allocation, post_policy,
    )
    assert len(post_result["active_production_targets"]) == 299
    assert len(post_result["reserved_staging_targets"]) == 9
    assert post_result["historical_retired_policy_targets"] == ["hist_01", "hist_02"]

    # 3. Adding one unclassified 311th target must FAIL CLOSED
    drift_policy = json.loads(json.dumps(post_policy))
    drift_policy["targets"]["extra_311"] = {
        "mime": "text/plain",
        "mode": "binary",
        "allowed_parents": ["root"],
    }
    assert len(drift_policy["targets"]) == 311
    with pytest.raises(
        orchestration.ProjectionError,
        match="PRIVATE_PRODUCTION_TARGET_DRIFT",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, drift_policy,
        )


def test_historical_retirement_policy_reconciliation_negative_cases():
    """Mandatory negative test suite for historical-retired policy validation."""
    projection, allocation, pre_policy, post_policy = _incident_regression_fixture()

    # Case 1: historical-retired projection ID absent from live policy => fail closed
    missing_hist_policy = json.loads(json.dumps(pre_policy))
    del missing_hist_policy["targets"]["hist_01"]
    with pytest.raises(
        orchestration.ProjectionError,
        match="HISTORICAL_RETIRED_TARGET_NOT_IN_POLICY",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, missing_hist_policy,
        )

    # Case 2: historical-retired record marker removed => fail closed
    no_marker_policy = json.loads(json.dumps(pre_policy))
    del no_marker_policy["targets"]["hist_01"]["retire_in_release"]
    with pytest.raises(
        orchestration.ProjectionError,
        match="HISTORICAL_RETIRED_TARGET_RETIREMENT_CHANGED",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, no_marker_policy,
        )

    # Case 3: historical-retired record marker changed in a way that breaks authority => fail closed
    changed_marker_policy = json.loads(json.dumps(pre_policy))
    changed_marker_policy["targets"]["hist_01"]["retire_in_release"] = "v1.8.0-1"
    with pytest.raises(
        orchestration.ProjectionError,
        match="HISTORICAL_RETIRED_TARGET_RETIREMENT_CHANGED",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, changed_marker_policy,
        )

    # Case 4: current-release retirement marker cannot be silently treated as historical
    current_release_marker_policy = json.loads(json.dumps(pre_policy))
    current_release_marker_policy["targets"]["hist_01"]["retire_in_release"] = "v2.0.0-1"
    with pytest.raises(
        orchestration.ProjectionError,
        match="HISTORICAL_RETIRED_TARGET_POINTS_TO_CURRENT_RELEASE",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, current_release_marker_policy,
        )

    # Case 5: arbitrary extra policy target remains PRIVATE_PRODUCTION_TARGET_DRIFT
    arbitrary_extra_policy = json.loads(json.dumps(pre_policy))
    arbitrary_extra_policy["targets"]["arbitrary_extra_target"] = {
        "mime": "text/plain",
        "mode": "binary",
        "allowed_parents": ["root"],
    }
    with pytest.raises(
        orchestration.ProjectionError,
        match="PRIVATE_PRODUCTION_TARGET_DRIFT",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, arbitrary_extra_policy,
        )

    # Case 6: reserved ID set mismatch remains fail closed
    # 6a: partial reserved IDs in post-reservation policy
    partial_reserved_policy = json.loads(json.dumps(post_policy))
    del partial_reserved_policy["targets"]["res_target_09"]
    with pytest.raises(
        orchestration.ProjectionError,
        match="PRIVATE_PRODUCTION_TARGET_DRIFT",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, partial_reserved_policy,
        )

    # 6b: unexpected reserved ID in policy
    wrong_reserved_policy = json.loads(json.dumps(post_policy))
    del wrong_reserved_policy["targets"]["res_target_09"]
    wrong_reserved_policy["targets"]["res_target_wrong"] = {
        "mime": "text/plain",
        "mode": "binary",
        "allowed_parents": ["root", "staging"],
        "publish_parent": "root",
        "staging_parent": "staging",
    }
    with pytest.raises(
        orchestration.ProjectionError,
        match="PRIVATE_PRODUCTION_TARGET_DRIFT",
    ):
        orchestration.validate_policy_reconciliation(
            projection, allocation, wrong_reserved_policy,
        )

    # 6c: duplicate reserved IDs in allocation
    duplicate_allocation = json.loads(json.dumps(allocation))
    duplicate_allocation["reservations"]["res_key_09"] = "res_target_01"
    with pytest.raises(
        orchestration.ProjectionError,
        match="RESERVATION_ID_DUPLICATE",
    ):
        orchestration.validate_policy_reconciliation(
            projection, duplicate_allocation, post_policy,
        )

    # 6d: reservation reuses active or historical target
    reuse_allocation = json.loads(json.dumps(allocation))
    reuse_allocation["reservations"]["res_key_09"] = "hist_01"
    with pytest.raises(
        orchestration.ProjectionError,
        match="RESERVATION_REUSES_EXISTING_TARGET",
    ):
        orchestration.validate_policy_reconciliation(
            projection, reuse_allocation, post_policy,
        )

    # Case 7: target mislabeled historical-retired when it is actually active for current baseline
    active_mislabeled_proj = json.loads(json.dumps(projection))
    active_mislabeled_proj["historical_retired_policy_target_ids"].append("active_001")
    with pytest.raises(
        orchestration.ProjectionError,
        match="HISTORICAL_RETIRED_TARGET_IN_ACTIVE_STATUS",
    ):
        orchestration.validate_policy_reconciliation(
            active_mislabeled_proj, allocation, pre_policy,
        )


def test_project_command_scopes_unallocated_targets_to_active_and_retired_artifacts(
    monkeypatch, tmp_path,
):
    class FakeArgs:
        release = "v1.9.0-1"
        engine_sha = "a" * 40
        allocation = None
        output = tmp_path / "projection.json"

    status_data = {
        "artifacts": [
            {"id": "code-old", "name": "code-old", "sha256": "s1"},
            {"id": "readme", "name": "readme", "sha256": "s2"},
        ]
    }
    production = {
        "status_id": "status-doc",
        "archive_id": "archive-doc",
        "targets": {
            "code-old": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["scripts"], "publish_parent": "scripts"},
            "readme": {"mime": DOC, "mode": "managed_doc", "allowed_parents": ["root"], "publish_parent": "root"},
            "historical-retired-1": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["archive"], "publish_parent": "archive", "retire_in_release": "v1.8.0-1"},
            "historical-retired-2": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["archive"], "publish_parent": "archive", "retire_in_release": "v1.8.1-1"},
            "unreleased-staging-target": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["staging"], "staging_parent": "staging"},
        },
    }
    instance = type("FakeInstance", (), {
        "read_json": lambda self, name: production if name == "production.json" else {
            "inputs": {"manifest.csv": {"id": "manifest-id"}},
            "parents": {"scripts": "scripts"},
        },
        "config_path": lambda self, name: tmp_path / name,
    })()
    monkeypatch.setattr(orchestration, "verify_execution_sha", lambda *a, **k: None)
    monkeypatch.setattr(orchestration, "Drive", lambda *a, **k: type("FakeDrive", (), {
        "get": lambda self, file_id: json.dumps(status_data).encode() if file_id == "status-doc" else b"drive_file_id,uid\ncode-old,code-old\nreadme,readme\n",
        "meta": lambda self, file_id: {"id": file_id, "modifiedTime": "2026-09-30T00:00:00Z", "version": "1"},
    })())
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"code-old\0readme\0")
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"fake content")

    result = orchestration._project_command(FakeArgs(), instance)
    assert result["state"] == "PROJECTED"
    assert result["active_production_target_count"] == 2
    assert result["historical_retired_policy_target_ids"] == [
        "historical-retired-1",
        "historical-retired-2",
    ]
    assert result["historical_retired_policy_markers"] == {
        "historical-retired-1": "v1.8.0-1",
        "historical-retired-2": "v1.8.1-1",
    }
    assert "unreleased-staging-target" not in [t["id"] for t in result["existing_targets"]]


def test_project_command_current_release_retirement_fails_closed(monkeypatch, tmp_path):
    class FakeArgs:
        release = "v1.9.0-1"
        engine_sha = "a" * 40
        allocation = None
        output = tmp_path / "projection.json"

    status_data = {
        "artifacts": [
            {"id": "code-old", "name": "code-old", "sha256": "s1"},
        ]
    }
    production = {
        "status_id": "status-doc",
        "archive_id": "archive-doc",
        "targets": {
            "code-old": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["scripts"], "publish_parent": "scripts"},
            "invalid-current-retirement": {"mime": "text/plain", "mode": "binary", "allowed_parents": ["archive"], "publish_parent": "archive", "retire_in_release": "v1.9.0-1"},
        },
    }
    instance = type("FakeInstance", (), {
        "read_json": lambda self, name: production if name == "production.json" else {
            "inputs": {"manifest.csv": {"id": "manifest-id"}},
            "parents": {"scripts": "scripts"},
        },
        "config_path": lambda self, name: tmp_path / name,
    })()
    monkeypatch.setattr(orchestration, "verify_execution_sha", lambda *a, **k: None)
    monkeypatch.setattr(orchestration, "Drive", lambda *a, **k: type("FakeDrive", (), {
        "get": lambda self, file_id: json.dumps(status_data).encode() if file_id == "status-doc" else b"drive_file_id,uid\ncode-old,code-old\n",
        "meta": lambda self, file_id: {"id": file_id, "modifiedTime": "2026-09-30T00:00:00Z", "version": "1"},
    })())
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"code-old\0")
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"fake content")

    with pytest.raises(
        orchestration.ProjectionError,
        match="RETIREMENT_TARGET_NOT_IN_PREVIOUS_STATUS",
    ):
        orchestration._project_command(FakeArgs(), instance)


def _build_test_topology(entries, staging_id="staging"):
    nodes = [
        {"id": "root-folder", "role": "ROOT", "parents": []},
        {"id": "current-zone-1", "role": "CURRENT_ZONE", "parents": ["root-folder"]},
        {"id": "current-zone-2", "role": "CURRENT_ZONE", "parents": ["root-folder"]},
        {"id": "history-zone", "role": "HISTORY_ZONE", "parents": ["root-folder"]},
        {"id": staging_id, "role": "STAGING_ZONE", "parents": ["root-folder"]},
        {"id": "evidence-zone", "role": "EVIDENCE_ZONE", "parents": ["root-folder"]},
    ]
    for e in entries:
        nodes.append({"id": e["id"], "role": "CURRENT_TARGET", "parents": ["current-zone-1"]})
    return nodes


def _v190_closure_entries():
    rows = _closure_entries()
    rows.append({
        "logical_key": "entry/context", "id": "id-context", "name": "technical_manual.md",
        "mime": "text/markdown", "mode": "binary",
        "before_hash": "f" * 64,
        "publish_parent": "root",
        "staging_parent": "staging",
    })
    return rows


def test_closure_contract_uses_explicit_topology_zones(monkeypatch):
    entries = _v190_closure_entries()
    staging_id = "staging"
    top = _build_test_topology(entries, staging_id=staging_id)
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {"parents": {"root": "root-folder", "archive": "history-zone"}}

    monkeypatch.setattr(orchestration, "_evidence_baseline", lambda drive, roots: [])
    closure = orchestration._build_closure_contract(
        drive=ClosureDrive(),
        instance=TopInstance(),
        projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
        allocation={"staging_id": staging_id},
        entries=entries,
        state={"status": {
            "state": "ROLLED_BACK", "current_release_id": "v1.8.1-1",
            "rolled_back_release_id": "v1.8.1-2", "code_commit": "b" * 40,
        }},
    )
    assert closure["zones"]["current"] == ["current-zone-1", "current-zone-2"]
    assert closure["zones"]["history"] == ["history-zone"]
    assert closure["zones"]["staging"] == [staging_id]
    assert closure["zones"]["evidence"] == ["evidence-zone"]
    assert "root-folder" not in closure["zones"]["current"]


def test_closure_contract_accepts_mixed_zone_planned_topology(monkeypatch):
    entries = _v190_closure_entries()
    top = _build_test_topology(entries, staging_id="staging")
    history_target = next(node for node in top if node["id"] == entries[0]["id"])
    history_target.update(role="HISTORY_TARGET", parents=["history-zone"])
    evidence_entry = next(
        entry for entry in entries
        if entry["logical_key"] == "evidence/bayi_legacy_context.md"
    )
    evidence_target = next(
        node for node in top if node["id"] == evidence_entry["id"]
    )
    evidence_target.update(role="EVIDENCE_TARGET", parents=["evidence-zone"])
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {"parents": {"root": "root-folder", "archive": "history-zone"}}

    monkeypatch.setattr(orchestration, "_evidence_baseline", lambda drive, roots: [])
    closure = orchestration._build_closure_contract(
        drive=ClosureDrive(),
        instance=TopInstance(),
        projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
        allocation={"staging_id": "staging"},
        entries=entries,
        state={"status": {
            "state": "ROLLED_BACK", "current_release_id": "v1.8.1-1",
            "rolled_back_release_id": "v1.8.1-2", "code_commit": "b" * 40,
        }},
    )
    assert closure["zones"]["history"] == ["history-zone"]
    assert closure["zones"]["evidence"] == ["evidence-zone"]


def test_v201_canonical_freeze_closure_uses_production_shaped_fallback():
    entries = _v190_closure_entries()
    entries[0]["publish_parent"] = "history-zone"
    policy = {
        "zones": {
            "root": "release-root",
            "current": ["root", "data"],
            "history": "history-zone",
            "staging": "stale-staging-zone",
            "evidence": "source-evidence",
        },
        "targets": {
            entry["id"]: {"publish_parent": entry["publish_parent"]}
            for entry in entries
        },
    }

    class ProductionShapedInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            raise RuntimeError(f"unexpected config read: {name}")

        def config_path(self, name):
            return Path("/synthetic-not-present") / name

    closure = orchestration._build_closure_contract(
        drive=ClosureDrive(),
        instance=ProductionShapedInstance(),
        projection={"release_id": "v2.0.1-1", "engine_sha": "a" * 40},
        allocation={"staging_id": "active-staging-zone"},
        entries=entries,
        state={"status": {
            "state": "COMPLETE", "current_release_id": "v2.0.0-1",
            "code_commit": "b" * 40,
        }},
    )

    assert closure["zones"] == {
        "current": ["data", "root"],
        "history": ["history-zone"],
        "staging": ["active-staging-zone"],
        "evidence": ["source-evidence"],
    }
    assert closure["protected"]


def test_closure_contract_excludes_root_from_current(monkeypatch):
    entries = _v190_closure_entries()
    top = _build_test_topology(entries, staging_id="staging")
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {"parents": {"root": "root-folder", "archive": "history-zone"}}

    monkeypatch.setattr(orchestration, "_evidence_baseline", lambda drive, roots: [])
    closure = orchestration._build_closure_contract(
        drive=ClosureDrive(),
        instance=TopInstance(),
        projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
        allocation={"staging_id": "staging"},
        entries=entries,
        state={"status": {
            "state": "ROLLED_BACK", "current_release_id": "v1.8.1-1",
            "rolled_back_release_id": "v1.8.1-2", "code_commit": "b" * 40,
        }},
    )
    assert "root-folder" not in closure["zones"]["current"]


def test_closure_contract_fails_closed_when_topology_missing_required_zone_role(monkeypatch):
    entries = _v190_closure_entries()
    top = [n for n in _build_test_topology(entries) if n.get("role") != "EVIDENCE_ZONE"]
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {}

    with pytest.raises(orchestration.ProjectionError, match="CLOSURE_"):
        orchestration._build_closure_contract(
            drive=ClosureDrive(),
            instance=TopInstance(),
            projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
            allocation={"staging_id": "staging"},
            entries=entries,
            state={"status": {"state": "COMPLETE", "current_release_id": "v1.8.1-1", "code_commit": "b" * 40}},
        )


def test_closure_contract_fails_closed_when_allocation_staging_id_missing_from_staging_zone(monkeypatch):
    entries = _v190_closure_entries()
    top = _build_test_topology(entries, staging_id="staging-zone-1")
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {}

    with pytest.raises(orchestration.ProjectionError, match="CLOSURE_STAGING_ZONE_MISMATCH"):
        orchestration._build_closure_contract(
            drive=ClosureDrive(),
            instance=TopInstance(),
            projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
            allocation={"staging_id": "different-staging-id"},
            entries=entries,
            state={"status": {"state": "COMPLETE", "current_release_id": "v1.8.1-1", "code_commit": "b" * 40}},
        )


def test_closure_contract_fails_closed_on_topology_target_mismatch(monkeypatch):
    entries = _v190_closure_entries()
    # Missing entry id-0 in topology
    top = [n for n in _build_test_topology(entries) if n.get("id") != "id-0"]
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {}

    with pytest.raises(orchestration.ProjectionError, match="CLOSURE_TOPOLOGY_INVALID"):
        orchestration._build_closure_contract(
            drive=ClosureDrive(),
            instance=TopInstance(),
            projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
            allocation={"staging_id": "staging"},
            entries=entries,
            state={"status": {"state": "COMPLETE", "current_release_id": "v1.8.1-1", "code_commit": "b" * 40}},
        )


def test_closure_contract_fails_closed_on_topology_overlap(monkeypatch):
    entries = _v190_closure_entries()
    top = _build_test_topology(entries, staging_id="current-zone-1")  # Overlap staging with current
    policy = {"topology": top}

    class TopInstance:
        def read_json(self, name):
            if name == "production.json":
                return policy
            return {}

    with pytest.raises(orchestration.ProjectionError, match="CLOSURE_TOPOLOGY_INVALID|CLOSURE_ZONE_OVERLAP"):
        orchestration._build_closure_contract(
            drive=ClosureDrive(),
            instance=TopInstance(),
            projection={"release_id": "v1.9.0-1", "engine_sha": "a" * 40},
            allocation={"staging_id": "current-zone-1"},
            entries=entries,
            state={"status": {"state": "COMPLETE", "current_release_id": "v1.8.1-1", "code_commit": "b" * 40}},
        )


def test_historical_under_declared_current_zone_still_fails_audit_current_history():
    from cba_kb.current_state import audit_current_history
    from test_canonical_registry import manifest, registry
    zones = {
        "current": ["current-zone-1"],
        "history": ["history-zone"],
        "staging": ["staging-zone"],
        "evidence": ["evidence-zone"],
        "folders": {},
    }
    items = [
        {"id": "doc-hist", "name": "historical_registrations_2017.txt", "parents": ["current-zone-1"]},
    ]
    with pytest.raises(ValueError, match="CURRENT_HISTORY_VIOLATION"):
        audit_current_history(items, zones, manifest(), registry())


def test_historical_subtree_elsewhere_under_root_not_pulled_into_current_inventory():
    from cba_kb.release import _inventory
    # A Drive mock with root-folder containing current-zone-1 and an unrelated evidence/doc tree
    folders = {
        "current-zone-1": [
            {"id": "f-curr-1", "name": "active.md", "mimeType": "text/markdown", "parents": ["current-zone-1"]},
        ],
        "evidence-zone": [
            {"id": "f-ev-1", "name": "evidence.md", "mimeType": "text/markdown", "parents": ["evidence-zone"]},
        ],
        "other-subtree": [
            {"id": "f-hist-other", "name": "historical_stuff.md", "mimeType": "text/markdown", "parents": ["other-subtree"]},
        ],
    }
    class MockDrive:
        def list(self, folder):
            return folders.get(folder, [])

    zones = {
        "current": ["current-zone-1"],
        "history": ["history-zone"],
        "staging": ["staging-zone"],
        "evidence": ["evidence-zone"],
    }
    items, zones_with_folders = _inventory(MockDrive(), zones)
    item_ids = {it["id"] for it in items}
    assert "f-curr-1" in item_ids
    assert "f-ev-1" in item_ids
    assert "f-hist-other" not in item_ids
