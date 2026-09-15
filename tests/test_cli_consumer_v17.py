import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from cba_kb.master import HEADERS


ROOT = Path(__file__).resolve().parents[1]


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
        "record_key": "2026-2027|synthetic-club|SYNTHETIC PLAYER",
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
    workbook.save(path)
    return row["record_key"]


def test_v17_consumer_cli_is_offline_and_writes_only_explicit_outputs(tmp_path):
    master = tmp_path / "synthetic-master.xlsx"
    record_key = write_master(master)
    selectors = tmp_path / "selectors.json"
    selectors.write_text(json.dumps({"record_keys": [record_key]}))
    profile = tmp_path / "profile.json"
    result = command(
        "consumer-profile",
        "--master", str(master),
        "--selectors", str(selectors),
        "--output", str(profile),
        "--release-id", "v1.7.0-synthetic",
        "--as-of", "2026-09-15T00:00:00Z",
        "--generator-sha", "d" * 64,
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(profile.read_text())["rows"][0]["record_key"] == record_key

    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps([
        {
            "doc_id": "doc-public",
            "rights": "public",
            "title": "Synthetic public title",
            "body": "Synthetic public body",
            "body_status": "AVAILABLE",
            "source_ref": "source-public",
            "public_export_allowed": True,
        },
        {
            "doc_id": "doc-private",
            "rights": "private",
            "title": "Synthetic private title",
            "body": "Synthetic private body",
            "body_status": "AVAILABLE",
            "source_ref": "source-private",
            "public_export_allowed": False,
        },
    ]))
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([
        {"source_id": "source-public", "type": "document"},
        {"source_id": "source-master", "type": "master"},
    ]))
    events = tmp_path / "events.json"
    events.write_text(json.dumps({
        "season": "2026-2027",
        "team": "Synthetic Club",
        "events": [],
        "sources": [],
        "as_of": "2026-09-15T00:00:00Z",
    }))
    evidence_body = "SYNTHETIC TARGET AUTHORIZED EVIDENCE"
    authorizations = tmp_path / "authorizations.json"
    authorizations.write_text(json.dumps([{
        "doc_id": "doc-private",
        "target": "ChatGPT",
        "allowed_scope": "private_acceptance",
        "authorization_basis": "synthetic-test-authorization",
        "frozen_at": "2026-09-15T00:00:00Z",
    }]))
    authorized_evidence = tmp_path / "authorized-evidence.json"
    authorized_evidence.write_text(json.dumps([{
        "doc_id": "doc-private",
        "body": evidence_body,
        "sha256": hashlib.sha256(evidence_body.encode()).hexdigest(),
        "source_ref": "synthetic-private-source",
    }]))
    output = tmp_path / "package-run"
    result = command(
        "consumer-package",
        "--profile", str(profile),
        "--documents", str(documents),
        "--sources", str(sources),
        "--events", str(events),
        "--authorizations", str(authorizations),
        "--authorized-evidence", str(authorized_evidence),
        "--output", str(output),
        "--release-id", "v1.7.0-synthetic",
        "--as-of", "2026-09-15T00:00:00Z",
        "--provenance", "synthetic-fixture",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert output.joinpath("canonical_consumer_payload.json").is_file()
    assert output.joinpath("targets/chatgpt/package_manifest.json").is_file()
    assert output.joinpath(
        "targets/chatgpt/authorized-evidence/doc-private.json",
    ).is_file()
    assert output.joinpath("targets/gemini-notebook/README.md").is_file()
    assert output.joinpath("targets/workbuddy/coverage_report.json").is_file()
    assert not output.joinpath(
        "targets/gemini-notebook/authorized-evidence/doc-private.json",
    ).exists()
    assert report["consumer_payload_sha256"] == json.loads(
        output.joinpath("canonical_consumer_payload.json").read_text(),
    )["consumer_payload_sha256"]
