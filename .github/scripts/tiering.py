"""风险分级：确定性 glob 语义 + 逐文件定级取最大。

分级表来源（LACD 退役 risk-tiers.yml 之后）：
- 唯一来源是本文件内联的冻结常量 T2_PATTERNS / T1_PATTERNS / T0_PATTERNS
  （内容 = risk-tiers.yml rev3 的逐字迁移，见 git 历史）；
- bot-broker-validate.py 持有同一 rev3 名单的独立冻结副本（Broker 侧从未读过 yml）；
- 本文件不读任何外部配置文件：get_tiers() 无参，避免"配置漂移"类故障。

设计决策（回应独立安全复审 Vuln 1）：
- 不使用 fnmatch（"*" 会跨 "/"，语义含糊）；
- 不使用 pathlib.PurePath.match（Python < 3.13 是右锚定，"**" 不匹配零层目录，
  在 3.12 实测 `src/**` 连 `src/cba_kb/foo.py` 都匹配不上）；
- 自实现 gitignore 风格翻译，行为与 Python 版本无关，语义见下文。

glob 语义：
- 含 "/" 的模式锚定仓库根；不含 "/" 的模式匹配任意层级 basename；
- "*" 匹配除 "/" 外任意字符；"?" 匹配除 "/" 外单个字符；
- "**/" 匹配零或多层目录；尾部 "/**" 匹配该目录下任意深度。

核心安全规则：
- 逐文件定级，未知路径 -> T2（fail-closed）；
- 整个 PR 的 tier = 所有文件 tier 的最大值。
  混合 PR（未知文件 + 普通文档）不会被"洗"成低 tier。
"""
from __future__ import annotations

import re

TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2}

# 冻结分级表（原 .github/risk-tiers.yml rev3，2026-10-12 内联迁移，逐字一致）。
# 修改此处 = 修改门禁分级语义，必须走受控 PR + 独立 Q2，不得随手改。
T2_PATTERNS = [
    ".github/**",                 # 门禁本身
    "**/AGENTS.md",               # AI 执行指令入口（含嵌套）
    "**/CLAUDE.md",               # AI 执行指令入口（含嵌套）
    "**/.claude/**",              # AI 指令目录
    "**/.cursor/**",              # AI 指令目录
    "Makefile",                   # 构建入口
    "Dockerfile",                 # 构建/部署（basename 语义，任意层级）
    "docker-compose*.yml",        # 编排
    "compose*.yml",               # 编排
    "setup.py",                   # 构建
    "setup.cfg",                  # 构建配置
    "pyproject.toml",             # 构建/测试配置
    "requirements*.txt",          # 依赖（basename 语义）
    "requirements/**",            # 依赖
    "requirements.lock",
    "uv.lock",                    # 依赖锁定
    "src/**/release/**",          # 发布链路
    "src/**/drive*.py",           # Drive 写入
    "src/**/schema/**",           # 数据 schema
    "src/**/evidence/**",         # 证据核心规则
    "automation/drive_io.py",     # Drive IO（实际落点）
    "automation/verify.py",       # 核验引擎（证据链心脏；136KB schema/binding/sha256 校验）
    "scripts/prepare_production.py",  # 生产链路脚本
    "scripts/check_secrets.py",   # 安全扫描自身（致盲它等于关掉警报）
    "tests/conftest.py",          # 测试架构（可颠覆全部测试）
    "tests/fixtures/**",          # fixture 数据（可能混入真实数据；变更走人工）
    "docs/mode2/**",              # AI 指令文件
]
T1_PATTERNS = [
    "src/**",
    "automation/**",              # 注：automation/git_io.py 在此（纯只读采集器，经代码实证无写操作，故 T1）；
                                  # automation/verify.py 例外上调 T2（核验引擎），见 T2 列表
    "scripts/**",
    "config/**",
]
T0_PATTERNS = [
    "docs/**",                    # docs/mode2/** 已被 T2 覆盖（max 规则）
    "tests/**",                   # tests/conftest.py、tests/fixtures/** 已被 T2 覆盖
    "*.md",                       # AGENTS.md 已被 T2 覆盖
    ".gitignore",
]


def glob_to_regex(pat: str) -> "re.Pattern[str]":
    """把一条分级模式翻译成全路径匹配的正则。

    语义：
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


def get_tiers():
    """返回内联冻结分级表的编译形态 {tier: [(pattern, regex)]}。

    不读文件。顶层结构断言防止手误改坏常量形状。
    """
    raw = {"T0": T0_PATTERNS, "T1": T1_PATTERNS, "T2": T2_PATTERNS}
    if set(raw) != {"T0", "T1", "T2"}:
        raise ValueError("tiering 内联分级表 schema 错误：顶层必须是 {T0,T1,T2}")
    compiled = {}
    for level in ("T0", "T1", "T2"):
        pats = raw[level]
        if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
            raise ValueError(f"tiering 内联分级表 schema 错误：{level} 必须是字符串列表")
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
