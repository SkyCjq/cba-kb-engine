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
