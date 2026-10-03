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

import datetime
import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .evidence_ledger import canonical_bytes

STATS_SCHEMA = "cba-kb.player-season-stats.v1"
STATS_SCHEMA_VERSION = 1
SEMANTIC_GRAIN = "PLAYER_SEASON_STATS"
RESEARCH_VIEW_GRAIN = "STATS"
PROVIDER_CBA_OFFICIAL = "CBA_OFFICIAL"
DEFAULT_COMPETITION = "CBA"

# Exact authoritative top-level schema allowlist
ALLOWED_TOP_LEVEL_FIELDS = frozenset({
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
    "match_type_label",
    "scope",
    "team_id",
    "team_name",
    "metrics",
    "raw_metrics",
    "provenance",
    "rights",
})

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

RAW_METRICS_PERCENTAGE_FIELDS = frozenset({
    "fieldGoalsPercentage",
    "threePointPercentage",
    "freeThrowsPercentage",
})

RAW_METRICS_TEXT_FIELDS = frozenset({
    "cnAlias",
    "teamCnAlias",
})

RAW_METRICS_ID_FIELDS = frozenset({
    "playerId",
    "season",
    "teamId",
})

RAW_METRICS_NUMERIC_FIELDS = (
    RAW_METRICS_MVP_ALLOWLIST
    - RAW_METRICS_PERCENTAGE_FIELDS
    - RAW_METRICS_TEXT_FIELDS
    - RAW_METRICS_ID_FIELDS
)

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

    # Reject unknown top-level fields
    unknown_top = set(record.keys()) - ALLOWED_TOP_LEVEL_FIELDS
    if unknown_top:
        raise StatsValidationError(
            f"UNKNOWN_TOP_LEVEL_FIELDS:{','.join(sorted(unknown_top))}"
        )

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

    # Strictly CBA_OFFICIAL provider and CBA competition
    if record.get("provider") != PROVIDER_CBA_OFFICIAL:
        raise StatsValidationError(f"INVALID_PROVIDER:{record.get('provider')}")

    if record.get("competition") != DEFAULT_COMPETITION:
        raise StatsValidationError(f"INVALID_COMPETITION:{record.get('competition')}")

    # Scalar non-empty checks for core identifiers
    for fld in ("provider_player_id", "season", "match_type"):
        val = record.get(fld)
        if val is None or isinstance(val, bool) or not isinstance(val, (str, int)) or not str(val).strip():
            raise StatsValidationError(f"INVALID_{fld.upper()}:{val!r}")

    player_name = record.get("player_name")
    if not isinstance(player_name, str) or not player_name.strip():
        raise StatsValidationError("PLAYER_NAME_REQUIRED")

    # Type-bound all allowlisted optional top-level metadata:
    mtl = record.get("match_type_label")
    if mtl is not None and (isinstance(mtl, bool) or not isinstance(mtl, str)):
        raise StatsValidationError(f"INVALID_MATCH_TYPE_LABEL:{mtl!r}")

    tid = record.get("team_id")
    if tid is not None and (isinstance(tid, bool) or not isinstance(tid, (str, int)) or not str(tid).strip()):
        raise StatsValidationError(f"INVALID_TEAM_ID:{tid!r}")

    tname = record.get("team_name")
    if tname is not None and (isinstance(tname, bool) or not isinstance(tname, str)):
        raise StatsValidationError(f"INVALID_TEAM_NAME:{tname!r}")

    # Scope and team context: accept only exact string values WHOLE_SEASON and TEAM_SPLIT
    scope = record.get("scope")
    if not isinstance(scope, str) or isinstance(scope, bool) or scope not in ("WHOLE_SEASON", "TEAM_SPLIT"):
        raise StatsValidationError(f"INVALID_SCOPE:{scope}")

    if scope == "TEAM_SPLIT":
        if tid is None:
            raise StatsValidationError("TEAM_SPLIT_REQUIRES_TEAM_ID")

    # Validate deterministic record_id recomputation
    expected_record_id = build_record_id(
        provider=record["provider"],
        season=record["season"],
        match_type=record["match_type"],
        scope=scope,
        provider_player_id=record["provider_player_id"],
        team_id=tid if scope == "TEAM_SPLIT" else None,
    )
    if record["record_id"] != expected_record_id:
        raise StatsValidationError(
            f"RECORD_ID_MISMATCH:got={record['record_id']},expected={expected_record_id}"
        )

    # Identity status and player_uid rule
    identity_status = record["identity_status"]
    if identity_status not in IDENTITY_STATUSES:
        raise StatsValidationError(f"INVALID_IDENTITY_STATUS:{identity_status}")

    id_res = record.get("identity_resolution")
    if not isinstance(id_res, dict):
        raise StatsValidationError("IDENTITY_RESOLUTION_DICT_REQUIRED")
    if id_res.get("status") != identity_status:
        raise StatsValidationError("IDENTITY_RESOLUTION_STATUS_MISMATCH")

    player_uid = record.get("player_uid")
    prov_player_id_str = str(record["provider_player_id"]).strip()

    if identity_status == "RESOLVED":
        if not player_uid or not isinstance(player_uid, str) or isinstance(player_uid, bool) or not player_uid.strip():
            raise StatsValidationError("RESOLVED_RECORD_REQUIRES_PLAYER_UID")

        res_method = id_res.get("resolution_method")
        if not isinstance(res_method, str) or isinstance(res_method, bool):
            raise StatsValidationError("IDENTITY_RESOLUTION_METHOD_REQUIRED")

        valid_expected_methods = {
            f"TRUSTED_EXTERNAL_ID_LINK:cba_player_id:{prov_player_id_str}",
            f"TRUSTED_EXTERNAL_ID_LINK:cba_player:{prov_player_id_str}",
            f"TRUSTED_EXTERNAL_ID_LINK:cba_official:{prov_player_id_str}",
            f"TRUSTED_EXTERNAL_ID_LINK:{prov_player_id_str}",
        }
        if res_method not in valid_expected_methods:
            raise StatsValidationError(f"RESOLVED_IDENTITY_REQUIRES_TRUSTED_LINK_METHOD:{res_method}")

        candidates = id_res.get("candidate_player_uids")
        if not isinstance(candidates, list) or candidates != [player_uid]:
            raise StatsValidationError(f"RESOLVED_PLAYER_UID_NOT_IN_CANDIDATE_UIDS:{player_uid}")
    else:
        if player_uid is not None:
            raise StatsValidationError(
                f"UNRESOLVED_RECORD_CANNOT_HAVE_PLAYER_UID:status={identity_status},player_uid={player_uid}"
            )

    # Validate metrics against authoritative MVP allowlist
    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        raise StatsValidationError("METRICS_DICT_REQUIRED")

    unknown_metrics = set(metrics) - CANONICAL_METRIC_MVP_ALLOWLIST
    if unknown_metrics:
        raise StatsValidationError(
            f"UNKNOWN_CANONICAL_METRIC_KEYS:{','.join(sorted(unknown_metrics))}"
        )

    for k, v in metrics.items():
        if v is None:
            continue
        if isinstance(v, bool):
            raise StatsValidationError(f"INVALID_METRIC_TYPE:{k}={v!r}(bool)")
        if k in ("field_goals_percentage", "three_point_percentage", "free_throws_percentage"):
            if not isinstance(v, str) or not _PERCENTAGE_RE.match(v):
                raise StatsValidationError(f"INVALID_PERCENTAGE_FORMAT:{k}={v}")
        elif k in ("field_goals_percentage_rate", "three_point_percentage_rate", "free_throws_percentage_rate"):
            if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v) or not (0.0 <= float(v) <= 1.0):
                raise StatsValidationError(f"INVALID_PERCENTAGE_RATE_RANGE:{k}={v}")
        elif k == "games_started":
            if not isinstance(v, int) or v < 0:
                raise StatsValidationError(f"INVALID_GAMES_STARTED:{k}={v}")
        else:
            if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                raise StatsValidationError(f"INVALID_METRIC_TYPE:{k}={v!r}")
            if v < 0:
                raise StatsValidationError(f"NEGATIVE_METRIC_VALUE:{k}={v}")

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
        for rk, rv in raw_metrics.items():
            if rv is None:
                continue
            if isinstance(rv, bool):
                raise StatsValidationError(f"RAW_METRICS_INVALID_TYPE:{rk}={rv!r}(bool)")
            if rk in RAW_METRICS_PERCENTAGE_FIELDS:
                if not isinstance(rv, str) or not _PERCENTAGE_RE.match(rv):
                    raise StatsValidationError(f"RAW_METRICS_INVALID_PERCENTAGE:{rk}={rv!r}")
            elif rk in RAW_METRICS_TEXT_FIELDS:
                if not isinstance(rv, str):
                    raise StatsValidationError(f"RAW_METRICS_INVALID_TEXT:{rk}={rv!r}")
            elif rk in RAW_METRICS_ID_FIELDS:
                if not isinstance(rv, (int, str)) or not str(rv).strip():
                    raise StatsValidationError(f"RAW_METRICS_INVALID_ID:{rk}={rv!r}")
            elif rk in RAW_METRICS_NUMERIC_FIELDS:
                if isinstance(rv, (int, float)):
                    if math.isnan(rv) or math.isinf(rv):
                        raise StatsValidationError(f"RAW_METRICS_NON_FINITE_VALUE:{rk}={rv}")
                elif isinstance(rv, str):
                    s = rv.strip()
                    try:
                        vf = float(s)
                        if math.isnan(vf) or math.isinf(vf):
                            raise ValueError()
                    except ValueError:
                        raise StatsValidationError(f"RAW_METRICS_NON_NUMERIC_STRING:{rk}={rv!r}")
                else:
                    raise StatsValidationError(f"RAW_METRICS_NON_SCALAR_VALUE:{rk}={type(rv).__name__}")
            else:
                raise StatsValidationError(f"RAW_METRICS_NON_SCALAR_VALUE:{rk}={type(rv).__name__}")

    # Validate rights
    rights = record.get("rights")
    if not isinstance(rights, dict):
        raise StatsValidationError("RIGHTS_DICT_REQUIRED")
    classification = rights.get("classification")
    if classification not in RIGHTS_CLASSIFICATIONS:
        raise StatsValidationError(f"INVALID_RIGHTS_CLASSIFICATION:{classification}")
    if not isinstance(rights.get("public_export_allowed"), bool):
        raise StatsValidationError("PUBLIC_EXPORT_ALLOWED_BOOL_REQUIRED")
    if rights.get("private_ai_consumption") != "TARGET_SPECIFIC":
        raise StatsValidationError(f"INVALID_PRIVATE_AI_CONSUMPTION:{rights.get('private_ai_consumption')}")
    if "materialization_authorized" in rights:
        if not isinstance(rights["materialization_authorized"], bool):
            raise StatsValidationError("MATERIALIZATION_AUTHORIZED_BOOL_REQUIRED")

    # Validate provenance
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise StatsValidationError("PROVENANCE_DICT_REQUIRED")

    source_uri = provenance.get("source_uri")
    if not isinstance(source_uri, str) or not source_uri.strip():
        raise StatsValidationError("PROVENANCE_SOURCE_URI_REQUIRED")

    endpoint = provenance.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise StatsValidationError("PROVENANCE_ENDPOINT_REQUIRED")

    request_contract = provenance.get("request_contract")
    if not isinstance(request_contract, dict):
        raise StatsValidationError("PROVENANCE_REQUEST_CONTRACT_DICT_REQUIRED")

    prov_season = provenance.get("season")
    if prov_season is None or str(prov_season) != str(record["season"]):
        raise StatsValidationError("PROVENANCE_SEASON_MISMATCH")

    prov_match_type = provenance.get("match_type")
    if prov_match_type is None or str(prov_match_type) != str(record["match_type"]):
        raise StatsValidationError("PROVENANCE_MATCH_TYPE_MISMATCH")

    raw_hash = provenance.get("raw_response_sha256")
    if not raw_hash or not _SHA256_HEX_RE.match(str(raw_hash)):
        raise StatsValidationError("PROVENANCE_RAW_SHA256_REQUIRED")

    decoded_hash = provenance.get("decoded_sha256")
    if not decoded_hash or not _SHA256_HEX_RE.match(str(decoded_hash)):
        raise StatsValidationError("PROVENANCE_DECODED_SHA256_REQUIRED")

    captured_at = provenance.get("captured_at")
    if captured_at is not None:
        if not isinstance(captured_at, str) or not captured_at.strip():
            raise StatsValidationError("PROVENANCE_CAPTURED_AT_STR_REQUIRED")
        try:
            dt = datetime.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StatsValidationError(f"INVALID_PROVENANCE_CAPTURED_AT:{captured_at}") from exc
        if dt.tzinfo is None:
            raise StatsValidationError(f"PROVENANCE_CAPTURED_AT_TIMEZONE_REQUIRED:{captured_at}")

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
        validate_stats_record(rec)
        rec_id = rec["record_id"]
        if rec_id not in by_key:
            by_key[rec_id] = rec
            continue

        existing = by_key[rec_id]

        # Check if identical full canonical record content
        if canonical_bytes(existing) == canonical_bytes(rec):
            # Idempotent duplicate: retain deterministically
            continue

        # Differing canonical records: must resolve via valid explicit timezone-aware captured_at supersession
        t_exist_raw = existing.get("provenance", {}).get("captured_at")
        t_rec_raw = rec.get("provenance", {}).get("captured_at")

        if not t_exist_raw or not t_rec_raw or not isinstance(t_exist_raw, str) or not isinstance(t_rec_raw, str):
            raise StatsError(
                f"AMBIGUOUS_CONFLICTING_STATS_DUPLICATE:{rec_id}: conflicting canonical records without temporal supersession timestamp"
            )

        try:
            dt_exist = datetime.datetime.fromisoformat(t_exist_raw.replace("Z", "+00:00"))
            dt_rec = datetime.datetime.fromisoformat(t_rec_raw.replace("Z", "+00:00"))
        except ValueError:
            raise StatsError(
                f"AMBIGUOUS_CONFLICTING_STATS_DUPLICATE:{rec_id}: unparseable supersession timestamp"
            )

        if dt_exist.tzinfo is None or dt_rec.tzinfo is None:
            raise StatsError(
                f"AMBIGUOUS_CONFLICTING_STATS_DUPLICATE:{rec_id}: naive supersession timestamp"
            )

        if dt_rec > dt_exist:
            # Newer explicit observation supersedes prior observation
            by_key[rec_id] = rec
        elif dt_exist > dt_rec:
            pass  # keep existing newer record
        else:
            raise StatsError(
                f"AMBIGUOUS_CONFLICTING_STATS_DUPLICATE:{rec_id}: equal instants with differing canonical content"
            )

    return [by_key[k] for k in sorted(by_key.keys())]
