"""Mode 2.1 可信策略门禁（Trusted Control Plane）— A1.

运行环境：pull_request_target，可信上下文，只读 base 代码。
本脚本及所 import 的 tiering.py / integrity-scan.py / canonical_diff.py
均来自 base checkout（不可被 PR 篡改）。

A1 范围（见 A1_SECURITY_CONTRACT.md）：静态门禁检查，不含动态授权控制器。
- 保留：tier/freeze/review/CI/SHA 绑定、重命名双路径、文件模式、digest 证据链。
- 移除（shelve 至 Phase C）：authz 检查发布、reverify 调度。
  A1 无自动合并，Human 为动态授权检查人。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiering  # noqa: E402  (trusted, 来自 base checkout)

# ---- 环境（GitHub Actions 注入；缺失即阻断） ----
GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO = os.environ.get("REPO", "")
PR_NUMBER = os.environ.get("PR_NUMBER", "")
HEAD_SHA = os.environ.get("HEAD_SHA", "")
BASE_SHA = os.environ.get("BASE_SHA", "")
PR_AUTHOR = os.environ.get("PR_AUTHOR", "")
BASE_REF = os.environ.get("BASE_REF", "")
TIER_PATH = os.environ.get("TIER_PATH", ".github/risk-tiers.yml")
TRUSTED_AUTHORS = {
    a.strip() for a in os.environ.get("TRUSTED_AUTHORS", "").split(",") if a.strip()
}
POLL_MINUTES = int(os.environ.get("GATE_POLL_MINUTES", "30"))
# SCOPE: full（默认，全部检查）| auth（授权状态复验：tier/freeze/review/审批/hold；
#   供 reverify workflow 在 issue_comment 事件后调用，不含 CI/bundle/完整性，
#   那些是 commit 绑定的，由 trusted workflow 负责）
SCOPE = os.environ.get("GATE_SCOPE", "full")

UNTRUSTED_WORKFLOW_NAME = "pr-gate-untrusted"
UNTRUSTED_WORKFLOW_PATH = ".github/workflows/pr-gate-untrusted.yml"
TEST_PATH_RX = re.compile(r"(?s:tests/acceptance/test_[^/]*\.py)\Z")
HEX64_RX = re.compile(r"[0-9a-f]{64}\Z")
CRITICAL_CLOSED = {"CLOSED", "RESOLVED", "FIXED"}


def fail(msg: str):
    print(f"POLICY-GATE FAIL: {msg}", flush=True)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}", flush=True)


def gh_api(path: str, method: str = "GET", data=None):
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    out = []
    url = f"https://api.github.com{path}"
    while url:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
            if isinstance(body, list):
                out.extend(body)
                link = resp.headers.get("Link", "")
                nxt = re.search(r'<([^>]+)>;\s*rel="next"', link)
                url = nxt.group(1) if nxt else None
            else:
                return body
    return out


# ================= 纯函数（可被 gate_selftest.py 独立测试） =================

def get_repo_permission(username: str):
    """查询用户在当前仓库的真实权限（rev6 P0-2：fail-closed）。

    author_association ≠ 权限。Ground truth：
    GET /repos/{R}/collaborators/{username}/permission。
    任何失败（403/404/超时/异常格式）→ 直接阻断，不回退启发式。
    （rev5 的 fail-open 回退已移除。）
    #10 实测须验证 GITHUB_TOKEN 能否调用此 API。
    """
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/collaborators/{username}/permission",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            perm = json.loads(resp.read().decode()).get("permission")
    except Exception as e:
        fail(f"无法核验审批人 {username} 的仓库权限（{e}），fail-closed 阻断")
    if perm not in ("admin", "write", "read", "none"):
        fail(f"审批人 {username} 的权限返回值未知（{perm!r}），fail-closed 阻断")
    return perm


def review_stance(reviews):
    """某用户最新的"决策性" review（rev5 P0-2b，与 GitHub 语义一致）。

    只看 APPROVED / CHANGES_REQUESTED / DISMISSED；COMMENTED / PENDING
    不改变决策（CHANGES_REQUESTED 后再 COMMENTED，仍然是要求修改）。
    DISMISSED 视为清零（无决策）。
    """
    for r in sorted(reviews, key=lambda x: x.get("submitted_at", ""), reverse=True):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            return r
    return None


def is_authorized(username: str) -> bool:
    """rev6：只认真实仓库写权限；查不到就阻断（fail-closed）。"""
    return get_repo_permission(username) in ("admin", "write")


def evaluate_approvals(reviews, pr_author: str, head_sha: str):
    """评估 Human approval，返回 (valid_logins, errors)。

    规则（rev5）：
    - 按用户聚合决策（review_stance）：CHANGES_REQUESTED 优先于其后的 COMMENTED；
    - 审批人须为授权主体（真实仓库写权限；查不到时回退启发式并告警）；
    - 任一授权 Human 的决策为 CHANGES_REQUESTED → 整体阻断；
    - 有效批准：决策为 APPROVED 且 commit_id == head_sha；排除 PR 作者。
    """
    by_user: dict = {}
    for r in reviews:
        login = ((r.get("user") or {}).get("login")) or ""
        if login:
            by_user.setdefault(login, []).append(r)
    errors, valid = [], []
    for login in sorted(by_user):
        if login == pr_author:
            continue
        st = review_stance(by_user[login])
        if st is None:
            continue
        if (st.get("user") or {}).get("type") != "User":
            continue
        if not is_authorized(login):
            continue
        if st.get("state") == "CHANGES_REQUESTED":
            errors.append(f"授权审查人 {login} 要求修改（CHANGES_REQUESTED），阻断")
        elif st.get("state") == "APPROVED" and st.get("commit_id") == head_sha:
            valid.append(login)
    return valid, errors


def validate_critical(critical):
    """rev4 阻断项 3：critical 列表中任一未关闭项 → 阻断。"""
    errs = []
    if not isinstance(critical, list):
        return ["critical 不是数组"]
    for i, item in enumerate(critical):
        if not isinstance(item, dict):
            errs.append(f"critical[{i}] 不是对象")
            continue
        if item.get("status") not in CRITICAL_CLOSED:
            errs.append(
                f"critical[{i}] 未关闭（status={item.get('status')!r}），阻断"
            )
    return errs


def validate_freeze(freeze, issue_no: int):
    """校验 freeze 记录与当前需求的绑定关系，返回错误列表（空=通过）。"""
    errs = []
    if not isinstance(freeze, dict):
        return ["freeze 不是 JSON 对象"]
    if freeze.get("task_id") != f"ISSUE-{issue_no}":
        errs.append(
            f"task_id={freeze.get('task_id')!r} 与当前 Issue #{issue_no} 不一致"
        )
    tp = freeze.get("test_path") or ""
    if not TEST_PATH_RX.match(tp):
        errs.append(f"test_path={tp!r} 不在允许范围 tests/acceptance/test_*.py 内")
    for field in ("spec_sha256", "test_sha256"):
        if not HEX64_RX.match(str(freeze.get(field) or "")):
            errs.append(f"{field} 非法（须为 64 位 hex）")
    return errs


def validate_tier_inputs(capability: str, semantic: str):
    """缺失或非法值直接阻断，不再静默默认 T0。"""
    errs = []
    for name, v in (("capability", capability), ("semantic", semantic)):
        if v not in ("T0", "T1", "T2"):
            errs.append(f"{name}={v!r} 缺失或非法，阻断（fail-closed）")
    return errs


def compute_final_tier(path_tier: str, capability: str, semantic: str) -> str:
    order = {"T0": 0, "T1": 1, "T2": 2}
    return max((path_tier, capability, semantic), key=lambda t: order[t])


def enforce_no_t2(final_tier: str):
    """rev4 阻断项 2：T2 必须转 13 阶段，轻量门禁不放行。"""
    if final_tier == "T2":
        return ["FINAL_TIER=T2：高风险变更必须转 13 阶段流程，本轻量门禁阻断"]
    return []


def extract_issuance(body: str, marker: str):
    """提取可信签发记录。marker 必须位于评论正文开头（去空白后）。"""
    stripped = (body or "").lstrip()
    m = re.match(r"<!--\s*" + re.escape(marker) + r"\s*(\{.*?\})\s*-->", stripped, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def verify_bundle_checksum(bundle: str):
    """rev4 阻断项 6：重算 END-OF-BUNDLE sha256，防传输/存储篡改。"""
    m = re.search(r"\nEND-OF-BUNDLE sha256:([0-9a-f]{64})\s*$", bundle)
    if not m:
        return ["bundle 校验串缺失"]
    body = bundle[:m.start()]
    if hashlib.sha256(body.encode()).hexdigest() != m.group(1):
        return ["bundle 校验串不一致（内容被篡改）"]
    return []


def compare_integrity_metrics(pr_m, base_m):
    """rev4 阻断项 7：双边可信指标比对（纯函数）。任一退化 → 错误列表。"""
    errs = []
    for name, op in (("test_funcs", ">="), ("test_files", ">="),
                     ("asserts", ">="), ("skips", "<="), ("xfails", "<=")):
        pv, bv = (pr_m or {}).get(name), (base_m or {}).get(name)
        if not isinstance(pv, int) or not isinstance(bv, int):
            errs.append(f"完整性指标缺失：{name}")
            continue
        bad = (pv < bv) if op == ">=" else (pv > bv)
        if bad:
            errs.append(f"测试完整性退化：{name} PR={pv} base={bv}（测试弱化，转人工）")
    return errs


# ================= 主流程 =================

def check_env():
    global HEAD_SHA, BASE_SHA, PR_AUTHOR, BASE_REF
    if not PR_NUMBER:
        fail("缺少环境变量：PR_NUMBER")
    # PR 上下文自动补齐（reverify 等事件无预置 SHA 时经 API 获取）
    if not (HEAD_SHA and BASE_SHA and PR_AUTHOR and BASE_REF):
        try:
            pr = gh_api(f"/repos/{REPO}/pulls/{PR_NUMBER}")
            HEAD_SHA = HEAD_SHA or pr["head"]["sha"]
            BASE_SHA = BASE_SHA or pr["base"]["sha"]
            PR_AUTHOR = PR_AUTHOR or pr["user"]["login"]
            BASE_REF = BASE_REF or pr["base"]["ref"]
        except Exception as e:
            fail(f"无法获取 PR 上下文：{e}")
    missing = [
        k for k, v in {
            "GH_TOKEN": GH_TOKEN, "REPO": REPO, "PR_NUMBER": PR_NUMBER,
            "HEAD_SHA": HEAD_SHA, "BASE_SHA": BASE_SHA, "PR_AUTHOR": PR_AUTHOR,
            "BASE_REF": BASE_REF,
        }.items() if not v
    ]
    if missing:
        fail(f"缺少环境变量：{', '.join(missing)}")
    if not TRUSTED_AUTHORS:
        fail("TRUSTED_AUTHORS 为空")
    if SCOPE not in ("full", "auth"):
        fail(f"GATE_SCOPE 非法：{SCOPE}")


def fetch_pr():
    pr = gh_api(f"/repos/{REPO}/pulls/{PR_NUMBER}")
    if pr.get("state") != "open":
        fail("PR 非 open 状态")
    if pr["head"]["sha"] != HEAD_SHA:
        fail(f"事件 HEAD_SHA 与 PR API 不一致：{HEAD_SHA} vs {pr['head']['sha']}")
    if pr["base"]["sha"] != BASE_SHA:
        fail(f"事件 BASE_SHA 与 PR API 不一致：{BASE_SHA} vs {pr['base']['sha']}")
    if pr["base"]["ref"] != "main":
        fail(f"目标分支必须为 main，当前为 {pr['base']['ref']}")
    return pr


def fetch_pr_files(pr):
    """返回 [{filename, status, previous_filename}]（rev8：保留重命名元信息）。

    GitHub PR Files API 提供 status（added/modified/removed/renamed 等）
    与 previous_filename（重命名时）。分级与 diff 必须同时看新旧路径，
    否则重命名可把 T2 文件降级为 T0（rev7 复审 P0-1）。
    """
    objs = gh_api(f"/repos/{REPO}/pulls/{PR_NUMBER}/files")
    # GitHub files API 最多返回 3000 个；超限则分级可能遗漏敏感路径 → 阻断
    if pr.get("changed_files", 0) > len(objs):
        fail(
            f"PR 变更文件数（{pr['changed_files']}）超过 API 枚举上限，"
            f"分级不完整，阻断"
        )
    return [{"filename": f["filename"],
             "status": f.get("status", ""),
             "previous_filename": f.get("previous_filename")}
            for f in objs]


def tier_paths(file_objs):
    """分级输入路径：重命名时新旧两个路径都参与，取最大 tier。

    rev8 P0-1：AGENTS.md → docs/x.txt 的重命名，原路径 T2 不能丢。
    A1 P4 修复：status=renamed 但 previous_filename 缺失/非法时直接阻断
    （GitHub API 异常不得按新路径降级定级）。
    """
    paths = []
    for fo in file_objs:
        paths.append(fo["filename"])
        if fo["status"] == "renamed":
            prev = fo.get("previous_filename")
            if not prev or not isinstance(prev, str):
                fail(f"重命名缺少 previous_filename：{fo['filename']}"
                     f"（API 元信息异常，阻断）")
            paths.append(prev)
    return paths


def pr_file_contents(file_objs):
    """{new_path: FileChange}（rev9：含模式）。

    重命名：base 从旧路径取，head 从新路径取；old_path 参与 digest 绑定
    与 diff 展示。模式经 Trees API 获取（一次调用）。
    """
    import canonical_diff as cd
    base_modes = github_tree_modes(BASE_SHA)
    head_modes = github_tree_modes(HEAD_SHA)
    contents = {}
    for fo in file_objs:
        new = fo["filename"]
        old = fo.get("previous_filename") if fo["status"] == "renamed" else None
        contents[new] = cd.FileChange(
            base_bytes=github_file_bytes(old or new, BASE_SHA),
            head_bytes=github_file_bytes(new, HEAD_SHA),
            old_path=old,
            base_mode=base_modes.get(old or new, ""),
            head_mode=head_modes.get(new, ""),
        )
    return contents


def fetch_issue_comments(issue_no: int):
    return gh_api(f"/repos/{REPO}/issues/{issue_no}/comments")


def fetch_pr_comments():
    return gh_api(f"/repos/{REPO}/pulls/{PR_NUMBER}/comments") + gh_api(
        f"/repos/{REPO}/issues/{PR_NUMBER}/comments"
    )


def latest_trusted_issuance(comments, marker: str):
    """取可信作者发布的、marker 在开头的最新签发记录。

    返回 (record, issuer, comment_body)：规格文本与元信息锁定同一条评论，
    防止"元信息与正文来自不同评论"的混淆。
    """
    cands = []
    for c in comments:
        login = ((c.get("user") or {}).get("login")) or ""
        if login not in TRUSTED_AUTHORS:
            continue
        body = c.get("body") or ""
        rec = extract_issuance(body, marker)
        if rec is not None:
            cands.append((c.get("created_at", ""), login, rec, body))
    if not cands:
        return None, None, None
    cands.sort(key=lambda x: x[0])
    return cands[-1][2], cands[-1][1], cands[-1][3]


def github_file_sha256(path: str, ref: str) -> str:
    """经 GitHub API 读取某 ref 上的文件字节并算 sha256（可信来源）。"""
    data = gh_api(f"/repos/{REPO}/contents/{path}?ref={ref}")
    if not isinstance(data, dict) or data.get("type") != "file":
        fail(f"无法读取文件：{path}@{ref[:8]}")
    raw = base64.b64decode(data.get("content") or "")
    return hashlib.sha256(raw).hexdigest()


def check_freeze(pr):
    m = re.search(r"(?:closes|fixes|resolves)\s+#(\d+)", pr.get("body") or "", re.I)
    if not m:
        fail("PR body 未关联 Issue（缺少 Closes #n）")
    issue_no = int(m.group(1))
    comments = fetch_issue_comments(issue_no)
    freeze, issuer, body = latest_trusted_issuance(comments, "mode2-freeze")
    if not freeze:
        fail(f"Issue #{issue_no} 未找到可信 freeze 签发记录")
    errs = validate_freeze(freeze, issue_no)
    if errs:
        fail("freeze 绑定校验失败：" + "；".join(errs))
    ok(f"freeze 记录发布者可信：{issuer}，task_id={freeze['task_id']}")

    # spec 内容重算：与元信息同一条评论的 spec 区块
    mm = re.search(
        r"<!--\s*mode2-spec-start\s*-->(.*?)<!--\s*mode2-spec-end\s*-->",
        body, re.S,
    )
    if not mm:
        fail("freeze 评论缺少 <!-- mode2-spec-start --> 区块")
    spec_text = mm.group(1).strip()
    if hashlib.sha256(spec_text.encode()).hexdigest() != freeze["spec_sha256"]:
        fail("spec_sha256 与实际规格文本不一致")
    ok("spec_sha256 绑定实际规格文本")

    # 验收测试文件哈希：经 API 按 HEAD_SHA 取实际字节重算
    # （文件由 PR commit 1 新建，base checkout 上不存在，不能读本地）
    tp = freeze["test_path"]
    digest = github_file_sha256(tp, HEAD_SHA)
    if digest != freeze["test_sha256"]:
        fail(f"验收测试文件哈希不一致：{tp}（实现者改了测试来修到绿？）")
    ok(f"验收测试文件哈希绑定一致：{tp}")

    if freeze.get("base_sha") != BASE_SHA:
        fail("freeze base_sha 与 PR base 不一致")
    ok("freeze base_sha 一致")
    return freeze, issue_no


def check_review():
    comments = fetch_pr_comments()
    review, issuer, _body = latest_trusted_issuance(comments, "mode2-review")
    if not review:
        fail("未找到可信 review 签发记录")
    if review.get("verdict") not in ("PASS", "PASS_WITH_NOTES"):
        fail(f"review verdict={review.get('verdict')} 非通过")
    if review.get("reviewed_head_sha") != HEAD_SHA:
        fail("reviewed_head_sha 与当前 HEAD 不一致")
    crit_errs = validate_critical(review.get("critical"))
    if crit_errs:
        fail("；".join(crit_errs))
    ok(f"review verdict={review['verdict']}，发布者可信：{issuer}，"
       f"critical 全关闭（{len(review['critical'])} 项）")
    return review


def select_latest_run(cands):
    """纯函数（rev6 P0-3）：选出可用的最新 run。

    - 若有任何未 completed 的执行在飞 → 返回 None（必须等待，绝不能用旧成功）。
    - 否则按 (created_at, run_attempt, id) 取最新（不用 updated_at；
      已完成的 run 的 updated_at 可能晚于新发起的 in_progress run）。
    """
    if not cands:
        return None
    if any(r.get("status") != "completed" for r in cands):
        return None
    return max(cands, key=lambda r: (
        r.get("created_at", ""), r.get("run_attempt", 0), r.get("id", 0)))


def find_verified_untrusted_run():
    """rev6 P0-3：workflow-run 精确绑定 + 等待在飞执行。

    - workflow 按名称精确匹配，并校验 path（防同名不同文件）；
    - API 按 head_sha 预过滤，候选 run 必须关联本 PR 编号；
    - 只要有针对本次 head 的执行尚未完成，就轮询等待（可信层常比隔离层先跑完），
      超时阻断；绝不用旧成功掩盖新执行。
    """
    wfs = gh_api(f"/repos/{REPO}/actions/workflows").get("workflows", [])
    wf = next((w for w in wfs if w.get("name") == UNTRUSTED_WORKFLOW_NAME), None)
    if not wf:
        fail(f"未找到 workflow：{UNTRUSTED_WORKFLOW_NAME}")
    if wf.get("path") != UNTRUSTED_WORKFLOW_PATH:
        fail(f"workflow path 不符：{wf.get('path')}（防同名 workflow 混淆）")
    deadline = time.time() + POLL_MINUTES * 60
    while True:
        runs = gh_api(
            f"/repos/{REPO}/actions/workflows/{wf['id']}/runs"
            f"?event=pull_request&head_sha={HEAD_SHA}&per_page=50"
        ).get("workflow_runs", [])
        cands = [
            r for r in runs
            if r.get("head_sha") == HEAD_SHA
            and any((p or {}).get("number") == int(PR_NUMBER)
                    for p in r.get("pull_requests", []))
        ]
        latest = select_latest_run(cands)
        if latest is not None:
            if latest.get("conclusion") != "success":
                fail(
                    f"隔离层最新运行结论={latest.get('conclusion')} "
                    f"(run {latest.get('id')} attempt {latest.get('run_attempt')})，"
                    f"不是 success"
                )
            ok(f"隔离层 workflow run 可信绑定：run {latest.get('id')} "
               f"attempt {latest.get('run_attempt')}，"
               f"head_sha={HEAD_SHA[:8]}，conclusion=success")
            return latest
        if cands:
            flying = [r.get("id") for r in cands if r.get("status") != "completed"]
            print(f"隔离层有 {len(flying)} 个运行尚未完成（run {flying}），"
                  f"等待，不采用旧成功结果…", flush=True)
        else:
            print("隔离层尚无本次 head 的运行记录，等待…", flush=True)
        if time.time() > deadline:
            fail(f"等待隔离层 CI 超时（{POLL_MINUTES} 分钟）")
        time.sleep(30)


def download_run_artifacts(run_id: int):
    arts = gh_api(f"/repos/{REPO}/actions/runs/{run_id}/artifacts").get("artifacts", [])
    files = {}
    for a in arts:
        if a.get("expired"):
            continue
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/actions/artifacts/{a['id']}/zip",
            headers={
                "Authorization": f"Bearer {GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req) as resp:
            zf = zipfile.ZipFile(io.BytesIO(resp.read()))
            for name in zf.namelist():
                files[name] = zf.read(name).decode("utf-8", errors="replace")
    return files


def bundle_file_list(bundle: str):
    """解析 bundle 的"变更文件"清单（纯函数）。"""
    fm = re.search(r"## 变更文件\s+```\n(.*?)```", bundle, re.S)
    if not fm:
        return set()
    return {x.strip() for x in fm.group(1).splitlines() if x.strip()}


def gh_api_or_none(path: str):
    """gh_api 的 404 容忍版：资源不存在返回 None，其他错误照常抛出。

    rev7 P0-1：严格区分"该 ref 确认无此文件"（404 → None）与鉴权/网络/
    路径错误（抛异常 → 上层 fail-closed）。
    """
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def github_file_bytes(path: str, ref: str) -> bytes:
    """经 Contents API 取文件字节（rev7 P0-1/P0-2b 修复）。

    - 404 → b""（该 commit 确认无此文件：新增/删除场景）。
    - encoding == "none"（1–100MB 大文件，content 为空）→ 经 blob API 取完整字节，
      不得误作空文件（否则摘要错误）。
    - 其他错误 → 抛异常（上层 fail-closed）。
    """
    data = gh_api_or_none(f"/repos/{REPO}/contents/{path}?ref={ref}")
    if data is None:
        return b""
    if not isinstance(data, dict) or data.get("type") != "file":
        fail(f"无法获取文件内容：{path}@{ref[:8]}（非文件类型）")
    if data.get("encoding") == "none":
        sha = data.get("sha")
        if not sha:
            fail(f"无法获取大文件内容：{path}@{ref[:8]}（无 blob sha）")
        blob = gh_api(f"/repos/{REPO}/git/blobs/{sha}")
        return base64.b64decode(blob.get("content") or "")
    return base64.b64decode(data.get("content") or "")


def github_tree_modes(ref: str):
    """经 Trees API 取 {path: mode}（rev9：文件模式纳入可信证据）。

    mode 如 "100644"/"100755"/"120000"（symlink）。不存在的路径不出现。
    rev10：truncated=true 时 fail-closed（不得用残缺集合做摘要）。
    """
    data = gh_api(f"/repos/{REPO}/git/trees/{ref}?recursive=1")
    if isinstance(data, dict) and data.get("truncated"):
        fail(f"Trees API 返回截断（{ref[:8]}），文件模式不完整，阻断")
    out = {}
    for e in data.get("tree", []):
        if e.get("type") == "blob":
            out[e["path"]] = e.get("mode", "")
    return out


def verify_bundle_diff(bundle: str, file_objs):
    """rev8：diff 双重验证——字节摘要 + 展示文本（含重命名）。

    1. DIFF_DIGEST：bundle 声明 == 可信层按 API 实际内容重算
       （{new_path: (base, head, old)}，重命名双路径绑定）。
    2. 展示文本：bundle 的 diff 节 == 可信层按 canonical_diff 重算。
    """
    import canonical_diff as cd
    m = re.search(r"DIFF_DIGEST:\s*([0-9a-f]{64})", bundle)
    if not m:
        return ["bundle 缺少 DIFF_DIGEST"]
    claimed = m.group(1)
    contents = pr_file_contents(file_objs)
    if cd.compute_diff_digest(contents) != claimed:
        return ["bundle 的 DIFF_DIGEST 与 GitHub 实际内容不符"]
    shown = cd.parse_diff_section(bundle)
    if shown is None:
        return ["bundle 缺少规范 diff 节"]
    expected = cd.capped_diff(cd.canonical_diffs_text(contents))
    if shown != expected:
        return ["bundle 展示的 diff 与可信重算不一致（展示文本不可信）"]
    return []


def find_missing_bundle_files(bundle: str, api_files):
    """bundle 清单缺失的 PR 文件（纯函数）。"""
    have = bundle_file_list(bundle)
    return [f for f in api_files if f not in have]


def check_bundle(files, file_objs):
    """rev5 P0-4：bundle 校验串重算 + 文件清单交叉核验。

    诚实边界：校验串只证明 bundle 在传输/存储中未被篡改，不证明内容真实；
    机器决策（tier/测试哈希/完整性）走 GitHub API 事实，不依赖 bundle 内容。
    bundle 是给 Human/Web 审查读的。此处额外核验：bundle 的文件清单必须
    覆盖 PR 实际全部文件（防"藏起敏感文件不给审查看"）。
    """
    bundle = files.get("review-bundle.md")
    if not bundle:
        fail("artifact 缺少 review-bundle.md")
    cs_errs = verify_bundle_checksum(bundle)
    if cs_errs:
        fail("；".join(cs_errs))
    ok("bundle 校验串重算一致（防传输/存储篡改）")
    if "TRUNCATED" in bundle:
        fail("审查材料被截断（TRUNCATED），证据不足，转人工审查")
    m = re.search(r"BUNDLE_HEAD_SHA:\s*([0-9a-f]{40})", bundle)
    if not m or m.group(1) != HEAD_SHA:
        fail("bundle 的 BUNDLE_HEAD_SHA 与当前 HEAD 不一致")
    mb = re.search(r"^- base:\s*([0-9a-f]{40})", bundle, re.M)
    if not mb or mb.group(1) != BASE_SHA:
        fail("bundle 的 base SHA 与 PR base 不一致")
    api_files = [fo["filename"] for fo in file_objs]
    missing = find_missing_bundle_files(bundle, api_files)
    if missing:
        fail(f"bundle 文件清单缺失 PR 实际文件（{len(missing)} 个，如 {missing[:3]}），"
             f"审查材料不完整，转人工")
    ok(f"bundle 绑定 HEAD/base 且文件清单覆盖全部 {len(api_files)} 个 PR 文件")
    diff_errs = verify_bundle_diff(bundle, file_objs)
    if diff_errs:
        fail("；".join(diff_errs))
    ok("bundle DIFF_DIGEST 与展示 diff 文本均与 GitHub 实际内容一致")


def scan_metrics(root: str):
    """用 base 版扫描器扫描指定目录，返回指标 dict（可信计算）。"""
    proc = subprocess.run(
        [sys.executable, ".github/scripts/integrity-scan.py",
         "--json", "--root", root],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        fail(f"完整性扫描失败（{root}）：{proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout)
    except Exception:
        fail(f"完整性扫描输出解析失败（{root}）")


def test_file_shas(root: str):
    """{相对路径: sha256}，覆盖 root 下全部 **/*.py（rev6 P0-1 扩展）。

    不只 test_*.py：tests/helper.py 这类被测试 import 的辅助模块、
    conftest.py 等同样影响测试行为，必须纳入内容绑定。
    （conftest.py 等本身也是 T2，改它在轻量门禁直接阻断；这里是纵深。）

    注意：本函数跟随符号链接（read_bytes）。PR 侧的内容绑定不得直接用它，
    必须走 git_blob_identity（见下），否则 symlink 替换可绕过（A1 P3）。
    """
    from pathlib import Path
    out = {}
    base = Path(root)
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*.py")):
        out[p.relative_to(base).as_posix()] = hashlib.sha256(
            p.read_bytes()).hexdigest()
    return out


def git_blob_identity(repo: str, rel: str, ref: str = "HEAD"):
    """返回 rel 在 repo 的 ref 中的 (mode, blob_sha)；不存在返回 (None, None).

    A1 P3 修复：用 git 对象身份（不跟随符号链接）替代文件系统读取，
    普通文件→symlink 的替换会被 mode 变化（100644→120000）捕获。
    """
    p = subprocess.run(
        ["git", "-C", repo, "ls-tree", ref, "--", rel],
        capture_output=True, text=True, check=False)
    out = p.stdout.strip()
    if not out:
        return (None, None)
    parts = out.split(None, 3)
    if len(parts) < 3:
        return (None, None)
    return (parts[0], parts[2])


def check_test_identities(base_ids, pr_ids):
    """纯函数：比对 {rel: (mode, blob_sha)}；不一致时 fail()。

    base_ids: base 侧身份；pr_ids: PR head 侧身份（必须在 worktree 存活时采集）。
    """
    for rel in sorted(base_ids):
        b_mode, b_blob = base_ids[rel]
        p_mode, p_blob = pr_ids.get(rel, (None, None))
        if p_blob is None:
            fail(f"测试文件被删除：tests/{rel}（转人工审查）")
        if p_mode != b_mode:
            fail(f"测试文件类型被改变：tests/{rel}"
                 f"（{b_mode}→{p_mode}，转人工审查）")
        if p_blob != b_blob:
            fail(f"测试文件内容被修改：tests/{rel}（转人工审查；"
                 f"实现者不应改测试来修到绿）")


def check_integrity_baseline():
    """rev5 P0-3：测试文件内容级绑定 + 双边可信重算。

    - base 侧：base checkout 本地（base 版扫描器 + 文件哈希，可信）；
    - PR 侧：git fetch PR head → 校验 FETCH_HEAD == HEAD_SHA（防核验中被更新）
      → worktree 只读挂载（不执行 PR 代码）→ base 版扫描器扫描 + 文件哈希。
    - 内容绑定：base 中存在的每个 test_*.py，在 PR head 中必须字节一致；
      删除/修改一律阻断（转人工审查）。新增测试文件允许。
      ——"assert True 替换"类弱化攻击在此被拦截（哈希变了），不再依赖计数。
    - 计数比对保留为二级信号。
    """
    base_m = scan_metrics("tests")
    base_shas = test_file_shas("tests")
    ok(f"base 基线：{base_m['test_funcs']} funcs / {len(base_shas)} 测试文件")
    base_ids = {rel: git_blob_identity(".", f"tests/{rel}")
                for rel in sorted(base_shas)}
    wt = "/tmp/pr-head-scan"
    try:
        p1 = subprocess.run(
            ["git", "fetch", "origin", f"pull/{PR_NUMBER}/head", "--depth=1"],
            capture_output=True, text=True, check=False,
        )
        if p1.returncode != 0:
            fail(f"获取 PR head 失败：{p1.stderr.strip()[:200]}")
        p1b = subprocess.run(
            ["git", "rev-parse", "FETCH_HEAD"],
            capture_output=True, text=True, check=False,
        )
        if p1b.stdout.strip() != HEAD_SHA:
            fail("FETCH_HEAD 与 HEAD_SHA 不一致（PR 在核验中被更新），请重跑")
        p2 = subprocess.run(
            ["git", "worktree", "add", "--detach", wt, "FETCH_HEAD"],
            capture_output=True, text=True, check=False,
        )
        if p2.returncode != 0:
            fail(f"挂载 PR head 失败：{p2.stderr.strip()[:200]}")
        pr_m = scan_metrics(os.path.join(wt, "tests"))
        # A1 P3 回归修复（v1.1 复审）：PR 侧身份必须在 worktree 移除前采集；
        # 之前写在 finally 之后，查的是已删除目录，误报"文件被删除"。
        pr_ids = {rel: git_blob_identity(wt, f"tests/{rel}")
                  for rel in sorted(base_shas)}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt],
                       capture_output=True, check=False)
    ok(f"PR head 实测：{pr_m['test_funcs']} funcs")
    # 内容级绑定：base 已有测试文件必须 git 对象一致（mode + blob SHA）
    # A1 P3：不用文件系统哈希（跟随 symlink），用 git ls-tree 的对象身份；
    # 普通文件→symlink 会被 mode 变化捕获，内容改动会被 blob SHA 捕获。
    check_test_identities(base_ids, pr_ids)
    ok(f"测试文件内容绑定通过：{len(base_shas)} 个 base 测试文件 git 对象一致")
    errs = compare_integrity_metrics(pr_m, base_m)
    if errs:
        fail("；".join(errs))
    ok(f"测试完整性计数通过：funcs {base_m['test_funcs']}->{pr_m['test_funcs']}，"
       f"skips {base_m['skips']}->{pr_m['skips']}")


def check_approval():
    reviews = gh_api(f"/repos/{REPO}/pulls/{PR_NUMBER}/reviews")
    valid, errors = evaluate_approvals(reviews, PR_AUTHOR, HEAD_SHA)
    if errors:
        fail("；".join(errors))
    if not valid:
        fail("缺少 Human approval（授权 Human 在当前 head 的有效 APPROVED）")
    ok(f"Human approval（当前 head，已验授权）：{valid}")
    return valid


def main():
    check_env()
    pr = fetch_pr()
    file_objs = fetch_pr_files(pr)
    # 新路径列表（bundle 清单对账用）；分级用新旧双路径
    files = [fo["filename"] for fo in file_objs]

    tiers = tiering.load_tiers(TIER_PATH)
    path_tier = tiering.compute_tier(tier_paths(file_objs), tiers)
    print(f"PATH_TIER={path_tier}（{len(file_objs)} 个文件逐一定级取最大，"
          f"重命名双路径）", flush=True)

    freeze, _issue_no = check_freeze(pr)
    # hold 标签：PR 自身或需求 Issue 任一挂 status:hold 即禁止合并（rev6）
    hold_on_pr = any((l or {}).get("name") == "status:hold"
                     for l in gh_api(f"/repos/{REPO}/issues/{PR_NUMBER}").get("labels", []))
    hold_on_issue = any((l or {}).get("name") == "status:hold"
                        for l in gh_api(f"/repos/{REPO}/issues/{_issue_no}").get("labels", []))
    if hold_on_pr or hold_on_issue:
        fail("status:hold 标签存在（PR 或需求 Issue），禁止合并")
    ok("无 status:hold 标签")
    tier_errs = validate_tier_inputs(
        freeze.get("capability", ""), freeze.get("semantic", "")
    )
    if tier_errs:
        fail("；".join(tier_errs))
    final_tier = compute_final_tier(
        path_tier, freeze["capability"], freeze["semantic"]
    )
    print(f"FINAL_TIER={final_tier}", flush=True)
    t2_errs = enforce_no_t2(final_tier)
    if t2_errs:
        fail("；".join(t2_errs))
    ok("FINAL_TIER 非 T2，可走轻量门禁")

    check_review()
    check_approval()

    if SCOPE == "auth":
        # reverify 模式：只复验授权状态（freeze/review/审批/hold/tier），
        # CI/bundle/完整性是 commit 绑定的，由 trusted workflow 负责。
        print("POLICY-GATE RESULT: PASS (SCOPE=auth)", flush=True)
        return

    run = find_verified_untrusted_run()
    art_files = download_run_artifacts(run["id"])
    check_bundle(art_files, file_objs)
    check_integrity_baseline()

    print("POLICY-GATE RESULT: PASS", flush=True)


if __name__ == "__main__":
    main()
