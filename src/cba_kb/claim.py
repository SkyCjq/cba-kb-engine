"""Claim contract, lifecycle transitions, and evidence-bound relationship view.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverables D03 and D07:
- claim_id: deterministic hash
- claim_text: non-empty string
- supporting_statement_ids: list of statement_ids
- status: unverified | corroborated | contradicted | superseded | review_required
- evidence_refs: list of evidence refs
- review_reason: nullable string
- provenance: dict or str
- relation_assertion: nullable evidence-bound relation (D07)

Safety & Invariants:
- Claim is a statement-backed research assertion, NOT a Canonical Fact.
- Automatic extraction defaults to unverified or review_required.
- Promotion to corroborated / contradicted / superseded requires reviewed or deterministic authority.
- Lexical similarity or unreviewed inference alone CANNOT promote claim status.
- Claim/Statement auto-write to Fact count = 0.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from .actor import ActorError, validate_actor_ref
from .evidence_ledger import canonical_bytes
from .statement import validate_statement_id

SCHEMA_VERSION = 1
CLAIM_VERSION = "v1.9"

CLAIM_STATUSES = frozenset({
    "unverified",
    "corroborated",
    "contradicted",
    "superseded",
    "review_required",
})

RELATION_TYPES = frozenset({
    "mentor",
    "mentee",
    "coach_of",
    "coached_by",
    "teammate_of",
    "rival_of",
    "family_of",
})

_CLAIM_ID = re.compile(r"claim_[0-9a-f]{24}\Z")
_RELATION_ID = re.compile(r"rel_[0-9a-f]{24}\Z")


class ClaimError(RuntimeError):
    pass


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaimError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise ClaimError(f"{label}_INVALID")
    return value.strip()


def validate_claim_id(value: str) -> str:
    value = _required_text(value, "CLAIM_ID")
    if not _CLAIM_ID.fullmatch(value):
        raise ClaimError("CLAIM_ID_FORMAT_INVALID")
    return value


def validate_relation_id(value: str) -> str:
    value = _required_text(value, "RELATION_ID")
    if not _RELATION_ID.fullmatch(value):
        raise ClaimError("RELATION_ID_FORMAT_INVALID")
    return value


def generate_claim_id(
    supporting_statement_ids: List[str],
    claim_text: str,
    status: str,
) -> str:
    """Generate deterministic claim_id."""
    payload = {
        "claim_text": claim_text.strip(),
        "supporting_statement_ids": sorted(supporting_statement_ids),
        "status": status,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:24]
    return f"claim_{digest}"


def generate_relation_id(
    relation_type: str,
    from_actor: Dict[str, Any],
    to_actor: Dict[str, Any],
    supporting_statement_ids: List[str],
) -> str:
    """Generate deterministic relation_id."""
    payload = {
        "relation_type": relation_type,
        "from_kind": from_actor.get("kind"),
        "from_id": from_actor.get("id"),
        "from_name": from_actor.get("raw_name"),
        "to_kind": to_actor.get("kind"),
        "to_id": to_actor.get("id"),
        "to_name": to_actor.get("raw_name"),
        "supporting_statement_ids": sorted(supporting_statement_ids),
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:24]
    return f"rel_{digest}"


def validate_relation_assertion(relation: Dict[str, Any]) -> Dict[str, Any]:
    """Validate evidence-bound relationship view assertion."""
    if not isinstance(relation, dict):
        raise ClaimError("RELATION_OBJECT_REQUIRED")
    
    required = {"relation_id", "relation_type", "from_actor", "to_actor", "evidence_refs", "supporting_statement_ids", "confidence"}
    missing = required - set(relation)
    if missing:
        raise ClaimError(f"RELATION_FIELDS_MISSING:{sorted(missing)}")

    relation_id = validate_relation_id(relation["relation_id"])
    rtype = _required_text(relation["relation_type"], "RELATION_TYPE")
    if rtype not in RELATION_TYPES:
        raise ClaimError("RELATION_TYPE_INVALID")

    try:
        from_actor = validate_actor_ref(relation["from_actor"])
        to_actor = validate_actor_ref(relation["to_actor"])
    except ActorError as exc:
        raise ClaimError("RELATION_ACTOR_INVALID") from exc

    if not isinstance(relation["evidence_refs"], list) or not relation["evidence_refs"]:
        raise ClaimError("RELATION_EVIDENCE_REFS_REQUIRED")

    if not isinstance(relation["supporting_statement_ids"], list) or not relation["supporting_statement_ids"]:
        raise ClaimError("RELATION_SUPPORTING_STATEMENT_IDS_REQUIRED")

    for sid in relation["supporting_statement_ids"]:
        validate_statement_id(sid)

    confidence = _required_text(relation["confidence"], "CONFIDENCE")

    return {
        "relation_id": relation_id,
        "relation_type": rtype,
        "from_actor": from_actor,
        "to_actor": to_actor,
        "evidence_refs": relation["evidence_refs"],
        "supporting_statement_ids": sorted(set(relation["supporting_statement_ids"])),
        "confidence": confidence,
    }


def validate_claim(claim: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a claim dict."""
    if not isinstance(claim, dict):
        raise ClaimError("CLAIM_OBJECT_REQUIRED")

    required = {
        "claim_id",
        "claim_text",
        "supporting_statement_ids",
        "status",
        "evidence_refs",
        "review_reason",
        "provenance",
    }
    missing = required - set(claim)
    if missing:
        raise ClaimError(f"CLAIM_FIELDS_MISSING:{sorted(missing)}")

    claim_id = validate_claim_id(claim["claim_id"])
    claim_text = _required_text(claim["claim_text"], "CLAIM_TEXT")

    supporting_stmt_ids = claim["supporting_statement_ids"]
    if not isinstance(supporting_stmt_ids, list) or not supporting_stmt_ids:
        raise ClaimError("SUPPORTING_STATEMENT_IDS_LIST_REQUIRED")
    for sid in supporting_stmt_ids:
        validate_statement_id(sid)
    sorted_stmt_ids = sorted(set(supporting_stmt_ids))

    status = _required_text(claim["status"], "STATUS")
    if status not in CLAIM_STATUSES:
        raise ClaimError("CLAIM_STATUS_INVALID")

    evidence_refs = claim["evidence_refs"]
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise ClaimError("CLAIM_EVIDENCE_REFS_REQUIRED")

    review_reason = claim["review_reason"]
    if status == "review_required" and not review_reason:
        raise ClaimError("REVIEW_REASON_REQUIRED_FOR_REVIEW_REQUIRED_STATUS")

    provenance = claim["provenance"]
    if provenance in (None, "", {}):
        raise ClaimError("CLAIM_PROVENANCE_REQUIRED")

    rel_assertion = None
    if "relation_assertion" in claim and claim["relation_assertion"] is not None:
        rel_assertion = validate_relation_assertion(claim["relation_assertion"])

    result = {
        "claim_id": claim_id,
        "claim_text": claim_text,
        "supporting_statement_ids": sorted_stmt_ids,
        "status": status,
        "evidence_refs": evidence_refs,
        "review_reason": review_reason,
        "provenance": provenance,
    }
    if rel_assertion is not None:
        result["relation_assertion"] = rel_assertion
    return result


def create_claim_from_statements(
    claim_text: str,
    statements: List[Dict[str, Any]],
    *,
    status: Optional[str] = None,
    review_reason: Optional[str] = None,
    provenance: Optional[Any] = None,
    relation_assertion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a claim from supporting statements.

    Safety:
    - Automatically defaults to 'unverified' or 'review_required'.
    - If any supporting statement has extraction_status == 'review_required',
      the claim status defaults to 'review_required'.
    - Promotion to corroborated / contradicted / superseded requires explicit
      authority, not mere lexical/LLM inference.
    """
    if not statements:
        raise ClaimError("STATEMENTS_REQUIRED_FOR_CLAIM")

    stmt_ids = [s["statement_id"] for s in statements]
    evidence_refs = []
    has_review_req = False
    for s in statements:
        evidence_refs.append(s["evidence_ref"])
        if s.get("extraction_status") == "review_required":
            has_review_req = True

    if status is None:
        if has_review_req:
            status = "review_required"
            review_reason = review_reason or "SUPPORTING_STATEMENT_REVIEW_REQUIRED"
        else:
            status = "unverified"
    elif status in {"corroborated", "contradicted", "superseded"}:
        # Caller requested verified transition: must have explicit reviewed provenance
        if not isinstance(provenance, dict) or not provenance.get("reviewed_by"):
            raise ClaimError(f"STATUS_{status.upper()}_REQUIRES_REVIEWED_PROVENANCE")

    cid = generate_claim_id(stmt_ids, claim_text, status)
    prov = provenance or {
        "generated_at": "1970-01-01T00:00:00Z",
        "statement_count": len(statements),
    }

    claim_dict: Dict[str, Any] = {
        "claim_id": cid,
        "claim_text": claim_text,
        "supporting_statement_ids": stmt_ids,
        "status": status,
        "evidence_refs": evidence_refs,
        "review_reason": review_reason,
        "provenance": prov,
    }
    if relation_assertion:
        claim_dict["relation_assertion"] = relation_assertion

    return validate_claim(claim_dict)


def transition_claim_status(
    claim: Dict[str, Any],
    new_status: str,
    *,
    reviewed_by: str,
    resolution_note: str,
    additional_evidence_refs: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Safely transition claim status under human/deterministic reviewed authority.

    Lexical similarity or LLM judgment alone cannot promote status.
    """
    _required_text(reviewed_by, "REVIEWED_BY")
    _required_text(resolution_note, "RESOLUTION_NOTE")

    if new_status not in CLAIM_STATUSES:
        raise ClaimError("CLAIM_STATUS_INVALID")

    updated = dict(claim)
    updated["status"] = new_status
    if new_status != "review_required":
        updated["review_reason"] = None

    if additional_evidence_refs:
        updated["evidence_refs"] = updated["evidence_refs"] + additional_evidence_refs

    prov = dict(updated.get("provenance") or {})
    prov["reviewed_by"] = reviewed_by
    prov["resolution_note"] = resolution_note
    prov["previous_status"] = claim.get("status")
    updated["provenance"] = prov

    # Re-compute deterministic claim_id with new status
    updated["claim_id"] = generate_claim_id(
        updated["supporting_statement_ids"],
        updated["claim_text"],
        new_status,
    )
    return validate_claim(updated)
