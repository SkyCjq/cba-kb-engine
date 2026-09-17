import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from cba_kb.document_mentions import (
    build_mention_artifact,
    serialize_mention_artifact,
)
from cba_kb.master import HEADERS
from cba_kb.player_identity import new_registry, serialize_registry


ROOT = Path(__file__).resolve().parents[1]
UID_A = "pid_0000000000000001"
RECORD_KEY = "2026-2027|synthetic-club|SYNTHETIC PLAYER"


def command(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            **(env or {}),
        },
        capture_output=True,
        text=True,
    )


def write_master(path):
    row = {key: None for key in HEADERS}
    row.update({
        "record_key": RECORD_KEY,
        "season": "2026-2027",
        "club_id": "synthetic-club",
        "club_official": "Synthetic Club",
        "player": "SYNTHETIC PLAYER",
        "source_file_id": "synthetic-source",
        "source_url": "https://example.test/source",
        "verification_level": "machine_validated",
    })
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "MASTER"
    sheet.append(HEADERS)
    sheet.append([row[key] for key in HEADERS])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def write_fixtures(instance):
    master = instance / "inputs/master.xlsx"
    write_master(master)
    registry = new_registry(
        [{
            "schema_version": 1,
            "player_uid": UID_A,
            "canonical_name": "SYNTHETIC PLAYER",
            "status": "ACTIVE",
            "redirect_to": None,
        }],
        record_links=[{
            "schema_version": 1,
            "record_key": RECORD_KEY,
            "player_uid": UID_A,
            "link_status": "same",
            "method": "MANUAL_REVIEW",
            "confidence": "HIGH",
            "evidence_refs": ["synthetic-identity-evidence"],
        }],
    )
    registry_path = instance / "data/player_identity/registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_bytes(serialize_registry(registry))
    mention_path = instance / "inputs/mentions.json"
    mention_path.parent.mkdir(parents=True, exist_ok=True)
    mention_path.write_bytes(
        serialize_mention_artifact(build_mention_artifact([], [])),
    )
    return registry_path, mention_path


def test_consumer_profile_v2_cli_requires_private_instance(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    registry, mention = write_fixtures(instance)
    result = command(
        "consumer-profile-v2",
        "--master", str(instance / "inputs/master.xlsx"),
        "--player-uid", UID_A,
        "--identity-registry", str(registry),
        "--mention-artifact", str(mention),
        "--output", str(instance / "outputs/profile.json"),
        "--release-id", "v1.8.0-synthetic",
        "--as-of", "2026-09-17T00:00:00Z",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 1
    assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr


def test_consumer_profile_v2_cli_builds_deterministic_private_profile(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    registry, mention = write_fixtures(instance)
    instance_args = ("--instance-root", str(instance))
    common = (
        "consumer-profile-v2",
        "--master", "inputs/master.xlsx",
        "--player-uid", UID_A,
        "--identity-registry", "data/player_identity/registry.json",
        "--mention-artifact", "inputs/mentions.json",
        "--release-id", "v1.8.0-synthetic",
        "--as-of", "2026-09-17T00:00:00Z",
        "--generator-sha", "d" * 64,
    )
    first = command(
        *instance_args,
        *common,
        "--output", "outputs/profile-1.json",
    )
    second = command(
        *instance_args,
        *common,
        "--output", "outputs/profile-2.json",
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_bytes = (instance / "outputs/profile-1.json").read_bytes()
    second_bytes = (instance / "outputs/profile-2.json").read_bytes()
    assert first_bytes == second_bytes
    value = json.loads(first_bytes)
    assert value["profile_version"] == "v2.0"
    assert value["identity_selector"] == "player_uid"
    assert value["record_keys"] == [RECORD_KEY]


def test_consumer_profile_v2_cli_rejects_outside_paths(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    registry, mention = write_fixtures(instance)
    outside = tmp_path / "outside.xlsx"
    write_master(outside)
    result = command(
        "--instance-root", str(instance),
        "consumer-profile-v2",
        "--master", str(outside),
        "--player-uid", UID_A,
        "--identity-registry", str(registry),
        "--mention-artifact", str(mention),
        "--output", "outputs/profile.json",
        "--release-id", "v1.8.0-synthetic",
        "--as-of", "2026-09-17T00:00:00Z",
    )
    assert result.returncode == 1
    assert "PROFILE_V2_PATH_OUTSIDE_PRIVATE_INSTANCE" in result.stderr
