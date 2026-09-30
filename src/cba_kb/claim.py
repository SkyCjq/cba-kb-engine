"""Claim contract, lifecycle transitions, and evidence-bound relationship view.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverables D03 and D07:
- claim_id: stable deterministic hash independent of mutable status
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
- Promotion to corroborated / contradicted / superseded requires evidence-bound
  reviewed or deterministic authority; free-form reviewer string or LLM self-assertion fails closed.
- Claim ID remains stable across status transitions.
- Claim/Statement auto-write to Fact count = 0.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
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

STRONG_CLAIM_STATUSES = frozenset({
    "corroborated",
    "contradicted",
    "superseded",
})

VALID_AUTHORITY_KINDS = frozenset({
    "human_review",
    "deterministic_rule",
})

FORBIDDEN_AUTHORITY_KINDS = frozenset({
    "llm_self_asserted",
    "unreviewed_inference",
    "model_prediction",
    "heuristic",
    "unreviewed",
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

TRANSITION_AUTHORITY_FIELDS = frozenset({
    "decision_ref",
    "authority_kind",
    "reviewer",
    "prior_claim_id",
    "prior_status",
    "target_status",
    "supporting_evidence_refs",
})


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
) -> str:
    """Generate stable deterministic claim_id across lifecycle (R8)."""
    payload = {
        "claim_text": claim_text.strip(),
        "supporting_statement_ids": sorted(set(supporting_statement_ids)),
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
        "supporting_statement_ids": sorted(set(supporting_statement_ids)),
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:24]
    return f"rel_{digest}"


def validate_relation_assertion(relation: Dict[str, Any]) -> Dict[str, Any]:
    """Validate evidence-bound relationship view assertion."""
    if not isinstance(relation, dict):
        raise ClaimError("RELATION_OBJECT_REQUIRED")
    
    required = {
        "relation_id", "relation_type", "from_actor", "to_actor",
        "evidence_refs", "supporting_statement_ids", "confidence"
    }
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


def validate_transition_authority(
    authority: Dict[str, Any],
    claim: Dict[str, Any],
    target_status: str,
) -> Dict[str, Any]:
    """Validate evidence-bound transition authority (R4)."""
    if not isinstance(authority, dict):
        raise ClaimError("TRANSITION_AUTHORITY_OBJECT_REQUIRED")

    missing = TRANSITION_AUTHORITY_FIELDS - set(authority)
    if missing:
        raise ClaimError(f"TRANSITION_AUTHORITY_FIELDS_MISSING:{sorted(missing)}")

    decision_ref = _required_text(authority["decision_ref"], "DECISION_REF")
    authority_kind = _required_text(authority["authority_kind"], "AUTHORITY_KIND")

    if authority_kind in FORBIDDEN_AUTHORITY_KINDS or authority_kind not in VALID_AUTHORITY_KINDS:
        raise ClaimError(f"UNAUTHORIZED_AUTHORITY_KIND:{authority_kind}")

    reviewer = _required_text(authority["reviewer"], "REVIEWER")
    if reviewer.lower() in {"llm_self_asserted", "unreviewed", "none", "unknown"}:
        raise ClaimError(f"INVALID_REVIEWER_AUTHORITY:{reviewer}")

    prior_claim_id = _required_text(authority["prior_claim_id"], "PRIOR_CLAIM_ID")
    if prior_claim_id != claim.get("claim_id"):
        raise ClaimError("PRIOR_CLAIM_ID_MISMATCH")

    prior_status = _required_text(authority["prior_status"], "PRIOR_STATUS")
    if prior_status != claim.get("status"):
        raise ClaimError("PRIOR_STATUS_MISMATCH")

    auth_target = _required_text(authority["target_status"], "TARGET_STATUS")
    if auth_target != target_status:
        raise ClaimError("TARGET_STATUS_MISMATCH")

    ev_refs = authority["supporting_evidence_refs"]
    if not isinstance(ev_refs, list) or not ev_refs:
        raise ClaimError("SUPPORTING_EVIDENCE_REFS_REQUIRED")

    timestamp = authority.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "decision_ref": decision_ref,
        "authority_kind": authority_kind,
        "reviewer": reviewer,
        "prior_claim_id": prior_claim_id,
        "prior_status": prior_status,
        "target_status": target_status,
        "supporting_evidence_refs": ev_refs,
        "timestamp": timestamp,
        "note": authority.get("note", ""),
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

    if status in STRONG_CLAIM_STATUSES:
        if not isinstance(provenance, dict):
            raise ClaimError("CLAIM_PROVENANCE_OBJECT_REQUIRED_FOR_STRONG_STATUS")
        transitions = provenance.get("transitions")
        latest_auth = provenance.get("latest_transition_authority") or provenance.get("transition_authority")
        if not transitions and not latest_auth:
            raise ClaimError(f"STRONG_STATUS_{status.upper()}_REQUIRES_PERSISTED_TRANSITION_AUTHORITY")
        found_target = False
        if isinstance(transitions, list):
            for t in transitions:
                if isinstance(t, dict) and t.get("to_status") == status and isinstance(t.get("authority"), dict):
                    found_target = True
                    break
        if not found_target and isinstance(latest_auth, dict) and latest_auth.get("target_status") == status:
            found_target = True
        if not found_target:
            raise ClaimError(f"STRONG_STATUS_{status.upper()}_TRANSITION_AUTHORITY_CHAIN_INVALID")

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
    transition_authority: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a claim from supporting statements.

    Safety:
    - Automatically defaults to 'unverified' or 'review_required'.
    - If any supporting statement has extraction_status == 'review_required',
      the claim status defaults to 'review_required'.
    - Strong statuses (corroborated, contradicted, superseded) require an explicit
      evidence-bound transition authority object.
    - Free-form reviewer text or LLM self-assertion fails closed.
    - Claim ID is stable across lifecycle (R8).
    - Persists auditable transition authority in provenance (G4-R2).
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

    # Stable claim_id independent of mutable status (R8)
    cid = generate_claim_id(stmt_ids, claim_text)

    prov: Dict[str, Any] = dict(provenance) if isinstance(provenance, dict) else {}
    prov.setdefault("generated_at", "1970-01-01T00:00:00Z")
    prov.setdefault("statement_count", len(statements))

    if status is None:
        if has_review_req:
            status = "review_required"
            review_reason = review_reason or "SUPPORTING_STATEMENT_REVIEW_REQUIRED"
        else:
            status = "unverified"
    elif status in STRONG_CLAIM_STATUSES:
        if not transition_authority:
            raise ClaimError(f"STATUS_{status.upper()}_REQUIRES_TRANSITION_AUTHORITY")
        # Validate authority against pre-state unverified
        prior_state = {
            "claim_id": cid,
            "status": "unverified",
        }
        val_auth = validate_transition_authority(transition_authority, prior_state, status)
        prov["transition_authority"] = val_auth
        prov["latest_transition_authority"] = val_auth
        transitions = list(prov.get("transitions", []))
        transitions.append({
            "from_status": "unverified",
            "to_status": status,
            "authority": val_auth,
        })
        prov["transitions"] = transitions
        prov["reviewed_by"] = val_auth.get("reviewer")

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
    transition_authority: Optional[Dict[str, Any]] = None,
    reviewed_by: Optional[str] = None,
    resolution_note: Optional[str] = None,
    additional_evidence_refs: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Safely transition claim status under evidence-bound reviewed authority (R4, R8).

    - Free-form reviewer string alone is rejected; explicit transition_authority object required.
    - Preserves stable claim_id.
    - Records transition in provenance.
    """
    if new_status not in CLAIM_STATUSES:
        raise ClaimError("CLAIM_STATUS_INVALID")

    if transition_authority is None:
        raise ClaimError("TRANSITION_AUTHORITY_REQUIRED: Free-form reviewer string alone rejected without transition authority object")

    validated_auth = validate_transition_authority(transition_authority, claim, new_status)

    updated = dict(claim)
    old_status = claim["status"]
    updated["status"] = new_status
    if new_status != "review_required":
        updated["review_reason"] = None

    if additional_evidence_refs:
        updated["evidence_refs"] = updated["evidence_refs"] + additional_evidence_refs

    prov = dict(updated.get("provenance") or {})
    transitions = list(prov.get("transitions", []))
    transitions.append({
        "from_status": old_status,
        "to_status": new_status,
        "authority": validated_auth,
    })
    prov["transitions"] = transitions
    prov["latest_transition_authority"] = validated_auth
    prov["transition_authority"] = validated_auth
    prov["reviewed_by"] = reviewed_by or validated_auth.get("reviewer")
    if resolution_note:
        prov["resolution_note"] = resolution_note
    updated["provenance"] = prov

    # Claim ID remains stable across lifecycle (R8)
    return validate_claim(updated)
