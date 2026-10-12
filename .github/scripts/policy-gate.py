"""LACD v1.0 可信策略门禁（Trusted Control Plane）.

运行环境：pull_request_target，可信上下文，只读 base 代码。
本脚本及所 import 的 tiering.py 均来自 base checkout（不可被 PR 篡改）。

LACD 范围（2026-10-12 迁移，Human 已批准设计；迁移 PR #140）：
- 做：PR 身份/来源/HEAD 绑定（防事件伪造、fork 混入、HEAD 漂移）、
  Bot 作者绑定（fail-closed，LACD_BOT_AUTHOR 可覆盖）、路径分级报告
  （含重命名双路径）、status:hold 标签阻断、测试完整性基线
  （防测试弱化/删除/symlink 替换）。
- 不做：mode2-freeze / mode2-review 评论解析（已退役）、
  Human 审批的 Python 重验（由 GitHub 原生分支保护强制；rev11 G-02 已移除）、
  tests/evidence 成功状态的重复核验（由 GitHub 原生 Required Checks 独立强制，
  避免 policy→evidence 轮询等待与循环依赖）、review bundle 校验
  （Q2 证据由开发者证据包与独立重读提供，不再经 Actions bundle 中转）。

T2 说明：FINAL_TIER 仍按路径分级报告；T2 准入授权移交 GitHub 原生机制
（.github/CODEOWNERS 要求 @SkyCjq review ＋分支保护的审批要求），policy 不再
以"缺 review JSON"硬阻断——硬阻断会使门禁自身的后续维护无法合并且只能走
bypass（更差）。分支保护实际配置须由 Human 在管理员界面确认，本脚本无法核验。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

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
# LACD 下所有 PR 经 GitHub App Broker 创建，作者恒为 Bot；fail-closed 默认，
# 如未来允许 Human 直建 PR，由 Human 专项决策后覆盖本变量（不改代码）。
BOT_AUTHOR = os.environ.get("LACD_BOT_AUTHOR", "cba-kb-muse-mode2-bot[bot]")

HOLD_LABEL = "status:hold"


def fail(msg: str):
    print(f"LACD POLICY FAIL: {msg}", flush=True)
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

def is_sha40(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 40
            and all(c in "0123456789abcdef" for c in value))


@dataclass(frozen=True)
class IdentityConfig:
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    bot_author: str
    base_ref: str = "main"


def validate_pr_identity(pr: dict, cfg: IdentityConfig) -> dict:
    """校验 PR 身份与来源（fail-closed，任一不符即阻断）。

    防：事件伪造（number/HEAD 与 API 不一致）、fork PR 混入
    pull_request_target 高权限上下文、HEAD 在核验中漂移、非 Bot 作者、
    草稿 PR、目标分支非 main。
    不断言：Human 审批（原生分支保护负责）、CI 成功（原生 Required Checks 负责）。
    """
    if not isinstance(pr, dict):
        fail("PR_IDENTITY: PR 对象非法")
    if pr.get("number") != cfg.pr_number:
        fail(f"PR_NUMBER_MISMATCH：事件 {cfg.pr_number} vs API {pr.get('number')}")
    if pr.get("state") != "open":
        fail(f"PR_NOT_OPEN：state={pr.get('state')}")
    if pr.get("draft"):
        fail("DRAFT_NOT_READY：草稿 PR 不做门禁判定")
    base, head = pr.get("base") or {}, pr.get("head") or {}
    if base.get("ref") != cfg.base_ref:
        fail(f"BASE_BRANCH_MISMATCH：须为 {cfg.base_ref}，实际 {base.get('ref')}")
    base_repo = (base.get("repo") or {}).get("full_name")
    head_repo = (head.get("repo") or {}).get("full_name")
    if base_repo != cfg.repo or head_repo != cfg.repo:
        fail(f"FORK_OR_REPO_MISMATCH：base_repo={base_repo} "
             f"head_repo={head_repo}，不接受 fork PR")
    if not is_sha40(cfg.head_sha) or head.get("sha") != cfg.head_sha:
        fail("HEAD_SHA_MISMATCH：事件 HEAD 与 PR API 不一致或格式非法")
    if not is_sha40(cfg.base_sha) or base.get("sha") != cfg.base_sha:
        fail("BASE_SHA_MISMATCH：事件 BASE 与 PR API 不一致或格式非法")
    login = ((pr.get("user") or {}).get("login")) or ""
    if login != cfg.bot_author:
        fail(f"NOT_BOT_AUTHORED：作者为 {login}，本门禁仅接受 {cfg.bot_author}"
             f"（LACD_BOT_AUTHOR 可覆盖，需 Human 专项决策）")
    return {
        "state": "IDENTITY_PASS",
        "repository": cfg.repo,
        "pr_number": cfg.pr_number,
        "head_sha": cfg.head_sha,
        "base_sha": cfg.base_sha,
        "author": login,
    }


def parse_closes_issue(body: str):
    """从 PR body 解析 Closes #n（有则返回编号，无则返回 None；不强制要求）。

    Q2-141-M02 修复：多个不同编号 → fail-closed（与 coordinator 的
    TASK_BINDING_AMBIGUOUS 一致；避免遗漏其他关联 Issue 的 hold 标签）。
    """
    nums = sorted({int(n) for n in re.findall(
        r"(?:closes|fixes|resolves)\s+#(\d+)", body or "", re.I)})
    if len(nums) > 1:
        fail(f"关联 Issue 不唯一：{nums}（须恰好一个 Closes #n）")
    return nums[0] if nums else None


def has_hold_label(labels) -> bool:
    return any((l or {}).get("name") == HOLD_LABEL for l in labels or [])


def tier_paths(file_objs):
    """分级输入路径：重命名时新旧两个路径都参与，取最大 tier。

    rev8 P0-1（保留）：AGENTS.md → docs/x.txt 的重命名，原路径 T2 不能丢。
    status=renamed 但 previous_filename 缺失/非法时直接阻断
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


def compare_integrity_metrics(pr_m, base_m):
    """rev11（G-04 审计修订，保留）：双边可信指标比对。数量变化降为审查提示，
    不自动判作弊。（注：ok() 仅打印日志行，无其他副作用。）
    """
    errs = []
    for name, op in (("test_funcs", ">="), ("test_files", ">="),
                     ("asserts", ">="), ("skips", "<="), ("xfails", "<=")):
        pv, bv = (pr_m or {}).get(name), (base_m or {}).get(name)
        if not isinstance(pv, int) or not isinstance(bv, int):
            errs.append(f"完整性指标缺失：{name}")
            continue
        bad = (pv < bv) if op == ">=" else (pv > bv)
        if bad:
            ok(f"测试完整性变化（审查提示，非阻断）：{name} PR={pv} base={bv}")
    return errs


# ================= 主流程 =================

def check_env() -> IdentityConfig:
    global HEAD_SHA, BASE_SHA, PR_AUTHOR, BASE_REF
    if not PR_NUMBER:
        fail("缺少环境变量：PR_NUMBER")
    # PR 上下文自动补齐（经 API 获取）
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
    try:
        number = int(PR_NUMBER)
    except ValueError:
        fail(f"PR_NUMBER 非法：{PR_NUMBER}")
    return IdentityConfig(repo=REPO, pr_number=number, head_sha=HEAD_SHA,
                          base_sha=BASE_SHA, bot_author=BOT_AUTHOR,
                          base_ref=BASE_REF)


def fetch_pr(cfg: IdentityConfig):
    pr = gh_api(f"/repos/{REPO}/pulls/{cfg.pr_number}")
    return validate_pr_identity(pr, cfg), pr


def fetch_pr_files(pr):
    """返回 [{filename, status, previous_filename}]（rev8：保留重命名元信息）。"""
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


def check_hold_labels(pr):
    """status:hold 标签阻断：PR 自身必查；PR body 关联了 Issue（Closes #n）
    则一并检查该 Issue 的标签（Human 在 Issue 挂 hold 同样生效）。"""
    if has_hold_label(pr.get("labels")):
        fail(f"{HOLD_LABEL} 标签存在于 PR，禁止合并")
    issue_no = parse_closes_issue(pr.get("body"))
    if issue_no:
        labels = gh_api(f"/repos/{REPO}/issues/{issue_no}").get("labels", [])
        if has_hold_label(labels):
            fail(f"{HOLD_LABEL} 标签存在于关联 Issue #{issue_no}，禁止合并")
        ok(f"无 {HOLD_LABEL} 标签（PR + Issue #{issue_no}）")
    else:
        ok(f"无 {HOLD_LABEL} 标签（PR；未关联 Issue）")


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

    A1 P3 修复（保留）：用 git 对象身份（不跟随符号链接）替代文件系统读取，
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
    """纯函数：比对 {rel: (mode, blob_sha)}；删除/类型改变时 fail()。

    rev11（G-04，保留）＋Q2-141-H02：普通测试文件的内容修改不硬阻断，
    但必须返回修改清单，由调用方以显著方式报告（"允许但须 Human 审查"，
    不是"内容绑定"）；删除仍阻断（证据销毁不可逆）；
    类型改变仍阻断（symlink 替换攻击，A1 P3）。
    返回：内容被修改的文件清单（相对路径列表）。
    """
    modified = []
    for rel in sorted(base_ids):
        b_mode, b_blob = base_ids[rel]
        p_mode, p_blob = pr_ids.get(rel, (None, None))
        if p_blob is None:
            fail(f"测试文件被删除：tests/{rel}（转人工审查）")
        if p_mode != b_mode:
            fail(f"测试文件类型被改变：tests/{rel}"
                 f"（{b_mode}→{p_mode}，转人工审查）")
        if p_blob != b_blob:
            modified.append(rel)
    return modified


def check_integrity_baseline():
    """rev5 P0-3（保留）：测试文件内容级绑定 + 双边可信重算。

    - base 侧：base checkout 本地（base 版扫描器 + 文件哈希，可信）；
    - PR 侧：git fetch PR head → 校验 FETCH_HEAD == HEAD_SHA（防核验中被更新）
      → worktree 只读挂载（不执行 PR 代码）→ base 版扫描器扫描 + 文件哈希。
    - 删除/类型改变阻断；普通内容修改允许（rev11 G-04）；计数比对为二级信号。
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
        # A1 P3 回归修复（保留）：PR 侧身份必须在 worktree 移除前采集。
        pr_ids = {rel: git_blob_identity(wt, f"tests/{rel}")
                  for rel in sorted(base_shas)}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt],
                       capture_output=True, check=False)
    ok(f"PR head 实测：{pr_m['test_funcs']} funcs")
    modified = check_test_identities(base_ids, pr_ids)
    # Q2-141-H02/LOW01：诚实表述——本检查只保证"未删除、未改类型"，
    # 不保证"内容一致"。内容修改允许，但必须显著报告供 Human 审查。
    ok(f"测试文件删除/类型检查通过：{len(base_shas)} 个 base 测试文件"
       f"存在且类型一致")
    if modified:
        print("TEST_MODIFICATIONS_REQUIRE_HUMAN_REVIEW：以下测试文件内容被修改"
              "（rev11 起允许，但须 Human 在 diff 中逐项审查，确认非测试弱化）：",
              flush=True)
        for rel in modified:
            print(f"  - tests/{rel}", flush=True)
    else:
        ok("测试文件内容无修改")
    errs = compare_integrity_metrics(pr_m, base_m)
    if errs:
        fail("；".join(errs))
    ok(f"测试完整性计数通过：funcs {base_m['test_funcs']}->{pr_m['test_funcs']}，"
       f"skips {base_m['skips']}->{pr_m['skips']}")


def main():
    cfg = check_env()
    identity, pr = fetch_pr(cfg)
    ok(f"PR 身份绑定通过：#{cfg.pr_number} {cfg.head_sha[:8]} "
       f"作者={identity['author']}")

    file_objs = fetch_pr_files(pr)
    tiers = tiering.load_tiers(TIER_PATH)
    path_tier = tiering.compute_tier(tier_paths(file_objs), tiers)
    print(f"PATH_TIER={path_tier}（{len(file_objs)} 个文件逐一定级取最大，"
          f"重命名双路径）", flush=True)
    # LACD：FINAL_TIER 仅报告。T2 准入授权由 GitHub 原生机制
    # （CODEOWNERS + 分支保护审批要求）承担；policy 不硬阻断，
    # 否则门禁自身维护只能走 bypass（更差）。分支保护配置须 Human 确认。
    print(f"FINAL_TIER={path_tier}（LACD：仅报告，不硬阻断）", flush=True)

    check_hold_labels(pr)
    check_integrity_baseline()

    verdict = {
        "state": "LACD_POLICY_PASS",
        "repository": cfg.repo,
        "pr_number": cfg.pr_number,
        "head_sha": cfg.head_sha,
        "base_sha": cfg.base_sha,
        "author": identity["author"],
        "path_tier": path_tier,
        "note": ("GitHub native branch protection, required tests/evidence "
                 "checks and Human review remain separate obligations"),
    }
    print("LACD POLICY RESULT: PASS", flush=True)
    print("LACD POLICY PASS: " + json.dumps(verdict, ensure_ascii=False,
                                           sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
