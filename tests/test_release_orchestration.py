import json
from pathlib import Path

import pytest

from cba_kb.common import digest
from cba_kb.release import publish
from scripts import prepare_production as orchestration


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


def test_projection_binds_exact_release_id_and_fails_closed():
    inputs = projection_inputs()
    inputs["release_id"] = "v1.5.5-2"
    with pytest.raises(orchestration.ProjectionError, match="RELEASE_ID_FORBIDDEN"):
        orchestration.project_targets(**inputs)


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
        lambda root, sha: {"head": sha, "clean": True},
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
        lambda root, sha: {"head": sha, "clean": True},
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
