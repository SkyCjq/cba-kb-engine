#!/usr/bin/env python3
"""Run the public operations path with CC0 synthetic inputs only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PRIVATE_ENV_VARS = (
    "CBA_KB_INSTANCE_ROOT",
    "CBA_KB_SOURCE_POLICY_JSON",
    "CBA_STATS_AES_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cli-mode",
        choices=("entrypoint", "module"),
        default="entrypoint",
        help="Use the installed cba-kb entry point, or python -m for unit tests.",
    )
    return parser.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    repo = Path(__file__).resolve().parents[1]
    fixtures = repo / "tests" / "fixtures" / "cleanroom"
    output = args.output.resolve()
    instance = output / "synthetic-instance"
    artifacts = output / "artifacts"
    (instance / "config").mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    contaminated = [name for name in PRIVATE_ENV_VARS if os.environ.get(name)]
    if contaminated:
        raise RuntimeError(f"PRIVATE_CONFIG_PRESENT:{','.join(contaminated)}")

    if args.cli_mode == "entrypoint":
        executable = shutil.which("cba-kb")
        if not executable:
            raise RuntimeError("CBA_KB_ENTRYPOINT_NOT_INSTALLED")
        cli = [executable]
    else:
        cli = [sys.executable, "-m", "cba_kb.cli"]

    env = dict(os.environ)
    for name in PRIVATE_ENV_VARS:
        env.pop(name, None)
    if args.cli_mode == "module":
        env["PYTHONPATH"] = str(repo / "src")

    steps = []

    def run(label: str, *arguments: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        command = [*cli, "--root", str(repo), *arguments]
        result = subprocess.run(
            command,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        steps.append({
            "label": label,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        })
        if result.returncode != expect:
            raise RuntimeError(
                f"{label} returned {result.returncode}, expected {expect}: {result.stderr}"
            )
        return result

    private_guard = run("private path fails closed", "identity-read", expect=1)
    if "PRIVATE_INSTANCE_REQUIRED" not in private_guard.stderr:
        raise RuntimeError("PRIVATE_INSTANCE_GUARD_MISSING")

    run("doctor", "doctor")
    run(
        "initialize synthetic identity",
        "--instance-root", str(instance),
        "identity-write",
        "--input", str(fixtures / "identity_registry.json"),
    )
    run(
        "validate synthetic identity",
        "--instance-root", str(instance),
        "identity-validate",
        "--input", str(fixtures / "identity_registry.json"),
    )

    normalized = artifacts / "normalized.json"
    review_source = artifacts / "review_source.json"
    statements_path = artifacts / "statements.json"
    claim_path = artifacts / "claim.json"
    stats_path = artifacts / "stats.json"
    stats_validation = artifacts / "stats_validation.json"
    research_view = artifacts / "research_view.json"

    run(
        "import and normalize rights-safe source",
        "source-intake",
        "--input", str(fixtures / "source.txt"),
        "--source-provider", "synthetic_fixture",
        "--source-locator", "cc0://cleanroom/source.txt",
        "--rights-classification", "public",
        "--public-export-allowed",
        "--output", str(normalized),
    )
    run(
        "route empty source to review",
        "source-intake",
        "--input", str(fixtures / "empty_source.txt"),
        "--source-provider", "synthetic_fixture",
        "--source-locator", "cc0://cleanroom/empty_source.txt",
        "--rights-classification", "public",
        "--public-export-allowed",
        "--output", str(review_source),
    )
    run(
        "extract statements",
        "statement-extract",
        "--normalized-document", str(normalized),
        "--identity-registry", str(fixtures / "identity_registry.json"),
        "--output", str(statements_path),
    )
    run(
        "create review-required claim",
        "claim-extract",
        "--statements", str(statements_path),
        "--claim-text", "合成球队可能调整虚构训练安排",
        "--status", "review_required",
        "--review-reason", "synthetic unverified claim",
        "--output", str(claim_path),
    )
    run(
        "ingest offline stats",
        "stats-ingest",
        "--season", "2099",
        "--offline-payload", str(fixtures / "stats_payload.json"),
        "--identity-registry", str(fixtures / "identity_registry.json"),
        "--output", str(stats_path),
    )
    run(
        "validate stats",
        "stats-validate",
        "--input", str(stats_path),
        "--output", str(stats_validation),
    )
    run(
        "generate research output",
        "research-view",
        "--name", "合成球员甲",
        "--player-uid", "SYNTH_PLAYER_0001",
        "--statements", str(statements_path),
        "--claims", str(claim_path),
        "--stats", str(stats_path),
        "--format", "json",
        "--output", str(research_view),
    )

    source = load_json(normalized)
    empty = load_json(review_source)
    statements = load_json(statements_path)
    claim = load_json(claim_path)
    stats = load_json(stats_path)
    view = load_json(research_view)

    known = next(s for s in statements if s["speaker_actor_ref"]["raw_name"] == "合成球员甲")
    unresolved = next(s for s in statements if s["speaker_actor_ref"]["raw_name"] == "合成教练乙")
    review_statement = next(s for s in statements if s["extraction_status"] == "review_required")
    stats_record = stats[0]

    coverage = {
        "known_identity": known["speaker_actor_ref"]["kind"] == "player"
        and known["speaker_actor_ref"]["id"] == "SYNTH_PLAYER_0001",
        "unresolved_actor": unresolved["speaker_actor_ref"]["kind"] == "unresolved"
        and unresolved["speaker_actor_ref"]["id"] is None,
        "statement": len(statements) >= 3 and known["extraction_status"] == "accepted",
        "missing_vs_zero": stats_record["metrics"]["points_per_game"] == 0.0
        and stats_record["metrics"]["blocks_per_game"] == 0.0
        and stats_record["metrics"]["assists_per_game"] is None
        and stats_record["metrics"]["rebounds_per_game"] is None,
        "stats_sample": stats_record["identity_status"] == "RESOLVED"
        and stats_record["semantic_grain"] == "PLAYER_SEASON_STATS",
        "rights_safe_source": source["rights"]["classification"] == "public"
        and source["rights"]["public_export_allowed"] is True,
        "review_required": empty["intake_status"] == "review_required"
        and review_statement["extraction_status"] == "review_required"
        and claim["status"] == "review_required",
    }
    if not all(coverage.values()):
        raise RuntimeError(f"SEMANTIC_COVERAGE_FAILED:{coverage}")
    if view["subject_player_uid"] != "SYNTH_PLAYER_0001":
        raise RuntimeError("RESEARCH_VIEW_IDENTITY_MISMATCH")

    report = {
        "requirement": "REQ-202-CLEANROOM-OPERATIONS-01",
        "fixture_license": "CC0-1.0",
        "private_config_present": False,
        "coverage": coverage,
        "steps": steps,
        "artifacts": sorted(str(path.relative_to(output)) for path in artifacts.iterdir()),
    }
    (output / "smoke-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "LOCAL_SMOKE_COMPLETED", "coverage": coverage}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
