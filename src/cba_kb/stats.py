"""Canonical Player Performance Stats domain model and validators for CBA-KB.

Conforms to REQ-200-PLAYER-STATS-01 FROZEN r1:
- Semantic grain: PLAYER_SEASON_STATS (Research View grain: STATS)
- Stable canonical record identity binding player_uid, provider, provider_player_id,
  season, match_type, scope, team context, metrics, provenance, and rights.
- Missing != zero: missing values stay null/None; zero is valid only when explicitly supplied.
- Whole-season vs team-split separation: transfer mid-season never creates duplicate whole-season truth.
- Identity resolution fail-closed: read-only against existing registry; candidate signals
  (name, alias, team, birthDate) yield REVIEW_REQUIRED / UNRESOLVED; never automatic SAME
  without trusted external-id / source-declared link.
- Rights fail-closed: default public_export_allowed = False, target-specific consumption.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .evidence_ledger import canonical_bytes

STATS_SCHEMA = "cba-kb.player-season-stats.v1"
STATS_SCHEMA_VERSION = 1
SEMANTIC_GRAIN = "PLAYER_SEASON_STATS"
RESEARCH_VIEW_GRAIN = "STATS"
PROVIDER_CBA_OFFICIAL = "CBA_OFFICIAL"
DEFAULT_COMPETITION = "CBA"

SCOPES = frozenset({"WHOLE_SEASON", "TEAM_SPLIT"})
IDENTITY_STATUSES = frozenset({"RESOLVED", "REVIEW_REQUIRED", "UNRESOLVED"})
RIGHTS_CLASSIFICATIONS = frozenset({"UNKNOWN_FOR_REDISTRIBUTION", "PUBLIC", "COPYRIGHTED", "PRIVATE"})

# Authoritative v2.0 MVP canonical metrics allowlist for PLAYER_SEASON_STATS
CANONICAL_METRIC_MVP_ALLOWLIST = frozenset({
    "games_played",
    "games_started",
    "minutes_per_game",
    "seconds_per_game",
    "points_per_game",
    "rebounds_per_game",
    "offensive_rebounds_per_game",
    "defensive_rebounds_per_game",
    "assists_per_game",
    "steals_per_game",
    "blocks_per_game",
    "turnovers_per_game",
    "fouls_per_game",
    "field_goals_made_per_game",
    "field_goals_attempted_per_game",
    "field_goals_percentage",
    "field_goals_percentage_rate",
    "three_point_made_per_game",
    "three_point_attempted_per_game",
    "three_point_percentage",
    "three_point_percentage_rate",
    "free_throws_made_per_game",
    "free_throws_attempted_per_game",
    "free_throws_percentage",
    "free_throws_percentage_rate",
})

# Authoritative MVP raw provider field allowlist
RAW_METRICS_MVP_ALLOWLIST = frozenset({
    "playerId",
    "cnAlias",
    "season",
    "teamId",
    "teamCnAlias",
    "playerTimes",
    "gameStartNum",
    "minutes",
    "seconds",
    "points",
    "rebounds",
    "reboundsOffensive",
    "reboundsDefensive",
    "assists",
    "steals",
    "blocked",
    "turnovers",
    "fouls",
    "fieldGoals",
    "fieldGoalsAttempted",
    "fieldGoalsPercentage",
    "fieldGoalsPercentageSort",
    "threePointGoals",
    "threePointAttempted",
    "threePointPercentage",
    "threePointPercentageSort",
    "freeThrows",
    "freeThrowsAttempted",
    "freeThrowsPercentage",
    "freeThrowsPercentageSort",
})

_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PERCENTAGE_RE = re.compile(r"^\d+(\.\d+)?%$")


class StatsError(RuntimeError):
    """Base error for CBA-KB stats domain."""
    pass


class StatsValidationError(StatsError):
    """Validation error for stats domain model constraints."""
    pass


class StatsProviderDriftError(StatsError):
    """Raised when provider envelope, encryption key, or schema materially drifts."""
    pass


class StatsIdentityResolutionError(StatsError):
    """Raised when identity resolution rules are violated."""
    pass


def build_record_id(
    provider: str,
    season: Union[str, int],
    match_type: Union[str, int],
    scope: str,
    provider_player_id: Union[str, int],
    team_id: Optional[Union[str, int]] = None,
) -> str:
    """Generate deterministic canonical record identifier."""
    scope_str = str(scope).upper()
    if scope_str == "TEAM_SPLIT":
        team_part = f":{team_id}" if team_id is not None else ":unknown_team"
    else:
        team_part = ""
    return f"stats:{provider}:{season}:{match_type}:{scope_str}:{provider_player_id}{team_part}"


def validate_stats_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a canonical player-season stats record against Frozen invariants."""
    if not isinstance(record, dict):
        raise StatsValidationError("STATS_RECORD_OBJECT_REQUIRED")

    # Required top-level keys
    required_keys = {
        "record_id",
        "semantic_grain",
        "schema_version",
        "player_uid",
        "identity_status",
        "identity_resolution",
        "provider",
        "provider_player_id",
        "player_name",
        "season",
        "competition",
        "match_type",
        "scope",
        "metrics",
        "provenance",
        "rights",
    }
    missing_keys = required_keys - set(record.keys())
    if missing_keys:
        raise StatsValidationError(f"MISSING_REQUIRED_KEYS:{','.join(sorted(missing_keys))}")

    # Enforce non-overridable semantic grain
    if record["semantic_grain"] != SEMANTIC_GRAIN:
        raise StatsValidationError(
            f"INVALID_SEMANTIC_GRAIN:{record['semantic_grain']}!= {SEMANTIC_GRAIN}"
        )

    if record["schema_version"] != STATS_SCHEMA_VERSION:
        raise StatsValidationError(
            f"INVALID_SCHEMA_VERSION:{record['schema_version']}!= {STATS_SCHEMA_VERSION}"
        )

    # Provider and provider player ID
    if not str(record["provider"]).strip():
        raise StatsValidationError("PROVIDER_REQUIRED")
    if not str(record["provider_player_id"]).strip():
        raise StatsValidationError("PROVIDER_PLAYER_ID_REQUIRED")
    if not str(record["player_name"]).strip():
        raise StatsValidationError("PLAYER_NAME_REQUIRED")
    if not str(record["season"]).strip():
        raise StatsValidationError("SEASON_REQUIRED")

    # Scope
    scope = str(record["scope"]).upper()
    if scope not in SCOPES:
        raise StatsValidationError(f"INVALID_SCOPE:{scope}")
    if scope == "TEAM_SPLIT":
        team_id = record.get("team_id")
        team_name = record.get("team_name")
        if not team_id and not team_name:
            raise StatsValidationError("TEAM_SPLIT_REQUIRES_TEAM_ID_OR_NAME")

    # Identity status and player_uid rule
    identity_status = record["identity_status"]
    if identity_status not in IDENTITY_STATUSES:
        raise StatsValidationError(f"INVALID_IDENTITY_STATUS:{identity_status}")

    player_uid = record.get("player_uid")
    if identity_status == "RESOLVED":
        if not player_uid or not isinstance(player_uid, str) or not player_uid.strip():
            raise StatsValidationError("RESOLVED_RECORD_REQUIRES_PLAYER_UID")
    else:
        if player_uid is not None:
            raise StatsValidationError(
                f"UNRESOLVED_RECORD_CANNOT_HAVE_PLAYER_UID:status={identity_status},player_uid={player_uid}"
            )

    # Validate identity_resolution block
    id_res = record.get("identity_resolution")
    if not isinstance(id_res, dict):
        raise StatsValidationError("IDENTITY_RESOLUTION_DICT_REQUIRED")
    if id_res.get("status") != identity_status:
        raise StatsValidationError("IDENTITY_RESOLUTION_STATUS_MISMATCH")

    # Validate metrics
    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        raise StatsValidationError("METRICS_DICT_REQUIRED")

    unknown_metrics = set(metrics) - CANONICAL_METRIC_MVP_ALLOWLIST
    if unknown_metrics:
        raise StatsValidationError(
            f"UNKNOWN_CANONICAL_METRIC_KEYS:{','.join(sorted(unknown_metrics))}"
        )

    # Invariant: games_played must be non-negative if present
    games_played = metrics.get("games_played")
    if games_played is not None:
        if not isinstance(games_played, (int, float)) or games_played < 0:
            raise StatsValidationError("INVALID_GAMES_PLAYED")

    # Invariant: percentage strings must match XX.X% if present, or None
    for pct_key in ("field_goals_percentage", "three_point_percentage", "free_throws_percentage"):
        pct_val = metrics.get(pct_key)
        if pct_val is not None:
            if not isinstance(pct_val, str) or not _PERCENTAGE_RE.match(pct_val):
                raise StatsValidationError(f"INVALID_PERCENTAGE_FORMAT:{pct_key}={pct_val}")

    # Invariant: percentage rates must be between 0.0 and 1.0 (or None)
    for rate_key in (
        "field_goals_percentage_rate",
        "three_point_percentage_rate",
        "free_throws_percentage_rate",
    ):
        rate_val = metrics.get(rate_key)
        if rate_val is not None:
            if not isinstance(rate_val, (int, float)) or not (0.0 <= float(rate_val) <= 1.0):
                raise StatsValidationError(f"INVALID_PERCENTAGE_RATE_RANGE:{rate_key}={rate_val}")

    # Validate raw_metrics against authoritative provider MVP allowlist if present
    raw_metrics = record.get("raw_metrics")
    if raw_metrics is not None:
        if not isinstance(raw_metrics, dict):
            raise StatsValidationError("RAW_METRICS_DICT_REQUIRED")
        unknown_raw = set(raw_metrics) - RAW_METRICS_MVP_ALLOWLIST
        if unknown_raw:
            raise StatsValidationError(
                f"UNKNOWN_RAW_PROVIDER_KEYS:{','.join(sorted(unknown_raw))}"
            )

    # Validate rights
    rights = record.get("rights")
    if not isinstance(rights, dict):
        raise StatsValidationError("RIGHTS_DICT_REQUIRED")
    classification = rights.get("classification")
    if classification not in RIGHTS_CLASSIFICATIONS:
        raise StatsValidationError(f"INVALID_RIGHTS_CLASSIFICATION:{classification}")
    if not isinstance(rights.get("public_export_allowed"), bool):
        raise StatsValidationError("PUBLIC_EXPORT_ALLOWED_BOOL_REQUIRED")

    # Validate provenance
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise StatsValidationError("PROVENANCE_DICT_REQUIRED")
    if not provenance.get("source_uri"):
        raise StatsValidationError("PROVENANCE_SOURCE_URI_REQUIRED")
    raw_hash = provenance.get("raw_response_sha256")
    if not raw_hash or not _SHA256_HEX_RE.match(str(raw_hash)):
        raise StatsValidationError("PROVENANCE_RAW_SHA256_REQUIRED")
    decoded_hash = provenance.get("decoded_sha256")
    if not decoded_hash or not _SHA256_HEX_RE.match(str(decoded_hash)):
        raise StatsValidationError("PROVENANCE_DECODED_SHA256_REQUIRED")

    return record


def resolve_stats_player_identity(
    provider_player_id: Union[str, int],
    player_name: str,
    registry: Any = None,
    candidate_signals: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str], List[str], str]:
    """Resolve provider player to canonical player_uid with Frozen fail-closed rules.

    Returns:
        (identity_status, resolved_player_uid, candidate_uids, resolution_reason)

    Rules:
    - If registry has a trusted external-identifier or source-declared link matching
      the provider key (e.g. 'cba_player_id:{id}', 'cba_player:{id}', or exact '{id}')
      with link_status='same' -> RESOLVED.
    - If no trusted link exists:
      Even if player_name, birthDate, or team matches registry candidates,
      fail-closed to REVIEW_REQUIRED. Exact name alone or name+team is NOT
      sufficient to create automatic SAME.
    - If no candidates match -> UNRESOLVED.
    """
    prov_id_str = str(provider_player_id).strip()
    norm_name = player_name.strip() if player_name else ""

    if registry is None:
        return "UNRESOLVED", None, [], "NO_REGISTRY_PROVIDED"

    # 1. Search for existing trusted external-identifier link
    possible_record_keys = {
        f"cba_player_id:{prov_id_str}",
        f"cba_player:{prov_id_str}",
        f"cba_official:{prov_id_str}",
        prov_id_str,
    }

    if isinstance(registry, dict):
        # Support dict format from new_registry / serialize_registry
        for link_entry in registry.get("record_links", []):
            if link_entry.get("record_key") in possible_record_keys:
                status = link_entry.get("link_status")
                method = link_entry.get("method")
                uid = link_entry.get("player_uid")
                if status == "same" and method in {"EXTERNAL_IDENTIFIER", "SOURCE_DECLARED"}:
                    return "RESOLVED", uid, [uid], f"TRUSTED_EXTERNAL_ID_LINK:{link_entry.get('record_key')}"

    # If registry has links attribute / mapping
    links = getattr(registry, "links", None)
    if isinstance(links, dict):
        for rk in possible_record_keys:
            link_entry = links.get(rk)
            if link_entry:
                status = link_entry.get("link_status")
                method = link_entry.get("method")
                uid = link_entry.get("player_uid")
                if status == "same" and method in {"EXTERNAL_IDENTIFIER", "SOURCE_DECLARED"}:
                    resolved_uid = registry.resolve_player_uid(uid) if hasattr(registry, "resolve_player_uid") else uid
                    return "RESOLVED", resolved_uid, [resolved_uid], f"TRUSTED_EXTERNAL_ID_LINK:{rk}"

    if hasattr(registry, "get_record_link"):
        for rk in possible_record_keys:
            link = registry.get_record_link(rk)
            if link and link.get("link_status") == "same" and link.get("method") in {"EXTERNAL_IDENTIFIER", "SOURCE_DECLARED"}:
                uid = link.get("player_uid")
                resolved_uid = registry.resolve_player_uid(uid) if hasattr(registry, "resolve_player_uid") else uid
                return "RESOLVED", resolved_uid, [resolved_uid], f"TRUSTED_EXTERNAL_ID_LINK:{rk}"

    # 2. Check candidates by name / alias in registry (read-only)
    candidate_uids = []
    if isinstance(registry, dict):
        for p in registry.get("players", []):
            if p.get("canonical_name") == norm_name:
                p_uid = p.get("player_uid")
                if p_uid:
                    candidate_uids.append(p_uid)
        for a in registry.get("aliases", []):
            if a.get("alias") == norm_name:
                p_uid = a.get("player_uid")
                if p_uid:
                    candidate_uids.append(p_uid)
    elif hasattr(registry, "lookup_player"):
        found = registry.lookup_player(norm_name)
        if found:
            candidate_uids.append(found.get("player_uid") if isinstance(found, dict) else getattr(found, "player_uid", str(found)))
    elif hasattr(registry, "players_by_name"):
        p = registry.players_by_name.get(norm_name)
        if p:
            candidate_uids.append(p.get("player_uid") if isinstance(p, dict) else getattr(p, "player_uid", str(p)))
    elif hasattr(registry, "players"):
        # Iterate over registry players
        players_iter = registry.players.values() if isinstance(registry.players, dict) else registry.players
        for p in players_iter:
            cname = p.get("canonical_name") if isinstance(p, dict) else getattr(p, "canonical_name", None)
            if cname == norm_name:
                p_uid = p.get("player_uid") if isinstance(p, dict) else getattr(p, "player_uid", None)
                if p_uid:
                    candidate_uids.append(p_uid)

    # De-duplicate candidate UIDs
    unique_candidates = sorted(list(set(filter(None, candidate_uids))))

    if unique_candidates:
        # Candidate signals exist but CANNOT automatically create SAME
        return (
            "REVIEW_REQUIRED",
            None,
            unique_candidates,
            "CANDIDATE_NAME_MATCH_REQUIRES_MANUAL_OR_EXTERNAL_ID_REVIEW",
        )

    return "UNRESOLVED", None, [], "NO_MATCHING_CANDIDATE_OR_EXTERNAL_ID"


def deduplicate_and_reconcile_stats(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate stats records idempotently or fail closed on ambiguous conflicts."""
    by_key: Dict[str, Dict[str, Any]] = {}

    for rec in records:
        rec_id = rec["record_id"]
        if rec_id not in by_key:
            by_key[rec_id] = rec
            continue

        existing = by_key[rec_id]

        # Check if identical canonical content
        existing_metrics = existing.get("metrics")
        rec_metrics = rec.get("metrics")
        if canonical_bytes(existing_metrics) == canonical_bytes(rec_metrics):
            # Idempotent duplicate: retain deterministically
            continue

        # Differing metrics: check provenance supersession timestamp if available
        t_exist = existing.get("provenance", {}).get("captured_at", "")
        t_rec = rec.get("provenance", {}).get("captured_at", "")
        if t_rec and t_exist and t_rec > t_exist:
            # Newer explicit observation supersedes prior observation
            by_key[rec_id] = rec
        elif t_exist and t_rec and t_exist > t_rec:
            pass  # keep existing newer record
        else:
            raise StatsError(
                f"AMBIGUOUS_CONFLICTING_STATS_DUPLICATE:{rec_id}: conflicting metrics without temporal supersession"
            )

    return [by_key[k] for k in sorted(by_key.keys())]
