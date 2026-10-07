"""Q2 Probe tests for REQ-202-PUBLIC-ENGINE-READINESS-01.

Probes:
1. test_commands_exist: README and OPERATIONS manual CLI commands exist in cba-kb CLI.
2. test_license_consistency: LICENSE, pyproject.toml, and README license declarations match.
3. test_no_private_leak_in_docs: No real Google Drive IDs or private user home paths in docs/.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_commands_exist():
    """Verify that every CLI command referenced in README and operational manuals exists in cba-kb."""
    cli_code = (ROOT / "src/cba_kb/cli.py").read_text(encoding="utf-8")
    registered_commands = set(re.findall(r"sub\.add_parser\(['\"]([^'\"]+)['\"]\)", cli_code))
    # Add commands added in loops: ('validate','build') and ('publish','verify','restore')
    for m in re.finditer(r"for cmd in \(([^)]+)\):.*?sub\.add_parser\(cmd\)", cli_code, re.DOTALL):
        for cmd_name in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
            registered_commands.add(cmd_name)

    assert "doctor" in registered_commands
    assert "source-intake" in registered_commands
    assert "statement-extract" in registered_commands
    assert "claim-extract" in registered_commands
    assert "research-view" in registered_commands
    assert "plan" in registered_commands
    assert "publish" in registered_commands
    assert "verify" in registered_commands

    # Documents to check
    doc_paths = [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs/operations/OPERATIONS.md",
        ROOT / "docs/operations/OPERATIONS.zh-CN.md",
    ]

    # Command reference regex: `cba-kb <subcommand>`
    cmd_pattern = re.compile(r'\bcba-kb\s+([a-z0-9_-]+)')

    checked_commands = set()
    for doc in doc_paths:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for match in cmd_pattern.finditer(text):
            cmd = match.group(1)
            if cmd in ("--help", "-h"):
                continue
            checked_commands.add(cmd)
            assert cmd in registered_commands, f"Command 'cba-kb {cmd}' referenced in {doc.name} is not registered in CLI"

    assert len(checked_commands) >= 5, f"Expected at least 5 checked commands, got {len(checked_commands)}"


def test_license_consistency():
    """Verify that LICENSE, pyproject.toml, and README license declarations match."""
    license_file = ROOT / "LICENSE"
    pyproject_file = ROOT / "pyproject.toml"
    readme_file = ROOT / "README.md"
    readme_zh_file = ROOT / "README.zh-CN.md"

    assert license_file.exists(), "LICENSE file must exist"
    license_text = license_file.read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0" in license_text

    pyproject_data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
    assert pyproject_data["project"]["version"] == "2.0.2"
    assert pyproject_data["project"]["license"]["text"] == "Apache-2.0"

    readme_text = readme_file.read_text(encoding="utf-8")
    assert "Apache License 2.0" in readme_text or "Apache-2.0" in readme_text
    assert "v2.0.2" in readme_text or "2.0.2" in readme_text

    readme_zh_text = readme_zh_file.read_text(encoding="utf-8")
    assert "Apache-2.0" in readme_zh_text
    assert "2.0.2" in readme_zh_text


def test_no_private_leak_in_docs():
    """Verify that no real Google Drive IDs or private home directory paths remain in docs/."""
    docs_dir = ROOT / "docs"
    if not docs_dir.exists():
        return

    drive_id_pattern = re.compile(r'\b1[0-9a-zA-Z_-]{27,43}\b')
    path_pattern = re.compile(r'/Users/[a-zA-Z0-9_-]+')

    for p in docs_dir.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        paths = path_pattern.findall(content)
        assert not paths, f"Private user home path leak in {p}: {paths}"

        dids = [
            x for x in drive_id_pattern.findall(content)
            if not (len(x) == 40 and all(c in '0123456789abcdefABCDEF' for c in x))
        ]
        assert not dids, f"Private Drive ID leak in {p}: {dids}"
