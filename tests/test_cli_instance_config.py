import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def command(*args, env=None):
    return subprocess.run(
        [sys.executable, '-m', 'cba_kb.cli', '--root', str(ROOT), *args],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'src'), **(env or {})},
        capture_output=True, text=True,
    )


def test_doctor_runs_clean_room_without_private_instance():
    result = command('doctor', env={'CBA_KB_INSTANCE_ROOT': ''})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['instance_configured'] is False
    assert report['production_enabled'] is False
    assert report['sandbox_folder_id'] is None


def test_private_command_fails_closed_without_instance(tmp_path):
    result = command('watch', '--output', str(tmp_path/'watch'),
                     env={'CBA_KB_INSTANCE_ROOT': ''})
    assert result.returncode != 0
    assert 'PRIVATE_INSTANCE_REQUIRED' in result.stderr

def test_consumer_and_evidence_validation_commands_are_private_and_deterministic(tmp_path):
    result = command(
        "consumer-validate",
        "--config", str(ROOT / "config/consumer_golden_questions_v1.yaml"),
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PASS"

    ledger = tmp_path / "evidence/REQ-161-CLOSEOUT-01/runtime-ledger"
    from cba_kb.evidence_ledger import RuntimeEvidenceLedger
    RuntimeEvidenceLedger(ledger, "REQ-161-CLOSEOUT-01").append(
        "STAGE_2_STARTED",
        {"mode": "CODEX_EXECUTOR"},
        recorded_at="2026-09-13T00:00:00Z",
    )
    result = command(
        "evidence-verify",
        "--ledger-root", str(ledger),
        "--requirement-id", "REQ-161-CLOSEOUT-01",
        env={"CBA_KB_INSTANCE_ROOT": ""},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["entries"] == 1
