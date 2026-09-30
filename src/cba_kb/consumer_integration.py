"""Consumer-safe materialization and rights projection for Statements and Claims.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D08:
- Rights fail-closed: Private evidence and unauthorized material must not leak to consumer projections.
- Target-specific rights filtering: Public export allowed only when classification == 'public'
  and public_export_allowed is True with valid evidence.
- Capability advertised as MATERIALIZED or NOT_MATERIALIZED(reason).
- Non-blocking: Missing Statement/Claim capability does not affect existing Facts/Identity/Documents/Events/Profile.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .claim import validate_claim
from .document_lane import assert_public_export_allowed
from .statement import validate_statement

CONSUMER_CAPABILITY_STATEMENT_CLAIM = "statement_claim_research"


class ConsumerSafetyError(RuntimeError):
    pass


def is_statement_publicly_exportable(statement: Dict[str, Any]) -> bool:
    """Check if statement satisfies fail-closed public export rules."""
    rights = statement.get("rights") or {}
    if not isinstance(rights, dict):
        return False
    classification = rights.get("classification")
    allowed = rights.get("public_export_allowed")
    evidence = rights.get("evidence")
    return classification == "public" and allowed is True and bool(evidence)


def is_claim_publicly_exportable(
    claim: Dict[str, Any],
    statement_map: Dict[str, Dict[str, Any]],
) -> bool:
    """A claim is publicly exportable only if all its supporting statements are publicly exportable."""
    supporting_ids = claim.get("supporting_statement_ids") or []
    if not supporting_ids:
        return False
    for sid in supporting_ids:
        stmt = statement_map.get(sid)
        if not stmt or not is_statement_publicly_exportable(stmt):
            return False
    return True


def sanitize_provenance(prov: Any) -> Any:
    """Sanitize provenance to strip raw absolute filesystem paths or private keys."""
    if isinstance(prov, str):
        # Scrub local machine path prefixes if any
        return re.sub(r"/(?:Users|home|root)/[^/\s]+/[^\s]+", "[REDACTED_LOCAL_PATH]", prov)
    elif isinstance(prov, dict):
        sanitized = {}
        for k, v in prov.items():
            if k in {"private_path", "raw_ref", "credentials", "token"}:
                continue
            sanitized[k] = sanitize_provenance(v)
        return sanitized
    elif isinstance(prov, list):
        return [sanitize_provenance(x) for x in prov]
    return prov


def project_statements_for_consumer(
    statements: List[Dict[str, Any]],
    target: str = "ChatGPT",
) -> Tuple[List[Dict[str, Any]], str]:
    """Project statements safely for consumer consumption.

    Returns (projected_statements, capability_status).
    """
    exported: List[Dict[str, Any]] = []
    
    for s in statements:
        validated = validate_statement(s)
        if is_statement_publicly_exportable(validated):
            clean_s = dict(validated)
            clean_s["provenance"] = sanitize_provenance(clean_s.get("provenance"))
            exported.append(clean_s)

    if exported:
        capability = "MATERIALIZED"
    else:
        capability = "NOT_MATERIALIZED(NO_PUBLIC_STATEMENT_EVIDENCE)"

    return exported, capability


def project_claims_for_consumer(
    claims: List[Dict[str, Any]],
    statements: List[Dict[str, Any]],
    target: str = "ChatGPT",
) -> Tuple[List[Dict[str, Any]], str]:
    """Project claims safely for consumer consumption."""
    stmt_map = {s["statement_id"]: validate_statement(s) for s in statements}
    exported: List[Dict[str, Any]] = []

    for c in claims:
        validated = validate_claim(c)
        if is_claim_publicly_exportable(validated, stmt_map):
            clean_c = dict(validated)
            clean_c["provenance"] = sanitize_provenance(clean_c.get("provenance"))
            exported.append(clean_c)

    if exported:
        capability = "MATERIALIZED"
    else:
        capability = "NOT_MATERIALIZED(NO_PUBLIC_CLAIM_EVIDENCE)"

    return exported, capability
