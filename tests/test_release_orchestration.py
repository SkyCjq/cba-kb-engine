import json
from pathlib import Path

import pytest

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
    }
    status = {
        "state": "COMPLETE",
        "artifacts": [
            {"id": "code-old", "name": "Makefile", "sha256": "old-code-sha"},
            {"id": "drive-map", "name": "drive_map.yaml", "sha256": "old-map-sha"},
            {"id": "entry-code", "name": "CODE_MANIFEST.md", "sha256": "old-code-doc"},
            {"id": "readme", "name": "00_README", "sha256": "old-readme"},
            {"id": "context", "name": "context.md", "sha256": "old-context"},
            {"id": "index", "name": "INDEX", "sha256": "old-index-sha"},
            {"id": "master", "name": "MASTER.xlsx", "sha256": "old-master"},
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
    }


def projection():
    return orchestration.project_targets(**projection_inputs())


def test_projection_is_read_only_deterministic_and_exact():
    first = orchestration.project_targets(**projection_inputs())
    second = orchestration.project_targets(**projection_inputs())
    assert first == second
    assert first["previous_artifact_count"] == 7
    assert first["reused_target_count"] == 7
    assert first["new_target_count"] == 1
    assert first["removed_target_count"] == 0
    assert first["carried_forward_count"] == 1
    assert first["final_artifact_count"] == 8
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
        self.policy = {
            "enabled": True,
            "targets": projection_inputs()["previous_targets"],
        }

    def read_json(self, name):
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
        "index", "master", "reserved-2",
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
    allocation = orchestration.reserve_staging(
        drive, ReservationInstance(tmp_path / "production.json"),
        release_id=RELEASE, projection=value,
        output=tmp_path / "allocation", single_writer=True,
    )["allocation"]
    drive.calls = []
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "Makefile").write_bytes(b"new-code")
    (root / "src/new.py").write_bytes(b"new-file")

    def fake_snapshot(drive, file_id, mode="binary"):
        return {
            "master": b"master",
            "registry": b"registry",
            "drive-map": b"version: 1\nroot: {}\nfolders: {}\n",
            "entry-code": b"old code manifest",
            "readme": b"old readme",
            "context": b"old context",
            "index": b"old index",
        }.get(file_id, b"old"), {
            "id": file_id, "version": "1", "modifiedTime": "t",
            "mimeType": "application/json", "parents": ["ai"],
        }

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
        instance=Instance(),
        engine_root=root,
        release_id=RELEASE,
        projection=value,
        allocation=allocation,
        output=tmp_path / "freeze",
    )

    assert result["entries"] == 8
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
    assert b"v1.5.4" not in candidates["entry/README"]
    assert b"v1.5.4" not in candidates["entry/context"]
    assert b"v1.5.4" not in candidates["derived/INDEX.md"]
    assert candidates["input/MASTER.xlsx"] == b"master"
    classification = json.loads(
        (tmp_path / "freeze/target_classification.json").read_text()
    )
    assert classification["control/drive_map.yaml"] == "CONTROL_METADATA_UPDATE"
    assert classification["input/MASTER.xlsx"] == "BUSINESS_FACT_CARRY_FORWARD"
    assert classification["code/src/new.py"] == "NEW_CODE_MIRROR"
