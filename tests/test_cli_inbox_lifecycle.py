"""CLI contract tests for the PR1 inbox lifecycle surface (Q1 freeze, behavioral)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def command(*args, env=None):
    # NOTE: PYTHONPATH is forced after the env override so a pre-existing
    # CI PYTHONPATH (e.g. $GITHUB_WORKSPACE) can never shadow ROOT/src.
    merged = {**os.environ, **(env or {}),
              "PYTHONPATH": str(ROOT / "src"),
              "PYTHONDONTWRITEBYTECODE": "1"}
    # The 1.0 s stability window is frozen and not configurable (Q2 A4).
    merged.pop("CBA_KB_INBOX_STABILITY_SECONDS", None)
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT, env=merged, capture_output=True, text=True, timeout=120,
    )


# Real wait for the frozen 1.0 s stability window (see the sibling module).
STABILITY_WAIT = 1.2


def private_instance(tmp_path):
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / "inbox/documents").mkdir(parents=True)
    return root


def test_inbox_scan_prints_stable_json_summary(tmp_path):
    private = private_instance(tmp_path)
    result = command("--instance-root", str(private), "inbox-scan")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    for key in ("scanned_files", "discoveries", "processed_new",
                "unsupported", "review_required", "skipped_system_or_symlink"):
        assert key in summary, key


def test_archive_status_all_and_source_id(tmp_path):
    private = private_instance(tmp_path)
    result = command("--instance-root", str(private), "archive-status", "--all")
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["source_count"] == 0
    assert status["by_processing_status"] == {}
    missing = command("--instance-root", str(private),
                      "archive-status", "--source-id", "src_doesnotexist")
    assert missing.returncode != 0
    assert "SOURCE_NOT_FOUND" in missing.stderr


def test_archive_query_requires_exactly_one_mode(tmp_path):
    private = private_instance(tmp_path)
    base = ["--instance-root", str(private), "archive-query"]
    assert command(*base).returncode != 0
    assert command(*base, "--id", "x", "--title", "y").returncode != 0
    empty = command(*base, "--title", "")
    assert empty.returncode != 0


def test_archive_query_rejects_bad_visibility(tmp_path):
    private = private_instance(tmp_path)
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "x",
                     "--visibility", "everyone")
    assert result.returncode != 0


def test_inbox_poll_rejects_sub_second_interval(tmp_path):
    private = private_instance(tmp_path)
    result = command("--instance-root", str(private),
                     "inbox-poll", "--interval-seconds", "0")
    assert result.returncode != 0


def test_commands_fail_closed_without_instance(tmp_path):
    for argv in (["inbox-scan"], ["archive-status", "--all"],
                 ["archive-query", "--keyword", "x"]):
        result = command(*argv, env={"CBA_KB_INSTANCE_ROOT": ""})
        assert result.returncode != 0
        assert "PRIVATE_INSTANCE_REQUIRED" in result.stderr


def test_concurrent_writer_is_refused(tmp_path):
    private = private_instance(tmp_path)
    lock_path = private / "data/inbox_lifecycle/.lock"
    proc = subprocess.Popen(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT),
         "--instance-root", str(private),
         "inbox-poll", "--interval-seconds", "30", "--max-scans", "100"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
             "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while not lock_path.is_file():
            assert time.time() < deadline, "poll did not take the lock"
            time.sleep(0.1)
        result = command("--instance-root", str(private), "inbox-scan")
        assert result.returncode != 0
        assert "INBOX_LIFECYCLE_LOCKED" in result.stderr
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_scan_never_writes_into_engine_source_tree(tmp_path):
    private = private_instance(tmp_path)
    before = {p for p in ROOT.rglob("*") if p.is_file()}
    (private / "inbox/documents/n.txt").write_text("hello\n")
    (private / "inbox/documents/n.txt.source.json").write_text(json.dumps({
        "schema_version": 1, "title": "t", "origin": "local_owned",
        "source_url": None,
        "rights": {"classification": "private",
                   "public_export_allowed": False, "evidence": []}}))
    command("--instance-root", str(private), "inbox-scan")
    time.sleep(STABILITY_WAIT)
    command("--instance-root", str(private), "inbox-scan")
    after = {p for p in ROOT.rglob("*") if p.is_file()}
    assert after - before == set()
