from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Callable, Mapping

from .git_io import GitInspector, GitHubInspector
from .models import (
    AUTOMATION_VERSION,
    RESULT_SCHEMA,
    TASK_SCHEMA,
    P2AError,
    VerificationResult,
    fail_result,
    load_json_bytes,
    load_yaml_bytes,
    require_exact_keys,
    require_git_sha,
    require_sha256,
    require_uuid,
    sha256_bytes,
)


TASK_FIELDS = {
    "schema_version", "req_id", "canonical_generation", "task_id", "task_type", "stage",
    "issued_at_utc", "issued_by", "automation_version", "policy_bundle_sha256", "repository",
    "expected_base_branch", "expected_base_sha", "feature_branch", "allowed_paths", "must_not_change",
    "allowed_actions", "forbidden_actions", "focused_tests", "full_regression", "max_internal_steps",
    "max_internal_retries", "max_runtime_minutes", "next_executor", "return_gate", "canonical_binding",
    "authority_binding", "transition_binding",
}
CANONICAL_BINDING_FIELDS = {
    "canonical_req_root_folder_id", "canonical_current_folder_id", "canonical_history_folder_id",
    "canonical_next_task_file_id", "canonical_codex_result_file_id", "canonical_update_mode",
}
AUTHORITY_BINDING_FIELDS = {
    "requirement_revision", "requirement_sha256", "predecessor_req_id", "predecessor_terminal_generation",
    "predecessor_terminal_task_id", "predecessor_terminal_task_sha256",
}
TRANSITION_BINDING_FIELDS = {
    "source_result_required", "supersedes_task_id", "supersedes_task_sha256", "transition_from_gate",
    "transition_commit_required",
}
RESULT_FIELDS = {
    "schema_version", "req_id", "canonical_generation", "task_id", "status", "classification",
    "automation_version", "policy_bundle_sha256", "repository", "base_sha", "head_sha", "feature_branch",
    "changed_files", "focused_tests", "full_regression", "pr", "ci", "output_artifacts",
    "forbidden_actions_observed", "machine_facts", "errors", "source_task_sha256",
    "recommended_next_gate", "return_gate", "generated_at_utc",
}
EXECUTORS = {"CODEX", "CODEX_RUNNER", "LOCAL_RUNTIME", "RUNNER", "WEB_AI", "CHATGPT_WORK", "HUMAN"}


def _list_of_strings(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise P2AError("TASK_SCHEMA_INVALID", f"{field_name} must be a non-empty string list", field=field_name)
    return value


def validate_task_document(task: Mapping[str, Any]) -> None:
    require_exact_keys(task, TASK_FIELDS, code="TASK_SCHEMA_INVALID", location="task")
    if task["schema_version"] != TASK_SCHEMA:
        raise P2AError("TASK_SCHEMA_INVALID", "Unexpected task schema", observed=task["schema_version"])
    if task["automation_version"] != AUTOMATION_VERSION:
        raise P2AError("POLICY_BINDING_MISMATCH", "Unexpected automation version", observed=task["automation_version"])
    if not isinstance(task["canonical_generation"], int) or task["canonical_generation"] < 1:
        raise P2AError("TASK_SCHEMA_INVALID", "canonical_generation must be a positive integer")
    require_uuid(task["task_id"], field_name="task_id")
    require_sha256(task["policy_bundle_sha256"], field_name="policy_bundle_sha256")
    require_git_sha(task["expected_base_sha"], field_name="expected_base_sha")
    for name in ("allowed_paths", "must_not_change", "allowed_actions", "forbidden_actions", "focused_tests"):
        _list_of_strings(task[name], name)
    if task["next_executor"] not in EXECUTORS:
        raise P2AError("EXECUTOR_MISMATCH", "Unknown next_executor", observed=task["next_executor"])
    if task["canonical_binding"].get("canonical_update_mode") != "IN_PLACE_SAME_FILE_ID":
        raise P2AError("TASK_SCHEMA_INVALID", "Only same-file-ID canonical updates are supported")
    require_exact_keys(task["canonical_binding"], CANONICAL_BINDING_FIELDS, code="TASK_SCHEMA_INVALID", location="canonical_binding")
    require_exact_keys(task["authority_binding"], AUTHORITY_BINDING_FIELDS, code="TASK_SCHEMA_INVALID", location="authority_binding")
    require_exact_keys(task["transition_binding"], TRANSITION_BINDING_FIELDS, code="TASK_SCHEMA_INVALID", location="transition_binding")
    require_sha256(task["authority_binding"]["requirement_sha256"], field_name="authority_binding.requirement_sha256")
    require_uuid(task["authority_binding"]["predecessor_terminal_task_id"], field_name="authority_binding.predecessor_terminal_task_id")
    require_sha256(task["authority_binding"]["predecessor_terminal_task_sha256"], field_name="authority_binding.predecessor_terminal_task_sha256")
    require_uuid(task["transition_binding"]["supersedes_task_id"], field_name="transition_binding.supersedes_task_id")
    require_sha256(task["transition_binding"]["supersedes_task_sha256"], field_name="transition_binding.supersedes_task_sha256")
    if task["transition_binding"]["transition_commit_required"] is not True:
        raise P2AError("TASK_SCHEMA_INVALID", "transition_commit_required must be true")


def verify_task_bytes(
    data: bytes,
    *,
    expected_task_sha256: str | None = None,
    expected_generation: int | None = None,
    expected_executor: str | None = None,
    expected_requirement_sha256: str | None = None,
    expected_policy_sha256: str | None = None,
    expected_base_sha: str | None = None,
    current_pointer_bytes: bytes | None = None,
) -> VerificationResult:
    facts: dict[str, Any] = {"task_sha256": sha256_bytes(data), "task_bytes": len(data)}
    try:
        task = load_yaml_bytes(data)
        validate_task_document(task)
        checks = {
            "task_sha256": (facts["task_sha256"], expected_task_sha256, "STALE_TASK"),
            "canonical_generation": (task["canonical_generation"], expected_generation, "STALE_TASK"),
            "next_executor": (task["next_executor"], expected_executor, "EXECUTOR_MISMATCH"),
            "requirement_sha256": (task["authority_binding"]["requirement_sha256"], expected_requirement_sha256, "REQUIREMENT_BINDING_MISMATCH"),
            "policy_bundle_sha256": (task["policy_bundle_sha256"], expected_policy_sha256, "POLICY_BINDING_MISMATCH"),
            "expected_base_sha": (task["expected_base_sha"], expected_base_sha, "BASELINE_DRIFT"),
        }
        for name, (actual, expected, code) in checks.items():
            if expected is not None and actual != expected:
                raise P2AError(code, f"{name} does not match frozen authority", expected=expected, observed=actual)
        if current_pointer_bytes is not None and current_pointer_bytes != data:
            raise P2AError("OLD_STABLE_POINTER", "Task bytes are not the canonical stable pointer")
        facts.update({"req_id": task["req_id"], "canonical_generation": task["canonical_generation"], "task_id": task["task_id"], "next_executor": task["next_executor"], "schema": "PASS"})
        return VerificationResult("PASS", "TASK_VERIFIED", facts)
    except P2AError as exc:
        return fail_result(exc, facts=facts)
    except Exception as exc:  # pragma: no cover - deliberately defensive
        return fail_result(P2AError("UNCLASSIFIED_EXCEPTION", "Unhandled task verification error", error=repr(exc)), facts=facts)


def path_allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def validate_result_document(result: Mapping[str, Any]) -> None:
    require_exact_keys(result, RESULT_FIELDS, code="RESULT_SCHEMA_INVALID", location="result")
    if result["schema_version"] != RESULT_SCHEMA:
        raise P2AError("RESULT_SCHEMA_INVALID", "Unexpected result schema", observed=result["schema_version"])
    if result["status"] not in {"PASS", "BLOCKED", "FAIL"}:
        raise P2AError("RESULT_SCHEMA_INVALID", "Unknown result status", observed=result["status"])
    require_uuid(result["task_id"], field_name="task_id", code="RESULT_SCHEMA_INVALID")
    for name in ("policy_bundle_sha256", "source_task_sha256"):
        require_sha256(result[name], field_name=name, code="RESULT_SCHEMA_INVALID")
    for name in ("base_sha", "head_sha"):
        require_git_sha(result[name], field_name=name, code="RESULT_SCHEMA_INVALID")
    if not isinstance(result["changed_files"], list) or not all(isinstance(item, str) for item in result["changed_files"]):
        raise P2AError("RESULT_SCHEMA_INVALID", "changed_files must be a string list")


def verify_result_bytes(
    result_data: bytes,
    task_data: bytes,
    *,
    git_root: str | Path | None = None,
    github_inspector: GitHubInspector | Any | None = None,
) -> VerificationResult:
    facts: dict[str, Any] = {"result_sha256": sha256_bytes(result_data), "result_bytes": len(result_data)}
    try:
        result = load_json_bytes(result_data)
        task = load_yaml_bytes(task_data)
        validate_result_document(result)
        validate_task_document(task)
        bindings = {
            "req_id": task["req_id"], "canonical_generation": task["canonical_generation"], "task_id": task["task_id"],
            "automation_version": task["automation_version"], "policy_bundle_sha256": task["policy_bundle_sha256"],
            "repository": task["repository"], "base_sha": task["expected_base_sha"], "feature_branch": task["feature_branch"],
            "source_task_sha256": sha256_bytes(task_data), "return_gate": task["return_gate"],
        }
        for name, expected in bindings.items():
            if result[name] != expected:
                code = "WRONG_RESULT_HASH" if name == "source_task_sha256" else "RESULT_BINDING_MISMATCH"
                raise P2AError(code, f"Result {name} does not bind the task", expected=expected, observed=result[name])
        unexpected = sorted(path for path in result["changed_files"] if not path_allowed(path, task["allowed_paths"]))
        if unexpected:
            raise P2AError("SCOPE_VIOLATION", "Result contains unexpected changed files", unexpected=unexpected)
        if result["status"] == "PASS":
            if result["forbidden_actions_observed"] != []:
                raise P2AError("FORBIDDEN_ACTION_OBSERVED", "PASS result recorded a forbidden action")
            for field in ("focused_tests", "full_regression"):
                if not isinstance(result[field], dict) or result[field].get("status") != "PASS":
                    raise P2AError("TEST_EVIDENCE_INCOMPLETE", f"{field} must be PASS")
            if not isinstance(result["pr"], dict) or not isinstance(result["ci"], dict):
                raise P2AError("CI_EVIDENCE_INCOMPLETE", "PASS result requires PR and CI evidence")
            if result["pr"].get("head_sha") != result["head_sha"] or result["ci"].get("head_sha") != result["head_sha"]:
                raise P2AError("PR_CI_HEAD_MISMATCH", "PR, CI and result heads must be identical")
            if result["ci"].get("workflow_name") != "Offline tests" or result["ci"].get("conclusion") != "success":
                raise P2AError("CI_NOT_GREEN", "Required Offline tests is not green")
            if github_inspector is None:
                raise P2AError("GITHUB_FACTS_UNAVAILABLE", "PASS result requires independent GitHub Code Truth")
            github = github_inspector.collect(result["pr"]["number"], result["ci"]["workflow_name"], result["head_sha"])
            observed_pr = github["pr"]
            expected_pr = {
                "number": result["pr"]["number"], "url": result["pr"]["url"], "state": "OPEN",
                "baseRefOid": result["base_sha"], "headRefOid": result["head_sha"],
            }
            for name, expected in expected_pr.items():
                if observed_pr.get(name) != expected:
                    raise P2AError("PR_FACTS_MISMATCH", "Result PR fact differs from GitHub Code Truth", field=name, expected=expected, observed=observed_pr.get(name))
            successful_run_ids = {
                item["id"] for item in github["runs"]
                if item["head_sha"] == result["head_sha"] and item["status"] == "completed" and item["conclusion"] == "success"
            }
            claimed_run_ids = {item["id"] for item in result["ci"].get("runs", [])}
            if claimed_run_ids and not claimed_run_ids.issubset(successful_run_ids):
                raise P2AError("CI_FACTS_MISMATCH", "Result claims a CI run not verified by GitHub", claimed=sorted(claimed_run_ids), verified=sorted(successful_run_ids))
        if git_root is not None:
            git = GitInspector(git_root)
            if git.head_sha() != result["head_sha"]:
                raise P2AError("GIT_HEAD_MISMATCH", "Local Git head does not match result head")
            if not git.is_ancestor(result["base_sha"], result["head_sha"]):
                raise P2AError("GIT_ANCESTRY_MISMATCH", "Frozen base is not an ancestor of result head")
            actual_changed = list(git.changed_files(result["base_sha"]))
            if actual_changed != sorted(result["changed_files"]):
                raise P2AError("CHANGED_FILES_MISMATCH", "Result changed files do not match Git", expected=actual_changed, observed=result["changed_files"])
        facts.update({"req_id": result["req_id"], "task_id": result["task_id"], "status": result["status"], "changed_files": sorted(result["changed_files"])})
        return VerificationResult("PASS", "EXECUTION_RESULT_VERIFIED", facts)
    except P2AError as exc:
        return fail_result(exc, facts=facts)
    except Exception as exc:
        return fail_result(P2AError("UNCLASSIFIED_EXCEPTION", "Unhandled result verification error", error=repr(exc)), facts=facts)


def guarded_verify(operation: Callable[[], VerificationResult]) -> VerificationResult:
    try:
        return operation()
    except P2AError as exc:
        return fail_result(exc)
    except Exception as exc:
        return fail_result(P2AError("UNCLASSIFIED_EXCEPTION", "Unhandled verifier exception", error=repr(exc)))
