from __future__ import annotations

import fnmatch
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .git_io import GitInspector, GitHubInspector
from .models import (
    AUTOMATION_VERSION,
    RETROSPECTIVE_PROOF_SCHEMA,
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


POST_MERGE_TASK_TYPE = "CODEX_READ_ONLY_POST_MERGE_RECONCILIATION"
HUMAN_MERGE_TASK_TYPE = "HUMAN_MERGE_EXECUTION"
DESCENDANT_MODE = "EXACT_P2A_PROCESS_CHAIN"
PROCESS_DESCENDANT_PATHS = {
    ".github/workflows/offline-tests.yml", "automation/verify.py",
    "tests/automation/test_negative_cases.py", "tests/automation/test_verify.py",
}
EXTERNAL_CONTROL_PLANE_PATHS = {
    "automation/models.py", "automation/verify.py",
}
RETROSPECTIVE_CAPABILITIES = {
    "VERIFIED_PREDECESSOR_REUSE_MERGED_PR": frozenset({
        "HUMAN_MERGE_TEST_EVIDENCE_CONTRACT", "HUMAN_MERGE_PR_STATE_CONTRACT",
    }),
}


def _human_frozen_review(task: Mapping[str, Any]) -> tuple[int, str, int]:
    """Read the exact PR head and green run named in the frozen Human task."""
    actions = task["allowed_actions"]
    head = [m.groups() for action in actions if (m := re.search(
        r"\brequire PR (\d+) head SHA exactly ([0-9a-f]{40})\b", action))]
    base = [m.groups() for action in actions if (m := re.search(
        r"\brequire PR (\d+) base SHA ([0-9a-f]{40})\b", action))]
    run = [m.groups() for action in actions if (m := re.search(
        r"\brequire Offline tests run (\d+) event pull_request status completed conclusion success and head SHA ([0-9a-f]{40})\b", action))]
    if len(head) != 1 or len(base) != 1 or len(run) != 1:
        raise P2AError("HUMAN_MERGE_FROZEN_PROVENANCE_MISSING", "Frozen Human task lacks exact reviewed PR and CI facts")
    if head[0][0] != base[0][0] or base[0][1] != task["expected_base_sha"] or run[0][1] != head[0][1]:
        raise P2AError("HUMAN_MERGE_FROZEN_PROVENANCE_MISMATCH", "Frozen reviewed base/head/CI disagree")
    return int(head[0][0]), head[0][1], int(run[0][0])


def _human_evidence_store(git_root: str | Path | None) -> Any:
    instance_root = os.environ.get("CBA_KB_INSTANCE_ROOT")
    if git_root is None or not instance_root:
        raise P2AError("PREDECESSOR_EVIDENCE_UNAVAILABLE", "Trusted Git root and CBA_KB_INSTANCE_ROOT are required")
    from .drive_io import GoogleDriveStore
    return GoogleDriveStore.from_trusted_runtime(git_root, instance_root)


def _verify_human_predecessor(
    result: Mapping[str, Any], task: Mapping[str, Any], store: Any,
    reviewed_head: str, reviewed_run_id: int,
) -> None:
    facts = result["machine_facts"]
    folder = task["canonical_binding"]["canonical_history_folder_id"]
    for name in ("predecessor_task_file_id", "predecessor_result_file_id", "predecessor_review_package_file_id"):
        if not isinstance(facts.get(name), str) or not facts[name]:
            raise P2AError("PREDECESSOR_EVIDENCE_MISSING", f"{name} is required")
    require_sha256(facts.get("predecessor_result_sha256"), field_name="predecessor_result_sha256", code="PREDECESSOR_RESULT_HASH_MISMATCH")
    source_task = store.read(facts["predecessor_task_file_id"])
    source_result = store.read(facts["predecessor_result_file_id"])
    source_package = store.read(facts["predecessor_review_package_file_id"])
    if any(item.folder_id != folder for item in (source_task, source_result, source_package)):
        raise P2AError("PREDECESSOR_EVIDENCE_MISMATCH", "Predecessor evidence must be in frozen history folder")
    if sha256_bytes(source_task.content) != task["authority_binding"]["predecessor_terminal_task_sha256"]:
        raise P2AError("PREDECESSOR_TASK_HASH_MISMATCH", "Predecessor task differs from frozen task provenance")
    if sha256_bytes(source_result.content) != facts["predecessor_result_sha256"]:
        raise P2AError("PREDECESSOR_RESULT_HASH_MISMATCH", "Predecessor result bytes differ from bound SHA")
    previous_task = load_yaml_bytes(source_task.content)
    previous_result = load_json_bytes(source_result.content)
    previous_package = load_json_bytes(source_package.content)
    validate_task_document(previous_task)
    validate_result_document(previous_result)
    if (previous_task["task_id"] != task["authority_binding"]["predecessor_terminal_task_id"]
            or previous_task["canonical_generation"] != task["canonical_generation"] - 1
            or previous_task["task_type"] in {HUMAN_MERGE_TASK_TYPE, POST_MERGE_TASK_TYPE}
            or previous_task["policy_bundle_sha256"] != task["policy_bundle_sha256"]
            or previous_task["authority_binding"]["requirement_sha256"] != task["authority_binding"]["requirement_sha256"]
            or previous_result["task_id"] != previous_task["task_id"]
            or previous_result["source_task_sha256"] != sha256_bytes(source_task.content)
            or previous_result["policy_bundle_sha256"] != task["policy_bundle_sha256"]
            or previous_result["repository"] != task["repository"]
            or previous_result["status"] != "PASS"):
        raise P2AError("PREDECESSOR_NOT_VERIFIED_PASS", "Predecessor task/result binding or PASS status is invalid")
    control = previous_package.get("CONTROL") or {}
    verified = previous_package.get("VERIFIED_MACHINE_FACTS") or {}
    if (control.get("task_id") != previous_task["task_id"]
            or control.get("policy_bundle_sha256") != task["policy_bundle_sha256"]
            or control.get("requirement_sha256") != task["authority_binding"]["requirement_sha256"]
            or verified.get("task_sha256") != sha256_bytes(source_task.content)
            or verified.get("result_sha256") != sha256_bytes(source_result.content)
            or verified.get("head_sha") != reviewed_head
            or verified.get("pr") != previous_result["pr"]
            or verified.get("ci") != previous_result["ci"]):
        raise P2AError("PREDECESSOR_EVIDENCE_MISMATCH", "Verified predecessor review package does not bind exact task/result")
    previous_pr, previous_ci = previous_result["pr"], previous_result["ci"]
    if (previous_pr.get("head_sha") != reviewed_head
            or previous_pr.get("base_sha") != task["expected_base_sha"]
            or previous_pr.get("number") != result["pr"]["number"]):
        raise P2AError("PREDECESSOR_REVIEWED_HEAD_MISMATCH", "Predecessor PR differs from frozen reviewed PR")
    if (previous_ci.get("workflow_name") != "Offline tests"
            or previous_ci.get("head_sha") != reviewed_head
            or previous_ci.get("conclusion") != "success"
            or reviewed_run_id not in {run.get("id") for run in previous_ci.get("runs", [])}):
        raise P2AError("PREDECESSOR_CI_MISMATCH", "Predecessor exact-head PR CI is not green and bound")
    if previous_package.get("generated_at_utc", "") >= result["pr"].get("merged_at", ""):
        raise P2AError("PREDECESSOR_EVIDENCE_MISMATCH", "Predecessor review package was not recorded before merge")


def _verify_human_merge_result(
    result: Mapping[str, Any], task: Mapping[str, Any], github_inspector: Any,
    git_root: str | Path | None, github: Mapping[str, Any], store: Any,
) -> None:
    pr, ci, facts = result["pr"], result["ci"], result["machine_facts"]
    number, reviewed_head, reviewed_run_id = _human_frozen_review(task)
    if task["expected_base_branch"] != "main" or pr.get("number") != number:
        raise P2AError("HUMAN_MERGE_FROZEN_PROVENANCE_MISMATCH", "Merged PR differs from frozen Human task")
    if not isinstance(facts, dict) or facts.get("requirement_sha256") != task["authority_binding"]["requirement_sha256"]:
        raise P2AError("REQUIREMENT_BINDING_MISMATCH", "Human merge result must bind frozen Requirement")
    if facts.get("evidence_mode") != "VERIFIED_PREDECESSOR_REUSE":
        raise P2AError("TEST_EVIDENCE_INCOMPLETE", "Human merge must use explicit verified predecessor evidence")
    for name in ("focused_tests", "full_regression"):
        evidence = result[name]
        if (not isinstance(evidence, dict) or evidence.get("status") != "NOT_RERUN_DURING_HUMAN_MERGE"
                or evidence.get("evidence_mode") != "VERIFIED_PREDECESSOR_REUSE"
                or evidence.get("predecessor_result_sha256") != facts.get("predecessor_result_sha256")):
            raise P2AError("TEST_EVIDENCE_INCOMPLETE", f"{name} must truthfully bind the verified predecessor")
    _verify_human_predecessor(result, task, store, reviewed_head, reviewed_run_id)
    if (pr.get("state") != "MERGED" or pr.get("head_sha") != reviewed_head
            or pr.get("base_sha") != task["expected_base_sha"]
            or pr.get("merge_commit_sha") != result["head_sha"]):
        raise P2AError("MERGE_COMMIT_MISMATCH", "Declared merged PR differs from frozen reviewed head/base or result head")
    observed = github["pr"]
    expected = {"number": number, "url": pr["url"], "state": "MERGED",
                "headRefOid": reviewed_head, "baseRefOid": task["expected_base_sha"]}
    for field, value in expected.items():
        if observed.get(field) != value:
            raise P2AError("PR_FACTS_MISMATCH", "Live merged PR differs from frozen review", field=field)
    merge = github_inspector._json("pr", "view", str(number), "--repo", task["repository"], "--json", "mergedAt,mergeCommit")
    if (not merge.get("mergedAt") or merge["mergedAt"] != pr.get("merged_at")
            or (merge.get("mergeCommit") or {}).get("oid") != result["head_sha"]):
        raise P2AError("MERGE_COMMIT_MISMATCH", "Live merge commit or merge time differs")
    main_ref = github_inspector._json("api", f"repos/{task['repository']}/git/ref/heads/main")
    if (main_ref.get("object") or {}).get("sha") != result["head_sha"]:
        raise P2AError("FINAL_MAIN_MISMATCH", "Live main advanced or differs from merge commit")
    reviewed_runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={reviewed_head}&per_page=100").get("workflow_runs", [])
    if not any(run.get("id") == reviewed_run_id and run.get("name") == "Offline tests"
               and run.get("head_sha") == reviewed_head and run.get("event") == "pull_request"
               and run.get("status") == "completed" and run.get("conclusion") == "success" for run in reviewed_runs):
        raise P2AError("PREDECESSOR_CI_MISMATCH", "Frozen reviewed PR run is not independently green")
    if ci.get("head_sha") != result["head_sha"]:
        raise P2AError("PR_CI_HEAD_MISMATCH", "Final-main CI head differs from merge commit")
    successful_main = {run["id"] for run in github["runs"]
                       if run["head_sha"] == result["head_sha"] and run["event"] == "push"
                       and run["status"] == "completed" and run["conclusion"] == "success"}
    claimed = {run["id"] for run in ci.get("runs", [])}
    if not claimed or not claimed.issubset(successful_main):
        raise P2AError("CI_FACTS_MISMATCH", "A successful hosted exact-main push run is required")
    if git_root is None:
        raise P2AError("GIT_FACTS_UNAVAILABLE", "Human merge requires final-main Git history")
    git = GitInspector(git_root)
    parents = git._run("rev-list", "--parents", "-n", "1", result["head_sha"]).split()
    if parents != [result["head_sha"], task["expected_base_sha"], reviewed_head]:
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Merge parents do not bind frozen base and reviewed head")


def verify_historical_source_result(
    store: Any, *, task_file_id: str, result_file_id: str, review_package_file_id: str,
    expected_task_sha256: str, expected_result_sha256: str, expected_review_package_sha256: str,
    expected_requirement_sha256: str, expected_policy_sha256: str, expected_repository: str,
    git_root: str | Path, github_inspector: Any,
) -> VerificationResult:
    """Recheck immutable Human merge source evidence after authorized main descendants.

    This is a separate transition-source gate. Current result verification retains its
    exact-live-main contract and never calls this path.
    """
    facts: dict[str, Any] = {"task_file_id": task_file_id, "result_file_id": result_file_id,
                             "review_package_file_id": review_package_file_id}
    try:
        for name, value in (
            ("expected_task_sha256", expected_task_sha256),
            ("expected_result_sha256", expected_result_sha256),
            ("expected_review_package_sha256", expected_review_package_sha256),
            ("expected_requirement_sha256", expected_requirement_sha256),
            ("expected_policy_sha256", expected_policy_sha256),
        ):
            require_sha256(value, field_name=name, code="HISTORICAL_SOURCE_BINDING_MISMATCH")
        if any(not isinstance(value, str) or not value for value in
               (task_file_id, result_file_id, review_package_file_id, expected_repository)):
            raise P2AError("HISTORICAL_SOURCE_BINDING_MISMATCH", "Immutable IDs and repository are required")
        task_file, result_file, package_file = (store.read(file_id) for file_id in
                                                (task_file_id, result_file_id, review_package_file_id))
        if (sha256_bytes(task_file.content) != expected_task_sha256
                or sha256_bytes(result_file.content) != expected_result_sha256
                or sha256_bytes(package_file.content) != expected_review_package_sha256):
            raise P2AError("HISTORICAL_SOURCE_HASH_MISMATCH", "Historical source bytes differ from frozen SHA")
        task = load_yaml_bytes(task_file.content)
        result = load_json_bytes(result_file.content)
        package = load_json_bytes(package_file.content)
        validate_task_document(task)
        validate_result_document(result)
        history_folder = task["canonical_binding"]["canonical_history_folder_id"]
        if any(item.folder_id != history_folder for item in (task_file, result_file, package_file)):
            raise P2AError("HISTORICAL_SOURCE_BINDING_MISMATCH", "Source evidence is outside immutable history")
        if task["task_type"] != HUMAN_MERGE_TASK_TYPE:
            raise P2AError("HISTORICAL_SOURCE_UNSUPPORTED", "Only frozen Human merge PASS sources are supported")
        if result["status"] != "PASS" or result["forbidden_actions_observed"]:
            raise P2AError("HISTORICAL_SOURCE_NOT_PASS", "Historical source must be a clean PASS result")
        bindings = {
            "req_id": task["req_id"], "canonical_generation": task["canonical_generation"],
            "task_id": task["task_id"], "automation_version": task["automation_version"],
            "policy_bundle_sha256": expected_policy_sha256, "repository": expected_repository,
            "base_sha": task["expected_base_sha"], "feature_branch": task["feature_branch"],
            "return_gate": task["return_gate"], "source_task_sha256": expected_task_sha256,
        }
        if (task["policy_bundle_sha256"] != expected_policy_sha256
                or task["authority_binding"]["requirement_sha256"] != expected_requirement_sha256
                or task["repository"] != expected_repository
                or any(result.get(key) != expected for key, expected in bindings.items())):
            raise P2AError("HISTORICAL_SOURCE_BINDING_MISMATCH", "Task/result/Requirement/policy/repository binding differs")
        control = package.get("CONTROL") or {}
        verified = package.get("VERIFIED_MACHINE_FACTS") or {}
        if (package.get("review_package_version") != "cba-kb.p2a-review-package.v1"
                or control.get("req_id") != task["req_id"]
                or control.get("task_id") != task["task_id"]
                or control.get("canonical_generation") != task["canonical_generation"]
                or control.get("policy_bundle_sha256") != expected_policy_sha256
                or control.get("requirement_sha256") != expected_requirement_sha256
                or control.get("gate") != task["return_gate"]
                or verified.get("task_sha256") != expected_task_sha256
                or verified.get("result_sha256") != expected_result_sha256
                or any(verified.get(key) != result[key] for key in (
                    "base_sha", "head_sha", "changed_files", "focused_tests", "full_regression",
                    "pr", "ci", "output_artifacts", "forbidden_actions_observed",
                ))):
            raise P2AError("HISTORICAL_SOURCE_PACKAGE_MISMATCH", "Immutable review package does not bind source")
        if result["changed_files"] != []:
            raise P2AError("HISTORICAL_SOURCE_SCOPE_VIOLATION", "Human merge result cannot declare repository edits")
        machine = result["machine_facts"]
        if (not isinstance(machine, dict)
                or machine.get("requirement_sha256") != expected_requirement_sha256
                or machine.get("evidence_mode") != "VERIFIED_PREDECESSOR_REUSE"
                or machine.get("identity_semantic_delta") != "ZERO"
                or any(machine.get(name) != 0 for name in (
                    "identity_authority_change", "identity_registry_mutation", "master_mutation",
                    "player_uid_mutation", "new_identity_decisions", "new_same_decisions",
                    "new_not_same_decisions", "machine_final_uid_decisions", "production_mutation",
                    "publish", "restore",
                ))
                or any(value != 0 for key, value in machine.items()
                       if key == "product_code_delta" or key.endswith("_product_code_delta"))):
            raise P2AError("HISTORICAL_SOURCE_MUTATION", "Identity, product or Production mutation is not eligible")
        for name in ("focused_tests", "full_regression"):
            evidence = result[name]
            if (not isinstance(evidence, dict)
                    or evidence.get("status") != "NOT_RERUN_DURING_HUMAN_MERGE"
                    or evidence.get("evidence_mode") != "VERIFIED_PREDECESSOR_REUSE"
                    or evidence.get("predecessor_result_sha256") != machine.get("predecessor_result_sha256")):
                raise P2AError("HISTORICAL_SOURCE_TEST_EVIDENCE_MISMATCH", "Test reuse lacks exact predecessor binding")
        number, reviewed_head, reviewed_run = _human_frozen_review(task)
        _verify_human_predecessor(result, task, store, reviewed_head, reviewed_run)
        pr, ci = result["pr"], result["ci"]
        if (pr.get("number") != number or pr.get("state") != "MERGED"
                or pr.get("head_sha") != reviewed_head
                or pr.get("base_sha") != task["expected_base_sha"]
                or pr.get("merge_commit_sha") != result["head_sha"]
                or not pr.get("merged_at")):
            raise P2AError("HISTORICAL_SOURCE_PR_MISMATCH", "Historical PR differs from frozen Human task")
        observed = github_inspector._json(
            "pr", "view", str(number), "--repo", expected_repository,
            "--json", "number,url,state,baseRefOid,headRefOid,mergedAt,mergeCommit",
        )
        if (observed.get("number") != number or observed.get("url") != pr.get("url")
                or observed.get("state") != "MERGED"
                or observed.get("baseRefOid") != pr["base_sha"]
                or observed.get("headRefOid") != reviewed_head
                or observed.get("mergedAt") != pr["merged_at"]
                or (observed.get("mergeCommit") or {}).get("oid") != result["head_sha"]):
            raise P2AError("HISTORICAL_SOURCE_PR_MISMATCH", "Historical PR differs from GitHub Code Truth")
        reviewed_runs = github_inspector._json(
            "api", f"repos/{expected_repository}/actions/runs?head_sha={reviewed_head}&per_page=100",
        ).get("workflow_runs", [])
        if not any(run.get("id") == reviewed_run and run.get("name") == "Offline tests"
                   and run.get("event") == "pull_request" and run.get("head_sha") == reviewed_head
                   and run.get("status") == "completed" and run.get("conclusion") == "success"
                   for run in reviewed_runs):
            raise P2AError("HISTORICAL_SOURCE_CI_MISMATCH", "Frozen reviewed-head CI is not green")
        if (ci.get("workflow_name") != "Offline tests" or ci.get("event") != "push"
                or ci.get("head_branch") != "main" or ci.get("head_sha") != result["head_sha"]
                or ci.get("status") != "completed" or ci.get("conclusion") != "success"):
            raise P2AError("HISTORICAL_SOURCE_CI_MISMATCH", "Historical exact-main CI facts differ")
        claimed_ids = {run.get("id") for run in ci.get("runs", [])}
        historical_runs = github_inspector._json(
            "api", f"repos/{expected_repository}/actions/runs?head_sha={result['head_sha']}&per_page=100",
        ).get("workflow_runs", [])
        green_ids = {run.get("id") for run in historical_runs if run.get("name") == "Offline tests"
                     and run.get("event") == "push" and run.get("head_branch") == "main"
                     and run.get("head_sha") == result["head_sha"]
                     and run.get("status") == "completed" and run.get("conclusion") == "success"}
        if not claimed_ids or not claimed_ids.issubset(green_ids):
            raise P2AError("HISTORICAL_SOURCE_CI_MISMATCH", "Historical exact-main push CI is not independently green")
        live_main = github_inspector._json("api", f"repos/{expected_repository}/git/ref/heads/main")
        current_main_sha = (live_main.get("object") or {}).get("sha")
        require_git_sha(current_main_sha, field_name="live_main.sha", code="HISTORICAL_SOURCE_MAIN_MISMATCH")
        git = GitInspector(git_root)
        if git.head_sha() != current_main_sha:
            raise P2AError("HISTORICAL_SOURCE_MAIN_MISMATCH", "Local Git checkout differs from live main")
        if (not git.is_ancestor(result["head_sha"], current_main_sha)
                or git._run("rev-list", "--parents", "-n", "1", result["head_sha"]).split()
                != [result["head_sha"], task["expected_base_sha"], reviewed_head]):
            raise P2AError("GIT_ANCESTRY_MISMATCH", "Historical merge is not in current main's exact ancestry")
        actual_delta = sorted(git._run("diff", "--name-only", task["expected_base_sha"], result["head_sha"]).splitlines())
        if actual_delta != sorted(machine.get("pr_changed_files", [])):
            raise P2AError("HISTORICAL_SOURCE_SCOPE_VIOLATION", "Historical merge files differ from reviewed PR scope")
        facts.update({"task_sha256": expected_task_sha256, "result_sha256": expected_result_sha256,
                      "review_package_sha256": expected_review_package_sha256,
                      "historical_head_sha": result["head_sha"], "current_main_sha": current_main_sha,
                      "historical_exact_main_ci_run_ids": sorted(claimed_ids), "task_id": task["task_id"]})
        return VerificationResult("PASS", "HISTORICAL_SOURCE_RESULT_VERIFIED", facts)
    except P2AError as exc:
        return fail_result(exc, facts=facts)
    except Exception as exc:  # pragma: no cover - deliberately defensive
        return fail_result(P2AError("UNCLASSIFIED_EXCEPTION", "Historical source verification failed", error=repr(exc)), facts=facts)



def _verify_post_merge_result(
    result: Mapping[str, Any], task: Mapping[str, Any], github_inspector: Any,
    git_root: str | Path | None, github: Mapping[str, Any],
) -> None:
    """Verify a merged PR against its reviewed head and the live exact-main run."""
    pr, ci = result["pr"], result["ci"]
    if task["feature_branch"] != "main" or task["expected_base_branch"] != "main":
        raise P2AError("POST_MERGE_TASK_MISMATCH", "Post-merge task must bind main")
    if result["head_sha"] != result["base_sha"] or result["head_sha"] != task["expected_base_sha"]:
        raise P2AError("FINAL_MAIN_MISMATCH", "Final main does not match the frozen task baseline")
    if not isinstance(result["machine_facts"], dict) or result["machine_facts"].get("requirement_sha256") != task["authority_binding"]["requirement_sha256"]:
        raise P2AError("REQUIREMENT_BINDING_MISMATCH", "Post-merge result must bind the frozen Requirement")
    for name in ("head_sha", "base_sha", "merge_commit_sha", "reviewed_head_sha"):
        require_git_sha(pr.get(name), field_name=f"pr.{name}", code="POST_MERGE_PROVENANCE_MISSING")
    reviewed_run_id = pr.get("reviewed_ci_run_id")
    if not isinstance(reviewed_run_id, int) or isinstance(reviewed_run_id, bool) or reviewed_run_id <= 0:
        raise P2AError("POST_MERGE_PROVENANCE_MISSING", "Reviewed pre-merge head requires a CI run ID")
    frozen_reviewed_heads = {
        match.group(1) for action in task["allowed_actions"]
        if (match := re.search(r"\breviewed (?:product )?head ([0-9a-f]{40})\b", action))
    }
    if frozen_reviewed_heads != {pr["reviewed_head_sha"]}:
        raise P2AError("POST_MERGE_PROVENANCE_MISSING", "Reviewed head is not anchored in the frozen task")
    if pr.get("state") != "MERGED" or pr["merge_commit_sha"] != result["head_sha"]:
        raise P2AError("MERGE_COMMIT_MISMATCH", "Declared merged PR does not bind final main")
    if ci["head_sha"] != result["head_sha"]:
        raise P2AError("PR_CI_HEAD_MISMATCH", "Exact-main CI must run on final main")
    observed_pr = github["pr"]
    expected_pr = {
        "number": pr["number"], "url": pr["url"], "state": "MERGED",
        "headRefOid": pr["head_sha"], "baseRefOid": pr["base_sha"],
    }
    for name, expected in expected_pr.items():
        if observed_pr.get(name) != expected:
            raise P2AError("PR_FACTS_MISMATCH", "Merged PR differs from GitHub Code Truth", field=name, expected=expected, observed=observed_pr.get(name))
    merge_facts = github_inspector._json(
        "pr", "view", str(pr["number"]), "--repo", task["repository"], "--json", "mergedAt,mergeCommit",
    )
    if not merge_facts.get("mergedAt") or (merge_facts.get("mergeCommit") or {}).get("oid") != result["head_sha"]:
        raise P2AError("MERGE_COMMIT_MISMATCH", "GitHub merged PR commit does not equal final main")
    main_ref = github_inspector._json("api", f"repos/{task['repository']}/git/ref/heads/main")
    if (main_ref.get("object") or {}).get("sha") != result["head_sha"]:
        raise P2AError("FINAL_MAIN_MISMATCH", "Live main advanced or differs from the declared reconciliation SHA")
    successful_main_ids = {
        run["id"] for run in github["runs"]
        if run["head_sha"] == result["head_sha"] and run["status"] == "completed"
        and run["conclusion"] == "success" and run["event"] == "push"
    }
    claimed_main_ids = {run["id"] for run in ci.get("runs", [])}
    if not claimed_main_ids or not claimed_main_ids.issubset(successful_main_ids):
        raise P2AError("CI_FACTS_MISMATCH", "A claimed successful hosted exact-main push run is required")
    reviewed_runs = github_inspector._json(
        "api", f"repos/{task['repository']}/actions/runs?head_sha={pr['reviewed_head_sha']}&per_page=100",
    ).get("workflow_runs", [])
    if not any(
        run.get("id") == reviewed_run_id and run.get("name") == "Offline tests"
        and run.get("head_sha") == pr["reviewed_head_sha"]
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("event") == "pull_request" for run in reviewed_runs
    ):
        raise P2AError("POST_MERGE_PROVENANCE_MISSING", "Reviewed pre-merge head has no verified successful PR run")
    if git_root is None:
        raise P2AError("GIT_FACTS_UNAVAILABLE", "Post-merge verification requires exact-main Git history")
    git = GitInspector(git_root)
    if not git.is_ancestor(pr["reviewed_head_sha"], pr["head_sha"]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Reviewed head is not an ancestor of the merged PR head")
    parents = git._run("rev-list", "--parents", "-n", "1", result["head_sha"]).split()
    if parents != [result["head_sha"], pr["base_sha"], pr["head_sha"]]:
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Merge parents do not bind the reviewed PR provenance")


def _descendant_frozen_product(task: Mapping[str, Any]) -> tuple[int, str, str, str, int]:
    matches = [m.groups() for action in task["allowed_actions"] if (m := re.search(
        r"\bhistorical product PR (\d+) base ([0-9a-f]{40}) reviewed head ([0-9a-f]{40}) merge commit ([0-9a-f]{40})\b",
        action,
    ))]
    runs = [m.group(1) for action in task["allowed_actions"] if (m := re.search(
        r"\bhistorical reviewed CI run (\d+)\b", action,
    ))]
    if len(matches) != 1 or len(runs) != 1 or not any("descendant-aware" in action for action in task["allowed_actions"]):
        raise P2AError("DESCENDANT_AUTHORITY_MISSING", "Frozen task lacks exact historical PR, CI, or descendant mode authority")
    number, base, reviewed, merge = matches[0]
    return int(number), base, reviewed, merge, int(runs[0])


def _read_descendant_package(
    store: Any, task: Mapping[str, Any], entry: Mapping[str, Any], prefix: str,
    *, required_status: str = "PASS",
) -> tuple[dict, dict]:
    folder = task["canonical_binding"]["canonical_history_folder_id"]
    keys = (f"{prefix}_task_file_id", f"{prefix}_result_file_id", f"{prefix}_review_package_file_id")
    if any(not isinstance(entry.get(key), str) or not entry[key] for key in keys):
        raise P2AError("DESCENDANT_EVIDENCE_MISSING", "Descendant task/result/review package ID is required", prefix=prefix)
    for key in (f"{prefix}_task_sha256", f"{prefix}_result_sha256"):
        require_sha256(entry.get(key), field_name=key, code="DESCENDANT_EVIDENCE_MISMATCH")
    task_file, result_file, package_file = (store.read(entry[key]) for key in keys)
    if any(item.folder_id != folder for item in (task_file, result_file, package_file)):
        raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Descendant evidence is outside frozen history folder")
    if (sha256_bytes(task_file.content) != entry[f"{prefix}_task_sha256"]
            or sha256_bytes(result_file.content) != entry[f"{prefix}_result_sha256"]):
        raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Descendant task/result bytes differ from claimed SHA")
    descendant_task = load_yaml_bytes(task_file.content)
    descendant_result = load_json_bytes(result_file.content)
    package = load_json_bytes(package_file.content)
    validate_task_document(descendant_task)
    validate_result_document(descendant_result)
    control, verified = package.get("CONTROL") or {}, package.get("VERIFIED_MACHINE_FACTS") or {}
    if (package.get("review_package_version") != "cba-kb.p2a-review-package.v1"
            or descendant_task["req_id"] != task["req_id"]
            or descendant_result["req_id"] != task["req_id"]
            or descendant_result["canonical_generation"] != descendant_task["canonical_generation"]
            or descendant_result["repository"] != task["repository"]
            or descendant_task["policy_bundle_sha256"] != task["policy_bundle_sha256"]
            or descendant_task["authority_binding"]["requirement_sha256"] != task["authority_binding"]["requirement_sha256"]
            or descendant_result["status"] != required_status
            or descendant_result["task_id"] != descendant_task["task_id"]
            or descendant_result["source_task_sha256"] != entry[f"{prefix}_task_sha256"]
            or descendant_result["policy_bundle_sha256"] != task["policy_bundle_sha256"]
            or control.get("task_id") != descendant_task["task_id"]
            or control.get("req_id") != task["req_id"]
            or control.get("canonical_generation") != descendant_task["canonical_generation"]
            or control.get("gate") != descendant_task["return_gate"]
            or control.get("requirement_sha256") != task["authority_binding"]["requirement_sha256"]
            or control.get("policy_bundle_sha256") != task["policy_bundle_sha256"]
            or verified.get("task_sha256") != entry[f"{prefix}_task_sha256"]
            or verified.get("result_sha256") != entry[f"{prefix}_result_sha256"]
            or verified.get("base_sha") != descendant_result["base_sha"]
            or verified.get("head_sha") != descendant_result["head_sha"]
            or verified.get("changed_files") != descendant_result["changed_files"]
            or verified.get("focused_tests") != descendant_result["focused_tests"]
            or verified.get("full_regression") != descendant_result["full_regression"]
            or verified.get("pr") != descendant_result["pr"]
            or verified.get("ci") != descendant_result["ci"]):
        raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Descendant P2A PASS and review package bindings disagree")
    return descendant_task, descendant_result


def _verify_retrospective_proof(
    store: Any, task: Mapping[str, Any], entry: Mapping[str, Any],
    historical_task: Mapping[str, Any], historical_result: Mapping[str, Any],
    process_result: Mapping[str, Any], github_inspector: Any, git: GitInspector,
) -> None:
    """Independently validate a later proof without rewriting a BLOCKED result."""
    proof_id = entry.get("retrospective_proof_file_id")
    proof_sha = require_sha256(entry.get("retrospective_proof_sha256"),
                               field_name="retrospective_proof_sha256", code="RETROSPECTIVE_PROOF_UNBOUND")
    if not isinstance(proof_id, str) or not proof_id:
        raise P2AError("RETROSPECTIVE_PROOF_UNBOUND", "Exact proof file ID is required")
    proof_file = store.read(proof_id)
    if sha256_bytes(proof_file.content) != proof_sha:
        raise P2AError("RETROSPECTIVE_PROOF_HASH_MISMATCH", "Proof bytes differ from frozen SHA")
    proof = load_json_bytes(proof_file.content)
    require_exact_keys(proof, {
        "schema_version", "status", "capability", "verifier_version", "verified_at_utc",
        "authority", "historical", "predecessor", "event", "repository",
        "requirement_sha256", "policy_bundle_sha256", "production_authority",
    }, code="RETROSPECTIVE_PROOF_INVALID", location="proof")
    require_exact_keys(proof["authority"], {"task_file_id", "task_sha256", "task_id", "workstream_id"},
                       code="RETROSPECTIVE_PROOF_INVALID", location="proof.authority")
    require_exact_keys(proof["historical"], {
        "task_file_id", "task_sha256", "result_file_id", "result_sha256",
        "review_package_file_id", "review_package_sha256", "status", "classification", "error_codes",
    }, code="RETROSPECTIVE_PROOF_INVALID", location="proof.historical")
    require_exact_keys(proof["predecessor"], {
        "task_file_id", "task_sha256", "result_file_id", "result_sha256", "review_package_file_id",
    }, code="RETROSPECTIVE_PROOF_INVALID", location="proof.predecessor")
    require_exact_keys(proof["event"], {
        "pr_number", "reviewed_head_sha", "actual_pr_head_sha", "base_sha", "merge_commit_sha",
        "merged_at", "reviewed_ci_run_id", "exact_main_ci_run_id",
    }, code="RETROSPECTIVE_PROOF_INVALID", location="proof.event")
    authority_sha = require_sha256(proof["authority"]["task_sha256"], field_name="proof.authority.task_sha256",
                                   code="RETROSPECTIVE_PROOF_UNBOUND")
    frozen_binding = f"retrospective proof SHA256 {proof_sha} authority SHA256 {authority_sha}"
    if not any(frozen_binding in action for action in task["allowed_actions"]):
        raise P2AError("RETROSPECTIVE_PROOF_UNBOUND", "Proof and authority hashes must be frozen in reconciliation task")
    authority_file = store.read(proof["authority"]["task_file_id"])
    if sha256_bytes(authority_file.content) != authority_sha:
        raise P2AError("RETROSPECTIVE_PROOF_UNBOUND", "Proof authority task bytes changed")
    authority = load_yaml_bytes(authority_file.content)
    if (authority.get("schema_version") != "cba-kb.p2a-stabilization-task.v1"
            or authority.get("task_id") != proof["authority"]["task_id"]
            or authority.get("workstream_id") != proof["authority"]["workstream_id"]
            or authority.get("repository") != task["repository"]):
        raise P2AError("RETROSPECTIVE_PROOF_UNBOUND", "Proof does not bind an independent frozen stabilization task")
    capability_codes = RETROSPECTIVE_CAPABILITIES.get(proof["capability"])
    if (proof["schema_version"] != RETROSPECTIVE_PROOF_SCHEMA or proof["status"] != "PASS"
            or proof["verifier_version"] != AUTOMATION_VERSION or capability_codes is None
            or proof["production_authority"] is not False):
        raise P2AError("RETROSPECTIVE_PROOF_INVALID", "Unsupported proof capability, version, status, or Production claim")
    if (proof["repository"] != task["repository"]
            or proof["requirement_sha256"] != task["authority_binding"]["requirement_sha256"]
            or proof["policy_bundle_sha256"] != task["policy_bundle_sha256"]):
        raise P2AError("RETROSPECTIVE_PROOF_BINDING_MISMATCH", "Proof authority bindings differ from reconciliation")
    historical = proof["historical"]
    package_file = store.read(entry["human_review_package_file_id"])
    if (historical["task_file_id"] != entry["human_task_file_id"]
            or historical["task_sha256"] != entry["human_task_sha256"]
            or historical["result_file_id"] != entry["human_result_file_id"]
            or historical["result_sha256"] != entry["human_result_sha256"]
            or historical["review_package_file_id"] != entry["human_review_package_file_id"]
            or historical["review_package_sha256"] != sha256_bytes(package_file.content)
            or historical["status"] != "BLOCKED"
            or historical["classification"] != historical_result["classification"]
            or historical["error_codes"] != [error.get("code") for error in historical_result["errors"]]):
        raise P2AError("RETROSPECTIVE_PROOF_BINDING_MISMATCH", "Proof does not bind unchanged historical BLOCKED bytes")
    predecessor = proof["predecessor"]
    if (predecessor["task_file_id"] != entry["process_task_file_id"]
            or predecessor["task_sha256"] != entry["process_task_sha256"]
            or predecessor["result_file_id"] != entry["process_result_file_id"]
            or predecessor["result_sha256"] != entry["process_result_sha256"]
            or predecessor["review_package_file_id"] != entry["process_review_package_file_id"]
            or process_result["status"] != "PASS"):
        raise P2AError("RETROSPECTIVE_PROOF_BINDING_MISMATCH", "Proof does not bind verified process predecessor")
    errors = historical_result["errors"]
    historical_facts = historical_result["machine_facts"]
    forbidden_mutations = (
        "production_mutation", "publish", "restore", "identity_authority_change",
        "identity_registry_mutation", "master_mutation", "player_uid_mutation",
        "new_identity_decisions", "new_same_decisions", "new_not_same_decisions",
        "machine_final_uid_decisions",
    )
    if (historical_result["status"] != "BLOCKED" or not isinstance(errors, list) or not errors
            or any(not isinstance(error, dict) or error.get("classification") != "PROCESS"
                   or error.get("re_freeze") != "NO" or error.get("code") not in capability_codes
                   for error in errors)
            or historical_result["forbidden_actions_observed"]
            or not isinstance(historical_facts, dict)
            or historical_facts.get("product_failure") is not False
            or historical_facts.get("identity_semantic_delta") != "ZERO"
            or any(historical_facts.get(key) != 0 for key in forbidden_mutations)
            or any(value != 0 for key, value in historical_facts.items() if key.endswith("_product_code_delta"))):
        raise P2AError("RETROSPECTIVE_INELIGIBLE", "Historical BLOCKED was not solely a registered process capability gap")
    predecessor_sha = entry["process_result_sha256"]
    for field in ("focused_tests", "full_regression"):
        evidence = historical_result[field]
        if (process_result[field].get("status") != "PASS"
                or not isinstance(evidence, dict)
                or evidence.get("status") != "NOT_RERUN_DURING_HUMAN_MERGE"
                or not isinstance(evidence.get("evidence_mode"), str)
                or not re.fullmatch(r"VERIFIED_[A-Z0-9_]*PREDECESSOR_REUSE", evidence["evidence_mode"])
                or predecessor_sha not in {value for key, value in evidence.items()
                                           if key.endswith("result_sha256")}):
            raise P2AError("RETROSPECTIVE_INELIGIBLE", "Historical test evidence does not bind verified predecessor PASS")
    event = proof["event"]
    number, reviewed_head, reviewed_run = _human_frozen_review(historical_task)
    historical_pr, historical_ci = historical_result["pr"], historical_result["ci"]
    if (event["pr_number"] != number or event["pr_number"] != entry["pr_number"]
            or event["reviewed_head_sha"] != reviewed_head
            or event["reviewed_head_sha"] != entry["reviewed_head_sha"]
            or event["actual_pr_head_sha"] != entry.get("actual_pr_head_sha", reviewed_head)
            or event["base_sha"] != historical_task["expected_base_sha"]
            or event["merge_commit_sha"] != entry["merge_commit_sha"]
            or event["reviewed_ci_run_id"] != reviewed_run
            or historical_pr.get("number") != number
            or historical_pr.get("head_sha") != reviewed_head
            or historical_pr.get("base_sha") != event["base_sha"]
            or historical_pr.get("merge_commit_sha") != event["merge_commit_sha"]
            or historical_pr.get("merged_at") != event["merged_at"]
            or historical_result["head_sha"] != event["merge_commit_sha"]
            or historical_ci.get("head_sha") != event["merge_commit_sha"]
            or historical_ci.get("conclusion") != "success"
            or historical_ci.get("event") != "push"
            or event["exact_main_ci_run_id"] not in {run.get("id") for run in historical_ci.get("runs", [])}):
        raise P2AError("RETROSPECTIVE_EVENT_MISMATCH", "Proof does not bind frozen Human task and historical event")
    observed = github_inspector._json("pr", "view", str(number), "--repo", task["repository"],
                                      "--json", "number,state,baseRefOid,headRefOid,mergedAt,mergeCommit")
    if (observed.get("number") != number or observed.get("state") != "MERGED"
            or observed.get("baseRefOid") != event["base_sha"]
            or observed.get("headRefOid") != event["actual_pr_head_sha"]
            or observed.get("mergedAt") != event["merged_at"]
            or (observed.get("mergeCommit") or {}).get("oid") != event["merge_commit_sha"]):
        raise P2AError("RETROSPECTIVE_EVENT_MISMATCH", "Proof differs from live merged PR Code Truth")
    runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={reviewed_head}&per_page=100").get("workflow_runs", [])
    if not any(run.get("id") == reviewed_run and run.get("name") == "Offline tests"
               and run.get("event") == "pull_request" and run.get("head_sha") == reviewed_head
               and run.get("status") == "completed" and run.get("conclusion") == "success" for run in runs):
        raise P2AError("RETROSPECTIVE_CI_MISMATCH", "Frozen reviewed PR CI is not independently green")
    runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={event['merge_commit_sha']}&per_page=100").get("workflow_runs", [])
    if not any(run.get("id") == event["exact_main_ci_run_id"] and run.get("name") == "Offline tests"
               and run.get("event") == "push" and run.get("head_branch") == "main"
               and run.get("head_sha") == event["merge_commit_sha"]
               and run.get("status") == "completed" and run.get("conclusion") == "success" for run in runs):
        raise P2AError("RETROSPECTIVE_CI_MISMATCH", "Historical merge lacks independently green exact-main CI")
    if (not git.is_ancestor(reviewed_head, event["actual_pr_head_sha"])
            or git._run("rev-list", "--parents", "-n", "1", event["merge_commit_sha"]).split()
            != [event["merge_commit_sha"], event["base_sha"], event["actual_pr_head_sha"]]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Proof does not bind exact historical merge ancestry")
    try:
        verified_at = datetime.fromisoformat(proof["verified_at_utc"].replace("Z", "+00:00"))
        blocked_at = datetime.fromisoformat(historical_result["generated_at_utc"].replace("Z", "+00:00"))
        merged_at = datetime.fromisoformat(event["merged_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise P2AError("RETROSPECTIVE_PROOF_INVALID", "Proof chronology is invalid") from exc
    if verified_at <= blocked_at or verified_at <= merged_at:
        raise P2AError("RETROSPECTIVE_PROOF_INVALID", "Proof must postdate historical BLOCKED and merge")


def _verify_immutable_external_authority_descendant(
    store: Any, task: Mapping[str, Any], entry: Mapping[str, Any], previous: str,
    github_inspector: Any, git: GitInspector,
) -> None:
    """Verify an opaque, immutable control-plane authority without inventing a task schema."""
    require_exact_keys(entry, {
        "type", "authority_mode", "merge_commit_sha", "reviewed_head_sha", "actual_pr_head_sha",
        "pr_number", "production_authority", "task_file_id", "task_sha256", "result_file_id",
        "result_sha256", "review_package_file_id", "review_package_sha256",
        "post_merge_result_file_id", "post_merge_result_sha256",
        "post_merge_review_package_file_id", "post_merge_review_package_sha256",
    }, code="EXTERNAL_DESCENDANT_UNBOUND", location="immutable external descendant")
    if (entry["type"] != "external_control_plane_descendant"
            or entry["authority_mode"] != "immutable_external_authority"
            or entry["production_authority"] is not False):
        raise P2AError("EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION", "Opaque authority cannot grant Production access")
    for name in ("merge_commit_sha", "reviewed_head_sha", "actual_pr_head_sha"):
        require_git_sha(entry[name], field_name=name, code="EXTERNAL_DESCENDANT_UNBOUND")
    kinds = ("task", "result", "review_package", "post_merge_result", "post_merge_review_package")
    for kind in kinds:
        require_sha256(entry[f"{kind}_sha256"], field_name=f"external.{kind}_sha256",
                       code="EXTERNAL_DESCENDANT_UNBOUND")
        if not isinstance(entry[f"{kind}_file_id"], str) or not entry[f"{kind}_file_id"]:
            raise P2AError("EXTERNAL_DESCENDANT_UNBOUND", "All five immutable file IDs are required")
    frozen = ("external control-plane descendant task SHA256 {task_sha256} result SHA256 {result_sha256} "
              "review SHA256 {review_package_sha256} post-merge result SHA256 {post_merge_result_sha256} "
              "post-merge review SHA256 {post_merge_review_package_sha256}").format(**entry)
    if frozen not in task["allowed_actions"]:
        raise P2AError("EXTERNAL_DESCENDANT_UNBOUND", "Reconciliation task lacks all five frozen evidence hashes")
    files = {kind: store.read(entry[f"{kind}_file_id"]) for kind in kinds}
    folders = {file.folder_id for file in files.values()}
    if (len(folders) != 1 or not next(iter(folders))
            or task["canonical_binding"]["canonical_history_folder_id"] in folders
            or any(sha256_bytes(files[kind].content) != entry[f"{kind}_sha256"] for kind in kinds)):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Opaque authority or companion evidence differs from frozen bytes")
    # The authority payload remains opaque. Only its exact file ID and raw SHA are consumed.
    source = load_json_bytes(files["result"].content)
    review = load_json_bytes(files["review_package"].content)
    post = load_json_bytes(files["post_merge_result"].content)
    post_review = load_json_bytes(files["post_merge_review_package"].content)
    workstream = source.get("workstream_id")
    stage = source.get("stage")
    if (source.get("schema_version") != "cba-kb.p2a-stabilization-result.v1"
            or source.get("status") != "PASS"
            or not isinstance(source.get("classification"), str)
            or not source["classification"].startswith("P2A_STABILIZATION_")
            or not source["classification"].endswith("_PASS")
            or not isinstance(workstream, str) or not workstream or workstream == task["req_id"]
            or not isinstance(stage, str) or not stage
            or source.get("authority_sha256") != entry["task_sha256"]
            or source.get("repository") != task["repository"]
            or source.get("base_sha") != previous
            or source.get("head_sha") != entry["reviewed_head_sha"]
            or not isinstance(source.get("feature_branch"), str) or not source["feature_branch"]
            or source.get("focused_tests", {}).get("status") != "PASS"
            or source.get("full_regression", {}).get("status") != "PASS"
            or source.get("secret_guard") != "PASS"):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Source result does not bind opaque authority and reviewed PASS")
    control, verified = review.get("CONTROL") or {}, review.get("VERIFIED_MACHINE_FACTS") or {}
    if (review.get("review_package_version") != "cba-kb.p2a-stabilization-review-package.v1"
            or control.get("workstream_id") != workstream or control.get("stage") != stage
            or control.get("authority_sha256") != entry["task_sha256"]
            or control.get("result_sha256") != entry["result_sha256"]
            or any(verified.get(name) != source.get(name) for name in (
                "base_sha", "head_sha", "changed_files", "focused_tests", "full_regression",
                "secret_guard", "pr", "ci", "machine_facts",
            ))):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Source review package does not bind opaque authority")
    classification = post.get("classification")
    if (post.get("schema_version") != "cba-kb.p2a-stabilization-post-merge-result.v1"
            or post.get("status") != "PASS"
            or not isinstance(classification, str)
            or not classification.startswith("P2A_STABILIZATION_")
            or not classification.endswith("_MERGE_VERIFIED")
            or post.get("workstream_id") != workstream or post.get("stage") != stage
            or post.get("repository") != task["repository"]
            or post.get("source_result_file_id") != entry["result_file_id"]
            or post.get("source_result_sha256") != entry["result_sha256"]
            or post.get("source_review_package_file_id") != entry["review_package_file_id"]
            or post.get("source_review_package_sha256") != entry["review_package_sha256"]
            or post.get("base_sha") != previous
            or post.get("reviewed_head_sha") != entry["reviewed_head_sha"]
            or post.get("head_sha") != entry["merge_commit_sha"]):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Post-merge result does not bind source authority")
    post_control, post_verified = post_review.get("CONTROL") or {}, post_review.get("VERIFIED_MACHINE_FACTS") or {}
    if (post_review.get("review_package_version") != "cba-kb.p2a-stabilization-post-merge-review-package.v1"
            or post_control.get("workstream_id") != workstream or post_control.get("stage") != stage
            or post_control.get("source_result_sha256") != entry["result_sha256"]
            or post_control.get("post_merge_result_sha256") != entry["post_merge_result_sha256"]
            or any(post_verified.get(name) != post.get(name) for name in (
                "pr", "ci", "changed_files", "machine_facts",
            ))):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Post-merge review package does not bind event")
    source_pr, source_ci, merged_pr, merged_ci = source["pr"], source["ci"], post["pr"], post["ci"]
    if (not isinstance(entry["pr_number"], int) or isinstance(entry["pr_number"], bool)
            or source_pr.get("number") != entry["pr_number"]
            or source_pr.get("state") != "OPEN" or source_pr.get("base_sha") != previous
            or source_pr.get("head_sha") != entry["reviewed_head_sha"]
            or merged_pr.get("number") != entry["pr_number"]
            or merged_pr.get("state") != "MERGED" or merged_pr.get("base_sha") != previous
            or merged_pr.get("reviewed_head_sha") != entry["reviewed_head_sha"]
            or merged_pr.get("merge_commit_sha") != entry["merge_commit_sha"]
            or not merged_pr.get("merged_at")):
        raise P2AError("EXTERNAL_DESCENDANT_PR_MISMATCH", "Opaque-authority PR event differs")
    if (source_ci.get("workflow_name") != "Offline tests" or source_ci.get("event") != "pull_request"
            or source_ci.get("head_sha") != entry["reviewed_head_sha"]
            or source_ci.get("status") != "completed" or source_ci.get("conclusion") != "success"
            or merged_ci.get("workflow_name") != "Offline tests" or merged_ci.get("event") != "push"
            or merged_ci.get("head_branch") != "main"
            or merged_ci.get("head_sha") != entry["merge_commit_sha"]
            or merged_ci.get("status") != "completed" or merged_ci.get("conclusion") != "success"):
        raise P2AError("EXTERNAL_DESCENDANT_CI_MISMATCH", "Opaque-authority CI metadata differs")
    pre_runs = {run.get("id") for run in source_ci.get("runs", [])}
    reviewed_runs = github_inspector._json(
        "api", f"repos/{task['repository']}/actions/runs?head_sha={entry['reviewed_head_sha']}&per_page=100",
    ).get("workflow_runs", [])
    merged_runs = github_inspector._json(
        "api", f"repos/{task['repository']}/actions/runs?head_sha={entry['merge_commit_sha']}&per_page=100",
    ).get("workflow_runs", [])
    valid_pre = {run.get("id") for run in reviewed_runs if run.get("name") == "Offline tests"
                 and run.get("event") == "pull_request" and run.get("head_sha") == entry["reviewed_head_sha"]
                 and run.get("status") == "completed" and run.get("conclusion") == "success"}
    valid_post = {run.get("id") for run in merged_runs if run.get("name") == "Offline tests"
                  and run.get("event") == "push" and run.get("head_branch") == "main"
                  and run.get("head_sha") == entry["merge_commit_sha"]
                  and run.get("status") == "completed" and run.get("conclusion") == "success"}
    if not pre_runs or not pre_runs.issubset(valid_pre) or merged_ci.get("run_id") not in valid_post:
        raise P2AError("EXTERNAL_DESCENDANT_CI_MISMATCH", "Opaque authority lacks exact hosted PR/main CI")
    observed = github_inspector._json(
        "pr", "view", str(entry["pr_number"]), "--repo", task["repository"],
        "--json", "number,state,baseRefOid,headRefOid,mergedAt,mergeCommit",
    )
    if (observed.get("number") != entry["pr_number"] or observed.get("state") != "MERGED"
            or observed.get("baseRefOid") != previous
            or observed.get("headRefOid") != entry["actual_pr_head_sha"]
            or observed.get("mergedAt") != merged_pr["merged_at"]
            or (observed.get("mergeCommit") or {}).get("oid") != entry["merge_commit_sha"]):
        raise P2AError("EXTERNAL_DESCENDANT_PR_MISMATCH", "Opaque-authority PR differs from GitHub Code Truth")
    source_facts, post_facts = source.get("machine_facts"), post.get("machine_facts")
    zero_fields = ("PRODUCT_CODE_DELTA", "PRODUCTION_MUTATION", "PUBLISH", "RESTORE",
                   "REQ181_POINTER_MUTATION", "REQ181_HISTORY_MUTATION")
    if (not isinstance(source_facts, dict) or not isinstance(post_facts, dict)
            or any(facts.get(name) != 0 for facts in (source_facts, post_facts) for name in zero_fields)
            or any(facts.get("IDENTITY_SEMANTIC_DELTA") != "ZERO" for facts in (source_facts, post_facts))
            or any(facts.get("MASTER_MUTATION", 0) != 0 for facts in (source_facts, post_facts))
            or any(facts.get("production_authority", False) is not False for facts in (source_facts, post_facts))):
        raise P2AError("EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION", "Opaque authority permits product, identity or Production mutation")
    allowed_process = {"automation/models.py", "automation/orchestrator.py", "automation/verify.py"}
    def process_path(path: str) -> bool:
        return path in allowed_process or path.startswith("tests/automation/")
    actual_files = set(git._run("diff", "--name-only", previous, entry["merge_commit_sha"]).splitlines())
    if (not isinstance(source.get("changed_files"), list)
            or not actual_files or actual_files != set(source["changed_files"])
            or actual_files != set(post.get("changed_files", []))
            or not all(process_path(path) for path in actual_files)):
        raise P2AError("EXTERNAL_DESCENDANT_SCOPE_VIOLATION", "Opaque-authority merge changes forbidden files")
    if (not git.is_ancestor(entry["reviewed_head_sha"], entry["actual_pr_head_sha"])
            or git._run("rev-list", "--parents", "-n", "1", entry["merge_commit_sha"]).split()
            != [entry["merge_commit_sha"], previous, entry["actual_pr_head_sha"]]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Opaque-authority merge ancestry differs")
    commits = git._run("rev-list", "--reverse", f"{previous}..{entry['actual_pr_head_sha']}").splitlines()
    if not commits or commits[-1] != entry["actual_pr_head_sha"]:
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Opaque-authority branch history is incomplete")
    for commit in commits:
        paths = git._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if not all(process_path(path) for path in paths):
            raise P2AError("EXTERNAL_DESCENDANT_SCOPE_VIOLATION", "Opaque-authority branch contains forbidden file")


def _verify_external_control_plane_descendant(
    store: Any, task: Mapping[str, Any], entry: Mapping[str, Any], previous: str,
    github_inspector: Any, git: GitInspector,
) -> None:
    """Bind an independent process merge to five immutable workstream artifacts."""
    mode = entry.get("authority_mode", "structured_stabilization_task")
    if mode == "immutable_external_authority":
        _verify_immutable_external_authority_descendant(store, task, entry, previous, github_inspector, git)
        return
    if mode != "structured_stabilization_task":
        raise P2AError("EXTERNAL_DESCENDANT_UNBOUND", "Unknown external authority mode")
    if "authority_mode" in entry:
        entry = {key: value for key, value in entry.items() if key != "authority_mode"}
    require_exact_keys(entry, {
        "type", "merge_commit_sha", "reviewed_head_sha", "actual_pr_head_sha", "pr_number",
        "production_authority", "task_file_id", "task_sha256", "result_file_id", "result_sha256",
        "review_package_file_id", "review_package_sha256", "post_merge_result_file_id",
        "post_merge_result_sha256", "post_merge_review_package_file_id",
        "post_merge_review_package_sha256",
    }, code="EXTERNAL_DESCENDANT_UNBOUND", location="external descendant")
    if entry["type"] != "external_control_plane_descendant" or entry["production_authority"] is not False:
        raise P2AError("EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION", "External proof cannot grant Production authority")
    for field in ("merge_commit_sha", "reviewed_head_sha", "actual_pr_head_sha"):
        require_git_sha(entry[field], field_name=field, code="EXTERNAL_DESCENDANT_UNBOUND")
    kinds = ("task", "result", "review_package", "post_merge_result", "post_merge_review_package")
    for kind in kinds:
        require_sha256(entry[f"{kind}_sha256"], field_name=f"external.{kind}_sha256",
                       code="EXTERNAL_DESCENDANT_UNBOUND")
        if not isinstance(entry[f"{kind}_file_id"], str) or not entry[f"{kind}_file_id"]:
            raise P2AError("EXTERNAL_DESCENDANT_UNBOUND", "External evidence needs exact file IDs")
    frozen = ("external control-plane descendant task SHA256 {task_sha256} result SHA256 {result_sha256} "
              "review SHA256 {review_package_sha256} post-merge result SHA256 {post_merge_result_sha256} "
              "post-merge review SHA256 {post_merge_review_package_sha256}").format(**entry)
    if frozen not in task["allowed_actions"]:
        raise P2AError("EXTERNAL_DESCENDANT_UNBOUND", "Frozen reconciliation task lacks all five external evidence hashes")
    files = {kind: store.read(entry[f"{kind}_file_id"]) for kind in kinds}
    folders = {item.folder_id for item in files.values()}
    if (len(folders) != 1 or not next(iter(folders))
            or task["canonical_binding"]["canonical_history_folder_id"] in folders
            or any(sha256_bytes(files[kind].content) != entry[f"{kind}_sha256"] for kind in kinds)):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "External evidence location or exact SHA differs")
    external_task = load_yaml_bytes(files["task"].content)
    source = load_json_bytes(files["result"].content)
    review = load_json_bytes(files["review_package"].content)
    post = load_json_bytes(files["post_merge_result"].content)
    post_review = load_json_bytes(files["post_merge_review_package"].content)
    if (external_task.get("schema_version") != "cba-kb.p2a-stabilization-task.v1"
            or external_task.get("repository") != task["repository"]
            or external_task.get("expected_base_branch") != "main"
            or external_task.get("expected_base_sha") != previous
            or not isinstance(external_task.get("workstream_id"), str)
            or not external_task["workstream_id"]
            or external_task["workstream_id"] == task["req_id"]
            or not isinstance(external_task.get("stabilization_generation"), int)
            or external_task["stabilization_generation"] < 1
            or "canonical_generation" in external_task
            or source.get("status") != "PASS"
            or not isinstance(source.get("classification"), str)
            or not source["classification"].startswith("P2A_STABILIZATION_")
            or not source["classification"].endswith("_PASS")
            or source.get("workstream_id") != external_task["workstream_id"]
            or source.get("stabilization_generation") != external_task.get("stabilization_generation")
            or source.get("task_id") != external_task.get("task_id")
            or source.get("source_task_sha256") != entry["task_sha256"]
            or source.get("repository") != task["repository"]
            or source.get("base_sha") != previous
            or source.get("head_sha") != entry["reviewed_head_sha"]
            or source.get("feature_branch") != external_task.get("feature_branch")
            or source.get("focused_tests", {}).get("status") != "PASS"
            or source.get("full_regression", {}).get("status") != "PASS"
            or source.get("secret_guard") != "PASS"):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "External source task/result PASS binding failed")
    if (review.get("review_package_version") != "cba-kb.p2a-stabilization-review-package.v1"
            or review.get("CONTROL", {}).get("workstream_id") != external_task["workstream_id"]
            or review["CONTROL"].get("stabilization_generation") != external_task.get("stabilization_generation")
            or review["CONTROL"].get("task_id") != external_task["task_id"]
            or review["CONTROL"].get("task_sha256") != entry["task_sha256"]
            or review["CONTROL"].get("result_sha256") != entry["result_sha256"]
            or review.get("VERIFIED_MACHINE_FACTS", {}).get("pr") != source.get("pr")
            or review["VERIFIED_MACHINE_FACTS"].get("ci") != source.get("ci")
            or review["VERIFIED_MACHINE_FACTS"].get("changed_files") != source.get("changed_files")):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "External source review package is not exact-bound")
    if (post.get("status") != "PASS"
            or post.get("classification") != "P2A_STABILIZATION_HUMAN_MERGE_VERIFIED"
            or post.get("workstream_id") != external_task["workstream_id"]
            or post.get("stabilization_generation") != external_task.get("stabilization_generation")
            or post.get("task_id") != external_task["task_id"]
            or post.get("repository") != task["repository"]
            or post.get("source_task_file_id") != entry["task_file_id"]
            or post.get("source_task_sha256") != entry["task_sha256"]
            or post.get("source_result_file_id") != entry["result_file_id"]
            or post.get("source_result_sha256") != entry["result_sha256"]
            or post.get("source_review_package_file_id") != entry["review_package_file_id"]
            or post.get("source_review_package_sha256") != entry["review_package_sha256"]
            or post.get("base_sha") != previous
            or post.get("reviewed_head_sha") != entry["reviewed_head_sha"]
            or post.get("head_sha") != entry["merge_commit_sha"]):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Post-merge proof does not bind source authority")
    if (post_review.get("review_package_version") != "cba-kb.p2a-stabilization-post-merge-review-package.v1"
            or post_review.get("CONTROL", {}).get("workstream_id") != external_task["workstream_id"]
            or post_review["CONTROL"].get("task_id") != external_task["task_id"]
            or post_review["CONTROL"].get("task_sha256") != entry["task_sha256"]
            or post_review["CONTROL"].get("source_result_sha256") != entry["result_sha256"]
            or post_review["CONTROL"].get("post_merge_result_sha256") != entry["post_merge_result_sha256"]
            or post_review.get("VERIFIED_MACHINE_FACTS", {}).get("pr") != post.get("pr")
            or post_review["VERIFIED_MACHINE_FACTS"].get("ci") != post.get("ci")
            or post_review["VERIFIED_MACHINE_FACTS"].get("changed_files") != post.get("changed_files")
            or post_review["VERIFIED_MACHINE_FACTS"].get("machine_facts") != post.get("machine_facts")):
        raise P2AError("EXTERNAL_DESCENDANT_EVIDENCE_MISMATCH", "Post-merge review package is not exact-bound")
    source_pr, source_ci, merged_pr, merged_ci = source["pr"], source["ci"], post["pr"], post["ci"]
    if (not isinstance(entry["pr_number"], int) or isinstance(entry["pr_number"], bool)
            or source_pr.get("number") != entry["pr_number"]
            or source_pr.get("base_sha") != previous
            or source_pr.get("head_sha") != entry["reviewed_head_sha"]
            or source_pr.get("state") != "OPEN"
            or merged_pr.get("number") != entry["pr_number"]
            or merged_pr.get("base_sha") != previous
            or merged_pr.get("reviewed_head_sha") != entry["reviewed_head_sha"]
            or merged_pr.get("merge_commit_sha") != entry["merge_commit_sha"]
            or not merged_pr.get("merged_at")
            or merged_pr.get("state") != "MERGED"):
        raise P2AError("EXTERNAL_DESCENDANT_PR_MISMATCH", "External PR facts differ across source and merge proof")
    if (source_ci.get("workflow_name") != "Offline tests"
            or source_ci.get("event") != "pull_request"
            or source_ci.get("head_sha") != entry["reviewed_head_sha"]
            or source_ci.get("status") != "completed"
            or source_ci.get("conclusion") != "success"
            or merged_ci.get("workflow_name") != "Offline tests"
            or merged_ci.get("event") != "push"
            or merged_ci.get("head_branch") != "main"
            or merged_ci.get("head_sha") != entry["merge_commit_sha"]
            or merged_ci.get("status") != "completed"
            or merged_ci.get("conclusion") != "success"):
        raise P2AError("EXTERNAL_DESCENDANT_CI_MISMATCH", "External reviewed or exact-main CI differs")
    pre_runs = {run.get("id") for run in source_ci.get("runs", [])}
    post_run = merged_ci.get("run_id")
    reviewed_runs = github_inspector._json(
        "api", f"repos/{task['repository']}/actions/runs?head_sha={entry['reviewed_head_sha']}&per_page=100",
    ).get("workflow_runs", [])
    merged_runs = github_inspector._json(
        "api", f"repos/{task['repository']}/actions/runs?head_sha={entry['merge_commit_sha']}&per_page=100",
    ).get("workflow_runs", [])
    valid_pre = {run.get("id") for run in reviewed_runs if run.get("name") == "Offline tests"
                 and run.get("event") == "pull_request" and run.get("head_sha") == entry["reviewed_head_sha"]
                 and run.get("status") == "completed" and run.get("conclusion") == "success"}
    valid_post = {run.get("id") for run in merged_runs if run.get("name") == "Offline tests"
                  and run.get("event") == "push" and run.get("head_branch") == "main"
                  and run.get("head_sha") == entry["merge_commit_sha"]
                  and run.get("status") == "completed" and run.get("conclusion") == "success"}
    if not pre_runs or not pre_runs.issubset(valid_pre) or post_run not in valid_post:
        raise P2AError("EXTERNAL_DESCENDANT_CI_MISMATCH", "External CI lacks exact hosted PR/main runs")
    observed = github_inspector._json(
        "pr", "view", str(entry["pr_number"]), "--repo", task["repository"],
        "--json", "number,state,baseRefOid,headRefOid,mergedAt,mergeCommit",
    )
    if (observed.get("number") != entry["pr_number"] or observed.get("state") != "MERGED"
            or observed.get("baseRefOid") != previous
            or observed.get("headRefOid") != entry["actual_pr_head_sha"]
            or observed.get("mergedAt") != merged_pr["merged_at"]
            or (observed.get("mergeCommit") or {}).get("oid") != entry["merge_commit_sha"]):
        raise P2AError("EXTERNAL_DESCENDANT_PR_MISMATCH", "External merge differs from GitHub Code Truth")
    source_facts, post_facts = source.get("machine_facts"), post.get("machine_facts")
    zero_fields = ("PRODUCT_CODE_DELTA", "PRODUCTION_MUTATION", "PUBLISH", "RESTORE",
                   "REQ181_POINTER_MUTATION", "REQ181_HISTORY_MUTATION")
    if (not isinstance(source_facts, dict) or not isinstance(post_facts, dict)
            or any(facts.get(field) != 0 for facts in (source_facts, post_facts) for field in zero_fields)
            or any(facts.get("IDENTITY_SEMANTIC_DELTA") != "ZERO" for facts in (source_facts, post_facts))
            or any(facts.get("production_authority", False) is not False for facts in (source_facts, post_facts))
            or not any("Production" in item for item in external_task.get("must_not_change", []))
            or not any("publish" in item.lower() for item in external_task.get("forbidden_actions", []))):
        raise P2AError("EXTERNAL_DESCENDANT_AUTHORITY_VIOLATION", "External evidence permits product, identity or Production mutation")
    actual_files = set(git._run("diff", "--name-only", previous, entry["merge_commit_sha"]).splitlines())
    if (not isinstance(source.get("changed_files"), list)
            or actual_files != set(source["changed_files"])
            or actual_files != set(post.get("changed_files", []))
            or not actual_files
            or not all(path in EXTERNAL_CONTROL_PLANE_PATHS or path.startswith("tests/automation/")
                       for path in actual_files)
            or not all(path_allowed(path, external_task.get("allowed_paths", [])) for path in actual_files)):
        raise P2AError("EXTERNAL_DESCENDANT_SCOPE_VIOLATION", "External merge exceeds verified process-only file scope")
    if (not git.is_ancestor(entry["reviewed_head_sha"], entry["actual_pr_head_sha"])
            or git._run("rev-list", "--parents", "-n", "1", entry["merge_commit_sha"]).split()
            != [entry["merge_commit_sha"], previous, entry["actual_pr_head_sha"]]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "External merge ancestry differs from reviewed PR")
    branch_commits = git._run("rev-list", "--reverse", f"{previous}..{entry['actual_pr_head_sha']}").splitlines()
    if not branch_commits or branch_commits[-1] != entry["actual_pr_head_sha"]:
        raise P2AError("GIT_ANCESTRY_MISMATCH", "External branch history is incomplete")
    for commit in branch_commits:
        paths = git._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if not all((path in EXTERNAL_CONTROL_PLANE_PATHS or path.startswith("tests/automation/"))
                   and path_allowed(path, external_task["allowed_paths"]) for path in paths):
            raise P2AError("EXTERNAL_DESCENDANT_SCOPE_VIOLATION", "External branch contains forbidden file mutation")


def _verify_post_merge_descendants(
    result: Mapping[str, Any], task: Mapping[str, Any], github_inspector: Any,
    git_root: str | Path | None, github: Mapping[str, Any], store: Any,
) -> None:
    pr, ci, facts = result["pr"], result["ci"], result["machine_facts"]
    number, historical_base, reviewed_head, historical_merge, reviewed_run = _descendant_frozen_product(task)
    if (task["expected_base_branch"] != "main" or task["feature_branch"] != "main"
            or result["head_sha"] != task["expected_base_sha"] or result["base_sha"] != result["head_sha"]):
        raise P2AError("FINAL_MAIN_MISMATCH", "Descendant reconciliation must bind frozen final main")
    if not isinstance(facts, dict) or facts.get("descendant_mode") != DESCENDANT_MODE:
        raise P2AError("DESCENDANT_AUTHORITY_MISSING", "Explicit bounded descendant mode is required")
    if facts.get("requirement_sha256") != task["authority_binding"]["requirement_sha256"]:
        raise P2AError("REQUIREMENT_BINDING_MISMATCH", "Descendant result does not bind frozen Requirement")
    if (pr.get("number") != number or pr.get("state") != "MERGED"
            or pr.get("base_sha") != historical_base or pr.get("head_sha") != reviewed_head
            or pr.get("merge_commit_sha") != historical_merge
            or pr.get("reviewed_head_sha") != reviewed_head
            or pr.get("reviewed_ci_run_id") != reviewed_run):
        raise P2AError("HISTORICAL_PR_MISMATCH", "Historical PR differs from frozen product review")
    require_git_sha(pr.get("actual_head_sha"), field_name="pr.actual_head_sha", code="HISTORICAL_PR_MISMATCH")
    if ci.get("head_sha") != result["head_sha"] or ci.get("conclusion") != "success":
        raise P2AError("CI_FACTS_MISMATCH", "Final-main CI must bind the frozen main SHA")
    observed = github["pr"]
    expected = {"number": number, "url": pr["url"], "state": "MERGED",
                "baseRefOid": historical_base, "headRefOid": pr["actual_head_sha"]}
    if any(observed.get(key) != value for key, value in expected.items()):
        raise P2AError("PR_FACTS_MISMATCH", "Historical PR differs from GitHub Code Truth")
    historical = github_inspector._json("pr", "view", str(number), "--repo", task["repository"], "--json", "mergedAt,mergeCommit")
    if not historical.get("mergedAt") or (historical.get("mergeCommit") or {}).get("oid") != historical_merge:
        raise P2AError("HISTORICAL_PR_MISMATCH", "Historical merge commit differs from GitHub")
    reviewed_runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={reviewed_head}&per_page=100").get("workflow_runs", [])
    if not any(run.get("id") == reviewed_run and run.get("name") == "Offline tests"
               and run.get("event") == "pull_request" and run.get("head_sha") == reviewed_head
               and run.get("status") == "completed" and run.get("conclusion") == "success" for run in reviewed_runs):
        raise P2AError("HISTORICAL_CI_MISMATCH", "Frozen reviewed product CI is not independently green")
    main_ref = github_inspector._json("api", f"repos/{task['repository']}/git/ref/heads/main")
    if (main_ref.get("object") or {}).get("sha") != result["head_sha"]:
        raise P2AError("FINAL_MAIN_MISMATCH", "Live main advanced beyond frozen final main")
    main_runs = {run["id"] for run in github["runs"]
                 if run["head_sha"] == result["head_sha"] and run["event"] == "push"
                 and run["status"] == "completed" and run["conclusion"] == "success"}
    claimed_main = {run["id"] for run in ci.get("runs", [])}
    if not claimed_main or not claimed_main.issubset(main_runs):
        raise P2AError("CI_FACTS_MISMATCH", "Final main requires successful hosted Offline tests push CI")
    if git_root is None:
        raise P2AError("GIT_FACTS_UNAVAILABLE", "Descendant reconciliation requires exact-main Git history")
    git = GitInspector(git_root)
    if not git.is_ancestor(reviewed_head, pr["actual_head_sha"]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Reviewed product head is not an ancestor of actual PR head")
    if git._run("rev-list", "--parents", "-n", "1", historical_merge).split() != [historical_merge, historical_base, pr["actual_head_sha"]]:
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Historical product merge parents differ")
    if not git.is_ancestor(historical_merge, result["head_sha"]):
        raise P2AError("GIT_ANCESTRY_MISMATCH", "Historical product merge is not an ancestor of final main")
    commits = git._run("rev-list", "--first-parent", "--reverse", f"{historical_merge}..{result['head_sha']}").splitlines()
    descendants = facts.get("descendants")
    if (not isinstance(descendants, list) or not descendants
            or not all(isinstance(item, dict) for item in descendants)
            or [item.get("merge_commit_sha") for item in descendants] != commits):
        raise P2AError("UNBOUND_DESCENDANT_COMMIT", "Every first-parent descendant commit needs exact ordered P2A evidence")
    previous = historical_merge
    for entry in descendants:
        if entry.get("type") == "external_control_plane_descendant":
            _verify_external_control_plane_descendant(store, task, entry, previous, github_inspector, git)
            previous = entry["merge_commit_sha"]
            continue
        merge_sha, pr_head = entry["merge_commit_sha"], entry["reviewed_head_sha"]
        actual_pr_head = entry.get("actual_pr_head_sha", pr_head)
        retrospective = ("retrospective_proof_file_id" in entry or "retrospective_proof_sha256" in entry)
        require_git_sha(merge_sha, field_name="descendant.merge_commit_sha", code="DESCENDANT_EVIDENCE_MISMATCH")
        require_git_sha(pr_head, field_name="descendant.reviewed_head_sha", code="DESCENDANT_EVIDENCE_MISMATCH")
        require_git_sha(actual_pr_head, field_name="descendant.actual_pr_head_sha", code="DESCENDANT_EVIDENCE_MISMATCH")
        if git._run("rev-list", "--parents", "-n", "1", merge_sha).split() != [merge_sha, previous, actual_pr_head]:
            raise P2AError("GIT_ANCESTRY_MISMATCH", "Descendant merge parents do not form exact main chain")
        if not git.is_ancestor(previous, actual_pr_head) or not git.is_ancestor(pr_head, actual_pr_head):
            raise P2AError("GIT_ANCESTRY_MISMATCH", "Descendant reviewed head does not descend from its frozen base")
        process_task, process_result = _read_descendant_package(store, task, entry, "process")
        human_task, human_result = _read_descendant_package(
            store, task, entry, "human", required_status="BLOCKED" if retrospective else "PASS",
        )
        if (process_task["task_type"] != "CODEX_PROCESS_REPAIR"
                or human_task["task_type"] != HUMAN_MERGE_TASK_TYPE
                or process_task["expected_base_sha"] != previous
                or human_task["expected_base_sha"] != previous
                or process_task["feature_branch"] != human_task["feature_branch"]
                or human_task["canonical_generation"] != process_task["canonical_generation"] + 1
                or human_task["authority_binding"]["predecessor_terminal_task_id"] != process_task["task_id"]
                or human_task["authority_binding"]["predecessor_terminal_task_sha256"] != entry["process_task_sha256"]
                or process_result["base_sha"] != previous
                or process_result["pr"].get("number") != entry.get("pr_number")
                or process_result["pr"].get("base_sha") != previous
                or process_result["pr"].get("head_sha") != pr_head
                or process_result["head_sha"] != pr_head
                or human_result["pr"].get("number") != entry.get("pr_number")
                or human_result["pr"].get("head_sha") != pr_head
                or human_result["pr"].get("base_sha") != previous
                or human_result["pr"].get("merge_commit_sha") != merge_sha
                or human_result["pr"].get("state") != "MERGED"
                or human_result["head_sha"] != merge_sha):
            raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Descendant process/Human PASS evidence does not bind actual merge")
        frozen_number, frozen_head, frozen_run = _human_frozen_review(human_task)
        if frozen_number != entry["pr_number"] or frozen_head != pr_head:
            raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Human task frozen review differs from descendant PR")
        if retrospective:
            _verify_retrospective_proof(store, task, entry, human_task, human_result,
                                        process_result, github_inspector, git)
        else:
            human_facts = human_result["machine_facts"]
            if (not isinstance(human_facts, dict)
                    or human_facts.get("evidence_mode") != "VERIFIED_PREDECESSOR_REUSE"
                    or human_facts.get("predecessor_task_file_id") != entry["process_task_file_id"]
                    or human_facts.get("predecessor_result_file_id") != entry["process_result_file_id"]
                    or human_facts.get("predecessor_review_package_file_id") != entry["process_review_package_file_id"]
                    or human_facts.get("predecessor_result_sha256") != entry["process_result_sha256"]):
                raise P2AError("DESCENDANT_EVIDENCE_MISMATCH", "Human PASS does not bind verified process predecessor")
        if (process_result["ci"].get("head_sha") != pr_head
                or process_result["ci"].get("conclusion") != "success"
                or human_result["ci"].get("head_sha") != merge_sha
                or human_result["ci"].get("conclusion") != "success"):
            raise P2AError("DESCENDANT_CI_MISMATCH", "Descendant PR or exact-main CI is not green")
        observed_descendant = github_inspector._json("pr", "view", str(entry["pr_number"]), "--repo", task["repository"],
                                                     "--json", "number,state,baseRefOid,headRefOid,mergedAt,mergeCommit")
        if (observed_descendant.get("number") != entry["pr_number"]
                or observed_descendant.get("state") != "MERGED"
                or observed_descendant.get("baseRefOid") != previous
                or observed_descendant.get("headRefOid") != actual_pr_head
                or (observed_descendant.get("mergeCommit") or {}).get("oid") != merge_sha
                or not observed_descendant.get("mergedAt")):
            raise P2AError("DESCENDANT_PR_MISMATCH", "Descendant PR is not the exact merged Code Truth")
        pr_runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={pr_head}&per_page=100").get("workflow_runs", [])
        process_run_ids = {run.get("id") for run in process_result["ci"].get("runs", [])}
        if (frozen_run not in process_run_ids or not process_run_ids
                or not process_run_ids.issubset({run.get("id") for run in pr_runs
                    if run.get("name") == "Offline tests" and run.get("event") == "pull_request"
                    and run.get("head_sha") == pr_head and run.get("status") == "completed"
                    and run.get("conclusion") == "success"})):
            raise P2AError("DESCENDANT_CI_MISMATCH", "Descendant reviewed head lacks exact successful PR CI")
        runs = github_inspector._json("api", f"repos/{task['repository']}/actions/runs?head_sha={merge_sha}&per_page=100").get("workflow_runs", [])
        claimed = {run.get("id") for run in human_result["ci"].get("runs", [])}
        successful = {run.get("id") for run in runs if run.get("name") == "Offline tests"
                      and run.get("event") == "push" and run.get("head_sha") == merge_sha
                      and run.get("status") == "completed" and run.get("conclusion") == "success"}
        if not claimed or not claimed.issubset(successful):
            raise P2AError("DESCENDANT_CI_MISMATCH", "Descendant merge lacks exact-main hosted push CI")
        actual_files = set(git._run("diff", "--name-only", previous, merge_sha).splitlines())
        process_files = set(process_result["changed_files"])
        if actual_files != process_files or not actual_files.issubset(PROCESS_DESCENDANT_PATHS):
            raise P2AError("DESCENDANT_SCOPE_VIOLATION", "Descendant delta is not exact authorized process-only scope")
        branch_commits = git._run("rev-list", "--reverse", f"{previous}..{actual_pr_head}").splitlines()
        if not branch_commits or branch_commits[-1] != actual_pr_head:
            raise P2AError("GIT_ANCESTRY_MISMATCH", "Descendant reviewed branch history is incomplete")
        for commit in branch_commits:
            commit_files = set(git._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())
            if (not commit_files.issubset(PROCESS_DESCENDANT_PATHS)
                    or not all(path_allowed(path, process_task["allowed_paths"]) for path in commit_files)):
                raise P2AError("DESCENDANT_SCOPE_VIOLATION", "Descendant branch contains a forbidden file delta")
        if not all(path_allowed(path, process_task["allowed_paths"]) for path in actual_files):
            raise P2AError("DESCENDANT_SCOPE_VIOLATION", "Descendant files exceed frozen process task allowlist")
        previous = merge_sha


def verify_result_bytes(
    result_data: bytes,
    task_data: bytes,
    *,
    git_root: str | Path | None = None,
    github_inspector: GitHubInspector | Any | None = None,
    predecessor_store: Any | None = None,
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
            human_merge = task["task_type"] == HUMAN_MERGE_TASK_TYPE
            if not human_merge:
                for field in ("focused_tests", "full_regression"):
                    if not isinstance(result[field], dict) or result[field].get("status") != "PASS":
                        raise P2AError("TEST_EVIDENCE_INCOMPLETE", f"{field} must be PASS")
            if not isinstance(result["pr"], dict) or not isinstance(result["ci"], dict):
                raise P2AError("CI_EVIDENCE_INCOMPLETE", "PASS result requires PR and CI evidence")
            post_merge = task["task_type"] == POST_MERGE_TASK_TYPE
            if (not post_merge and not human_merge and result["pr"].get("head_sha") != result["head_sha"]) or result["ci"].get("head_sha") != result["head_sha"]:
                raise P2AError("PR_CI_HEAD_MISMATCH", "PR, CI and result heads must be identical")
            if result["ci"].get("workflow_name") != "Offline tests" or result["ci"].get("conclusion") != "success":
                raise P2AError("CI_NOT_GREEN", "Required Offline tests is not green")
            if github_inspector is None:
                raise P2AError("GITHUB_FACTS_UNAVAILABLE", "PASS result requires independent GitHub Code Truth")
            github = github_inspector.collect(result["pr"]["number"], result["ci"]["workflow_name"], result["head_sha"])
            if post_merge:
                if isinstance(result["machine_facts"], dict) and result["machine_facts"].get("descendant_mode") == DESCENDANT_MODE:
                    store = predecessor_store if predecessor_store is not None else _human_evidence_store(git_root)
                    _verify_post_merge_descendants(result, task, github_inspector, git_root, github, store)
                else:
                    _verify_post_merge_result(result, task, github_inspector, git_root, github)
            elif human_merge:
                store = predecessor_store if predecessor_store is not None else _human_evidence_store(git_root)
                _verify_human_merge_result(result, task, github_inspector, git_root, github, store)
            else:
                observed_pr = github["pr"]
                expected_pr = {
                    "number": result["pr"]["number"], "url": result["pr"]["url"], "state": "OPEN",
                    "headRefOid": result["head_sha"],
                }
                # Only bounded repair may start at the previous feature head.
                if task["task_type"] != "CODEX_BOUNDED_REPAIR":
                    expected_pr["baseRefOid"] = result["base_sha"]
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
            if result["status"] == "PASS" and task["task_type"] == HUMAN_MERGE_TASK_TYPE:
                expected_changed = sorted(result["machine_facts"].get("pr_changed_files", []))
                if result["changed_files"] != [] or actual_changed != expected_changed:
                    raise P2AError("CHANGED_FILES_MISMATCH", "Merge delta does not match reviewed PR scope", expected=actual_changed, observed=expected_changed)
            elif actual_changed != sorted(result["changed_files"]):
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
