#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合订本（bundle）生成器 —— 整套流程里唯一面向"消费端"的脚本。

为什么要合订：
  · NotebookLM/Gemini Notebook 每个 notebook 的来源数有上限（Standard 50，付费档
    100/300/500/600），单源上限 50 万字 / 200MB。1019 条记录 + 几百篇笔记不可能逐条塞。
  · ChatGPT 和 ima 的 RAG 对"几百个小文件"检索质量明显差于"十几个结构清晰的大文件 + 一份索引"。

输出（全部写到 40_ai_投喂与索引/）：
  CBA_注册_2024-2025.md / 2025-2026.md / 2026-2027.md   每赛季一份，按球队分节
  CBA_跨赛季变动.md                                       换队与合同到期
  笔记_<主题>.md                                          10_sources_原始证据/wechat/processed 与 30_notes_人工知识 按 tags 聚合
  INDEX.md                                                总索引，喂给模型的第一份文件

投喂建议：
  · NotebookLM —— 用 Drive API 以 Google Docs 格式上传这些 .md（转换后才享受自动同步；
    PDF 和普通上传文件不会自动更新）。20_data_结构化事实/*.csv 以 Google Sheets 格式上传。
  · ChatGPT —— Project 里挂 40_ai_投喂与索引/ 所在的 Drive 文件夹，提示词里让它先读 INDEX.md。
  · ima —— 每月拖一次这十来个文件，而不是每天拖几十个碎片。

用法: python3 build_bundles.py
"""
import csv, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "20_data_结构化事实"
WECHAT = ROOT / "10_sources_原始证据" / "wechat" / "processed"
NOTES = ROOT / "30_notes_人工知识"
OUT = ROOT / "40_ai_投喂与索引"
DATA_DISPLAY = "20_data_结构化事实"

# V1.1_FINAL_MASTER_GUARD
FINAL_MASTER = DATA / "CBA_2017-2027_国内球员注册_MASTER.xlsx"

def guard_legacy_bundle_builder() -> None:
    """Prevent the pre-MASTER builder from overwriting current AI views with stale partial data."""
    if FINAL_MASTER.exists():
        raise SystemExit(
            "CBA-KB v1.1 FINAL: build_bundles.py is a legacy pre-MASTER builder and is disabled "
            "when CBA_2017-2027_国内球员注册_MASTER.xlsx is present. "
            "Do not regenerate 40_ai from archived cba_registrations/player_club_changes inputs. "
            "Use a future MASTER-aware v2.1 builder."
        )

OUT_DISPLAY = "40_ai_投喂与索引"
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

FIELDS = [
    ("registration_type", "注册类型"), ("registration_method", "注册方式"),
    ("contract_category", "合同类别"), ("contract_term_field", "合同期限字段"),
    ("contract_term_official", "官网原值"), ("contract_expiry_date_iso", "到期日ISO"),
    ("former_cba_club", "原CBA俱乐部"), ("disclosure_deadline", "公示截止"),
    ("registration_status", "状态"), ("notes", "备注"),
    ("loan_destination", "租借去向"),
]


def rd(name):
    p = DATA / name
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def season_bundle(season, rows):
    by_club = defaultdict(list)
    for r in rows:
        by_club[(r["club_normalized"], r["club_id"])].append(r)
    L = [f"# CBA {season} 赛季国内球员注册信息", "",
         f"生成时间：{STAMP}　|　记录数：{len(rows)}　|　球队：{len(by_club)}",
         "",
         "本文件由 CBA 官网各球队注册明细页（server.cbaleague.com/news_register/detail）",
         "抓取后生成。事实字段保留官网原文，未做任何解释性加工。每支球队标注该队明细页链接，",
         "引用时请以链接页当前状态为准 —— 官网页面为实时更新，本文件只代表抓取时刻的状态。", ""]
    for (club, cid), rs in sorted(by_club.items(), key=lambda x: x[0][1]):
        rs.sort(key=lambda r: int(r["sequence_official"] or 0))
        L += [f"## {club}", "",
              f"- club_id：`{cid}`　注册人数：{len(rs)}",
              f"- 官网明细页：{rs[0]['source_team_url']}",
              f"- 官网更新时间：{rs[0]['source_update_time']}", ""]
        for r in rs:
            parts = [f"{lab}：{r[k]}" for k, lab in FIELDS if r.get(k) and r[k] != "/"]
            L.append(f"{r['sequence_official']}. **{r['player']}** —— " + "；".join(parts))
        L.append("")
    return "\n".join(L)


def moves_bundle(moves, regs):
    by_season = defaultdict(list)
    for m in moves:
        by_season[m["to_season"]].append(m)
    L = [f"# CBA 跨赛季变动（2024-2025 → 2026-2027）", "",
         f"生成时间：{STAMP}　|　换队记录：{len(moves)}", "",
         "口径说明：以球员姓名为跨赛季主键，比对相邻两个赛季的 club_id 是否变化后推导得出，",
         "**不是官网直接给出的字段**。已用官网 registration_type 交叉验证。同名球员会造成误判，",
         "首次出现在最早赛季的球员无法回溯上一站。", ""]
    for s in sorted(by_season):
        ms = by_season[s]
        L += [f"## {s} 赛季（{len(ms)} 人）", ""]
        for m in sorted(ms, key=lambda x: x["to_club"]):
            L.append(f"- **{m['player']}**：{m['from_club']} → {m['to_club']}　"
                     f"（{m['to_registration_type']}/{m['to_registration_method']}，"
                     f"{m['to_contract_category']}）　{m['source_team_url']}")
        L.append("")
    exp = defaultdict(list)
    for r in regs:
        d = r.get("contract_expiry_date_iso") or ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            exp[d[:4]].append(r)
    L += ["## 合同到期年份分布（仅 2025-2026 起官网提供到期日字段）", ""]
    for y in sorted(exp):
        L.append(f"- {y} 年：{len(exp[y])} 人")
    return "\n".join(L) + "\n"


def parse_fm(text):
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    out = {}
    for ln in fm.splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        try:
            out[k.strip()] = json.loads(v)
        except Exception:                            # noqa: BLE001
            out[k.strip()] = v.strip('"')
    return out, body


def note_bundles():
    roots = [p for p in (WECHAT, NOTES) if p.exists()]
    if not roots:
        return [], 0
    groups, n = defaultdict(list), 0
    paths = []
    for r in roots:
        paths.extend(r.glob("*.md"))
    for p in sorted(paths):
        fm, body = parse_fm(p.read_text(encoding="utf-8"))
        n += 1
        tags = fm.get("tags") or ["未分类"]
        for t in (tags if isinstance(tags, list) else [tags]) or ["未分类"]:
            groups[str(t)].append((fm, body, p))
    made = []
    for tag, items in groups.items():
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", tag)
        L = [f"# 笔记合订本 · {tag}", "",
             f"生成时间：{STAMP}　|　篇数：{len(items)}", ""]
        for fm, body, p in sorted(items, key=lambda x: x[0].get("published_at", ""), reverse=True):
            L += [f"## {fm.get('title') or p.stem}", "",
                  f"- 来源：{fm.get('source_url','')}",
                  f"- 公众号/作者：{fm.get('account','')} {fm.get('author','')}".rstrip(),
                  f"- 发布：{fm.get('published_at','')}　采集：{fm.get('captured_at','')}",
                  f"- uid：`{fm.get('uid', p.stem)}`", "", body.strip(), ""]
        f = OUT / f"笔记_{safe}.md"
        f.write_text("\n".join(L), encoding="utf-8")
        made.append((f, len(items)))
    return made, n


def main():
    guard_legacy_bundle_builder()
    OUT.mkdir(parents=True, exist_ok=True)
    # 清掉上一轮由笔记生成、但本轮已无来源的旧笔记 bundle，避免 dry-run 误上传陈旧产物。
    for stale in OUT.glob("笔记_*.md"):
        stale.unlink()
    regs, moves = rd("cba_registrations.csv"), rd("player_club_changes.csv")
    by_season = defaultdict(list)
    for r in regs:
        by_season[r["season"]].append(r)

    files = []
    for s in sorted(by_season):
        f = OUT / f"CBA_注册_{s}.md"
        f.write_text(season_bundle(s, by_season[s]), encoding="utf-8")
        files.append((f, len(by_season[s]), f"{s} 赛季全部注册记录，按球队分节"))
    if moves:
        f = OUT / "CBA_跨赛季变动.md"
        f.write_text(moves_bundle(moves, regs), encoding="utf-8")
        files.append((f, len(moves), "跨赛季换队与合同到期年份分布（推导字段）"))
    nb, n_notes = note_bundles()
    for f, c in nb:
        files.append((f, c, "公众号/本地文件笔记合订本"))

    # ---- 总索引 ----
    L = ["# 知识库总索引（INDEX）", "",
         f"生成时间：{STAMP}", "",
         "**给模型的使用说明**：先读本索引确定该看哪个文件，再定位具体内容。",
         "涉及人数、队伍数、日期分布等可计数的问题，一律以 `20_data_结构化事实/` 中的结构化主表为准，",
         "不要从合订本正文里数数。合订本正文用于解释背景和引用原文。", "",
         "## 权威口径", "",
         "- 事实来源：CBA 官网各球队注册明细页，接口 `https://server.cbaleague.com/news_register/detail?id=<article_id>`",
         "- 赛季总页：2024-2025 `66b1de8bab`｜2025-2026 `68932081bf`｜2026-2027 `6a72fc344a`",
         "- 字段口径差异：2024-2025 为「合同剩余年限」，2025-2026 起改为「合同到期日」，两者不可直接比较",
         "- 官网页面实时更新，所有结论都必须带抓取时间",
         "", "## 结构化数据（问数字、做统计用这些）", ""]
    for n, d in [("cba_registrations.csv", f"规范长表，{len(regs)} 行 × 29 列，主键 record_key = 赛季|球队|姓名"),
                 ("cba_team_pages.csv", "60 个球队明细页的 article_id 与接口地址，抓取种子表"),
                 ("player_club_changes.csv", f"{len(moves)} 条跨赛季换队（推导字段，非官网原文）")]:
        if (DATA / n).exists():
            L.append(f"- `{DATA_DISPLAY}/{Path(n).stem}` —— {d}")
    if (DATA / "cba_registrations.jsonl").exists():
        L.append("- `cba_registrations.jsonl` 仅保留为本地可选机器导出，不属于当前 Drive/AI 默认来源")
    L += ["", "## 合订本（问背景、要引用原文用这些）", ""]
    for f, c, d in files:
        L.append(f"- `{OUT_DISPLAY}/{f.stem}` —— {d}（{c} 条；Google Docs 的 Drive fileSize 不能用于判断正文完整性）")
    L += ["", "## 已知数据缺口", "",
          "- 本库仅覆盖**国内球员**注册信息；外籍球员不在这三个官网页面口径内",
          "- 2024-2025 赛季无「合同到期日」字段，只有「合同剩余年限」",
          f"- 笔记区当前 {n_notes} 篇，公众号采集管线尚未大规模回填", ""]
    (OUT / "INDEX.md").write_text("\n".join(L), encoding="utf-8")

    print(f"合订本 {len(files)} 份 + INDEX.md -> {OUT}")
    for f, c, _ in files:
        print(f"  {f.name:<32} {c:>5} 条  {f.stat().st_size // 1024:>4} KB")


if __name__ == "__main__":
    main()
