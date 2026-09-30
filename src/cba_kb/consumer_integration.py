"""Consumer-safe materialization and rights projection for Statements and Claims.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D08:
- Rights fail-closed: Private evidence and unauthorized material must not leak to consumer projections.
- Target-specific rights filtering: Public export allowed only when classification == 'public'
  and public_export_allowed is True with valid evidence, AND explicitly authorized for target.
- Capability advertised as MATERIALIZED or NOT_MATERIALIZED(reason).
- Non-blocking: Missing Statement/Claim capability does not affect existing Facts/Identity/Documents/Events/Profile.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .claim import validate_claim
from .consumer_package import (
    CONSUMER_TARGETS,
    TARGET_SLUGS,
    ConsumerPackageError,
    normalize_authorizations,
)
from .statement import validate_statement

CONSUMER_CAPABILITY_STATEMENT_CLAIM = "statement_claim_research"

_LOCAL_PATH_RE = re.compile(r"(?:/(?:Users|home|root|private|var|tmp)/[^\s,;\"'\]]+|file://[^\s,;\"'\]]+)")
_DRIVE_LOCATOR_RE = re.compile(r"https?://(?:docs|drive)\.google\.com/[^\s,;\"'\]]+")
_RAW_DRIVE_ID_RE = re.compile(r"\b[0-9a-zA-Z_-]{28,50}\b")


class ConsumerSafetyError(RuntimeError):
    pass


def sanitize_locator_string(
    locator: str,
    doc_id: str,
    target_slug: str,
) -> str:
    """Sanitize locator string to remove local paths and private Drive locators."""
    if not isinstance(locator, str):
        return f"consumer:{target_slug}:{doc_id}"
    cleaned = locator
    if _LOCAL_PATH_RE.search(cleaned) or _DRIVE_LOCATOR_RE.search(cleaned):
        return f"consumer:{target_slug}:{doc_id}"
    # Check if string is a raw private drive ID
    if _RAW_DRIVE_ID_RE.fullmatch(cleaned.strip()):
        return f"consumer:{target_slug}:{doc_id}"
    return cleaned


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


def sanitize_provenance(prov: Any, doc_id: str = "unknown", target_slug: str = "consumer") -> Any:
    """Sanitize provenance to strip raw absolute filesystem paths or private keys."""
    if isinstance(prov, str):
        if _LOCAL_PATH_RE.search(prov) or _DRIVE_LOCATOR_RE.search(prov) or _RAW_DRIVE_ID_RE.search(prov):
            return f"consumer:{target_slug}:{doc_id}"
        return prov
    elif isinstance(prov, dict):
        sanitized = {}
        for k, v in prov.items():
            if k in {"private_path", "raw_ref", "credentials", "token", "file_id", "drive_id"}:
                continue
            sanitized[k] = sanitize_provenance(v, doc_id=doc_id, target_slug=target_slug)
        return sanitized
    elif isinstance(prov, list):
        return [sanitize_provenance(x, doc_id=doc_id, target_slug=target_slug) for x in prov]
    return prov


def sanitize_actor_for_consumer(
    actor: Dict[str, Any],
    doc_id: str,
    target_slug: str,
) -> Dict[str, Any]:
    """Scrub local paths or private evidence locators from actor ref."""
    clean_actor = dict(actor)
    if "evidence_ref" in clean_actor:
        clean_actor["evidence_ref"] = sanitize_locator_string(
            str(clean_actor["evidence_ref"]), doc_id, target_slug
        )
    if "provenance" in clean_actor:
        clean_actor["provenance"] = sanitize_provenance(
            clean_actor["provenance"], doc_id=doc_id, target_slug=target_slug
        )
    return clean_actor


def project_statements_for_consumer(
    statements: List[Dict[str, Any]],
    target: str = "ChatGPT",
    authorizations: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Project statements safely for consumer consumption under target authorization.

    Returns (projected_statements, capability_status).
    """
    if target not in CONSUMER_TARGETS:
        raise ConsumerSafetyError(
            f"UNAUTHORIZED_TARGET: target '{target}' is not in authorized targets {CONSUMER_TARGETS}"
        )

    target_slug = TARGET_SLUGS.get(target, "consumer")

    if authorizations is None:
        # Fail closed when authorization is absent (R1)
        return [], "NOT_MATERIALIZED(NO_TARGET_AUTHORIZATIONS_PROVIDED)"

    try:
        auth_map = normalize_authorizations(authorizations)
    except ConsumerPackageError as exc:
        raise ConsumerSafetyError(f"INVALID_AUTHORIZATIONS: {exc}") from exc

    if not auth_map:
        return [], "NOT_MATERIALIZED(EMPTY_TARGET_AUTHORIZATIONS)"

    exported: List[Dict[str, Any]] = []

    for s in statements:
        validated = validate_statement(s)
        doc_id = validated["doc_id"]

        # 1. Target authorization check
        if (target, doc_id) not in auth_map:
            continue

        # 2. Public exportability check
        if not is_statement_publicly_exportable(validated):
            continue

        clean_s = dict(validated)

        # 3. Sanitize all locators and provenance (R1)
        clean_s["source_ref"] = sanitize_locator_string(clean_s.get("source_ref", ""), doc_id, target_slug)
        clean_s["evidence_ref"] = sanitize_locator_string(clean_s.get("evidence_ref", ""), doc_id, target_slug)
        clean_s["provenance"] = sanitize_provenance(clean_s.get("provenance"), doc_id=doc_id, target_slug=target_slug)

        if "speaker_actor_ref" in clean_s:
            clean_s["speaker_actor_ref"] = sanitize_actor_for_consumer(
                clean_s["speaker_actor_ref"], doc_id, target_slug
            )

        if "subject_actor_refs" in clean_s:
            clean_s["subject_actor_refs"] = [
                sanitize_actor_for_consumer(sub, doc_id, target_slug)
                for sub in clean_s["subject_actor_refs"]
            ]

        exported.append(clean_s)

    if exported:
        capability = "MATERIALIZED"
    else:
        capability = "NOT_MATERIALIZED(NO_AUTHORIZED_PUBLIC_STATEMENT_EVIDENCE)"

    return exported, capability


def project_claims_for_consumer(
    claims: List[Dict[str, Any]],
    statements: List[Dict[str, Any]],
    target: str = "ChatGPT",
    authorizations: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Project claims safely for consumer consumption under target authorization."""
    if target not in CONSUMER_TARGETS:
        raise ConsumerSafetyError(
            f"UNAUTHORIZED_TARGET: target '{target}' is not in authorized targets {CONSUMER_TARGETS}"
        )

    target_slug = TARGET_SLUGS.get(target, "consumer")

    if authorizations is None:
        return [], "NOT_MATERIALIZED(NO_TARGET_AUTHORIZATIONS_PROVIDED)"

    # Pre-project statements for this target
    exported_stmts, _ = project_statements_for_consumer(
        statements, target=target, authorizations=authorizations
    )
    exported_stmt_ids = {s["statement_id"] for s in exported_stmts}

    exported: List[Dict[str, Any]] = []

    for c in claims:
        validated = validate_claim(c)
        supporting_ids = validated.get("supporting_statement_ids") or []
        if not supporting_ids:
            continue

        # A claim is exportable only if ALL supporting statements are authorized and exported
        if not all(sid in exported_stmt_ids for sid in supporting_ids):
            continue

        clean_c = dict(validated)

        # Sanitize supporting evidence refs
        raw_evidence_refs = clean_c.get("supporting_evidence_refs") or []
        clean_c["supporting_evidence_refs"] = [
            sanitize_locator_string(str(ref), "claim", target_slug)
            for ref in raw_evidence_refs
        ]

        clean_c["provenance"] = sanitize_provenance(clean_c.get("provenance"), doc_id="claim", target_slug=target_slug)

        # Sanitize status history
        if "status_history" in clean_c:
            cleaned_history = []
            for h in clean_c["status_history"]:
                if isinstance(h, dict):
                    ch = dict(h)
                    if "transition_authority" in ch and isinstance(ch["transition_authority"], dict):
                        auth = dict(ch["transition_authority"])
                        if "supporting_evidence_refs" in auth:
                            auth["supporting_evidence_refs"] = [
                                sanitize_locator_string(str(r), "claim", target_slug)
                                for r in auth.get("supporting_evidence_refs", [])
                            ]
                        ch["transition_authority"] = auth
                    cleaned_history.append(ch)
                else:
                    cleaned_history.append(h)
            clean_c["status_history"] = cleaned_history

        exported.append(clean_c)

    if exported:
        capability = "MATERIALIZED"
    else:
        capability = "NOT_MATERIALIZED(NO_AUTHORIZED_PUBLIC_CLAIM_EVIDENCE)"

    return exported, capability
