import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from cba_kb.master import HEADERS
from cba_kb.player_identity import new_registry, serialize_registry


ROOT = Path(__file__).resolve().parents[1]
UID_A = "pid_0000000000000001"


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
        "record_key": "2026-2027|synthetic-club|Synthetic Player",
        "season": "2026-2027",
        "club_id": "synthetic-club",
        "club_official": "Synthetic Club",
        "player": "Synthetic Player",
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


def write_registry(path):
    registry = new_registry([
        {
            "schema_version": 1,
            "player_uid": UID_A,
            "canonical_name": "Other Player",
            "status": "ACTIVE",
            "redirect_to": None,
        },
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_registry(registry))


def rewrite_csv(path):
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    for row in rows:
        row["human_decision"] = "APPROVE"
        row["reviewed_at"] = "2026-09-17T00:00:00Z"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(rows[0]),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(output.getvalue(), encoding="utf-8")


def test_cli_private_path_boundary(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "config").mkdir()
    write_master(instance / "inputs/master.xlsx")
    write_registry(instance / "data/player_identity/registry.json")
    outside = tmp_path / "outside.json"
    result = command(
        "--instance-root",
        str(instance),
        "identity-coverage-inventory",
        "--master",
        "inputs/master.xlsx",
        "--identity-registry",
        "data/player_identity/registry.json",
        "--output",
        str(outside),
    )
    assert result.returncode == 1
    assert "IDENTITY_COVERAGE_PATH_OUTSIDE_PRIVATE_INSTANCE" in result.stderr
    assert not outside.exists()


def test_cli_full_synthetic_private_workflow(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "config").mkdir()
    write_master(instance / "inputs/master.xlsx")
    write_registry(instance / "data/player_identity/registry.json")
    base_registry_before = (
        instance / "data/player_identity/registry.json"
    ).read_bytes()
    common = ("--instance-root", str(instance))

    result = command(
        *common,
        "identity-coverage-inventory",
        "--master",
        "inputs/master.xlsx",
        "--identity-registry",
        "data/player_identity/registry.json",
        "--output",
        "outputs/inventory.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-coverage-candidates",
        "--master",
        "inputs/master.xlsx",
        "--identity-registry",
        "data/player_identity/registry.json",
        "--output",
        "outputs/candidates.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-review-prepare",
        "--candidates",
        "outputs/candidates.json",
        "--output-json",
        "outputs/review_packet.json",
        "--output-csv",
        "outputs/review_packet.csv",
    )
    assert result.returncode == 0, result.stderr
    rewrite_csv(instance / "outputs/review_packet.csv")

    result = command(
        *common,
        "identity-review-validate",
        "--packet",
        "outputs/review_packet.json",
        "--reviewed-csv",
        "outputs/review_packet.csv",
        "--output",
        "outputs/reviewed_decisions.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-review-apply",
        "--master",
        "inputs/master.xlsx",
        "--base-registry",
        "data/player_identity/registry.json",
        "--packet",
        "outputs/review_packet.json",
        "--reviewed-decisions",
        "outputs/reviewed_decisions.json",
        "--output-registry",
        "outputs/candidate_registry.json",
        "--output-manifest",
        "outputs/candidate_registry_manifest.json",
        "--output-ledger",
        "outputs/coverage_ledger.json",
        "--created-at",
        "2026-09-17T00:00:00Z",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-coverage-reconcile",
        "--master",
        "inputs/master.xlsx",
        "--ledger",
        "outputs/coverage_ledger.json",
        "--final-registry",
        "outputs/candidate_registry.json",
        "--base-registry",
        "data/player_identity/registry.json",
        "--review-packet",
        "outputs/review_packet.json",
        "--reviewed-decisions",
        "outputs/reviewed_decisions.json",
        "--output",
        "outputs/reconciliation.json",
    )
    assert result.returncode == 0, result.stderr

    result = command(
        *common,
        "identity-coverage-certify",
        "--master",
        "inputs/master.xlsx",
        "--base-registry",
        "data/player_identity/registry.json",
        "--final-registry",
        "outputs/candidate_registry.json",
        "--review-packet",
        "outputs/review_packet.json",
        "--reviewed-decisions",
        "outputs/reviewed_decisions.json",
        "--candidate-registry-manifest",
        "outputs/candidate_registry_manifest.json",
        "--ledger",
        "outputs/coverage_ledger.json",
        "--output",
        "outputs/certificate.json",
    )
    assert result.returncode == 0, result.stderr

    certificate = json.loads(
        (instance / "outputs/certificate.json").read_text(encoding="utf-8")
    )
    packet = json.loads(
        (instance / "outputs/review_packet.json").read_text(encoding="utf-8")
    )
    reviewed = json.loads(
        (instance / "outputs/reviewed_decisions.json").read_text(
            encoding="utf-8",
        )
    )
    candidate = json.loads(
        (instance / "outputs/candidate_registry.json").read_text(
            encoding="utf-8",
        )
    )
    manifest = json.loads(
        (instance / "outputs/candidate_registry_manifest.json").read_text(
            encoding="utf-8",
        )
    )
    ledger = json.loads(
        (instance / "outputs/coverage_ledger.json").read_text(
            encoding="utf-8",
        )
    )
    assert manifest["base_registry_sha256"] == json.loads(
        (instance / "data/player_identity/registry.json").read_text(
            encoding="utf-8",
        )
    )["registry_sha256"]
    assert manifest["candidate_registry_sha256"] == candidate[
        "registry_sha256"
    ]
    assert manifest["review_packet_sha256"] == packet[
        "review_packet_sha256"
    ]
    assert manifest["reviewed_decisions_sha256"] == reviewed[
        "reviewed_decisions_sha256"
    ]
    assert certificate["full_record_coverage_complete"] is True
    assert certificate["full_identity_resolution_complete"] is False
    assert certificate["review_packet_sha256"] == packet[
        "review_packet_sha256"
    ]
    assert certificate["reviewed_decisions_sha256"] == reviewed[
        "reviewed_decisions_sha256"
    ]
    assert certificate["candidate_registry_manifest_sha256"] == manifest[
        "candidate_registry_manifest_sha256"
    ]
    assert certificate["coverage_ledger_sha256"] == ledger[
        "coverage_ledger_sha256"
    ]
    assert (
        instance / "data/player_identity/registry.json"
    ).read_bytes() == base_registry_before
