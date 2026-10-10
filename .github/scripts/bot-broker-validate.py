#!/usr/bin/env python3
"""bot-broker-validate.py — Bot Broker 请求校验器（可信代码，随 main 分支发布）.

职责：独立验证 repository_dispatch 请求
{request_id, attempt_id, issue, branch, sha, operation}（AC04 六元组，
Human 2026-10-09 批准由三元组扩展）。
全部校验通过才允许 Broker 以 GitHub App 身份创建 PR；任一失败即拒绝。

环境变量：
  PAYLOAD       github.event.client_payload 的 JSON（业务输入，仅允许六元组
                request_id/attempt_id/issue/branch/sha/operation）
  SENDER_LOGIN  github.event.sender.login（可信 GitHub 身份；Q1 裁定：允许集 {SkyCjq}）
  REPO          github.repository（必须锁定为 SkyCjq/cba-kb-engine）
  GH_TOKEN      只读 GitHub API 调用凭证（供 `gh api` 使用）
  GITHUB_OUTPUT (可选) 写入步骤输出（decision/branch/issue/sha/base_sha/pr_number）
  BRANCH/SHA    幂等/重验/创建后核验模式的输入（已校验值，由 workflow 传递）
  BASE_SHA      --recheck-head 的输入（审核时记录的 merge_base_commit.sha）

用法：
  python3 bot-broker-validate.py                 # 全量校验（AC04–AC13、AC16–AC17）
  python3 bot-broker-validate.py --recheck-head  # 建 PR 前重验 HEAD 与 diff 基准（AC10）
  python3 bot-broker-validate.py --find-idempotent# 幂等入口复用：打印已核验 PR 编号或空行（FND-02）
  python3 bot-broker-validate.py --verify-created# 创建后回读核验实际 PR（FND-04）

成功：stdout 输出单行决策 JSON，exit 0。
失败：stdout 输出 {"decision":"deny","reason":...,"ac":"ACxx"}，exit 非零。
reason 永不包含 secret/token。仅使用 Python 标准库；不执行候选分支的任何内容。
"""

import json
import os
import re
import subprocess
import sys
from urllib.parse import urlencode

# ---------------------------------------------------------------- 冻结常量
OWNER_REPO = "SkyCjq/cba-kb-engine"
ALLOWED_SENDERS = {"SkyCjq"}          # Q1 裁定
BASE_REF = "main"
BRANCH_RE = re.compile(r"^muse/issue-(\d+)$")   # AC06：严格命名，大小写敏感
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PAYLOAD_MAX_BYTES = 65536
COMPARE_FILE_LIMIT = 300              # GitHub compare API 文件清单上限（AC13）
BOT_LOGIN = "cba-kb-muse-mode2-bot[bot]"  # Q4 冻结值（PR #117 已验证），参与归属核验
OPERATION_CREATE_PR = "create_pr"  # AC04：唯一允许的 operation（Human 2026-10-09 批准六元组）
# R4 FND-16：request_id 必须绑定完整 40 位 SHA。6 位前缀在构造上无法实现
# "同 ID 不同 SHA 拒绝"（aaaaaa+b*34 可复用 bot-pr-12-aaaaaa）。
# D1：request_id = "bot-pr-{issue}-{sha}"（完整 SHA）；不同 SHA 必然导出不同 ID，
# 校验即全字符串相等比对。Human 已批准该细化（定向 Round 4）。
REQUEST_ID_RE = re.compile(r"^bot-pr-(\d+)-([0-9a-f]{40})$")


def derive_request_id(issue, sha):
    """A+ 协议：稳定 request_id 派生规则（调用方与 Broker 共用）。

    D1（R4 定向修复）：request_id = "bot-pr-{issue}-{sha}"，sha 为完整 40 位
    hex。仓库锁定为 SkyCjq/cba-kb-engine、operation 固定为 create_pr，二者不
    进入 ID。同一逻辑请求重试时 request_id 不变，attempt_id 递增。
    调用方（impl/caller/broker_request.py）复用本函数，避免两处实现漂移。"""
    return "bot-pr-%d-%s" % (issue, sha)

# 冻结 T2 保护名单（risk-tiers.yml rev3；命中即拒绝，AC11）
T2_PATTERNS = [
    ".github/**",
    "**/AGENTS.md",
    "**/CLAUDE.md",
    "**/.claude/**",
    "**/.cursor/**",
    "Makefile",
    "Dockerfile",
    "docker-compose*.yml",
    "compose*.yml",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "requirements*.txt",
    "requirements/**",
    "requirements.lock",
    "uv.lock",
    "src/**/release/**",
    "src/**/drive*.py",
    "src/**/schema/**",
    "src/**/evidence/**",
    "automation/drive_io.py",
    "automation/verify.py",
    "scripts/prepare_production.py",
    "scripts/check_secrets.py",
    "tests/conftest.py",
    "tests/fixtures/**",
    "docs/mode2/**",
]
# T1 / T0：Q3 裁定为允许名单（沿用 tiering.py 语义）
T1_PATTERNS = ["src/**", "automation/**", "scripts/**", "config/**"]
T0_PATTERNS = ["docs/**", "tests/**", "*.md", ".gitignore"]


class ApiError(Exception):
    pass


class Deny(Exception):
    """内部拒绝信号：归属核验等函数用 raise 代替直接 deny，
    以便调用方区分场景并定制报错（FND-02/FND-04）。"""

    def __init__(self, ac, reason):
        super().__init__(reason)
        self.ac = ac
        self.reason = reason


# ---------------------------------------------------------- glob 匹配
# 语义（risk-tiers.yml rev3 注释）：
#   含 "/" 的模式锚定仓库根；不含 "/" 的模式匹配任意层级 basename；
#   "*" 匹配除 "/" 外任意字符；"?" 匹配除 "/" 外单个字符；
#   "**/" 匹配零或多层目录；尾部 "/**" 匹配该目录下任意深度。
def _anchored_regex(pattern):
    dstar_slash = "\x00"
    p = pattern
    suffix = ""
    if p.endswith("/**"):
        p = p[: -len("/**")]
        suffix = "(?:/.*)?"
    p = p.replace("**/", dstar_slash)
    out = []
    for ch in p:
        if ch == dstar_slash:
            out.append("(?:.*/)?")
        elif ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch in ".^$+{}[]|()\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    out.append(suffix)
    return "".join(out)


def _seg_regex(pattern):
    out = []
    for ch in pattern:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch in ".^$+{}[]|()\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def glob_match(pattern, path):
    """风险路径模式匹配。pattern 来自冻结名单（可信），path 来自 API（数据）。"""
    if "/" not in pattern:
        name = path.rsplit("/", 1)[-1]
        return re.fullmatch(_seg_regex(pattern), name) is not None
    return re.fullmatch(_anchored_regex(pattern), path) is not None


def classify(path):
    """返回 T2 / T1 / T0 / UNKNOWN。T2 优先（max 规则）；未知 → UNKNOWN（fail-closed）。"""
    for pat in T2_PATTERNS:
        if glob_match(pat, path):
            return "T2"
    for pat in T1_PATTERNS:
        if glob_match(pat, path):
            return "T1"
    for pat in T0_PATTERNS:
        if glob_match(pat, path):
            return "T0"
    return "UNKNOWN"


# ---------------------------------------------------------- API 调用
def gh_api(path, params=None, timeout=30):
    """只读 GitHub API 调用。任何失败抛 ApiError（fail-closed，不降级）。
    FND-01：显式 --method GET，不依赖 gh 对带参请求的默认方法推断。"""
    url = path
    if params:
        url += "?" + urlencode(params)
    try:
        r = subprocess.run(
            ["gh", "api", "--method", "GET", url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # 缺 gh / 超时 / 其他执行失败
        raise ApiError("gh api execution failed: %s" % type(exc).__name__)
    if r.returncode != 0:
        first = (r.stderr or "").strip().splitlines()
        hint = first[0][:120] if first else "unknown error"
        raise ApiError("GitHub API error: %s" % hint)
    try:
        return json.loads(r.stdout)
    except Exception:
        raise ApiError("GitHub API returned non-JSON")


# ---------------------------------------------------------- 输出
def deny(ac, reason):
    rec = {"decision": "deny", "reason": reason, "ac": ac}
    print(json.dumps(rec))
    # A+ 协议：workflow 的校验步骤设置 BROKER_DENY_FILE 时，把 deny JSON 同步
    # 落盘，供终态回执步骤读取机器可读的拒绝原因（REJECTED 回执的 error_class /
    # error_detail 即取自这里）。不设置该变量的其他调用模式不受影响。
    deny_file = os.environ.get("BROKER_DENY_FILE", "")
    if deny_file:
        try:
            with open(deny_file, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(rec))
        except Exception:
            pass  # 落盘失败不掩盖拒绝本身；回执步骤按"无机器可读证据"处理
    sys.exit(1)


def approve(branch, issue, sha, base_sha, idempotent=False, pr_number=None):
    decision = {"decision": "approve_idempotent" if idempotent else "approve"}
    decision["base_sha"] = base_sha  # FND-04：记录 diff 基准，供重验与审计
    if idempotent:
        decision["pr_number"] = pr_number
    print(json.dumps(decision))
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("decision=%s\n" % decision["decision"])
            fh.write("branch=%s\n" % branch)
            fh.write("issue=%d\n" % issue)
            fh.write("sha=%s\n" % sha)
            fh.write("base_sha=%s\n" % base_sha)
            if idempotent:
                fh.write("pr_number=%s\n" % pr_number)
    sys.exit(0)


# ---------------------------------------------------------- 校验主体
def parse_contract():
    """AC04：输入契约。仅允许恰好六元组
    {request_id, attempt_id, issue, branch, sha, operation}。

    Human 2026-10-09 明确批准：AC04 从三元组扩展为六元组，仍严格校验 —
    未知字段拒绝；request_id 必须符合稳定派生规则（Broker 重算比对，不一致
    即拒绝，防调用方伪造错配 ID）；operation 必须为 "create_pr"。
    R4 D1：request_id 绑定完整 40 位 SHA（Human 定向 Round 4 批准细化）。

    返回 (issue, branch, sha, attempt_id)。"""
    raw = os.environ.get("PAYLOAD", "")
    if len(raw.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        deny("AC04", "payload too large")
    try:
        payload = json.loads(raw)
    except Exception:
        deny("AC04", "payload is not valid JSON")
    if not isinstance(payload, dict):
        deny("AC04", "payload must be a JSON object")
    allowed = {"request_id", "attempt_id", "issue", "branch", "sha", "operation"}
    keys = set(payload.keys())
    extra = keys - allowed
    if extra:
        deny("AC04", "unexpected payload fields: %s" % sorted(extra)[:5])
    missing = allowed - keys
    if missing:
        deny("AC04", "missing payload fields: %s" % sorted(missing))
    request_id = payload["request_id"]
    attempt_id = payload["attempt_id"]
    issue, branch, sha = payload["issue"], payload["branch"], payload["sha"]
    operation = payload["operation"]
    if isinstance(issue, bool) or not isinstance(issue, int) or not 0 < issue < 2**31:
        deny("AC04", "issue must be a positive integer")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        deny("AC04", "branch must be a non-empty string (<=255 chars)")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        deny("AC04", "sha must be a 40-char lowercase hex commit SHA")
    # operation：唯一允许 "create_pr"，其他值一律拒绝（防调用方复用通道做别的操作）
    if not isinstance(operation, str) or operation != OPERATION_CREATE_PR:
        deny("AC04", 'operation must be exactly "create_pr"')
    # attempt_id：正整数；Broker 不用它做决策，仅记录/回显（幂等键是 request_id）
    if (
        isinstance(attempt_id, bool)
        or not isinstance(attempt_id, int)
        or not 0 < attempt_id < 2**31
    ):
        deny("AC04", "attempt_id must be a positive integer")
    # request_id：Broker 按冻结规则重算期望值并比对，不一致 → 拒绝。
    # 防调用方伪造错配的 request_id（例如把旧 ID 贴到新 SHA 的请求上）。
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        deny("AC04", "request_id must match ^bot-pr-<issue>-[0-9a-f]{40}$ (full SHA, R4 D1)")
    expected = derive_request_id(issue, sha)
    if request_id != expected:
        deny(
            "AC04",
            "request_id %r does not match the derived value for this "
            "issue/sha (expected %r)" % (request_id, expected),
        )
    return issue, branch, sha, attempt_id


def check_sender():
    """AC05：调用者身份只认 GitHub 可信事件 sender，不认 payload 自述。"""
    sender = os.environ.get("SENDER_LOGIN", "")
    if sender not in ALLOWED_SENDERS:
        deny("AC05", "sender is not in the allowed set")


def check_branch_binding(issue, branch):
    """AC06：分支严格符合 ^muse/issue-<N>$ 且 N == issue。"""
    m = BRANCH_RE.fullmatch(branch)
    if not m or int(m.group(1)) != issue:
        deny("AC06", "branch must match ^muse/issue-<N>$ with N == issue")


def check_repo():
    """AC09：目标仓库锁定，防 fork/错仓库混淆。"""
    if os.environ.get("REPO", "") != OWNER_REPO:
        deny("AC09", "repository is not the locked target")


def check_issue(issue):
    """AC08：重新读取 Issue，必须为 open；查询失败一律拒绝。
    FND-10：GitHub Issues API 会返回 PR（含 pull_request 键）；Q2"open 即可"
    指的是 Issue 的状态，不包括把 PR 当作普通任务 Issue。"""
    try:
        iss = gh_api("repos/%s/issues/%d" % (OWNER_REPO, issue))
    except ApiError as exc:
        deny("AC08", "issue lookup failed: %s" % exc)
    if not isinstance(iss, dict) or iss.get("state") != "open":
        deny("AC08", "issue is not open")
    # FND-13：按"键存在性"拒绝，不按值类型——null/false/字符串等异常值同样 fail-closed。
    if "pull_request" in iss:
        deny("AC08", "issue lookup returned a pull request, not an issue")


def get_branch_head(branch):
    """AC07：读取 GitHub 实时分支 HEAD。结构异常抛 ApiError（fail-closed）。"""
    try:
        br = gh_api("repos/%s/branches/%s" % (OWNER_REPO, branch))
    except ApiError as exc:
        raise ApiError("branch lookup failed: %s" % exc)
    if not isinstance(br, dict):
        raise ApiError("branch lookup returned non-object")
    commit = br.get("commit")
    if not isinstance(commit, dict):
        raise ApiError("branch object missing commit")
    head = commit.get("sha")
    if not isinstance(head, str) or not SHA_RE.fullmatch(head):
        raise ApiError("branch HEAD is unavailable")
    return head


def check_head(issue, branch, sha):
    try:
        head = get_branch_head(branch)
    except ApiError as exc:
        deny("AC07", str(exc))
    if head != sha:
        deny("AC07", "sha does not equal the branch HEAD on GitHub")


def check_paths(sha):
    """AC11/AC12/AC13：compare 取完整文件清单并逐文件分级。
    返回 merge_base_commit.sha（FND-04：记录 diff 基准）。"""
    try:
        cmp_ = gh_api("repos/%s/compare/%s...%s" % (OWNER_REPO, BASE_REF, sha))
    except ApiError as exc:
        deny("AC13", "compare lookup failed: %s" % exc)
    if not isinstance(cmp_, dict):
        deny("AC13", "compare returned malformed data")
    mbc = cmp_.get("merge_base_commit")
    if not isinstance(mbc, dict):
        deny("AC13", "compare missing merge_base_commit")
    base_sha = mbc.get("sha")
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        deny("AC13", "compare merge_base_commit.sha malformed")
    files = cmp_.get("files")
    status = cmp_.get("status")
    # 空 diff 不得凭借"未触及保护路径"通过（AC13）
    if not isinstance(files, list) or status == "identical" or len(files) == 0:
        deny("AC13", "empty diff cannot pass path review")
    # 300 上限：无法证明完整性则拒绝（AC13）
    if len(files) >= COMPARE_FILE_LIMIT:
        deny("AC13", "compare file list may be truncated (>=300 files)")
    for f in files:
        if not isinstance(f, dict):
            deny("AC11", "compare returned malformed file entry")
        filename = f.get("filename")
        if not isinstance(filename, str) or not filename:
            deny("AC11", "compare returned malformed filename")
        paths = [filename]
        # 重命名同时检查原路径与目标路径（AC11）；缺原路径 → 无法判定 → 拒绝（FND-07）
        if f.get("status") == "renamed":
            prev = f.get("previous_filename")
            if not isinstance(prev, str) or not prev:
                deny(
                    "AC11",
                    "renamed without previous_filename: cannot verify original path",
                )
            paths.append(prev)
        for p in paths:
            tier = classify(p)
            if tier == "T2":
                deny("AC11", "protected path in diff: %s" % p)
            elif tier not in ("T1", "T0"):
                deny("AC12", "path not covered by allow policy: %s" % p)
            # T1/T0 → 允许（Q3 裁定）
    return base_sha


def _check_pr_attribution(pr, branch, sha):
    """单 PR 归属核验（FND-02/FND-03）：state==open、user.login==BOT_LOGIN、
    head.repo/base.repo 均为目标仓库、head.ref==branch、head.sha==sha、
    base.ref==main。完全匹配 → 返回编号；任一不符 → raise Deny（含编号）。"""
    number = pr.get("number")
    if isinstance(number, bool) or not isinstance(number, int):
        raise Deny("AC17", "existing-PR entry has malformed number")
    if pr.get("state") != "open":
        raise Deny("AC17", "PR #%d: state is not open" % number)
    user = pr.get("user")
    if not isinstance(user, dict) or user.get("login") != BOT_LOGIN:
        raise Deny(
            "AC17",
            "conflicting PR #%d: author %r is not the frozen bot login"
            % (
                number,
                user.get("login") if isinstance(user, dict) else user,
            ),
        )
    head = pr.get("head")
    base = pr.get("base")
    for side_name, side in (("head", head), ("base", base)):
        if not isinstance(side, dict):
            raise Deny("AC17", "PR #%d: %s is malformed" % (number, side_name))
        repo = side.get("repo")
        if not isinstance(repo, dict) or repo.get("full_name") != OWNER_REPO:
            raise Deny(
                "AC17",
                "PR #%d: %s repo is not %s" % (number, side_name, OWNER_REPO),
            )
    head_ref, head_sha, base_ref = head.get("ref"), head.get("sha"), base.get("ref")
    if (
        not isinstance(head_ref, str)
        or not isinstance(head_sha, str)
        or not isinstance(base_ref, str)
    ):
        raise Deny("AC17", "PR #%d: ref/sha fields malformed" % number)
    if base_ref == BASE_REF and head_ref == branch and head_sha == sha:
        return number
    # Q5 裁定：既有 PR 内容/状态不符本次请求 → 拒绝并报告冲突
    raise Deny(
        "AC17",
        "conflicting open PR #%d exists for this branch "
        "(base=%s head=%s sha=%s)" % (number, base_ref, head_ref, head_sha[:12]),
    )


def find_idempotent_pr(branch, sha):
    """AC16/AC17/Q5/FND-02/FND-03/FND-07：
    在同分支 open PR 中寻找通过完整归属核验的 PR。
    返回 PR 编号；无匹配返回 None；任一冲突/结构异常 → raise Deny。
    核验项：state==open、user.login==BOT_LOGIN、
    head.repo/base.repo 均为 SkyCjq/cba-kb-engine、
    head.ref==branch、head.sha==sha、base.ref==main。

    R5 说明（V:466-469 单页问题 → 经证明不是缺口，故不改分页）：
    本循环对**每一个**条目都是决定性的 —— 非对象 → Deny（FND-07）；
    _check_pr_attribution 对任一不符 → raise Deny；完全匹配 → 要么记录
    （首个），要么因"多个匹配" raise Deny。因此只要第 1 页非空，函数必在
    第 1 页内终止（return 或 raise）；第 1 页为空 ⟺ 该分支无 open PR
    （API 按 head+state 过滤），返回 None 正确。第 2 页及以后**不可能**
    改变任何结局 —— 翻页在此处是死代码。fail-closed 语义本身保证了完备性。
    """
    try:
        prs = gh_api(
            "repos/%s/pulls" % OWNER_REPO,
            {"head": "SkyCjq:%s" % branch, "state": "open", "per_page": "100"},
        )
    except ApiError as exc:
        raise Deny("AC16", "existing-PR lookup failed: %s" % exc)
    if not isinstance(prs, list):
        raise Deny("AC16", "existing-PR lookup returned malformed data")
    matched = None
    for pr in prs:
        # FND-07：非对象条目不得静默跳过 → 拒绝
        if not isinstance(pr, dict):
            raise Deny(
                "AC16",
                "existing-PR entry is not an object; cannot determine attribution",
            )
        number = _check_pr_attribution(pr, branch, sha)
        if matched is not None:
            raise Deny("AC16", "multiple matching open PRs for this branch")
        matched = number
    return matched


def check_idempotent(branch, sha):
    """AC16/AC17/Q5：幂等。仅当本次请求全部校验通过后才可返回既有 PR 编号；
    既有 PR 内容/状态不符本次请求 → 拒绝并报告冲突。"""
    try:
        return find_idempotent_pr(branch, sha)
    except Deny as d:
        deny(d.ac, d.reason)


def _idempotency_inputs():
    """--find-idempotent / --verify-created 的输入校验。"""
    repo = os.environ.get("REPO", "")
    branch = os.environ.get("BRANCH", "")
    sha = os.environ.get("SHA", "")
    if (
        repo != OWNER_REPO
        or not branch
        or not BRANCH_RE.fullmatch(branch)
        or not sha
        or not SHA_RE.fullmatch(sha)
    ):
        return None
    return branch, sha


def cmd_find_idempotent():
    """--find-idempotent：供 workflow 幂等入口复用完整归属核验（FND-02）。
    找到 → 打印编号；无 → 打印空行；冲突/异常 → deny（非零退出）。"""
    inp = _idempotency_inputs()
    if inp is None:
        deny("AC16", "find-idempotent: invalid input")
    branch, sha = inp
    try:
        n = find_idempotent_pr(branch, sha)
    except Deny as d:
        deny(d.ac, d.reason)
    print(n if n is not None else "")
    sys.exit(0)


def _critical(ac, reason):
    """CRITICAL 报错：含 PR 编号与期望/实际，exit 非零。Broker 只报不关（R9）。"""
    print(json.dumps({"decision": "deny", "reason": "CRITICAL: " + reason, "ac": ac}))
    sys.exit(1)


def _verify_single_pr(number, branch, sha):
    """按编号直接回读 pulls/{n} 并做与 find_idempotent_pr 相同的归属核验。
    FND-04 R2：绑定本次创建 —— workflow 从 `gh pr create` 的输出解析出编号后，
    按该编号回读，而不是按分支查列表（列表可能返回并发创建的其他 PR）。"""
    try:
        pr = gh_api("repos/%s/pulls/%d" % (OWNER_REPO, number))
    except ApiError as exc:
        _critical("AC15", "created PR #%d re-read failed: %s" % (number, exc))
    if not isinstance(pr, dict):
        _critical("AC15", "created PR #%d returned malformed data" % number)
    return _check_pr_attribution(pr, branch, sha)


def cmd_verify_created():
    """--verify-created：创建后回读核验实际 PR（FND-04/AC15）。
    成功 → verify_created_ok + 编号；PR 不存在/归属不符 → 明确报错（含编号），非零退出。"""
    inp = _idempotency_inputs()
    if inp is None:
        deny("AC15", "verify-created: invalid input")
    branch, sha = inp
    pr_number_env = os.environ.get("PR_NUMBER", "")
    if pr_number_env:
        # FND-04 R2：绑定本次创建的编号
        if not re.fullmatch(r"[1-9][0-9]*", pr_number_env):
            deny("AC15", "verify-created: PR_NUMBER malformed")
        try:
            n = _verify_single_pr(int(pr_number_env), branch, sha)
        except Deny as d:
            _critical(d.ac, "created PR failed attribution verification: " + d.reason)
    else:
        # 回退：按分支查列表（兼容旧调用；workflow 已改用 PR_NUMBER 绑定）
        try:
            n = find_idempotent_pr(branch, sha)
        except Deny as d:
            _critical(d.ac, "created PR failed attribution verification: " + d.reason)
        if n is None:
            _critical(
                "AC15",
                "PR not found after successful create; cannot verify attribution",
            )
    print(json.dumps({"decision": "verify_created_ok", "pr_number": n}))
    sys.exit(0)


def recheck_head():
    """--recheck-head：建 PR 前最后一刻重验（AC10/FND-04）：
    1) 分支 HEAD 仍 == 审核通过的 sha；
    2) diff 基准未变：compare(当前 main...sha) 的 merge_base_commit.sha
       仍 == 审核时记录的 base_sha。
    任一不符 → deny。merge-base 变化即拒绝/重审：变化可来自正常合并，
    也可来自快进 —— 例如候选分支历史为 root→A→B，main 从 root 快进到 A 时，
    merge-base(main,B) 会从 root 变为 A（无历史改写，属安全方向的误拒，
    FND-11；fail-closed 下接受该代价）。"""
    repo = os.environ.get("REPO", "")
    branch = os.environ.get("BRANCH", "")
    sha = os.environ.get("SHA", "")
    base_sha = os.environ.get("BASE_SHA", "")
    valid = (
        repo == OWNER_REPO
        and branch
        and BRANCH_RE.fullmatch(branch)
        and sha
        and SHA_RE.fullmatch(sha)
        and base_sha
        and SHA_RE.fullmatch(base_sha)
    )
    if not valid:
        print(
            json.dumps(
                {"decision": "deny", "reason": "recheck: invalid input", "ac": "AC10"}
            )
        )
        sys.exit(1)
    try:
        head = get_branch_head(branch)
    except ApiError as exc:
        print(
            json.dumps(
                {"decision": "deny", "reason": "recheck: %s" % exc, "ac": "AC10"}
            )
        )
        sys.exit(1)
    if head != sha:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "recheck: HEAD moved since validation",
                    "ac": "AC10",
                }
            )
        )
        sys.exit(1)
    try:
        cmp_ = gh_api("repos/%s/compare/%s...%s" % (OWNER_REPO, BASE_REF, sha))
    except ApiError as exc:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "recheck: compare failed: %s" % exc,
                    "ac": "AC10",
                }
            )
        )
        sys.exit(1)
    mbc = cmp_.get("merge_base_commit") if isinstance(cmp_, dict) else None
    cur_base = mbc.get("sha") if isinstance(mbc, dict) else None
    if not isinstance(cur_base, str) or cur_base != base_sha:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "recheck: diff basis changed since validation "
                    "(merge-base moved)",
                    "ac": "AC10",
                }
            )
        )
        sys.exit(1)
    print(json.dumps({"decision": "recheck_ok", "base_sha": base_sha}))
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if "--recheck-head" in argv:
        recheck_head()
        return
    if "--find-idempotent" in argv:
        cmd_find_idempotent()
        return
    if "--verify-created" in argv:
        cmd_verify_created()
        return
    issue, branch, sha, _attempt_id = parse_contract()  # AC04
    check_sender()  # AC05
    check_branch_binding(issue, branch)  # AC06
    check_repo()  # AC09
    check_issue(issue)  # AC08
    check_head(issue, branch, sha)  # AC07
    base_sha = check_paths(sha)  # AC11/AC12/AC13；返回 diff 基准（FND-04）
    pr_number = check_idempotent(branch, sha)  # AC16/AC17/Q5
    if pr_number is not None:
        approve(branch, issue, sha, base_sha, idempotent=True, pr_number=pr_number)
    approve(branch, issue, sha, base_sha)


if __name__ == "__main__":
    main()
