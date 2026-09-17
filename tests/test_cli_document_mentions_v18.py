import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
UID_A = "pid_0000000000000001"
UID_B = "pid_0000000000000002"
DOC_COPYRIGHT = "doc_000000000000000000000002"


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


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def mention(doc_id, player_uid, status="undecided", method="exact_name"):
    return {
        "schema_version": 1,
        "doc_id": doc_id,
        "player_uid": player_uid,
        "mention_status": status,
        "mention_role": "mentioned",
        "mention_method": method,
        "mention_confidence": "UNKNOWN",
        "evidence_ref": "synthetic-cli-evidence",
    }


def test_mention_cli_fails_closed_without_private_instance(tmp_path):
    source = tmp_path / "artifact.json"
    source.write_text("{}")
    result = command(
        "mention-validate",
        "--input", str(source),
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 1
    assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr


def test_mention_cli_builds_and_validates_private_artifacts(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    documents = [{
        "doc_id": DOC_COPYRIGHT,
        "rights": {
            "classification": "copyrighted",
            "public_export_allowed": False,
            "evidence": [],
        },
    }]
    mentions = [
        mention(DOC_COPYRIGHT, UID_A),
        mention(DOC_COPYRIGHT, UID_B, status="same", method="manual"),
    ]
    authorizations = [{
        "doc_id": DOC_COPYRIGHT,
        "target": "ChatGPT",
        "allowed_scope": "private_acceptance",
        "authorization_basis": "synthetic-cli-authorization",
        "frozen_at": "2026-09-17T00:00:00Z",
    }]
    write_json(instance / "inputs/documents.json", documents)
    write_json(instance / "inputs/mentions.json", mentions)
    write_json(instance / "inputs/authorizations.json", authorizations)
    instance_args = ("--instance-root", str(instance))

    first = command(
        *instance_args,
        "mention-build",
        "--documents", "inputs/documents.json",
        "--mentions", "inputs/mentions.json",
        "--authorizations", "inputs/authorizations.json",
        "--output", "outputs/artifact-1.json",
    )
    assert first.returncode == 0, first.stderr
    first_summary = json.loads(first.stdout)
    artifact_one = instance / "outputs/artifact-1.json"
    assert artifact_one.is_file()
    assert first_summary["confirmed"] == 1
    assert first_summary["undecided"] == 1
    assert first_summary["silent_drop_count"] == 0

    second = command(
        *instance_args,
        "mention-build",
        "--documents", "inputs/documents.json",
        "--mentions", "inputs/mentions.json",
        "--authorizations", "inputs/authorizations.json",
        "--output", "outputs/artifact-2.json",
    )
    assert second.returncode == 0, second.stderr
    artifact_two = instance / "outputs/artifact-2.json"
    assert artifact_one.read_bytes() == artifact_two.read_bytes()

    validated = command(
        *instance_args,
        "mention-validate",
        "--input", "outputs/artifact-1.json",
    )
    assert validated.returncode == 0, validated.stderr
    validated_summary = json.loads(validated.stdout)
    assert {
        key: value
        for key, value in first_summary.items()
        if key != "output"
    } == validated_summary

    duplicate_output = command(
        *instance_args,
        "mention-build",
        "--documents", "inputs/documents.json",
        "--mentions", "inputs/mentions.json",
        "--authorizations", "inputs/authorizations.json",
        "--output", "outputs/artifact-1.json",
    )
    assert duplicate_output.returncode == 1
    assert "MENTION_OUTPUT_EXISTS" in duplicate_output.stderr

    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    rejected = command(
        *instance_args,
        "mention-validate",
        "--input", str(outside),
    )
    assert rejected.returncode == 1
    assert "MENTION_PATH_OUTSIDE_PRIVATE_INSTANCE" in rejected.stderr
