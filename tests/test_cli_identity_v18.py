import json
import os
from pathlib import Path
import subprocess
import sys

from cba_kb.player_identity import new_registry, serialize_registry


ROOT = Path(__file__).resolve().parents[1]
UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"


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


def player(uid, name):
    return {
        "schema_version": 1,
        "player_uid": uid,
        "canonical_name": name,
        "status": "ACTIVE",
        "redirect_to": None,
    }


def link(record_key, uid):
    return {
        "schema_version": 1,
        "record_key": record_key,
        "player_uid": uid,
        "link_status": "same",
        "method": "MANUAL_REVIEW",
        "confidence": "HIGH",
        "evidence_refs": ["synthetic-cli-evidence"],
    }


def test_identity_cli_fails_closed_without_private_instance(tmp_path):
    result = command("identity-read", env={"CBA_KB_INSTANCE_ROOT": ""})
    assert result.returncode == 1
    assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr
    source = tmp_path / "registry.json"
    source.write_text("{}")
    validate = command(
        "identity-validate",
        "--input", str(source),
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert validate.returncode == 1
    assert "PRIVATE_INSTANCE_REQUIRED" in validate.stderr


def test_identity_cli_round_trip_candidates_and_compare_and_swap(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    registry = new_registry(
        [player(UID_A, "刘晓宇"), player(UID_B, "刘晓宇")],
        record_links=[
            link("2017-2018|beijing_shougang|刘晓宇", UID_A),
            link("2024-2025|beijing_konggu|刘晓宇", UID_B),
        ],
    )
    source = tmp_path / "registry.json"
    source.write_bytes(serialize_registry(registry))
    instance_args = ("--instance-root", str(instance))

    written = command(
        *instance_args,
        "identity-write",
        "--input", str(source),
    )
    assert written.returncode == 0, written.stderr
    store_path = instance / "data/player_identity/registry.json"
    assert store_path.read_bytes() == serialize_registry(registry)

    read_one = command(*instance_args, "identity-read")
    read_two = command(*instance_args, "identity-read")
    assert read_one.returncode == 0, read_one.stderr
    assert read_one.stdout == read_two.stdout
    assert json.loads(read_one.stdout) == registry

    candidates = command(
        *instance_args,
        "identity-candidates",
        "--name", "刘晓宇",
    )
    assert candidates.returncode == 0, candidates.stderr
    candidate_result = json.loads(candidates.stdout)
    assert candidate_result["status"] == "REVIEW_REQUIRED"
    assert candidate_result["identity_assertion"] == "NONE"
    assert candidate_result["automatic_link_allowed"] is False
    assert len(candidate_result["candidates"]) == 2

    stale = command(
        *instance_args,
        "identity-write",
        "--input", str(source),
    )
    assert stale.returncode == 1
    assert "CURRENT_SHA256_REQUIRED" in stale.stderr

    replaced = command(
        *instance_args,
        "identity-write",
        "--input", str(source),
        "--expected-current-sha256", registry["registry_sha256"],
    )
    assert replaced.returncode == 0, replaced.stderr


def test_identity_validate_is_machine_readable_and_fail_closed(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    registry = new_registry([player(UID_A, "Synthetic Player")])
    source = tmp_path / "registry.json"
    source.write_bytes(serialize_registry(registry))
    result = command(
        "--instance-root", str(instance),
        "identity-validate",
        "--input", str(source),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["registry_sha256"] == registry["registry_sha256"]

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}")
    failed = command(
        "--instance-root", str(instance),
        "identity-validate",
        "--input", str(invalid),
    )
    assert failed.returncode == 1
    assert "IDENTITY_REGISTRY_SCHEMA_INVALID" in failed.stderr
