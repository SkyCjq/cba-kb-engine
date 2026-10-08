from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cba_kb.source_policy import excluded_drive_ids


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ENV_VARS = (
    "CBA_KB_INSTANCE_ROOT",
    "CBA_KB_SOURCE_POLICY_JSON",
    "CBA_STATS_AES_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def clean_env():
    env = dict(os.environ)
    for name in PRIVATE_ENV_VARS:
        env.pop(name, None)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def test_cleanroom_operations_smoke(tmp_path):
    output = tmp_path / "cleanroom-output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_cleanroom_smoke.py"),
            "--cli-mode", "module",
            "--output", str(output),
        ],
        cwd=ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "smoke-report.json").read_text(encoding="utf-8"))
    assert report["fixture_license"] == "CC0-1.0"
    assert report["private_config_present"] is False
    assert set(report["coverage"]) == {
        "known_identity",
        "unresolved_actor",
        "statement",
        "missing_vs_zero",
        "stats_sample",
        "rights_safe_source",
        "review_required",
    }
    assert all(report["coverage"].values())


def test_no_private_config_dependency(tmp_path):
    with pytest.raises(RuntimeError, match="PRIVATE_SOURCE_POLICY_REQUIRED"):
        excluded_drive_ids({})

    result = subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), "identity-read"],
        cwd=ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr
    assert not (tmp_path / "private-instance").exists()
