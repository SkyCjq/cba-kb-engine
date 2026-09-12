"""Strict adapter for the frozen official CBA registration source contract."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import random
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API = "https://server.cbaleague.com/news_register/detail"
PAGE = "https://www.cbaleague.com/#/news-register/detail/{article_id}"
SOURCE_CONTRACTS = (
    ("2024-2025", "66b1de8bab"),
    ("2025-2026", "68932081bf"),
    ("2026-2027", "6a72fc344a"),
)
MIN_DELAY_SECONDS = 1.5
RETRIES = 3
TIMEOUT_SECONDS = 20
EXPECTED_TEAMS = 20
ROW_KEY_FIELDS = ("season", "article_id", "raw_player_name")


class SourceFetchClosed(RuntimeError):
    def __init__(self, message, requests=0):
        super().__init__(message)
        self.requests = requests


class SourceSchemaDrift(ValueError):
    pass


def registry():
    return {
        f"cba-registration:{season}": {
            "source_id": f"cba-registration:{season}",
            "season": season,
            "article_id": article_id,
            "api_url": f"{API}?id={article_id}",
            "page_url": PAGE.format(article_id=article_id),
            "rights_basis": "official_public_read_only",
        }
        for season, article_id in SOURCE_CONTRACTS
    }


def fetch(source, *, opener=urlopen, sleeper=time.sleep, randomizer=None):
    """Read one official JSON response with bounded retries and backoff."""
    rng = randomizer or random.Random()
    request = Request(source["api_url"], headers={"Accept": "application/json"})
    failures = []
    for attempt in range(RETRIES + 1):
        if attempt:
            sleeper(MIN_DELAY_SECONDS * (2 ** (attempt - 1)) + rng.uniform(0, .5))
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                if status in (403, 429):
                    raise SourceFetchClosed(f"SOURCE_HTTP_{status}", attempt + 1)
                if status != 200:
                    raise URLError(f"unexpected HTTP status {status}")
                raw = response.read()
                if not raw:
                    raise SourceSchemaDrift("SOURCE_EMPTY_RESPONSE")
                return raw, attempt + 1
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise SourceFetchClosed(f"SOURCE_HTTP_{exc.code}", attempt + 1) from exc
            failures.append(type(exc).__name__)
        except SourceFetchClosed:
            raise
        except SourceSchemaDrift:
            raise
        except (OSError, TimeoutError, URLError) as exc:
            failures.append(type(exc).__name__)
    raise SourceFetchClosed(
        "SOURCE_RETRIES_EXHAUSTED:" + ",".join(failures), RETRIES + 1,
    )


def _find_html(value):
    if isinstance(value, str) and "<table" in value.lower():
        return value
    if isinstance(value, dict):
        for key in ("content", "newsContent", "news_content", "html", "detail"):
            if key in value:
                found = _find_html(value[key])
                if found:
                    return found
        for child in value.values():
            found = _find_html(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_html(child)
            if found:
                return found
    return None


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def normalize(raw, source):
    """Parse the embedded official HTML table without inferring identities/dates."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceSchemaDrift("SOURCE_JSON_SCHEMA_DRIFT") from exc
    html = _find_html(payload)
    if not html:
        raise SourceSchemaDrift("SOURCE_HTML_TABLE_MISSING")
    parser = _Table()
    parser.feed(html)
    rows = [row for row in parser.rows if len(row) >= 2]
    if rows and any(token in rows[0][0].lower() for token in ("球队", "俱乐部", "team")):
        rows = rows[1:]
    teams = []
    normalized = []
    for row in rows:
        team = re.sub(r"\s+", " ", row[0]).strip()
        if not team:
            raise SourceSchemaDrift("SOURCE_TEAM_MISSING")
        teams.append(team)
        names = []
        for cell in row[1:]:
            names.extend(part.strip() for part in re.split(r"[\n,，;；]+", cell) if part.strip())
        if not names:
            raise SourceSchemaDrift("SOURCE_PLAYER_LIST_MISSING")
        for name in names:
            normalized.append({
                "season": source["season"],
                "article_id": source["article_id"],
                "raw_player_name": name,
                "raw_team_name": team,
                "source_url": source["page_url"],
                "event_date": None,
                "business_event": None,
            })
    if len(set(teams)) != EXPECTED_TEAMS:
        raise SourceSchemaDrift(f"TEAM_PAGE_COUNT_NOT_20:{len(set(teams))}")
    if len(teams) != len(set(teams)):
        raise SourceSchemaDrift("SOURCE_TEAM_DUPLICATE")
    return normalized
