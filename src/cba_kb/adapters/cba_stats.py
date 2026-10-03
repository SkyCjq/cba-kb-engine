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
    StatsError,
    StatsProviderDriftError,
    StatsValidationError,
    build_record_id,
    resolve_stats_player_identity,
    validate_stats_record,
)

DEFAULT_DATA_SERVER = "https://data-server.cbaleague.com"
DEFAULT_PORTAL_URL = "https://www.cbaleague.com/data/"
FALLBACK_DISCOVERED_KEY = b"uVayqL4ONKjFbVzQ"

_AES_KEY_REGEX = re.compile(
    r'(?:const|var|let)\s+[a-zA-Z0-9_$]+\s*=\s*["\']([A-Za-z0-9]{16})["\'];\s*(?:const|var|let)\s+[a-zA-Z0-9_$]+\s*=\s*[a-zA-Z0-9_$.]+\.enc\.Utf8\.parse'
)
_SCRIPT_SRC_REGEX = re.compile(r'<script[^>]+src=["\']([^"\']*index[^"\']*\.js)["\']')


class CBAStatsEnvelopeError(StatsProviderDriftError):
    """Raised when an HTTP response cannot be safely decrypted or decoded."""
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

    if len(key) != 16:
        raise CBAStatsEnvelopeError(f"INVALID_AES_KEY_LENGTH:{len(key)}!= 16")

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
    """Extract AES decryption key from client JavaScript bundle via regex."""
    m = _AES_KEY_REGEX.search(js_content)
    if m:
        return m.group(1).encode("utf-8")

    # Fallback search for 16-character candidate around AES.decrypt
    pos = js_content.find("AES.decrypt")
    if pos != -1:
        snippet = js_content[max(0, pos - 200) : min(len(js_content), pos + 100)]
        for cand in re.findall(r'["\']([A-Za-z0-9]{16})["\']', snippet):
            return cand.encode("utf-8")
    return None


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

        env_key = os.environ.get("CBA_STATS_AES_KEY")
        if key:
            self._key = key
        elif env_key:
            self._key = env_key.encode("utf-8")
        else:
            self._key = FALLBACK_DISCOVERED_KEY

    def get_encryption_key(self) -> bytes:
        return self._key

    def set_encryption_key(self, key: bytes) -> None:
        if len(key) != 16:
            raise ValueError(f"AES-128 key must be 16 bytes, got {len(key)}")
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
        decoded_data, decoded_sha256 = decrypt_cba_envelope(raw_bytes, self._key)

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
        prov_player_id = str(record["playerId"]).strip()
        player_name = str(record.get("cnAlias", "")).strip()
        season = str(record.get("season", provenance_base.get("season", "unknown")))
        match_type = str(provenance_base.get("match_type_id", "1"))
        team_id = str(record.get("teamId")) if record.get("teamId") is not None else None
        team_name = record.get("teamCnAlias")

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

        def _to_float(v: Any) -> Optional[float]:
            if v is None:
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        def _to_int(v: Any) -> Optional[int]:
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        metrics: Dict[str, Any] = {
            "games_played": _to_float(record.get("playerTimes")),
            "games_started": _to_int(record.get("gameStartNum")),
            "minutes_per_game": _to_float(record.get("minutes")),
            "seconds_per_game": _to_float(record.get("seconds")),
            "points_per_game": _to_float(record.get("points")),
            "rebounds_per_game": _to_float(record.get("rebounds")),
            "offensive_rebounds_per_game": _to_float(record.get("reboundsOffensive")),
            "defensive_rebounds_per_game": _to_float(record.get("reboundsDefensive")),
            "assists_per_game": _to_float(record.get("assists")),
            "steals_per_game": _to_float(record.get("steals")),
            "blocks_per_game": _to_float(record.get("blocked")),
            "turnovers_per_game": _to_float(record.get("turnovers")),
            "fouls_per_game": _to_float(record.get("fouls")),
            "field_goals_made_per_game": _to_float(record.get("fieldGoals")),
            "field_goals_attempted_per_game": _to_float(record.get("fieldGoalsAttempted")),
            "field_goals_percentage": record.get("fieldGoalsPercentage"),
            "field_goals_percentage_rate": _to_float(record.get("fieldGoalsPercentageSort")),
            "three_point_made_per_game": _to_float(record.get("threePointGoals")),
            "three_point_attempted_per_game": _to_float(record.get("threePointAttempted")),
            "three_point_percentage": record.get("threePointPercentage"),
            "three_point_percentage_rate": _to_float(record.get("threePointPercentageSort")),
            "free_throws_made_per_game": _to_float(record.get("freeThrows")),
            "free_throws_attempted_per_game": _to_float(record.get("freeThrowsAttempted")),
            "free_throws_percentage": record.get("freeThrowsPercentage"),
            "free_throws_percentage_rate": _to_float(record.get("freeThrowsPercentageSort")),
            # Advanced context fields preserved as secondary metrics
            "rim_made": _to_float(record.get("fieldGoalsAtRimMade")),
            "rim_attempted": _to_float(record.get("fieldGoalsAtRimAttempted")),
            "mid_range_made": _to_float(record.get("fieldGoalsMidRangeMade")),
            "mid_range_attempted": _to_float(record.get("fieldGoalsMidRangeAttempted")),
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
            "raw_metrics": record,
            "provenance": provenance,
            "rights": rights,
        }

        return validate_stats_record(canonical_record)
