"""CBA League official statistical data provider adapter.

Conforms to REQ-200-PLAYER-STATS-01:
- Discovers and decrypts public client AES-128-ECB + PKCS7 encrypted envelopes.
- Key and envelope details are runtime discovery/configuration concerns; fails closed on provider drift.
- Transforms raw provider records into canonical player-season stats records.
- Preserves explicit numeric zeros and distinguishes them from missing/null values.
- Never hardcodes current key as eternal schema truth; supports dynamic discovery,
  configured keys, and offline test injection.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import ssl
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..stats import (
    DEFAULT_COMPETITION,
    PROVIDER_CBA_OFFICIAL,
    RAW_METRICS_MVP_ALLOWLIST,
    StatsError,
    StatsProviderDriftError,
    StatsValidationError,
    build_record_id,
    resolve_stats_player_identity,
    validate_stats_record,
)

DEFAULT_DATA_SERVER = "https://data-server.cbaleague.com"
DEFAULT_PORTAL_URL = "https://www.cbaleague.com/data/"

# Minimal MVP allowlist of provider fields needed for frozen base Stats semantics
RAW_METRICS_MVP_FIELDS = RAW_METRICS_MVP_ALLOWLIST

_AES_KEY_REGEX = re.compile(
    r'(?:const|var|let)\s+[a-zA-Z0-9_$]+\s*=\s*["\']([A-Za-z0-9]{16})["\'];\s*(?:const|var|let)\s+[a-zA-Z0-9_$]+\s*=\s*[a-zA-Z0-9_$.]+\.enc\.Utf8\.parse'
)
_SCRIPT_SRC_REGEX = re.compile(r'<script[^>]+src=["\']([^"\']+\.js)["\']')


class CBAStatsEnvelopeError(StatsProviderDriftError):
    """Raised when an HTTP response cannot be safely decrypted or decoded."""
    pass


class KeyDiscoveryError(StatsProviderDriftError):
    """Raised when AES key discovery fails or is ambiguous."""
    pass


class StatsProviderContractError(StatsProviderDriftError):
    """Raised when provider record violates required schema or semantic contract."""
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def decrypt_cba_envelope(raw_bytes: bytes, key: bytes) -> Tuple[Any, str]:
    """Decrypt a raw CBA data server response envelope.

    Envelope format:
    JSON string containing a Base64-encoded AES-128-ECB ciphertext with PKCS7 padding.

    Returns:
        (decoded_json_object, decoded_sha256)
    """
    if not raw_bytes:
        raise CBAStatsEnvelopeError("EMPTY_RESPONSE_PAYLOAD")

    if not key or len(key) != 16:
        raise CBAStatsEnvelopeError(f"INVALID_AES_KEY_LENGTH:{len(key) if key else 0}!= 16")

    try:
        raw_text = raw_bytes.decode("utf-8")
        b64_str = json.loads(raw_text)
        if not isinstance(b64_str, str):
            raise CBAStatsEnvelopeError("ENVELOPE_NOT_JSON_STRING")
        ciphertext = base64.b64decode(b64_str)
    except Exception as exc:
        raise CBAStatsEnvelopeError(f"MALFORMED_ENVELOPE_OUTER_JSON:{exc}") from exc

    if not ciphertext or len(ciphertext) % 16 != 0:
        raise CBAStatsEnvelopeError(f"INVALID_CIPHERTEXT_LENGTH:{len(ciphertext)}")

    try:
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        unpadded = unpadder.update(decrypted) + unpadder.finalize()
    except Exception as exc:
        raise CBAStatsEnvelopeError(f"AES_DECRYPTION_OR_UNPADDING_FAILED:{exc}") from exc

    try:
        decoded_data = json.loads(unpadded.decode("utf-8"))
        decoded_sha = sha256_bytes(unpadded)
    except Exception as exc:
        raise CBAStatsEnvelopeError(f"DECRYPTED_PAYLOAD_NOT_VALID_JSON:{exc}") from exc

    return decoded_data, decoded_sha


def discover_client_key_from_bundle(js_content: str) -> Optional[bytes]:
    """Extract AES decryption key from client JavaScript bundle via regex/heuristic.
    
    Returns candidate bytes if unambiguous, or None.
    """
    candidates = set(_AES_KEY_REGEX.findall(js_content))
    if not candidates:
        pos = js_content.find("AES.decrypt")
        if pos != -1:
            snippet = js_content[max(0, pos - 200) : min(len(js_content), pos + 100)]
            candidates.update(re.findall(r'["\']([A-Za-z0-9]{16})["\']', snippet))

    if len(candidates) == 1:
        cand = next(iter(candidates)).encode("utf-8")
        if len(cand) == 16:
            return cand
    return None


def discover_key_from_portal(portal_url: str = DEFAULT_PORTAL_URL, timeout: int = 15) -> bytes:
    """Fetch portal HTML, discover client script bundle, and extract current AES key."""
    try:
        ctx = ssl.create_default_context()
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
            "Referer": "https://www.cbaleague.com/",
        }
        req = urllib.request.Request(portal_url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise KeyDiscoveryError(f"PORTAL_FETCH_FAILED:{portal_url}:{exc}") from exc

    script_matches = _SCRIPT_SRC_REGEX.findall(html)
    if not script_matches:
        raise KeyDiscoveryError(f"NO_SCRIPTS_FOUND_IN_PORTAL:{portal_url}")

    # Prioritize bundles matching index.*.js
    index_scripts = [s for s in script_matches if "index" in s]
    target_scripts = index_scripts if index_scripts else script_matches

    found_keys = set()
    for s_path in target_scripts:
        bundle_url = urllib.parse.urljoin(portal_url, s_path)
        try:
            b_req = urllib.request.Request(bundle_url, headers=headers)
            with urllib.request.urlopen(b_req, context=ctx, timeout=timeout) as b_resp:
                js_content = b_resp.read().decode("utf-8", errors="replace")
            cand = discover_client_key_from_bundle(js_content)
            if cand:
                found_keys.add(cand)
        except Exception:
            continue

    if len(found_keys) == 0:
        raise KeyDiscoveryError("KEY_DISCOVERY_NO_CANDIDATE_FOUND")
    if len(found_keys) > 1:
        raise KeyDiscoveryError(f"KEY_DISCOVERY_AMBIGUOUS_CANDIDATES:{len(found_keys)}")

    return next(iter(found_keys))


class CBAStatsAdapter:
    """Public CBA stats provider adapter."""

    def __init__(
        self,
        base_url: str = DEFAULT_DATA_SERVER,
        portal_url: str = DEFAULT_PORTAL_URL,
        key: Optional[bytes] = None,
        timeout: int = 15,
    ):
        self.base_url = base_url.rstrip("/")
        self.portal_url = portal_url
        self.timeout = timeout
        self._key: Optional[bytes] = None

        if key is not None:
            self.set_encryption_key(key)
        else:
            env_key = os.environ.get("CBA_STATS_AES_KEY")
            if env_key:
                self.set_encryption_key(env_key.encode("utf-8"))

    def get_encryption_key(self) -> bytes:
        if self._key is None:
            self._key = discover_key_from_portal(self.portal_url, timeout=self.timeout)
        return self._key

    def set_encryption_key(self, key: bytes) -> None:
        if not key or len(key) != 16:
            raise ValueError(f"AES-128 key must be exactly 16 bytes, got {len(key) if key else 0}")
        self._key = key

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Perform authenticated/encrypted HTTP request against CBA data server."""
        url = f"{self.base_url}{endpoint}"
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
            "Referer": "https://www.cbaleague.com/",
            "Origin": "https://www.cbaleague.com",
            "isEncrypt": "encrypt",
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache",
        }

        body_bytes = None
        if payload is not None:
            body_bytes = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json;charset=UTF-8"

        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        t_start = datetime.datetime.now(datetime.timezone.utc)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=self.timeout) as resp:
                status = resp.status
                raw_bytes = resp.read()
                resp_headers = dict(resp.headers)
        except Exception as exc:
            raise StatsProviderDriftError(f"HTTP_REQUEST_FAILED:{url}:{exc}") from exc
        t_end = datetime.datetime.now(datetime.timezone.utc)

        raw_sha256 = sha256_bytes(raw_bytes)
        decryption_key = self.get_encryption_key()
        decoded_data, decoded_sha256 = decrypt_cba_envelope(raw_bytes, decryption_key)

        return {
            "url": url,
            "endpoint": endpoint,
            "method": method,
            "status": status,
            "request_payload": payload,
            "raw_bytes": raw_bytes,
            "raw_sha256": raw_sha256,
            "decoded_data": decoded_data,
            "decoded_sha256": decoded_sha256,
            "captured_at": t_end.isoformat(),
            "duration_ms": round((t_end - t_start).total_seconds() * 1000, 2),
        }

    def fetch_seasons(self) -> List[Dict[str, Any]]:
        resp = self._request("/api/com-code-tables/getSeason")
        return resp["decoded_data"]

    def fetch_match_types(self) -> List[Dict[str, Any]]:
        resp = self._request("/api/com-code-tables/getMatchType")
        return resp["decoded_data"]

    def fetch_teams(self, season: Union[str, int]) -> List[Dict[str, Any]]:
        resp = self._request(f"/api/com-code-tables/getTeam?type=2&season={season}")
        return resp["decoded_data"]

    def fetch_player_base_page(
        self,
        season: Union[str, int],
        match_type_id: int = 1,
        page_number: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        count_ranger: int = 1,
        player_ranger: int = 1,
        sort: int = 3,
        rank: int = 1,
    ) -> Dict[str, Any]:
        """Fetch a page of player base performance statistics."""
        payload = {
            "season": int(season),
            "matchTypeId": int(match_type_id),
            "countRanger": count_ranger,
            "playerRanger": player_ranger,
            "teamId": team_id,
            "acrossTeamId": None,
            "type": None,
            "startTime": None,
            "endTime": None,
            "startRound": None,
            "endRound": None,
            "startMatchOrder": None,
            "endMatchOrder": None,
            "sort": sort,
            "rank": rank,
        }
        endpoint = f"/api/player-base-list?pageNumber={page_number}&pageSize={page_size}"
        return self._request(endpoint, method="POST", payload=payload)

    def transform_provider_record(
        self,
        record: Dict[str, Any],
        provenance_base: Dict[str, Any],
        registry: Any = None,
        scope: str = "WHOLE_SEASON",
        match_type_label: str = "REGULAR_SEASON",
    ) -> Dict[str, Any]:
        """Transform a raw provider player-base record into a canonical Stats record."""
        if not isinstance(record, dict):
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:RECORD_NOT_DICT:{type(record).__name__}"
            )

        # 1. Core identifiers validation
        if "playerId" not in record:
            raise StatsProviderContractError("PROVIDER_SCHEMA_DRIFT:MISSING_IDENTIFIER:playerId")
        raw_pid = record["playerId"]
        if raw_pid is None or (isinstance(raw_pid, str) and not raw_pid.strip()):
            raise StatsProviderContractError("PROVIDER_SCHEMA_DRIFT:INVALID_IDENTIFIER:playerId_empty")
        if not isinstance(raw_pid, (int, str)) or isinstance(raw_pid, bool):
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:playerId:{type(raw_pid).__name__}"
            )
        prov_player_id = str(raw_pid).strip()

        if "cnAlias" not in record:
            raise StatsProviderContractError("PROVIDER_SCHEMA_DRIFT:MISSING_IDENTIFIER:cnAlias")
        raw_name = record["cnAlias"]
        if raw_name is None or not isinstance(raw_name, str) or not raw_name.strip():
            raise StatsProviderContractError("PROVIDER_SCHEMA_DRIFT:INVALID_IDENTIFIER:cnAlias_empty")
        player_name = raw_name.strip()

        raw_season = record.get("season", provenance_base.get("season"))
        if raw_season is None or (isinstance(raw_season, str) and not str(raw_season).strip()):
            raise StatsProviderContractError("PROVIDER_SCHEMA_DRIFT:MISSING_IDENTIFIER:season")
        if not isinstance(raw_season, (int, str)) or isinstance(raw_season, bool):
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:season:{type(raw_season).__name__}"
            )
        season = str(raw_season).strip()

        match_type = str(provenance_base.get("match_type_id", "1"))
        raw_team_id = record.get("teamId")
        if raw_team_id is not None and (not isinstance(raw_team_id, (int, str)) or isinstance(raw_team_id, bool)):
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:teamId:{type(raw_team_id).__name__}"
            )
        team_id = str(raw_team_id).strip() if raw_team_id is not None else None
        team_name = record.get("teamCnAlias")
        if team_name is not None and not isinstance(team_name, str):
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:teamCnAlias:{type(team_name).__name__}"
            )

        # 2. Strict metric parsing with contract drift validation
        def _parse_float(field: str, *, required: bool = False) -> Optional[float]:
            if field not in record:
                if required:
                    raise StatsProviderContractError(f"PROVIDER_SCHEMA_DRIFT:MISSING_CORE_METRIC:{field}")
                return None
            val = record[field]
            if val is None:
                return None
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
            if isinstance(val, str):
                s = val.strip()
                if not s:
                    return None
                try:
                    return float(s)
                except ValueError as exc:
                    raise StatsProviderContractError(
                        f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:{field}:{val!r} not float"
                    ) from exc
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:{field}:{type(val).__name__}"
            )

        def _parse_int(field: str, *, required: bool = False) -> Optional[int]:
            if field not in record:
                if required:
                    raise StatsProviderContractError(f"PROVIDER_SCHEMA_DRIFT:MISSING_CORE_METRIC:{field}")
                return None
            val = record[field]
            if val is None:
                return None
            if isinstance(val, int) and not isinstance(val, bool):
                return val
            if isinstance(val, float) and val.is_integer():
                return int(val)
            if isinstance(val, str):
                s = val.strip()
                if not s:
                    return None
                try:
                    return int(float(s))
                except ValueError as exc:
                    raise StatsProviderContractError(
                        f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:{field}:{val!r} not int"
                    ) from exc
            raise StatsProviderContractError(
                f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:{field}:{type(val).__name__}"
            )

        def _parse_pct_str(field: str) -> Optional[str]:
            if field not in record:
                return None
            val = record[field]
            if val is None:
                return None
            if not isinstance(val, str):
                raise StatsProviderContractError(
                    f"PROVIDER_SCHEMA_DRIFT:INCOMPATIBLE_TYPE:{field}:{type(val).__name__}"
                )
            return val.strip()

        # Core metrics required by provider contract
        gp = _parse_float("playerTimes", required=True)
        pts = _parse_float("points", required=True)
        reb = _parse_float("rebounds", required=True)
        ast = _parse_float("assists", required=True)

        record_id = build_record_id(
            provider=PROVIDER_CBA_OFFICIAL,
            season=season,
            match_type=match_type,
            scope=scope,
            provider_player_id=prov_player_id,
            team_id=team_id if scope == "TEAM_SPLIT" else None,
        )

        # Fail-closed identity resolution
        candidate_signals = {
            "birth_date": record.get("birthDate"),
            "team_name": team_name,
            "nationality": record.get("nationality"),
        }
        id_status, player_uid, candidates, reason = resolve_stats_player_identity(
            provider_player_id=prov_player_id,
            player_name=player_name,
            registry=registry,
            candidate_signals=candidate_signals,
        )

        metrics: Dict[str, Any] = {
            "games_played": gp,
            "games_started": _parse_int("gameStartNum", required=False),
            "minutes_per_game": _parse_float("minutes", required=False),
            "seconds_per_game": _parse_float("seconds", required=False),
            "points_per_game": pts,
            "rebounds_per_game": reb,
            "offensive_rebounds_per_game": _parse_float("reboundsOffensive", required=False),
            "defensive_rebounds_per_game": _parse_float("reboundsDefensive", required=False),
            "assists_per_game": ast,
            "steals_per_game": _parse_float("steals", required=False),
            "blocks_per_game": _parse_float("blocked", required=False),
            "turnovers_per_game": _parse_float("turnovers", required=False),
            "fouls_per_game": _parse_float("fouls", required=False),
            "field_goals_made_per_game": _parse_float("fieldGoals", required=False),
            "field_goals_attempted_per_game": _parse_float("fieldGoalsAttempted", required=False),
            "field_goals_percentage": _parse_pct_str("fieldGoalsPercentage"),
            "field_goals_percentage_rate": _parse_float("fieldGoalsPercentageSort", required=False),
            "three_point_made_per_game": _parse_float("threePointGoals", required=False),
            "three_point_attempted_per_game": _parse_float("threePointAttempted", required=False),
            "three_point_percentage": _parse_pct_str("threePointPercentage"),
            "three_point_percentage_rate": _parse_float("threePointPercentageSort", required=False),
            "free_throws_made_per_game": _parse_float("freeThrows", required=False),
            "free_throws_attempted_per_game": _parse_float("freeThrowsAttempted", required=False),
            "free_throws_percentage": _parse_pct_str("freeThrowsPercentage"),
            "free_throws_percentage_rate": _parse_float("freeThrowsPercentageSort", required=False),
        }

        provenance = {
            "source_uri": provenance_base.get("url", f"{self.base_url}/api/player-base-list"),
            "endpoint": provenance_base.get("endpoint", "/api/player-base-list"),
            "request_contract": provenance_base.get("request_payload", {}),
            "raw_response_sha256": provenance_base["raw_sha256"],
            "decoded_sha256": provenance_base["decoded_sha256"],
            "captured_at": provenance_base.get("captured_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
            "provider_version": "cba_data_server_2026",
        }

        # Default fail-closed rights
        rights = {
            "classification": "UNKNOWN_FOR_REDISTRIBUTION",
            "public_export_allowed": False,
            "private_ai_consumption": "TARGET_SPECIFIC",
            "materialization_authorized": False,
        }

        raw_metrics_mvp = {
            k: record[k] for k in sorted(RAW_METRICS_MVP_FIELDS) if k in record
        }

        canonical_record = {
            "record_id": record_id,
            "semantic_grain": "PLAYER_SEASON_STATS",
            "schema_version": 1,
            "player_uid": player_uid,
            "identity_status": id_status,
            "identity_resolution": {
                "status": id_status,
                "candidate_player_uids": candidates,
                "resolution_method": reason,
                "reason": reason,
            },
            "provider": PROVIDER_CBA_OFFICIAL,
            "provider_player_id": prov_player_id,
            "player_name": player_name,
            "season": season,
            "competition": DEFAULT_COMPETITION,
            "match_type": match_type,
            "match_type_label": match_type_label,
            "scope": scope,
            "team_id": team_id,
            "team_name": team_name,
            "metrics": metrics,
            "raw_metrics": raw_metrics_mvp,
            "provenance": provenance,
            "rights": rights,
        }

        return validate_stats_record(canonical_record)
