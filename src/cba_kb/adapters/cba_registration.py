"""Strict adapter for the frozen official CBA registration source contract."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..aliases import Clubs, UnresolvedClub


API = "https://server.cbaleague.com/news_register/detail"
PAGE = "https://www.cbaleague.com/#/news-register/detail/{article_id}"
SOURCE_CONTRACT_REVISION = "r5-20260912-detail-discovery"
SOURCE_CONTRACTS = (
    ("2024-2025", "66b1de8bab"),
    ("2025-2026", "68932081bf"),
    ("2026-2027", "6a72fc344a"),
)
MIN_DELAY_SECONDS = 1.5
RETRIES = 3
TIMEOUT_SECONDS = 20
EXPECTED_TEAMS = 20
ROW_KEY_FIELDS = ("season", "child_article_id", "row_index")
REQUIRED_LOGICAL_FIELDS = (
    "运动员", "注册类型", "合同类别", "原CBA俱乐部", "公示截止时间", "备注",
)
CONTRACT_INFORMATION_VARIANTS = ("合同剩余年限", "合同到期日")
CHILD_LINK = re.compile(
    r"^https://www\.cbaleague\.com/#/news-register/detail/([0-9a-f]+)$"
)
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


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
            "kind": "discovery",
            "source_contract_revision": SOURCE_CONTRACT_REVISION,
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


def _json_data(raw):
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceSchemaDrift("SOURCE_JSON_SCHEMA_DRIFT") from exc
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise SourceSchemaDrift("SOURCE_JSON_SCHEMA_DRIFT")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SourceSchemaDrift("SOURCE_DATA_REQUIRED")
    return data


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def discover(raw, source):
    """Resolve the official child publications from one frozen index article."""
    data = _json_data(raw)
    html = data.get("detail_content")
    if not isinstance(html, str) or not html.strip():
        raise SourceSchemaDrift("SOURCE_DETAIL_CONTENT_REQUIRED")
    parser = _Links()
    parser.feed(html)
    children = {}
    for href, anchor_team_name in parser.links:
        if "/news-register/detail/" not in href:
            continue
        match = CHILD_LINK.fullmatch(href)
        if not match:
            raise SourceSchemaDrift("SOURCE_CHILD_LINK_MALFORMED")
        article_id = match.group(1)
        if article_id in children:
            raise SourceSchemaDrift("SOURCE_CHILD_ARTICLE_ID_DUPLICATE")
        children[article_id] = {
            "source_id": source["source_id"],
            "season": source["season"],
            "parent_index_article_id": source["article_id"],
            "article_id": article_id,
            "kind": "child",
            "anchor_team_name": anchor_team_name,
            "api_url": f"{API}?id={article_id}",
            "page_url": PAGE.format(article_id=article_id),
        }
    if not children:
        raise SourceSchemaDrift("SOURCE_CHILD_LINKS_REQUIRED")
    return [children[article_id] for article_id in sorted(children)]


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = 0
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.tables += 1
        elif tag == "tr":
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


def _compact(value):
    return re.sub(r"\s+", "", value or "")


def _title_team(title):
    match = re.search(r"（([^，,）)]+)", title or "")
    if not match:
        raise SourceSchemaDrift("SOURCE_TEAM_TITLE_MISSING")
    team = match.group(1).strip()
    if not team:
        raise SourceSchemaDrift("SOURCE_TEAM_TITLE_MISSING")
    return team


def _resolve_team(source, article_team, anchor_team, clubs):
    resolved = []
    for label, name in (("title", article_team), ("anchor", anchor_team)):
        if not name:
            continue
        try:
            resolved.append((label, clubs.resolve(name, source["season"])))
        except UnresolvedClub as exc:
            raise SourceSchemaDrift(
                f"SOURCE_TEAM_ALIAS_UNRESOLVED:{label}:{name}"
            ) from exc
    if not resolved:
        raise SourceSchemaDrift("SOURCE_TEAM_UNRESOLVED")
    club_ids = {club_id for _, club_id in resolved}
    if len(club_ids) != 1:
        raise SourceSchemaDrift("SOURCE_TEAM_ALIAS_CONFLICT")
    return club_ids.pop()


def normalize(raw, source, *, clubs=None):
    """Parse one official child registration table without inferring semantics."""
    data = _json_data(raw)
    html = data.get("detail_content")
    if not isinstance(html, str) or not html.strip():
        raise SourceSchemaDrift("SOURCE_DETAIL_CONTENT_REQUIRED")
    parser = _Table()
    parser.feed(html)
    if parser.tables != 1 or not parser.rows:
        raise SourceSchemaDrift("SOURCE_HTML_TABLE_MISSING")
    rows = [[_compact(cell) for cell in row] for row in parser.rows]
    header_index = None
    contract_field = None
    for index, row in enumerate(rows):
        labels = set(row)
        variants = [field for field in CONTRACT_INFORMATION_VARIANTS if field in labels]
        if set(REQUIRED_LOGICAL_FIELDS) <= labels and len(variants) == 1:
            header_index = index
            contract_field = variants[0]
            break
        if (set(REQUIRED_LOGICAL_FIELDS) <= labels and len(variants) != 1) or (
                len(set(REQUIRED_LOGICAL_FIELDS) & labels) >= 4 and not variants):
            raise SourceSchemaDrift("SOURCE_TABLE_SCHEMA_DRIFT")
    if header_index is None:
        raise SourceSchemaDrift("SOURCE_TABLE_SCHEMA_DRIFT")

    header = rows[header_index]
    positions = {label: header.index(label) for label in REQUIRED_LOGICAL_FIELDS}
    positions["合同原信息"] = header.index(contract_field)
    clubs = clubs or Clubs(DEFAULT_ROOT)
    club_id = _resolve_team(
        source, _title_team(data.get("title")), source.get("anchor_team_name"), clubs,
    )
    canonical_team = clubs.clubs[club_id]["official_domestic"][0]
    article_team = _title_team(data.get("title"))
    normalized = []
    for row_index, row in enumerate(rows[header_index + 1:], start=1):
        if not row or not row[0].isdigit():
            continue
        if len(row) < 8 or not row[positions["运动员"]]:
            raise SourceSchemaDrift("SOURCE_PLAYER_ROW_SCHEMA_DRIFT")
        normalized.append({
            "season": source["season"],
            "child_article_id": source["article_id"],
            "row_index": row_index,
            "raw_player_name": row[positions["运动员"]],
            "registration_type": row[positions["注册类型"]],
            "contract_category": row[positions["合同类别"]],
            "contract_information_field": contract_field,
            "contract_information": row[positions["合同原信息"]],
            "former_club": row[positions["原CBA俱乐部"]],
            "public_deadline": row[positions["公示截止时间"]],
            "notes": row[positions["备注"]] if positions["备注"] < len(row) else "",
            "raw_team_name": article_team,
            "anchor_team_name": source.get("anchor_team_name") or "",
            "canonical_team_id": club_id,
            "canonical_team_name": canonical_team,
            "source_url": source["page_url"],
            "event_date": None,
            "business_event": None,
        })
    if not normalized:
        raise SourceSchemaDrift("SOURCE_PLAYER_ROWS_REQUIRED")
    return normalized


def normalize_children(raw_by_article, children, source, *, clubs=None):
    """Normalize every discovered child and enforce canonical team coverage."""
    clubs = clubs or Clubs(DEFAULT_ROOT)
    rows = []
    canonical_teams = set()
    for child in children:
        raw = raw_by_article.get(child["article_id"])
        if raw is None:
            raise SourceSchemaDrift("SOURCE_CHILD_RAW_MISSING")
        child_rows = normalize(raw, child, clubs=clubs)
        rows.extend(child_rows)
        canonical_teams.update(row["canonical_team_id"] for row in child_rows)
    if len(canonical_teams) != EXPECTED_TEAMS:
        raise SourceSchemaDrift(
            f"CANONICAL_TEAM_COUNT_NOT_20:{len(canonical_teams)}"
        )
    return rows
