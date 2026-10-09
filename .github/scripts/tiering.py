"""Mode 2.1 风险分级：确定性 glob 语义 + 逐文件定级取最大（rev3）。

设计决策（回应独立安全复审 Vuln 1）：
- 不使用 fnmatch（"*" 会跨 "/"，语义含糊）；
- 不使用 pathlib.PurePath.match（Python < 3.13 是右锚定，"**" 不匹配零层目录，
  在 3.12 实测 `src/**` 连 `src/cba_kb/foo.py` 都匹配不上）；
- 自实现 gitignore 风格翻译，行为与 Python 版本无关，语义见 risk-tiers.yml 头部文档。

核心安全规则：
- 逐文件定级，未知路径 -> T2（fail-closed）；
- 整个 PR 的 tier = 所有文件 tier 的最大值。
  混合 PR（未知文件 + 普通文档）不再被"洗"成低 tier。
"""
from __future__ import annotations

import re

import yaml

TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2}


def glob_to_regex(pat: str) -> "re.Pattern[str]":
    """把一条 risk-tiers 模式翻译成全路径匹配的正则。

    语义（与 risk-tiers.yml 头部文档保持一致）：
    - 含 "/" 的模式：锚定仓库根目录；
    - 不含 "/" 的模式：匹配任意层级的 basename（如 "*.md"、"Dockerfile"）；
    - "*" 匹配除 "/" 外的任意字符；"?" 匹配除 "/" 外的单个字符；
    - "**/" 匹配零或多层目录；尾部的 "/**" 匹配该目录下任意深度；
      其余位置的 "**" 等价于 ".*"。
    """
    anchored = "/" in pat
    i, n, out = 0, len(pat), []
    if not anchored:
        out.append("(?:.*/)?")
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pat[i:i + 2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("(?s:" + "".join(out) + r")\Z")


def load_tiers(path: str):
    """加载并严格校验 risk-tiers.yml，返回 {tier: [(pattern, regex)]}。"""
    with open(path, encoding="utf-8") as f:
        tiers = yaml.safe_load(f)
    if not isinstance(tiers, dict) or set(tiers) != {"T0", "T1", "T2"}:
        raise ValueError("risk-tiers.yml schema 错误：顶层必须是 {T0,T1,T2}")
    compiled = {}
    for level in ("T0", "T1", "T2"):
        pats = tiers[level]
        if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
            raise ValueError(f"risk-tiers.yml schema 错误：{level} 必须是字符串列表")
        compiled[level] = [(p, glob_to_regex(p)) for p in pats]
    return compiled


def file_tier(path: str, tiers) -> str:
    """单个文件的 tier；未知路径保守定为 T2。"""
    for level in ("T2", "T1", "T0"):
        for _pat, rx in tiers[level]:
            if rx.match(path):
                return level
    return "T2"


def compute_tier(files, tiers) -> str:
    """PR 的 tier = 各文件 tier 的最大值；空列表视为异常，保守 T2。"""
    files = list(files)
    if not files:
        return "T2"
    return max((file_tier(f, tiers) for f in files), key=lambda t: TIER_ORDER[t])
