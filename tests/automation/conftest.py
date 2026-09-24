from __future__ import annotations

import copy

import pytest
import yaml

from automation.models import canonical_json_bytes, sha256_bytes


TASK_ID = "de26fcbb-ffd2-49d5-a689-74a294e3818e"
POLICY_SHA = "c628cc45e855090464eaaf1f4f8dae15636ddbc9451096654095759a3c2050b5"
BASE_SHA = "294cb0d628b3b6b226a4dee2b6142b61ec44fddc"
HEAD_SHA = "b" * 40
REQ_SHA = "0d99da7b61dbe38f6439ec538009d6d3f8a1eb0715ad106e6bffe71a7427713d"


@pytest.fixture
def task_dict():
    return {
        "schema_version": "cba-kb.p2a-task.v1",
        "req_id": "REQ-WORKFLOW-P2A-01",
        "canonical_generation": 3,
        "task_id": TASK_ID,
        "task_type": "CODEX_P2A_IMPLEMENT_TEST_CANARY_PR",
        "stage": "CODEX_IMPLEMENTATION_NEGATIVE_RECOVERY_REAL_HANDOFF_CANARY",
        "issued_at_utc": "2026-09-24T07:42:29Z",
        "issued_by": "ChatGPT-Web-Auditor",
        "automation_version": "0.1.0",
        "policy_bundle_sha256": POLICY_SHA,
        "repository": "SkyCjq/cba-kb-engine",
        "expected_base_branch": "main",
        "expected_base_sha": BASE_SHA,
        "feature_branch": "codex/REQ-WORKFLOW-P2A-01",
        "allowed_paths": ["automation/**", "tests/automation/**", "requirements/REQ-WORKFLOW-P2A-01/**", "Makefile"],
        "must_not_change": ["src/cba_kb/**", ".github/workflows/offline-tests.yml", "pyproject.toml", "requirements.lock"],
        "allowed_actions": ["implement P2A"],
        "forbidden_actions": ["Production mutation", "P2B/P3A"],
        "focused_tests": ["python -m pytest -q tests/automation", "python scripts/check_secrets.py --tracked"],
        "full_regression": "python -m pytest -q",
        "max_internal_steps": 6,
        "max_internal_retries": 1,
        "max_runtime_minutes": 45,
        "next_executor": "CODEX_RUNNER",
        "return_gate": "WEB_MERGE_READY_REQ_WORKFLOW_P2A_01",
        "canonical_binding": {
            "canonical_req_root_folder_id": "root",
            "canonical_current_folder_id": "current",
            "canonical_history_folder_id": "history",
            "canonical_next_task_file_id": "stable",
            "canonical_codex_result_file_id": "result",
            "canonical_update_mode": "IN_PLACE_SAME_FILE_ID",
        },
        "authority_binding": {
            "requirement_revision": "r2-20260924-p2a-sequence-refreeze",
            "requirement_sha256": REQ_SHA,
            "predecessor_req_id": "REQ-180-PROD-RELEASE-01",
            "predecessor_terminal_generation": 57,
            "predecessor_terminal_task_id": "9e62971c-a5be-4bc0-87a2-cc6928288433",
            "predecessor_terminal_task_sha256": "4" * 64,
        },
        "transition_binding": {
            "source_result_required": True,
            "supersedes_task_id": "7cf15a62-e5c8-4b04-a792-4dfccfc3e691",
            "supersedes_task_sha256": "d" * 64,
            "transition_from_gate": "WEB_TASK_SCHEMA_REFREEZE_REQ_WORKFLOW_P2A_01",
            "transition_commit_required": True,
        },
    }


@pytest.fixture
def task_bytes(task_dict):
    return yaml.safe_dump(task_dict, sort_keys=False, allow_unicode=True).encode()


@pytest.fixture
def result_dict(task_dict, task_bytes):
    return {
        "schema_version": "cba-kb.p2a-result.v1",
        "req_id": task_dict["req_id"],
        "canonical_generation": 3,
        "task_id": TASK_ID,
        "status": "PASS",
        "classification": "IMPLEMENTATION_CANARIES_READY_FOR_WEB_MERGE_REVIEW",
        "automation_version": "0.1.0",
        "policy_bundle_sha256": POLICY_SHA,
        "repository": "SkyCjq/cba-kb-engine",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "feature_branch": "codex/REQ-WORKFLOW-P2A-01",
        "changed_files": ["automation/verify.py", "tests/automation/test_verify.py"],
        "focused_tests": {"status": "PASS"},
        "full_regression": {"status": "PASS"},
        "pr": {"number": 33, "url": "https://example.test/pr/33", "head_sha": HEAD_SHA, "base_sha": BASE_SHA},
        "ci": {"workflow_name": "Offline tests", "head_sha": HEAD_SHA, "conclusion": "success"},
        "output_artifacts": [],
        "forbidden_actions_observed": [],
        "machine_facts": {},
        "errors": [],
        "source_task_sha256": sha256_bytes(task_bytes),
        "recommended_next_gate": "WEB_MERGE_READY_REQ_WORKFLOW_P2A_01",
        "return_gate": "WEB_MERGE_READY_REQ_WORKFLOW_P2A_01",
        "generated_at_utc": "2026-09-24T08:00:00Z",
    }


@pytest.fixture
def result_bytes(result_dict):
    return canonical_json_bytes(result_dict)


@pytest.fixture
def clone():
    return copy.deepcopy
