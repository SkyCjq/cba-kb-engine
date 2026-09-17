"""Deterministic player identity relations over canonical record keys.

This module never mutates canonical registration facts.  Player identifiers are
caller-supplied opaque values; no permanent identifier generator is provided.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .common import atomic, lock
from .evidence_ledger import canonical_bytes


SCHEMA_VERSION = 1
IDENTITY_VERSION = "v1.8"
LOOKUP_MODE = "EXACT_NAME_OR_ALIAS_ONLY"
PLAYER_STATUSES = frozenset({"ACTIVE", "REDIRECTED"})
LINK_STATUSES = frozenset({"same", "not_same", "undecided"})
LINK_METHODS = frozenset({
    "EXTERNAL_IDENTIFIER",
    "SOURCE_DECLARED",
    "MANUAL_REVIEW",
    "REVIEW_PENDING",
})
CONFIDENCE_LEVELS = frozenset({"HIGH", "MEDIUM", "LOW", "UNKNOWN"})
ALIAS_TYPES = frozenset({
    "NAME_VARIANT",
    "OCR_CORRUPTION",
    "TRANSLITERATION",
    "OTHER",
})
_PLAYER_UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{15,127}\Z")


class PlayerIdentityError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PlayerIdentityError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise PlayerIdentityError(f"{label}_INVALID")
    return value.strip()


def _required_token(value, label):
    value = _required_text(value, label)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        raise PlayerIdentityError(f"{label}_INVALID")
    return value


def normalize_name(value):
    """Normalize only presentation, never infer identity or apply fuzzy logic."""
    text = unicodedata.normalize("NFKC", _required_text(value, "NAME"))
    return " ".join(text.split())


def validate_player_uid(value):
    value = _required_text(value, "PLAYER_UID")
    if not _PLAYER_UID.fullmatch(value):
        raise PlayerIdentityError("PLAYER_UID_OPAQUE_FORMAT_REQUIRED")
    return value


def _evidence_refs(value, label, *, required):
    if not isinstance(value, list):
        raise PlayerIdentityError(f"{label}_LIST_REQUIRED")
    refs = sorted({
        _required_text(item, f"{label}_ITEM")
        for item in value
    })
    if required and not refs:
        raise PlayerIdentityError(f"{label}_REQUIRED")
    return refs


def _validate_player(value):
    if not isinstance(value, dict):
        raise PlayerIdentityError("PLAYER_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "player_uid",
        "canonical_name",
        "status",
        "redirect_to",
    }
    if set(value) != required:
        raise PlayerIdentityError("PLAYER_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerIdentityError("PLAYER_SCHEMA_VERSION_INVALID")
    player_uid = validate_player_uid(value["player_uid"])
    name = normalize_name(value["canonical_name"])
    status = _required_token(value["status"], "PLAYER_STATUS")
    if status not in PLAYER_STATUSES:
        raise PlayerIdentityError("PLAYER_STATUS_INVALID")
    redirect_to = value["redirect_to"]
    if status == "ACTIVE":
        if redirect_to is not None:
            raise PlayerIdentityError("ACTIVE_PLAYER_REDIRECT_FORBIDDEN")
    else:
        redirect_to = validate_player_uid(redirect_to)
        if redirect_to == player_uid:
            raise PlayerIdentityError("PLAYER_SELF_REDIRECT_FORBIDDEN")
    return {
        "schema_version": SCHEMA_VERSION,
        "player_uid": player_uid,
        "canonical_name": name,
        "status": status,
        "redirect_to": redirect_to,
    }


def _validate_alias(value):
    if not isinstance(value, dict):
        raise PlayerIdentityError("PLAYER_ALIAS_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "player_uid",
        "alias",
        "alias_type",
        "evidence_refs",
    }
    if set(value) != required:
        raise PlayerIdentityError("PLAYER_ALIAS_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerIdentityError("PLAYER_ALIAS_SCHEMA_VERSION_INVALID")
    alias_type = _required_token(value["alias_type"], "ALIAS_TYPE")
    if alias_type not in ALIAS_TYPES:
        raise PlayerIdentityError("ALIAS_TYPE_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "player_uid": validate_player_uid(value["player_uid"]),
        "alias": normalize_name(value["alias"]),
        "alias_type": alias_type,
        "evidence_refs": _evidence_refs(
            value["evidence_refs"], "ALIAS_EVIDENCE_REFS", required=True,
        ),
    }


def _validate_record_link(value):
    if not isinstance(value, dict):
        raise PlayerIdentityError("PLAYER_RECORD_LINK_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "record_key",
        "player_uid",
        "link_status",
        "method",
        "confidence",
        "evidence_refs",
    }
    if set(value) != required:
        raise PlayerIdentityError("PLAYER_RECORD_LINK_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerIdentityError("PLAYER_RECORD_LINK_SCHEMA_VERSION_INVALID")
    status = _required_text(value["link_status"], "LINK_STATUS")
    if status not in LINK_STATUSES:
        raise PlayerIdentityError("LINK_STATUS_INVALID")
    method = _required_token(value["method"], "LINK_METHOD")
    if method not in LINK_METHODS:
        raise PlayerIdentityError("LINK_METHOD_INVALID")
    confidence = _required_token(value["confidence"], "LINK_CONFIDENCE")
    if confidence not in CONFIDENCE_LEVELS:
        raise PlayerIdentityError("LINK_CONFIDENCE_INVALID")
    if method == "EXTERNAL_IDENTIFIER" and status != "same":
        raise PlayerIdentityError("EXTERNAL_IDENTIFIER_SAME_REQUIRED")
    return {
        "schema_version": SCHEMA_VERSION,
        "record_key": _required_text(value["record_key"], "RECORD_KEY"),
        "player_uid": validate_player_uid(value["player_uid"]),
        "link_status": status,
        "method": method,
        "confidence": confidence,
        "evidence_refs": _evidence_refs(
            value["evidence_refs"],
            "LINK_EVIDENCE_REFS",
            required=status in {"same", "not_same"},
        ),
    }


def _player_sort_key(player):
    return player["player_uid"]


def _alias_sort_key(alias):
    return (
        normalize_name(alias["alias"]),
        alias["player_uid"],
        alias["alias_type"],
    )


def _link_sort_key(link):
    return (link["record_key"], link["player_uid"], link["link_status"])


def _resolve_redirect(players_by_uid, player_uid):
    seen = set()
    current = player_uid
    while players_by_uid[current]["status"] == "REDIRECTED":
        if current in seen:
            raise PlayerIdentityError("PLAYER_REDIRECT_CYCLE")
        seen.add(current)
        current = players_by_uid[current]["redirect_to"]
    return current


def validate_registry(value):
    if not isinstance(value, dict):
        raise PlayerIdentityError("IDENTITY_REGISTRY_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "identity_version",
        "players",
        "aliases",
        "record_links",
        "registry_sha256",
    }
    if set(value) != required:
        raise PlayerIdentityError("IDENTITY_REGISTRY_SCHEMA_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise PlayerIdentityError("IDENTITY_REGISTRY_VERSION_INVALID")
    if value["identity_version"] != IDENTITY_VERSION:
        raise PlayerIdentityError("IDENTITY_REGISTRY_VERSION_INVALID")
    if not all(
        isinstance(value[key], list)
        for key in ("players", "aliases", "record_links")
    ):
        raise PlayerIdentityError("IDENTITY_REGISTRY_LISTS_REQUIRED")

    players = [_validate_player(item) for item in value["players"]]
    aliases = [_validate_alias(item) for item in value["aliases"]]
    links = [_validate_record_link(item) for item in value["record_links"]]
    players.sort(key=_player_sort_key)
    aliases.sort(key=_alias_sort_key)
    links.sort(key=_link_sort_key)

    if players != value["players"]:
        raise PlayerIdentityError("IDENTITY_PLAYER_ORDER_INVALID")
    if aliases != value["aliases"]:
        raise PlayerIdentityError("IDENTITY_ALIAS_ORDER_INVALID")
    if links != value["record_links"]:
        raise PlayerIdentityError("IDENTITY_LINK_ORDER_INVALID")

    player_uids = [item["player_uid"] for item in players]
    if len(player_uids) != len(set(player_uids)):
        raise PlayerIdentityError("PLAYER_UID_DUPLICATE")
    players_by_uid = {item["player_uid"]: item for item in players}

    alias_keys = [
        (item["player_uid"], normalize_name(item["alias"]))
        for item in aliases
    ]
    if len(alias_keys) != len(set(alias_keys)):
        raise PlayerIdentityError("PLAYER_ALIAS_DUPLICATE")

    link_keys = [
        (item["record_key"], item["player_uid"])
        for item in links
    ]
    if len(link_keys) != len(set(link_keys)):
        raise PlayerIdentityError("PLAYER_RECORD_LINK_DUPLICATE")

    record_keys = {item["record_key"] for item in links}
    if record_keys & set(player_uids):
        raise PlayerIdentityError("PLAYER_UID_DERIVED_FROM_RECORD_KEY_FORBIDDEN")
    identity_text = {
        normalize_name(item["canonical_name"])
        for item in players
    } | {
        normalize_name(item["alias"])
        for item in aliases
    }
    if set(player_uids) & identity_text:
        raise PlayerIdentityError("PLAYER_UID_DERIVED_FROM_NAME_FORBIDDEN")

    for item in aliases:
        player = players_by_uid.get(item["player_uid"])
        if player is None or player["status"] != "ACTIVE":
            raise PlayerIdentityError("ALIAS_ACTIVE_PLAYER_REQUIRED")

    same_record_keys = [
        item["record_key"]
        for item in links
        if item["link_status"] == "same"
    ]
    if len(same_record_keys) != len(set(same_record_keys)):
        raise PlayerIdentityError("RECORD_KEY_MULTIPLE_SAME_LINKS")
    for item in links:
        player = players_by_uid.get(item["player_uid"])
        if player is None or player["status"] != "ACTIVE":
            raise PlayerIdentityError("LINK_ACTIVE_PLAYER_REQUIRED")

    for player in players:
        if player["status"] != "REDIRECTED":
            continue
        if player["redirect_to"] not in players_by_uid:
            raise PlayerIdentityError("PLAYER_REDIRECT_TARGET_MISSING")
        _resolve_redirect(players_by_uid, player["player_uid"])

    core = {
        "schema_version": SCHEMA_VERSION,
        "identity_version": IDENTITY_VERSION,
        "players": players,
        "aliases": aliases,
        "record_links": links,
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value["registry_sha256"]:
        raise PlayerIdentityError("IDENTITY_REGISTRY_HASH_MISMATCH")
    return {**core, "registry_sha256": value["registry_sha256"]}


def new_registry(players, *, aliases=None, record_links=None):
    normalized_players = [_validate_player(item) for item in players]
    normalized_aliases = [_validate_alias(item) for item in aliases or []]
    normalized_links = [
        _validate_record_link(item) for item in record_links or []
    ]
    core = {
        "schema_version": SCHEMA_VERSION,
        "identity_version": IDENTITY_VERSION,
        "players": sorted(normalized_players, key=_player_sort_key),
        "aliases": sorted(normalized_aliases, key=_alias_sort_key),
        "record_links": sorted(normalized_links, key=_link_sort_key),
    }
    candidate = {
        **core,
        "registry_sha256": hashlib.sha256(canonical_bytes(core)).hexdigest(),
    }
    return validate_registry(candidate)


def serialize_registry(value):
    return canonical_bytes(validate_registry(value))


def load_registry_bytes(data):
    try:
        value = json.loads(data.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise PlayerIdentityError("IDENTITY_REGISTRY_JSON_INVALID") from exc
    return validate_registry(value)


def load_registry(path):
    try:
        return load_registry_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise PlayerIdentityError("IDENTITY_REGISTRY_READ_FAILED") from exc


def identity_summary(value):
    value = validate_registry(value)
    statuses = {item["link_status"] for item in value["record_links"]}
    return {
        "schema_version": SCHEMA_VERSION,
        "identity_version": IDENTITY_VERSION,
        "registry_sha256": value["registry_sha256"],
        "players": len(value["players"]),
        "active_players": sum(
            item["status"] == "ACTIVE" for item in value["players"]
        ),
        "redirected_players": sum(
            item["status"] == "REDIRECTED" for item in value["players"]
        ),
        "aliases": len(value["aliases"]),
        "record_links": len(value["record_links"]),
        "record_keys": len({item["record_key"] for item in value["record_links"]}),
        "same_links": sum(item["link_status"] == "same" for item in value["record_links"]),
        "not_same_links": sum(
            item["link_status"] == "not_same" for item in value["record_links"]
        ),
        "undecided_links": sum(
            item["link_status"] == "undecided" for item in value["record_links"]
        ),
        "link_statuses": sorted(statuses),
    }


def discover_candidates(value, name):
    value = validate_registry(value)
    query = normalize_name(name)
    by_uid = {
        player["player_uid"]: player
        for player in value["players"]
        if player["status"] == "ACTIVE"
    }
    matches = {}
    for player in by_uid.values():
        if normalize_name(player["canonical_name"]) == query:
            matches.setdefault(player["player_uid"], []).append({
                "match_type": "CANONICAL_NAME",
                "value": player["canonical_name"],
            })
    for alias in value["aliases"]:
        if alias["player_uid"] not in by_uid:
            continue
        if normalize_name(alias["alias"]) == query:
            matches.setdefault(alias["player_uid"], []).append({
                "match_type": "ALIAS",
                "value": alias["alias"],
            })
    candidates = [
        {
            "player_uid": player_uid,
            "canonical_name": by_uid[player_uid]["canonical_name"],
            "matches": sorted(
                matches[player_uid],
                key=lambda item: (item["match_type"], item["value"]),
            ),
        }
        for player_uid in sorted(matches)
    ]
    if not candidates:
        status = "NOT_FOUND"
    elif len(candidates) == 1:
        status = "CANDIDATE_ONLY"
    else:
        status = "REVIEW_REQUIRED"
    return {
        "schema_version": SCHEMA_VERSION,
        "lookup": LOOKUP_MODE,
        "query": query,
        "status": status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "identity_assertion": "NONE",
        "automatic_link_allowed": False,
    }


def add_player(value, *, player_uid, canonical_name):
    value = validate_registry(value)
    player = _validate_player({
        "schema_version": SCHEMA_VERSION,
        "player_uid": player_uid,
        "canonical_name": canonical_name,
        "status": "ACTIVE",
        "redirect_to": None,
    })
    if player["player_uid"] in {item["player_uid"] for item in value["players"]}:
        raise PlayerIdentityError("PLAYER_UID_DUPLICATE")
    return new_registry(
        [*value["players"], player],
        aliases=value["aliases"],
        record_links=value["record_links"],
    )


def add_alias(value, *, player_uid, alias, alias_type, evidence_refs):
    value = validate_registry(value)
    item = _validate_alias({
        "schema_version": SCHEMA_VERSION,
        "player_uid": player_uid,
        "alias": alias,
        "alias_type": alias_type,
        "evidence_refs": evidence_refs,
    })
    return new_registry(
        value["players"],
        aliases=[*value["aliases"], item],
        record_links=value["record_links"],
    )


def set_record_link(
    value,
    *,
    record_key,
    player_uid,
    link_status,
    method,
    confidence,
    evidence_refs,
    replace=False,
):
    value = validate_registry(value)
    item = _validate_record_link({
        "schema_version": SCHEMA_VERSION,
        "record_key": record_key,
        "player_uid": player_uid,
        "link_status": link_status,
        "method": method,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
    })
    key = (item["record_key"], item["player_uid"])
    existing = {
        (link["record_key"], link["player_uid"]): link
        for link in value["record_links"]
    }
    if key in existing and existing[key] != item and not replace:
        raise PlayerIdentityError("PLAYER_RECORD_LINK_EXISTS")
    links = [
        link for link in value["record_links"]
        if (link["record_key"], link["player_uid"]) != key
    ]
    links.append(item)
    return new_registry(
        value["players"],
        aliases=value["aliases"],
        record_links=links,
    )


def merge_players(
    value,
    *,
    source_player_uid,
    target_player_uid,
    evidence_refs,
):
    """Redirect one player into another without touching any canonical fact."""
    value = validate_registry(value)
    evidence = _evidence_refs(
        evidence_refs, "MERGE_EVIDENCE_REFS", required=True,
    )
    by_uid = {item["player_uid"]: item for item in value["players"]}
    if source_player_uid not in by_uid or target_player_uid not in by_uid:
        raise PlayerIdentityError("MERGE_PLAYER_MISSING")
    if by_uid[source_player_uid]["status"] != "ACTIVE":
        raise PlayerIdentityError("MERGE_SOURCE_ACTIVE_REQUIRED")
    target_uid = _resolve_redirect(by_uid, target_player_uid)
    if target_uid == source_player_uid:
        raise PlayerIdentityError("MERGE_SELF_FORBIDDEN")

    target = by_uid[target_uid]
    source = by_uid[source_player_uid]
    target_names = {
        normalize_name(target["canonical_name"]),
        *(
            normalize_name(item["alias"])
            for item in value["aliases"]
            if item["player_uid"] == target_uid
        ),
    }
    transferred_aliases = []
    source_names = [
        (source["canonical_name"], "NAME_VARIANT"),
        *[
            (item["alias"], item["alias_type"])
            for item in value["aliases"]
            if item["player_uid"] == source_player_uid
        ],
    ]
    for alias, alias_type in source_names:
        normalized = normalize_name(alias)
        if normalized in target_names:
            continue
        transferred_aliases.append(_validate_alias({
            "schema_version": SCHEMA_VERSION,
            "player_uid": target_uid,
            "alias": normalized,
            "alias_type": alias_type,
            "evidence_refs": evidence,
        }))
        target_names.add(normalized)

    existing = {
        (item["record_key"], item["player_uid"]): item
        for item in value["record_links"]
    }
    links = []
    for item in value["record_links"]:
        if item["player_uid"] != source_player_uid:
            links.append(item)
            continue
        candidate = {**item, "player_uid": target_uid}
        current = existing.get((candidate["record_key"], target_uid))
        if current is not None:
            if current != candidate:
                raise PlayerIdentityError("MERGE_LINK_CONFLICT")
            continue
        links.append(candidate)

    players = []
    for player in value["players"]:
        if player["player_uid"] == source_player_uid:
            players.append({
                **player,
                "status": "REDIRECTED",
                "redirect_to": target_uid,
            })
        else:
            players.append(player)
    aliases = [
        item for item in value["aliases"]
        if item["player_uid"] not in {source_player_uid, target_uid}
    ]
    aliases.extend(
        item for item in value["aliases"]
        if item["player_uid"] == target_uid
    )
    aliases.extend(transferred_aliases)
    return new_registry(players, aliases=aliases, record_links=links)


def split_record_link(
    value,
    *,
    source_player_uid,
    record_key,
    new_player,
    link,
    evidence_refs,
):
    """Move one explicitly reviewed link to a new player without fact mutation."""
    value = validate_registry(value)
    new_player = _validate_player(new_player)
    new_link = _validate_record_link(link)
    evidence = _evidence_refs(
        evidence_refs, "SPLIT_EVIDENCE_REFS", required=True,
    )
    if new_player["status"] != "ACTIVE":
        raise PlayerIdentityError("SPLIT_NEW_PLAYER_ACTIVE_REQUIRED")
    if new_link["player_uid"] != new_player["player_uid"]:
        raise PlayerIdentityError("SPLIT_LINK_PLAYER_MISMATCH")
    if new_link["record_key"] != record_key:
        raise PlayerIdentityError("SPLIT_RECORD_KEY_MISMATCH")
    if evidence != new_link["evidence_refs"]:
        raise PlayerIdentityError("SPLIT_EVIDENCE_MISMATCH")
    old_key = (record_key, source_player_uid)
    old_links = [
        item for item in value["record_links"]
        if (item["record_key"], item["player_uid"]) == old_key
    ]
    if len(old_links) != 1 or old_links[0]["link_status"] != "same":
        raise PlayerIdentityError("SPLIT_SOURCE_SAME_LINK_REQUIRED")
    links = [
        item for item in value["record_links"]
        if (item["record_key"], item["player_uid"]) != old_key
    ]
    links.append(new_link)
    return new_registry(
        [*value["players"], new_player],
        aliases=value["aliases"],
        record_links=links,
    )


class IdentityStore:
    """Write-once/compare-and-swap storage rooted in one Private Instance."""

    def __init__(self, instance):
        instance_root = Path(instance.root).resolve()
        data_root = Path(instance.data_root).resolve()
        if not data_root.is_relative_to(instance_root):
            raise PlayerIdentityError("IDENTITY_STORE_OUTSIDE_INSTANCE")
        self.root = data_root / "player_identity"
        self.path = self.root / "registry.json"

    def read(self):
        if not self.path.exists():
            return new_registry([])
        return load_registry(self.path)

    def write(self, value, *, expected_current_sha256=None):
        value = validate_registry(value)
        data = serialize_registry(value)
        self.root.mkdir(parents=True, exist_ok=True)
        with lock(self.root / ".identity.lock"):
            current = load_registry(self.path) if self.path.exists() else None
            if current is None:
                if expected_current_sha256 not in (None, ""):
                    raise PlayerIdentityError("IDENTITY_STORE_HEAD_MISMATCH")
            else:
                if not expected_current_sha256:
                    raise PlayerIdentityError(
                        "IDENTITY_STORE_CURRENT_SHA256_REQUIRED",
                    )
                if (
                    current["registry_sha256"]
                    != expected_current_sha256
                ):
                    raise PlayerIdentityError("IDENTITY_STORE_HEAD_MISMATCH")
            atomic(self.path, data)
        return {
            "path": str(self.path),
            "registry_sha256": value["registry_sha256"],
            "bytes": len(data),
        }
