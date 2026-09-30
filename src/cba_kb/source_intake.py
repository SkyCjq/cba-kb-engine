"""Source Intake Contract v1 and normalized document binding.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D05:
- source_provider: provider transport name
- source_item_id_or_locator: provider-specific item ID or path
- acquired_at: ISO 8601 timestamp
- media_type: MIME media type
- content_hash: sha256 hex of normalized content
- rights: rights classification dict
- provenance: provenance dictionary
- raw_ref: raw source reference
- normalized_document_ref: doc_id
- intake_status: accepted | review_required | rejected

Invariants:
- Provider-specific source transport must not become knowledge semantics.
- Deduplication is content/provenance aware, not provider-name based.
- Persist/normalize before Statement/Claim consumption (R5).
- Downstream Statement/Claim IDs depend strictly on normalized content, not provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import atomic
from .document_lane import content_hash as doc_content_hash, doc_id as compute_doc_id, normalize_text
from .document_mentions import validate_doc_id
from .evidence_ledger import canonical_bytes

SCHEMA_VERSION = 1
SOURCE_INTAKE_VERSION = "v1.9"

SOURCE_PROVIDERS = frozenset({
    "local_file",
    "google_drive_doc",
    "wechat_browser_clip",
    "ima_file_export",
    "synthetic_fixture",
})

INTAKE_STATUSES = frozenset({
    "accepted",
    "review_required",
    "rejected",
})

_HASH_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

NORMALIZED_DOCUMENT_FIELDS = frozenset({
    "schema_version",
    "doc_id",
    "content_hash",
    "normalized_text",
    "source_provider",
    "source_locator",
    "rights",
    "provenance",
    "acquired_at",
    "media_type",
})


class SourceIntakeError(RuntimeError):
    pass


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceIntakeError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise SourceIntakeError(f"{label}_INVALID")
    return value.strip()


def validate_content_hash(value: str) -> str:
    value = _required_text(value, "CONTENT_HASH")
    if not _HASH_SHA256.fullmatch(value):
        raise SourceIntakeError("CONTENT_HASH_SHA256_INVALID")
    return value


def validate_normalized_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate normalized document artifact produced before Statement consumption (R5)."""
    if not isinstance(doc, dict):
        raise SourceIntakeError("NORMALIZED_DOCUMENT_OBJECT_REQUIRED")

    missing = NORMALIZED_DOCUMENT_FIELDS - set(doc)
    if missing:
        raise SourceIntakeError(f"NORMALIZED_DOCUMENT_FIELDS_MISSING:{sorted(missing)}")

    doc_id = validate_doc_id(doc["doc_id"])
    content_hash = validate_content_hash(doc["content_hash"])
    text = doc["normalized_text"]
    if not isinstance(text, str):
        raise SourceIntakeError("NORMALIZED_TEXT_STRING_REQUIRED")

    expected_hash = doc_content_hash(text)
    if content_hash != expected_hash:
        raise SourceIntakeError(f"CONTENT_HASH_MISMATCH: expected {expected_hash} got {content_hash}")

    expected_doc_id = compute_doc_id(text)
    if doc_id != expected_doc_id:
        raise SourceIntakeError(f"DOC_ID_MISMATCH: expected {expected_doc_id} got {doc_id}")

    rights = doc["rights"]
    if not isinstance(rights, dict) or "classification" not in rights:
        raise SourceIntakeError("RIGHTS_OBJECT_REQUIRED")

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "content_hash": content_hash,
        "normalized_text": text,
        "source_provider": _required_text(doc["source_provider"], "SOURCE_PROVIDER"),
        "source_locator": _required_text(doc["source_locator"], "SOURCE_LOCATOR"),
        "rights": rights,
        "provenance": doc["provenance"],
        "acquired_at": _required_text(doc["acquired_at"], "ACQUIRED_AT"),
        "media_type": _required_text(doc["media_type"], "MEDIA_TYPE"),
    }


def make_normalized_document(
    doc_id: str,
    content_hash: str,
    normalized_text: str,
    *,
    source_provider: str,
    source_locator: str,
    rights: Dict[str, Any],
    provenance: Dict[str, Any],
    acquired_at: str,
    media_type: str = "text/plain",
) -> Dict[str, Any]:
    """Construct and validate a normalized document record."""
    return validate_normalized_document({
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "content_hash": content_hash,
        "normalized_text": normalized_text,
        "source_provider": source_provider,
        "source_locator": source_locator,
        "rights": rights,
        "provenance": provenance,
        "acquired_at": acquired_at,
        "media_type": media_type,
    })


def validate_source_intake(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a normalized source intake envelope dict."""
    if not isinstance(envelope, dict):
        raise SourceIntakeError("SOURCE_INTAKE_OBJECT_REQUIRED")

    required = {
        "source_provider",
        "source_item_id_or_locator",
        "acquired_at",
        "media_type",
        "content_hash",
        "rights",
        "provenance",
        "raw_ref",
        "normalized_document_ref",
        "intake_status",
    }
    missing = required - set(envelope)
    if missing:
        raise SourceIntakeError(f"SOURCE_INTAKE_FIELDS_MISSING:{sorted(missing)}")

    provider = _required_text(envelope["source_provider"], "SOURCE_PROVIDER")
    if provider not in SOURCE_PROVIDERS:
        raise SourceIntakeError("SOURCE_PROVIDER_INVALID")

    locator = _required_text(envelope["source_item_id_or_locator"], "SOURCE_LOCATOR")
    acquired_at = _required_text(envelope["acquired_at"], "ACQUIRED_AT")
    media_type = _required_text(envelope["media_type"], "MEDIA_TYPE")
    chash = validate_content_hash(envelope["content_hash"])

    rights = envelope["rights"]
    if not isinstance(rights, dict) or "classification" not in rights:
        raise SourceIntakeError("RIGHTS_OBJECT_REQUIRED")

    provenance = envelope["provenance"]
    if provenance in (None, "", {}):
        raise SourceIntakeError("PROVENANCE_REQUIRED")

    raw_ref = _required_text(str(envelope["raw_ref"]), "RAW_REF")

    doc_ref = envelope["normalized_document_ref"]
    if doc_ref is not None:
        doc_ref = validate_doc_id(doc_ref)

    status = _required_text(envelope["intake_status"], "INTAKE_STATUS")
    if status not in INTAKE_STATUSES:
        raise SourceIntakeError("INTAKE_STATUS_INVALID")

    result = {
        "source_provider": provider,
        "source_item_id_or_locator": locator,
        "acquired_at": acquired_at,
        "media_type": media_type,
        "content_hash": chash,
        "rights": rights,
        "provenance": provenance,
        "raw_ref": raw_ref,
        "normalized_document_ref": doc_ref,
        "intake_status": status,
    }
    if "normalized_document" in envelope and envelope["normalized_document"] is not None:
        result["normalized_document"] = validate_normalized_document(envelope["normalized_document"])

    return result


def normalize_source_payload(
    raw_content: bytes | str,
    *,
    source_provider: str,
    source_item_id_or_locator: str,
    media_type: str = "text/plain",
    rights: Optional[Dict[str, Any]] = None,
    acquired_at: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
    raw_ref: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Normalize raw intake payload into envelope and canonical normalized text.

    Decouples source provider details from canonical document semantics.
    Creates auditable normalized_document artifact (R5).
    """
    if isinstance(raw_content, bytes):
        if media_type == "application/pdf" or raw_content.startswith(b"%PDF-") or source_item_id_or_locator.lower().endswith(".pdf"):
            from .document_sources import parse_pdf
            res = parse_pdf(raw_content, source_locator=source_item_id_or_locator)
            text = res.get("text", "")
        else:
            try:
                text = raw_content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw_content.decode("gb18030", errors="replace")
    elif isinstance(raw_content, str):
        text = raw_content
    else:
        raise SourceIntakeError("RAW_CONTENT_BYTES_OR_STR_REQUIRED")

    norm_text = normalize_text(text)
    c_hash = doc_content_hash(norm_text)
    doc_id = compute_doc_id(norm_text)

    ts = acquired_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r_dict = rights or {
        "classification": "private",
        "public_export_allowed": False,
        "evidence": [],
    }
    prov = provenance or {
        "intake_version": SOURCE_INTAKE_VERSION,
        "provider": source_provider,
        "locator": source_item_id_or_locator,
    }

    norm_doc = make_normalized_document(
        doc_id=doc_id,
        content_hash=c_hash,
        normalized_text=norm_text,
        source_provider=source_provider,
        source_locator=source_item_id_or_locator,
        rights=r_dict,
        provenance=prov,
        acquired_at=ts,
        media_type=media_type,
    )

    envelope = validate_source_intake({
        "source_provider": source_provider,
        "source_item_id_or_locator": source_item_id_or_locator,
        "acquired_at": ts,
        "media_type": media_type,
        "content_hash": c_hash,
        "rights": r_dict,
        "provenance": prov,
        "raw_ref": raw_ref or source_item_id_or_locator,
        "normalized_document_ref": doc_id,
        "intake_status": "accepted" if norm_text.strip() else "review_required",
        "normalized_document": norm_doc,
    })

    return envelope, norm_text


def save_normalized_document(path: Path | str, doc: Dict[str, Any]) -> None:
    validated = validate_normalized_document(doc)
    atomic(Path(path), json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def load_normalized_document(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise SourceIntakeError(f"NORMALIZED_DOCUMENT_FILE_NOT_FOUND:{path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return validate_normalized_document(data)
