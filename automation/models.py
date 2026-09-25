from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


AUTOMATION_VERSION = "0.1.0"
TASK_SCHEMA = "cba-kb.p2a-task.v1"
RESULT_SCHEMA = "cba-kb.p2a-result.v1"
REVIEW_PACKAGE_SCHEMA = "cba-kb.p2a-review-package.v1"
RETROSPECTIVE_PROOF_SCHEMA = "cba-kb.p2a-retrospective-proof.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class P2AError(RuntimeError):
    """A typed fail-closed workflow error."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class VerificationResult:
    status: str
    classification: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[Mapping[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "classification": self.classification,
            "facts": dict(self.facts),
            "errors": [dict(item) for item in self.errors],
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_yaml_bytes(data: bytes) -> dict[str, Any]:
    value = yaml.safe_load(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise P2AError("TASK_SCHEMA_INVALID", "Task must decode to a mapping")
    return value


def load_json_bytes(data: bytes, *, code: str = "RESULT_SCHEMA_INVALID") -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2AError(code, "Artifact is not valid UTF-8 JSON", error=str(exc)) from exc
    if not isinstance(value, dict):
        raise P2AError(code, "Artifact must decode to a mapping")
    return value


def read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def require_exact_keys(value: Mapping[str, Any], expected: set[str], *, code: str, location: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise P2AError(code, f"{location} fields do not match the frozen schema", missing=missing, unknown=unknown)


def require_sha256(value: Any, *, field_name: str, code: str = "TASK_SCHEMA_INVALID") -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise P2AError(code, f"{field_name} must be a lowercase SHA256", field=field_name, value=value)
    return value


def require_git_sha(value: Any, *, field_name: str, code: str = "TASK_SCHEMA_INVALID") -> str:
    if not isinstance(value, str) or not GIT_SHA_RE.fullmatch(value):
        raise P2AError(code, f"{field_name} must be a lowercase Git object ID", field=field_name, value=value)
    return value


def require_uuid(value: Any, *, field_name: str, code: str = "TASK_SCHEMA_INVALID") -> str:
    if not isinstance(value, str):
        raise P2AError(code, f"{field_name} must be a UUID string", field=field_name)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise P2AError(code, f"{field_name} must be a UUID string", field=field_name, value=value) from exc
    if str(parsed) != value.lower():
        raise P2AError(code, f"{field_name} must use canonical UUID form", field=field_name, value=value)
    return value


def fail_result(error: P2AError, *, facts: Mapping[str, Any] | None = None) -> VerificationResult:
    return VerificationResult("BLOCKED", error.code, facts or {}, (error.as_dict(),))
