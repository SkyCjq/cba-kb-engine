"""Person-ready Actor contract and resolution.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D02:
- kind = player | person | unresolved
- id = player_uid | person_uid | null
- raw_name
- evidence_ref

Rules:
- player => existing stable player_uid; does not mutate Player Registry.
- person => only when an already-authoritative person_uid exists; v1.9 does not create a full Person Registry.
- unresolved => raw_name + evidence_ref, id=null; unresolved is a valid result.
- same-name must never create identity.
- actor extraction may propose candidates; it may not make new final identity decisions.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from .player_identity import PlayerIdentityError, normalize_name, validate_player_uid

ACTOR_KINDS = frozenset({"player", "person", "unresolved"})
_PERSON_UID = re.compile(r"PERS-[A-Za-z0-9_-]{8,64}\Z")


class ActorError(RuntimeError):
    pass


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActorError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise ActorError(f"{label}_INVALID")
    return value.strip()


def validate_person_uid(value: str) -> str:
    value = _required_text(value, "PERSON_UID")
    if not _PERSON_UID.fullmatch(value):
        raise ActorError("PERSON_UID_FORMAT_INVALID")
    return value


def validate_actor_ref(actor_ref: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and return normalized actor_ref dictionary."""
    if not isinstance(actor_ref, dict):
        raise ActorError("ACTOR_REF_OBJECT_REQUIRED")
    
    required = {"kind", "id", "raw_name", "evidence_ref"}
    if not required <= set(actor_ref):
        raise ActorError(f"ACTOR_REF_FIELDS_MISSING:{sorted(required - set(actor_ref))}")
    
    extra = set(actor_ref) - required
    if extra:
        raise ActorError(f"ACTOR_REF_EXTRA_FIELDS:{sorted(extra)}")

    kind = _required_text(actor_ref["kind"], "ACTOR_KIND")
    if kind not in ACTOR_KINDS:
        raise ActorError("ACTOR_KIND_INVALID")

    raw_name = _required_text(actor_ref["raw_name"], "ACTOR_RAW_NAME")
    evidence_ref = actor_ref["evidence_ref"]
    if evidence_ref in (None, "", {}):
        raise ActorError("ACTOR_EVIDENCE_REF_REQUIRED")

    actor_id = actor_ref["id"]
    if kind == "player":
        if actor_id is None:
            raise ActorError("PLAYER_ID_REQUIRED")
        try:
            actor_id = validate_player_uid(actor_id)
        except PlayerIdentityError as exc:
            raise ActorError("PLAYER_UID_INVALID") from exc
    elif kind == "person":
        if actor_id is None:
            raise ActorError("PERSON_ID_REQUIRED")
        actor_id = validate_person_uid(actor_id)
    elif kind == "unresolved":
        if actor_id is not None:
            raise ActorError("UNRESOLVED_ACTOR_MUST_HAVE_NULL_ID")

    return {
        "kind": kind,
        "id": actor_id,
        "raw_name": raw_name,
        "evidence_ref": evidence_ref,
    }


def make_actor_ref(
    kind: str,
    raw_name: str,
    evidence_ref: Any,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct and validate an actor_ref dict."""
    return validate_actor_ref({
        "kind": kind,
        "id": actor_id,
        "raw_name": raw_name,
        "evidence_ref": evidence_ref,
    })


def resolve_actor(
    raw_name: str,
    *,
    evidence_ref: Any,
    identity_registry: Optional[Dict[str, Any]] = None,
    person_registry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve an actor name against available registries without guessing.

    Invariants:
    - Same-name must never create identity.
    - If multiple active players share the name, resolution fails to 'unresolved' (id=null).
    - If name is not found or ambiguous, returns 'unresolved' (id=null).
    - Never mutates input registries or canonical player truth.
    """
    cleaned_name = normalize_name(raw_name)
    
    if identity_registry:
        players = identity_registry.get("players", [])
        aliases = identity_registry.get("aliases", [])
        
        # Build exact name index for active players
        matching_uids = set()
        for p in players:
            if p.get("status") == "ACTIVE" and p.get("canonical_name") == cleaned_name:
                matching_uids.add(p["player_uid"])
        
        # Check aliases
        for a in aliases:
            if a.get("alias_name") == cleaned_name:
                uid = a.get("player_uid")
                # Ensure player is active
                for p in players:
                    if p.get("player_uid") == uid and p.get("status") == "ACTIVE":
                        matching_uids.add(uid)
                        break

        # Same-name check: must be unique!
        if len(matching_uids) == 1:
            return make_actor_ref(
                kind="player",
                actor_id=next(iter(matching_uids)),
                raw_name=raw_name,
                evidence_ref=evidence_ref,
            )
        elif len(matching_uids) > 1:
            # Ambiguous: multiple players with same name -> unresolved!
            return make_actor_ref(
                kind="unresolved",
                actor_id=None,
                raw_name=raw_name,
                evidence_ref=evidence_ref,
            )

    # Check person registry if provided
    if person_registry:
        persons = person_registry.get("persons", [])
        matching_person_uids = set()
        for pr in persons:
            if pr.get("canonical_name") == cleaned_name:
                matching_person_uids.add(pr["person_uid"])
        if len(matching_person_uids) == 1:
            return make_actor_ref(
                kind="person",
                actor_id=next(iter(matching_person_uids)),
                raw_name=raw_name,
                evidence_ref=evidence_ref,
            )
        elif len(matching_person_uids) > 1:
            return make_actor_ref(
                kind="unresolved",
                actor_id=None,
                raw_name=raw_name,
                evidence_ref=evidence_ref,
            )

    # Default fail-safe: unresolved non-player / unknown actor
    return make_actor_ref(
        kind="unresolved",
        actor_id=None,
        raw_name=raw_name,
        evidence_ref=evidence_ref,
    )
