from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .drive_io import DirectoryDriveStore, GoogleDriveStore
from .git_io import GitHubInspector
from .handoff import TransitionIntent, transition_commit
from .ledger import AppendOnlyLedger
from .models import P2AError, canonical_json_bytes, load_json_bytes, load_yaml_bytes, read_bytes, sha256_bytes
from .review_package import build_review_package
from .verify import verify_historical_source_result, verify_result_bytes, verify_task_bytes


def _emit(value: Any) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value))


def _task_verify(args: argparse.Namespace) -> int:
    data = read_bytes(args.task)
    current = read_bytes(args.current) if args.current else None
    result = verify_task_bytes(
        data,
        expected_task_sha256=args.expected_task_sha256,
        expected_generation=args.expected_generation,
        expected_executor=args.expected_executor,
        expected_requirement_sha256=args.expected_requirement_sha256,
        expected_policy_sha256=args.expected_policy_sha256,
        expected_base_sha=args.expected_base_sha,
        current_pointer_bytes=current,
    )
    if result.ok and args.ledger:
        task = load_yaml_bytes(data)
        AppendOnlyLedger(args.ledger).append(
            req_id=task["req_id"], task_id=task["task_id"], canonical_generation=task["canonical_generation"],
            event="TASK_VERIFIED", result="PASS", policy_bundle_sha256=task["policy_bundle_sha256"],
            input_binding_sha256=sha256_bytes(data), output_binding_sha256=sha256_bytes(data),
        )
    _emit(result.as_dict())
    return 0 if result.ok else 2


def _result_verify(args: argparse.Namespace) -> int:
    result_bytes = read_bytes(args.result)
    task_bytes = read_bytes(args.task)
    result_doc = load_json_bytes(result_bytes)
    result = verify_result_bytes(
        result_bytes, task_bytes, git_root=args.git_root,
        github_inspector=GitHubInspector(result_doc["repository"]) if result_doc.get("status") == "PASS" else None,
    )
    if result.ok and args.ledger:
        task = load_yaml_bytes(task_bytes)
        AppendOnlyLedger(args.ledger).append(
            req_id=task["req_id"], task_id=task["task_id"], canonical_generation=task["canonical_generation"],
            event="EXECUTION_RESULT_VERIFIED", result="PASS", policy_bundle_sha256=task["policy_bundle_sha256"],
            input_binding_sha256=sha256_bytes(task_bytes), output_binding_sha256=sha256_bytes(result_bytes),
        )
    _emit(result.as_dict())
    return 0 if result.ok else 2


def _historical_source_verify(args: argparse.Namespace) -> int:
    store = GoogleDriveStore.from_trusted_runtime(args.git_root, args.instance_root)
    result = verify_historical_source_result(
        store,
        task_file_id=args.task_file_id,
        result_file_id=args.result_file_id,
        review_package_file_id=args.review_package_file_id,
        expected_task_sha256=args.expected_task_sha256,
        expected_result_sha256=args.expected_result_sha256,
        expected_review_package_sha256=args.expected_review_package_sha256,
        expected_requirement_sha256=args.expected_requirement_sha256,
        expected_policy_sha256=args.expected_policy_sha256,
        expected_repository=args.expected_repository,
        git_root=args.git_root,
        github_inspector=GitHubInspector(args.expected_repository),
    )
    _emit(result.as_dict())
    return 0 if result.ok else 2


def _review_package(args: argparse.Namespace) -> int:
    task_bytes = read_bytes(args.task)
    result_bytes = read_bytes(args.result)
    result_doc = load_json_bytes(result_bytes)
    verification = verify_result_bytes(
        result_bytes, task_bytes, git_root=args.git_root,
        github_inspector=GitHubInspector(result_doc["repository"]) if result_doc.get("status") == "PASS" else None,
    )
    if not verification.ok:
        _emit(verification.as_dict())
        return 2
    task = load_yaml_bytes(task_bytes)
    result = load_json_bytes(result_bytes)
    package = build_review_package(task, result, task_bytes=task_bytes, result_bytes=result_bytes)
    package_bytes = canonical_json_bytes(package)
    Path(args.output).write_bytes(package_bytes)
    if args.ledger:
        AppendOnlyLedger(args.ledger).append(
            req_id=task["req_id"], task_id=task["task_id"], canonical_generation=task["canonical_generation"],
            event="REVIEW_PACKAGE_BUILT", result="PASS", policy_bundle_sha256=task["policy_bundle_sha256"],
            input_binding_sha256=sha256_bytes(result_bytes), output_binding_sha256=sha256_bytes(package_bytes),
        )
    _emit({"status": "PASS", "classification": "REVIEW_PACKAGE_BUILT", "output": str(Path(args.output)), "package": package})
    return 0


def _transition_commit(args: argparse.Namespace) -> int:
    intent_data = load_json_bytes(read_bytes(args.intent), code="TRANSITION_BINDING_MISMATCH")
    common = {
        "provider", "ledger_path", "predecessor_task_file_id", "predecessor_sha256", "predecessor_revision",
        "source_result_file_id", "source_result_sha256", "expected_requirement_sha256", "expected_policy_sha256",
        "expected_supersedes_task_id", "expected_return_gate", "expected_next_executor", "successor_path",
        "history_folder_id", "history_name", "stable_file_id",
    }
    provider_fields = {"store_root"} if intent_data.get("provider") == "directory" else {"engine_root", "instance_root"}
    required = common | provider_fields
    if set(intent_data) != required:
        raise P2AError("TRANSITION_BINDING_MISMATCH", "Transition intent fields changed", missing=sorted(required - set(intent_data)), unknown=sorted(set(intent_data) - required))
    if intent_data["provider"] == "directory":
        store = DirectoryDriveStore(intent_data["store_root"])
    elif intent_data["provider"] == "google_drive":
        store = GoogleDriveStore.from_trusted_runtime(intent_data["engine_root"], intent_data["instance_root"])
    else:
        raise P2AError("PROVIDER_UNSUPPORTED", "Transition provider is not supported", provider=intent_data["provider"])
    ledger = AppendOnlyLedger(intent_data["ledger_path"])
    outcome = transition_commit(
        store,
        ledger,
        TransitionIntent(
            predecessor_task_file_id=intent_data["predecessor_task_file_id"],
            predecessor_sha256=intent_data["predecessor_sha256"],
            predecessor_revision=intent_data["predecessor_revision"],
            source_result_file_id=intent_data["source_result_file_id"],
            source_result_sha256=intent_data["source_result_sha256"],
            expected_requirement_sha256=intent_data["expected_requirement_sha256"],
            expected_policy_sha256=intent_data["expected_policy_sha256"],
            expected_supersedes_task_id=intent_data["expected_supersedes_task_id"],
            expected_return_gate=intent_data["expected_return_gate"],
            expected_next_executor=intent_data["expected_next_executor"],
            successor_bytes=read_bytes(intent_data["successor_path"]),
            history_folder_id=intent_data["history_folder_id"],
            history_name=intent_data["history_name"],
            stable_file_id=intent_data["stable_file_id"],
        ),
    )
    _emit(outcome)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m automation.orchestrator")
    sub = root.add_subparsers(dest="command", required=True)
    task = sub.add_parser("task-verify")
    task.add_argument("--task", required=True)
    task.add_argument("--current")
    task.add_argument("--expected-task-sha256")
    task.add_argument("--expected-generation", type=int)
    task.add_argument("--expected-executor")
    task.add_argument("--expected-requirement-sha256")
    task.add_argument("--expected-policy-sha256")
    task.add_argument("--expected-base-sha")
    task.add_argument("--ledger")
    task.set_defaults(handler=_task_verify)
    result = sub.add_parser("result-verify")
    result.add_argument("--result", required=True)
    result.add_argument("--task", required=True)
    result.add_argument("--git-root")
    result.add_argument("--ledger")
    result.set_defaults(handler=_result_verify)
    historical = sub.add_parser("historical-source-verify")
    for name in (
        "task-file-id", "result-file-id", "review-package-file-id",
        "expected-task-sha256", "expected-result-sha256", "expected-review-package-sha256",
        "expected-requirement-sha256", "expected-policy-sha256", "expected-repository",
        "git-root", "instance-root",
    ):
        historical.add_argument("--" + name, required=True)
    historical.set_defaults(handler=_historical_source_verify)
    review = sub.add_parser("review-package")
    review.add_argument("--task", required=True)
    review.add_argument("--result", required=True)
    review.add_argument("--output", required=True)
    review.add_argument("--git-root")
    review.add_argument("--ledger")
    review.set_defaults(handler=_review_package)
    transition = sub.add_parser("transition-commit")
    transition.add_argument("--intent", required=True)
    transition.set_defaults(handler=_transition_commit)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except P2AError as exc:
        _emit({"status": "BLOCKED", "classification": exc.code, "errors": [exc.as_dict()]})
        return 2
    except Exception as exc:
        error = P2AError("UNCLASSIFIED_EXCEPTION", "Unhandled orchestrator exception", error=repr(exc))
        _emit({"status": "BLOCKED", "classification": error.code, "errors": [error.as_dict()]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
