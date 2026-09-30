"""Verification Queue fail-safe for unresolved semantics.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D04:
- verification_id: deterministic hash
- object_type: claim | statement | actor | source | derived_signal
- object_ref: str | dict
- reason_code: str
- evidence_refs: list
- status: open | resolved
- resolution_ref: nullable
- created_from: str | dict

Invariants:
- Unresolved actors, ambiguous attributions, uncertain sources, ambiguous claims
  or derived signals are routed here rather than being silently guessed or dropped.
- No automatic resolution may create identity/business/fact truth.
- Sorting and serialization are deterministic.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import atomic
from .evidence_ledger import canonical_bytes

SCHEMA_VERSION = 1
VERIFICATION_QUEUE_VERSION = "v1.9"

OBJECT_TYPES = frozenset({
    "claim",
    "statement",
    "actor",
    "source",
    "derived_signal",
})

VERIFICATION_STATUSES = frozenset({
    "open",
    "resolved",
})

_VERIFICATION_ID = re.compile(r"vq_[0-9a-f]{24}\Z")


class VerificationQueueError(RuntimeError):
    pass


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationQueueError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise VerificationQueueError(f"{label}_INVALID")
    return value.strip()


def validate_verification_id(value: str) -> str:
    value = _required_text(value, "VERIFICATION_ID")
    if not _VERIFICATION_ID.fullmatch(value):
        raise VerificationQueueError("VERIFICATION_ID_FORMAT_INVALID")
    return value


def generate_verification_id(
    object_type: str,
    object_ref: Any,
    reason_code: str,
    evidence_refs: List[Any],
) -> str:
    """Generate deterministic verification_id."""
    payload = {
        "object_type": object_type,
        "object_ref": object_ref,
        "reason_code": reason_code,
        "evidence_refs": evidence_refs,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:24]
    return f"vq_{digest}"


def validate_verification_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a verification queue item dict."""
    if not isinstance(item, dict):
        raise VerificationQueueError("VERIFICATION_ITEM_OBJECT_REQUIRED")

    required = {
        "verification_id",
        "object_type",
        "object_ref",
        "reason_code",
        "evidence_refs",
        "status",
        "resolution_ref",
        "created_from",
    }
    missing = required - set(item)
    if missing:
        raise VerificationQueueError(f"VERIFICATION_FIELDS_MISSING:{sorted(missing)}")

    verification_id = validate_verification_id(item["verification_id"])
    object_type = _required_text(item["object_type"], "OBJECT_TYPE")
    if object_type not in OBJECT_TYPES:
        raise VerificationQueueError("OBJECT_TYPE_INVALID")

    object_ref = item["object_ref"]
    if object_ref in (None, "", {}):
        raise VerificationQueueError("OBJECT_REF_REQUIRED")

    reason_code = _required_text(item["reason_code"], "REASON_CODE")
    if not re.fullmatch(r"[A-Z0-9_]+\Z", reason_code):
        raise VerificationQueueError("REASON_CODE_FORMAT_INVALID")

    evidence_refs = item["evidence_refs"]
    if not isinstance(evidence_refs, list):
        raise VerificationQueueError("EVIDENCE_REFS_LIST_REQUIRED")

    status = _required_text(item["status"], "STATUS")
    if status not in VERIFICATION_STATUSES:
        raise VerificationQueueError("STATUS_INVALID")

    resolution_ref = item["resolution_ref"]
    if status == "open" and resolution_ref is not None:
        raise VerificationQueueError("OPEN_ITEM_CANNOT_HAVE_RESOLUTION_REF")
    if status == "resolved" and resolution_ref is None:
        raise VerificationQueueError("RESOLVED_ITEM_MUST_HAVE_RESOLUTION_REF")

    created_from = item["created_from"]
    if created_from in (None, "", {}):
        raise VerificationQueueError("CREATED_FROM_REQUIRED")

    return {
        "verification_id": verification_id,
        "object_type": object_type,
        "object_ref": object_ref,
        "reason_code": reason_code,
        "evidence_refs": evidence_refs,
        "status": status,
        "resolution_ref": resolution_ref,
        "created_from": created_from,
    }


class VerificationQueue:
    """In-memory and file-backed verification queue ledger."""

    def __init__(self, items: Optional[List[Dict[str, Any]]] = None):
        self._items: Dict[str, Dict[str, Any]] = {}
        if items:
            for item in items:
                self.add(item)

    def add(self, item: Dict[str, Any]) -> str:
        """Add a validated item to the queue (idempotent deduplication)."""
        validated = validate_verification_item(item)
        vid = validated["verification_id"]
        self._items[vid] = validated
        return vid

    def create_and_add(
        self,
        object_type: str,
        object_ref: Any,
        reason_code: str,
        evidence_refs: List[Any],
        created_from: Any,
        status: str = "open",
        resolution_ref: Optional[Any] = None,
    ) -> str:
        """Helper to create and add a new verification item."""
        vid = generate_verification_id(object_type, object_ref, reason_code, evidence_refs)
        item = {
            "verification_id": vid,
            "object_type": object_type,
            "object_ref": object_ref,
            "reason_code": reason_code,
            "evidence_refs": evidence_refs,
            "status": status,
            "resolution_ref": resolution_ref,
            "created_from": created_from,
        }
        return self.add(item)

    def get(self, verification_id: str) -> Optional[Dict[str, Any]]:
        return self._items.get(verification_id)

    def list_items(
        self,
        *,
        object_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return sorted items matching filter criteria."""
        results = []
        for item in self._items.values():
            if object_type and item["object_type"] != object_type:
                continue
            if status and item["status"] != status:
                continue
            results.append(item)
        # Deterministic sorting
        return sorted(results, key=lambda x: (x["status"], x["object_type"], x["verification_id"]))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": VERIFICATION_QUEUE_VERSION,
            "items": self.list_items(),
            "count": len(self._items),
        }

    def save(self, path: Path | str) -> None:
        payload = self.to_dict()
        atomic(Path(path), json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")

    @classmethod
    def load(cls, path: Path | str) -> "VerificationQueue":
        p = Path(path)
        if not p.is_file():
            raise VerificationQueueError(f"VERIFICATION_QUEUE_FILE_NOT_FOUND:{path}")
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else data
        return cls(items)
