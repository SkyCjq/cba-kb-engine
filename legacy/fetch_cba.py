#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch immutable CBA domestic-player registration snapshots.

The public endpoint is ``https://server.cbaleague.com/news_register/detail``.
Every fetch writes a timestamped snapshot; it never overwrites prior evidence.
This module preserves the website's raw table labels.  Normalization and
business inference belong downstream.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
SEED_CSV = ROOT / "20_data_结构化事实" / "cba_team_pages.csv"
SNAP_DIR = ROOT / "10_sources_原始证据" / "official_cba" / "snapshots"

API = "https://server.cbaleague.com/news_register/detail"
PAGE = "https://www.cbaleague.com/#/news-register/detail/{}"
SEASON_INDEX = {
    "2024-2025": "66b1de8bab",
    "2025-2026": "68932081bf",
    "2026-2027": "6a72fc344a",
}
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ID_RE = re.compile(r"detail[/?=](?:id=)?([0-9a-f]{8,12})")


def get_json(article_id: str, retries: int = 3, timeout: int = 20) -> dict[str, Any]:
    url = f"{API}?{urlencode({'id': article_id})}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Referer": "https://www.cbaleague.com/",
                    "Accept": "application/json, text/plain, */*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            if not isinstance(payload, dict):
                raise ValueError(f"JSON 顶层不是对象：{type(payload).__name__}")
            return payload
        except Exception as error:  # noqa: BLE001 - retry network and decode errors
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2**attempt + random.random())
    raise RuntimeError(f"{article_id} 抓取失败: {last_error}")


def walk(obj: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    """Yield ``(JSON path, value)`` pairs for schema inspection."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(obj, list):
        yield path + "[]", obj
        for value in obj[:1]:
            yield from walk(value, f"{path}[0]")
    else:
        yield path, obj


def find_html(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Find the first JSON string containing an HTML table."""
    for path, value in walk(payload):
        if isinstance(value, str) and "<table" in value.lower():
            return path, value
    return None, None


def normalize_cell_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _span(cell, attribute: str) -> int:
    try:
        return max(1, int(cell.get(attribute, "1")))
    except (TypeError, ValueError):
        return 1


def _unique_headers(labels: list[str]) -> list[str]:
    totals: dict[str, int] = {}
    for label in labels:
        totals[label] = totals.get(label, 0) + 1
    seen: dict[str, int] = {}
    output = []
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
        if not label:
            output.append(f"未命名列_{len(output) + 1}")
        elif totals[label] == 1:
            output.append(label)
        else:
            output.append(f"{label}_{seen[label]}")
    return output


def _expanded_header(row) -> list[str]:
    labels = []
    for cell in row.xpath("./td|./th"):
        label = normalize_cell_text(cell.text_content())
        labels.extend([label] * _span(cell, "colspan"))
    return _unique_headers(labels)


def _expanded_values(row) -> list[str]:
    values = []
    for cell in row.xpath("./td|./th"):
        value = normalize_cell_text(cell.text_content())
        values.append(value)
        values.extend([""] * (_span(cell, "colspan") - 1))
    return values


def parse_table(html: str) -> list[dict[str, str]]:
    """Parse player rows from the live CBA HTML table.

    The live page has title and update-time rows before the real header.  The
    official ``注册类型`` header spans two raw columns, so duplicate labels are
    retained as ``注册类型_1`` and ``注册类型_2`` instead of inventing semantics.
    Footnotes after the numbered player rows are excluded.
    """
    try:
        from lxml import html as lxml_html
    except ImportError:
        raise SystemExit("需要 lxml：pip install lxml") from None

    document = lxml_html.fromstring(html)
    candidates: list[list[dict[str, str]]] = []
    for table in document.xpath("//table"):
        rows = table.xpath(".//tr")
        header_index = None
        header: list[str] = []
        for index, row in enumerate(rows):
            candidate = _expanded_header(row)
            compact = [re.sub(r"\s+", "", value) for value in candidate]
            if any("运动员" in value or "球员" in value for value in compact) and any(
                "序号" in value for value in compact
            ):
                header_index = index
                header = candidate
                break
        if header_index is None:
            continue

        parsed = []
        for row in rows[header_index + 1 :]:
            values = _expanded_values(row)
            if not values:
                continue
            sequence = re.sub(r"\s+", "", values[0])
            if not sequence.isdigit():
                continue
            if len(values) < len(header):
                values.extend([""] * (len(header) - len(values)))
            elif len(values) > len(header):
                values = values[: len(header)]
            parsed.append(dict(zip(header, values)))
        if parsed:
            candidates.append(parsed)

    if not candidates:
        return []
    return max(candidates, key=len)


def probe(article_id: str) -> Path:
    payload = get_json(article_id)
    print(f"--- {article_id} live probe ---")
    print("top_level_keys:", list(payload))
    print("code:", payload.get("code"), "message:", payload.get("message"))
    data = payload.get("data")
    print("data_type:", type(data).__name__)
    print("data_keys:", list(data) if isinstance(data, dict) else [])

    path, html = find_html(payload)
    print("table_field:", path or "未找到")
    rows = parse_table(html) if html else []
    print("parsed_player_rows:", len(rows))
    print("parsed_fields:", list(rows[0]) if rows else [])

    output = SNAP_DIR / "_probe" / f"{article_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("raw_json:", output)
    if payload.get("code") != 200 or not isinstance(data, dict) or not html or not rows:
        raise RuntimeError("live probe 未通过结构与表格校验；原始 JSON 已保留")
    return output


def team_ids_from_index(season: str) -> tuple[list[str], str]:
    payload = get_json(SEASON_INDEX[season])
    blob = json.dumps(payload, ensure_ascii=False)
    ids: list[str] = []
    seen = {SEASON_INDEX[season]}
    for match in ID_RE.finditer(blob):
        article_id = match.group(1)
        if article_id not in seen:
            seen.add(article_id)
            ids.append(article_id)
    if len(ids) >= 15:
        return ids, "index"
    with SEED_CSV.open(encoding="utf-8-sig") as handle:
        fallback = [
            row["article_id"]
            for row in csv.DictReader(handle)
            if row["season"] == season
        ]
    return fallback, "seed_csv"


def fetch_season(season: str, delay: float = 1.5) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = SNAP_DIR / season / stamp
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)

    article_ids, id_source = team_ids_from_index(season)
    print(f"[{season}] 球队页 {len(article_ids)} 个（来源: {id_source}）")
    if len(article_ids) != 20:
        print(f"  ! 预期 20 个球队页，实际 {len(article_ids)} 个；本次快照需复核")

    rows: list[dict[str, Any]] = []
    metadata = []
    for index, article_id in enumerate(article_ids, 1):
        payload = get_json(article_id)
        raw_path = output_dir / "raw" / f"{article_id}.json"
        raw_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        table_path, html = find_html(payload)
        players = parse_table(html) if html else []
        blob = json.dumps(payload, ensure_ascii=False)
        title = next(
            (
                value
                for path, value in walk(payload)
                if path.endswith(("title", "Title")) and isinstance(value, str)
            ),
            "",
        )
        for sequence, player in enumerate(players, 1):
            rows.append(
                {
                    "season": season,
                    "article_id": article_id,
                    "sequence_official": sequence,
                    "source_article_title": title,
                    "source_team_url": PAGE.format(article_id),
                    "source_api_url": f"{API}?id={article_id}",
                    "retrieved_at_utc": stamp,
                    **player,
                }
            )
        metadata.append(
            {
                "article_id": article_id,
                "title": title,
                "n_players": len(players),
                "table_path": table_path,
                "sha256": hashlib.sha256(blob.encode()).hexdigest()[:16],
            }
        )
        print(
            f"  {index:>2}/{len(article_ids)} {article_id} "
            f"{len(players):>3} 人 {title[:40]}"
        )
        time.sleep(delay + random.random())

    fields = sorted({key for row in rows for key in row})
    lead = ["season", "article_id", "sequence_official"]
    fields = lead + [field for field in fields if field not in lead]
    with (output_dir / "players_raw.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "season": season,
                "retrieved_at_utc": stamp,
                "id_source": id_source,
                "n_teams": len(article_ids),
                "n_players": len(rows),
                "teams": metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[{season}] {len(rows)} 条 -> {output_dir}")
    return output_dir


def load_snapshot(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    with (path / "players_raw.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    name_column = next(
        (
            column
            for column in ("运动员", "姓名", "球员", "player", "球员姓名")
            if rows and column in rows[0]
        ),
        None,
    )
    if not name_column:
        raise SystemExit(f"{path} 中找不到姓名列，请检查 players_raw.csv 表头")
    keyed = {
        f"{row['season']}|{row['article_id']}|{row[name_column]}": row for row in rows
    }
    return keyed, name_column


def diff(season: str) -> Path:
    snapshots = sorted(
        path
        for path in (SNAP_DIR / season).iterdir()
        if path.is_dir() and (path / "players_raw.csv").exists()
    )
    if len(snapshots) < 2:
        raise SystemExit(f"{season} 只有 {len(snapshots)} 个快照，无法比对")
    old_path, new_path = snapshots[-2], snapshots[-1]
    old, _ = load_snapshot(old_path)
    new, _ = load_snapshot(new_path)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for key in sorted(set(old) & set(new)):
        for field in set(old[key]) & set(new[key]):
            if field == "retrieved_at_utc":
                continue
            if old[key][field] != new[key][field]:
                changed.append((key, field, old[key][field], new[key][field]))

    lines = [
        f"# {season} 注册信息变更",
        "",
        f"- 上次快照：`{old_path.name}`",
        f"- 本次快照：`{new_path.name}`",
        f"- 新增 {len(added)} / 消失 {len(removed)} / 字段变更 {len(changed)}",
        "",
    ]
    for title, items in (("## 新增", added), ("## 消失", removed)):
        lines.append(title)
        lines += [f"- {key}" for key in items] or ["- 无"]
        lines.append("")
    lines.append("## 字段变更")
    lines += [
        f"- {key} · {field}：`{old_value}` → `{new_value}`"
        for key, field, old_value, new_value in changed
    ] or ["- 无"]
    output = SNAP_DIR / season / f"changes_{new_path.name}.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"变更日志 -> {output}\n新增 {len(added)} / "
        f"消失 {len(removed)} / 变更 {len(changed)}"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", metavar="ARTICLE_ID")
    parser.add_argument("--season", choices=sorted(SEASON_INDEX))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--diff", metavar="SEASON", choices=sorted(SEASON_INDEX))
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()
    selected = sum(bool(value) for value in (args.probe, args.season, args.all, args.diff))
    if selected > 1:
        raise SystemExit("--probe / --season / --all / --diff 只能选一个")
    if args.probe:
        probe(args.probe)
    elif args.diff:
        diff(args.diff)
    elif args.all:
        for season in sorted(SEASON_INDEX):
            fetch_season(season, args.delay)
    elif args.season:
        fetch_season(args.season, args.delay)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
