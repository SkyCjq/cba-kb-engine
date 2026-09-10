#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
笔记自动归类 —— 填 frontmatter 的 players / teams / season / tags。

核心判断：**球员和球队不该交给 LLM 抽取。**

我们手里已经有 1,019 条官网注册记录，486 个真实球员姓名、20 支球队及其历年官方名。
拿这份名单去正文里做确定性匹配，准确率可控、可复现、零成本、零幻觉；
LLM 抽人名会把「小外援」「某后卫」当实体，还会把同一个人写成三种形式。
只有主题标签（topic）这种真正需要理解语义的，才值得上模型 —— 而且必须限定在受控词表内。

用法：
    python3 classify_notes.py                 # 归类所有 status=待归类 的笔记
    python3 classify_notes.py --all           # 全部重跑
    python3 classify_notes.py --dry-run
    python3 classify_notes.py --report        # 只看统计和未覆盖词，不改文件

两字姓名的问题：中文没有词边界，「刘东」「张帆」「周鹏」这类两字名极易在别的词里
误命中。本脚本把它们单独放进 players_low_confidence，不混进 players，
由你抽查后决定是否提升。这是刻意的保守设计，宁可漏不可错。
"""
import argparse, csv, json, re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WECHAT = ROOT / "10_sources_原始证据" / "wechat" / "processed"
NOTES = ROOT / "30_notes_人工知识"
REG = ROOT / "20_data_结构化事实" / "cba_registrations.csv"

# 球队别名：官网自己改过名，正文里的写法更杂。键是 club_id。
TEAM_ALIASES = {
    "jilin_jiutai": ["吉林九台农商行", "吉林东北虎", "吉林队", "东北虎"],
    "shandong_gaosu": ["山东高速", "山东山高", "山东队"],
    "guangzhou_longshi": ["广州龙狮", "龙狮", "广州队"],
    "liaoning_shenyang_sansheng": ["辽宁沈阳三生", "辽宁队", "辽篮"],
    "beijing_shougang": ["北京首钢", "首钢", "北京队"],
    "beijing_konggu": ["北京控股", "北控"],
    "guangdong_hongyuan": ["广东宏远", "宏远", "广东队"],
    "zhejiang_guangsha": ["浙江广厦", "广厦"],
    "zhejiang_chouzhou": ["浙江稠州", "稠州", "浙江队"],
    "xinjiang_guanghui": ["新疆广汇", "新疆队"],
    "shanghai_jiushi": ["上海久事", "上海队"],
    "shenzhen_xinshiji": ["深圳新世纪", "深圳队"],
    "qingdao_guoxin_haitian": ["青岛国信海天", "青岛队"],
    "nanjing_tongxi": ["南京同曦", "同曦"],
    "jiangsu_kendiya": ["江苏肯帝亚", "肯帝亚"],
    "sichuan_jincheng": ["四川锦城", "四川队"],
    "tianjin_ronggang": ["天津荣钢", "天津队"],
    "shanxi_fenjiu": ["山西汾酒", "山西队"],
    "fujian_xunxing": ["福建浔兴", "浔兴"],
    "ningbo_fubang": ["宁波富邦", "宁波队"],
}

# 主题标签的关键词规则。取值必须在 config/taxonomy.yaml 的 topic 列表内。
TOPIC_RULES = {
    "注册与转会": ["注册", "转会", "签约", "加盟", "交易", "认领", "顶薪合同", "自由球员", "独家签约权"],
    "合同与薪资": ["合同", "薪资", "工资帽", "顶薪", "底薪", "续约", "买断", "球员合同"],
    "选秀": ["选秀", "状元", "顺位", "试训营", "选秀大会"],
    "伤病": ["伤病", "受伤", "手术", "康复", "复出", "赛季报销", "十字韧带"],
    "赛程与战绩": ["赛程", "战绩", "季后赛", "常规赛", "总决赛", "排名", "客场", "主场", "比分"],
    "俱乐部运营": ["俱乐部", "管理层", "主教练", "换帅", "总经理", "股权", "母公司"],
    "联赛政策": ["新规", "政策", "规程", "联赛办公室", "篮协", "准入", "工资帽制度", "外援政策"],
    "商业与赞助": ["赞助", "球衣", "冠名", "商务", "版权", "票务", "上座"],
    "青训与梯队": ["青训", "梯队", "U19", "U17", "青年队", "自行培养"],
    "外援": ["外援", "亚外", "小外", "大外", "归化"],
}


def load_entities():
    players, clubs = set(), {}
    with REG.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["player"]:
                players.add(r["player"])
            clubs[r["club_id"]] = r["club_normalized"]
    aliases = {}
    for cid, name in clubs.items():
        for a in {name, *TEAM_ALIASES.get(cid, [])}:
            aliases[a] = cid
    return players, clubs, aliases


def split_fm(text):
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    out = {}
    for ln in fm.splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        try:
            out[k.strip()] = json.loads(v.strip())
        except Exception:                                    # noqa: BLE001
            out[k.strip()] = v.strip().strip('"')
    return out, body.lstrip("\n")


def dump_fm(fm, body):
    return "\n".join(["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}"
                                for k, v in fm.items()] + ["---", "", body.rstrip(), ""])


def classify(body, players, clubs, aliases):
    hits_hi, hits_lo = [], []
    for p in sorted(players, key=len, reverse=True):
        if p in body:
            (hits_hi if len(p) >= 3 else hits_lo).append(p)

    teams = []
    for a in sorted(aliases, key=len, reverse=True):
        if a in body and aliases[a] not in teams:
            teams.append(aliases[a])

    seasons = sorted(set(re.findall(r"(20\d{2}-20\d{2})", body)))
    tags = [t for t, kws in TOPIC_RULES.items() if any(k in body for k in kws)]

    # 有实体但无主题时，人工看一眼比乱贴标签强
    status = "已归类" if (tags and (hits_hi or teams)) else "待归类"
    return {
        "players": sorted(hits_hi),
        "players_low_confidence": sorted(hits_lo),
        "teams": teams,
        "team_names": [clubs[t] for t in teams],
        "season": seasons[0] if len(seasons) == 1 else "",
        "seasons_mentioned": seasons,
        "tags": tags,
        "status": status,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    players, clubs, aliases = load_entities()
    print(f"实体库：{len(players)} 名球员，{len(clubs)} 支球队（{len(aliases)} 个别名）")
    roots = [r for r in (WECHAT, NOTES) if r.exists()]
    paths = []
    for r in roots:
        paths.extend(r.glob("*.md"))
    if not paths:
        print(f"{WECHAT} 与 {NOTES} 下都没有可归类 Markdown。")
        return

    stat, unresolved = Counter(), Counter()
    for p in sorted(paths):
        fm, body = split_fm(p.read_text(encoding="utf-8"))
        if not a.all and fm.get("status") not in ("待归类", "", None):
            continue
        res = classify(body, players, clubs, aliases)
        stat[res["status"]] += 1
        for t in res["tags"]:
            stat[f"tag:{t}"] += 1
        if res["status"] == "待归类":
            unresolved[p.name] += 1

        line = (f"  {p.name:<26} 球员{len(res['players'])}"
                f"(低置信{len(res['players_low_confidence'])}) "
                f"球队{len(res['teams'])} 标签{res['tags']} -> {res['status']}")
        print(line)
        if res["players"]:
            print(f"      {'、'.join(res['players'][:8])}")
        if res["teams"]:
            print(f"      {'、'.join(res['team_names'])}")
        if a.dry_run or a.report:
            continue
        fm.update(res)
        p.write_text(dump_fm(fm, body), encoding="utf-8")

    print("\n统计：", dict(stat))
    if unresolved:
        print("需人工过目（无主题标签或无实体）：", ", ".join(unresolved))
    print("\n下一步：python3 scripts/build_bundles.py")


if __name__ == "__main__":
    main()
