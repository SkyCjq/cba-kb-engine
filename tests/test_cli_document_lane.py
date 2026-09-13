import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FIXED_AT = "2026-09-13T06:00:00Z"


def command(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        },
        capture_output=True,
        text=True,
    )


def private_instance(tmp_path):
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / "inbox/documents").mkdir(parents=True)
    return root


def test_document_ingest_cli_writes_only_private_archive(tmp_path):
    private = private_instance(tmp_path)
    source = private / "inbox/documents/story.txt"
    source.write_text("CLI document\n")
    result = command(
        "--instance-root",
        str(private),
        "document-ingest",
        "--capture-channel",
        "local_file",
        "--source",
        "story.txt",
        "--captured-at",
        FIXED_AT,
        "--latency-seconds",
        "0",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "ACCEPTED"
    archive = private / "data/document_lane/archive" / output["doc_id"]
    assert (archive / "record.json").is_file()
    assert (archive / "text.txt").read_text() == "CLI document\n"


def test_document_batch_cli_does_not_require_drive_for_local_entries(tmp_path):
    private = private_instance(tmp_path)
    (private / "inbox/documents/one.txt").write_text("one\n")
    (private / "inbox/documents/two.txt").write_text("two\n")
    manifest = private / "inbox/documents/manifest.json"
    manifest.write_text(json.dumps({
        "documents": [
            {"path": "one.txt", "capture_channel": "local_file"},
            {"path": "two.txt", "capture_channel": "local_file"},
        ]
    }))
    result = command(
        "--instance-root",
        str(private),
        "document-batch",
        "--manifest",
        "manifest.json",
        "--captured-at",
        FIXED_AT,
        "--latency-seconds",
        "0",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["documents"] == 2
    assert output["accepted"] == 2
    assert len(list((private / "data/document_lane/reports").glob("*.json"))) == 1


def test_document_ingest_cli_supports_ima_export_without_network(tmp_path):
    private = private_instance(tmp_path)
    source = private / "inbox/documents/ima-export.md"
    source.write_text("---\ntitle: ima export\n---\nlocal ima body\n")
    result = command(
        "--instance-root",
        str(private),
        "document-ingest",
        "--capture-channel",
        "ima_file_export",
        "--source",
        "ima-export.md",
        "--captured-at",
        FIXED_AT,
        "--latency-seconds",
        "0",
        env={
            "CBA_KB_INSTANCE_ROOT": "",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
        },
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    archive = Path(output["archive_path"])
    record = json.loads((archive / "record.json").read_text())
    assert record["capture_channel"] == "ima_file_export"
    assert record["rights"]["classification"] == "unknown"
    assert record["rights"]["public_export_allowed"] is False
    provenance = json.loads((archive / "provenance.json").read_text())
    raw = archive / provenance["captures"][0]["raw_path"]
    assert raw.read_bytes() == source.read_bytes()


def test_document_cli_fails_closed_without_private_instance(tmp_path):
    result = command(
        "document-ingest",
        "--capture-channel",
        "local_file",
        "--source",
        "missing.txt",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode != 0
    assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr
