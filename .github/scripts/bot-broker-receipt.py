#!/usr/bin/env python3
"""bot-broker-receipt.py — Broker 可信结果回执写入器（A+ 协议）。

运行环境：bot-pr-broker workflow 内，以 GITHUB_TOKEN（github-actions[bot]
身份）执行。**本脚本及调用它的步骤 env 中绝不出现 App token。**

职责：在目标 Issue 下维护**一条**与 request_id 绑定的结构化回执评论：
  定位 marker：<!-- bot-broker-receipt:{request_id} -->
  存在（作者为 github-actions[bot] 且 marker 匹配）→ 更新；不存在 → 创建；
  绝不重复发多条。

  R4 D2/D3：回执 marker 与一切写入键都用**重算的派生 ID**（完整 SHA），不用
  payload 声称的 ID；写 PENDING 前必须先完成 sender 白名单与完整契约校验
  （workflow 顺序保证 + 本脚本复用校验器做防御性复验）。

状态机（Broker 写入）：PENDING（校验通过后）→ RUNNING（即将创建）
→ CREATED（已创建**并回读确认**，含编号/URL/SHA）/ REJECTED（仅"格式合法但
政策拒绝"且有可信 deny 证据，AC06+）。终态（CREATED/REJECTED/FAILED）一经
写入**不可覆盖**（R5 FND-17：覆盖尝试 → 拒绝并记证据）。创建已尝试但未确认
→ 不写任何终态（不写 FAILED；调用方判 UNKNOWN/HOLD）。
CANCELLED / UNKNOWN 由调用方推断，Broker 不写。
非法请求（sender 不在白名单 / 契约层 AC04 / 身份层 AC05）**不得**向合法派生
marker 写入任何内容（R5 FND-17）。

回执内容为 JSON 代码块，字段：
  request_id / status / run_id / run_url / attempt_id / issue / branch /
  head_sha / base_sha / pr_number? / pr_url? / timestamp(UTC) /
  error_class? / error_detail?
reason 永不含 secret/token（校验器的 deny reason 本就不含）。

用法：
  python3 bot-broker-receipt.py --status PENDING|RUNNING   # 过程回执（需先通过校验）
  python3 bot-broker-receipt.py --finalize                  # 终态回执
  python3 bot-broker-receipt.py --rejected-if-derivable      # 校验失败路径：
      仅当 sender 合法、可派生合法 request_id、且拒绝 AC 为 AC06+（格式合法但
      政策拒绝）时写 REJECTED；AC04/AC05/无证据 → 静默退出 0，不写回执

环境变量：
  GH_TOKEN      GITHUB_TOKEN（只写回执评论，不做其他写操作）
  REPO          SkyCjq/cba-kb-engine（锁定）
  SENDER_LOGIN  github.event.sender.login（防御性复验用）
  PAYLOAD       github.event.client_payload 的 JSON（含六元组）
  RUN_ID        github.run_id
  RUN_URL       本次 run 的 URL
  BASE_SHA      校验器记录的 diff 基准（--status RUNNING / --finalize 用）
  VALIDATE_DECISION   校验步骤输出 decision（--finalize 用）
  VALIDATE_OUTCOME    校验步骤 outcome（success/failure；--finalize 用）
  VALIDATE_PR_NUMBER  幂等命中时的 PR 编号（--finalize 用）
  CREATE_PR_NUMBER    建 PR 步骤输出的 PR 编号（--finalize 用）
  DENY_FILE     BROKER_DENY_FILE 路径（--finalize / --rejected-if-derivable
                读拒绝证据用）

成功：exit 0。任何失败（API 错误/输入异常）→ exit 非零（fail-closed；
调用方按"无有效回执"走对账/超时路径，见 A+ 协议）。

仅使用 Python 标准库。
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

OWNER_REPO = "SkyCjq/cba-kb-engine"
RECEIPT_AUTHOR = "github-actions[bot]"
VALID_STATUSES = {"PENDING", "RUNNING", "CREATED", "REJECTED", "FAILED"}
# R5 FND-17：终态集合 —— 一旦写入，任何不同状态的覆盖尝试都必须被拒绝。
TERMINAL_STATUSES = {"CREATED", "REJECTED", "FAILED"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _load_validator():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(here, "bot-broker-validate.py"))
    spec = importlib.util.spec_from_file_location("bot_broker_validate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VALIDATOR = _load_validator()


class ApiError(Exception):
    pass


def gh_api(path, params=None, method="GET", fields=None, timeout=30):
    """GitHub API 调用。失败抛 ApiError（fail-closed）。"""
    url = path
    if params:
        url += "?" + urlencode(params)
    cmd = ["gh", "api", "--method", method, url]
    for k, v in (fields or {}).items():
        cmd += ["-f", "%s=%s" % (k, v)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise ApiError("gh api execution failed: %s" % type(exc).__name__)
    if r.returncode != 0:
        first = (r.stderr or "").strip().splitlines()
        hint = first[0][:120] if first else "unknown error"
        raise ApiError("GitHub API error: %s" % hint)
    if not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        raise ApiError("GitHub API returned non-JSON")


def fail(msg):
    print("receipt FATAL: %s" % msg, file=sys.stderr)
    sys.exit(1)


def marker_for(request_id):
    return "<!-- bot-broker-receipt:%s -->" % request_id


def load_request():
    """严格加载请求（D2/D3）：复用校验器的完整契约校验 + sender 白名单。

    marker 与一切写入键都用**重算的派生 ID**（完整 SHA），不用 payload 声称的
    ID。任一校验失败 → fail（fail-closed，不写回执）。
    返回 {"request_id"(派生), "attempt_id", "issue", "branch", "sha"}。"""
    repo = os.environ.get("REPO", "")
    if repo != OWNER_REPO:
        fail("REPO is not the locked target")
    sender = os.environ.get("SENDER_LOGIN", "")
    if sender not in _VALIDATOR.ALLOWED_SENDERS:
        fail("SENDER_LOGIN is not in the allowed set")
    # parse_contract 内部失败即 deny → exit 非零（fail-closed）。
    try:
        issue, branch, sha, attempt_id = _VALIDATOR.parse_contract()
    except SystemExit as exc:
        # deny() 已打印 JSON；转为回执脚本的 FATAL 语义（非零退出）。
        sys.exit(exc.code if isinstance(exc.code, int) else 1)
    return {
        "request_id": _VALIDATOR.derive_request_id(issue, sha),
        "attempt_id": attempt_id,
        "issue": issue,
        "branch": branch,
        "sha": sha,
    }


def load_request_lenient():
    """宽松解析（仅 --rejected-if-derivable 用）：返回派生 ID 或 None。

    R5 FND-17：本函数是**纯只读**的 —— 只解析、不产生任何写入；"写不写"的
    决定权在 cmd_rejected_if_derivable 的门控（sender 白名单 + AC04/AC05 过滤），
    不在本函数。

    D3：仅当 issue 为正整数、sha 为 40 位 hex、声称 request_id 格式合法且
    == 派生值时，才认为可派生；否则返回 None（调用方静默退出 0，不写回执）。"""
    raw = os.environ.get("PAYLOAD", "")
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    issue = payload.get("issue")
    sha = payload.get("sha")
    claimed = payload.get("request_id")
    attempt_id = payload.get("attempt_id")
    if (
        isinstance(issue, bool) or not isinstance(issue, int)
        or not 0 < issue < 2**31
        or not isinstance(sha, str) or not SHA_RE.fullmatch(sha)
        or not isinstance(claimed, str)
        or not _VALIDATOR.REQUEST_ID_RE.fullmatch(claimed)
    ):
        return None
    derived = _VALIDATOR.derive_request_id(issue, sha)
    if claimed != derived:
        return None
    if (
        isinstance(attempt_id, bool) or not isinstance(attempt_id, int)
        or not 0 < attempt_id < 2**31
    ):
        attempt_id = 0
    return {
        "request_id": derived,
        "attempt_id": attempt_id,
        "issue": issue,
        "branch": payload.get("branch") if isinstance(payload.get("branch"), str) else "",
        "sha": sha,
    }


def build_receipt(req, status, extra):
    """组装回执 JSON（None 字段省略）。"""
    try:
        run_id = int(os.environ.get("RUN_ID", "") or 0)
    except ValueError:
        run_id = 0
    receipt = {
        "request_id": req["request_id"],
        "status": status,
        "run_id": run_id,
        "run_url": os.environ.get("RUN_URL", ""),
        "attempt_id": req["attempt_id"],
        "issue": req["issue"],
        "branch": req["branch"],
        "head_sha": req["sha"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    base_sha = os.environ.get("BASE_SHA", "")
    if base_sha:
        receipt["base_sha"] = base_sha
    for key in ("pr_number", "pr_url", "error_class", "error_detail"):
        if extra.get(key) is not None:
            receipt[key] = extra[key]
    return receipt


def render_body(req, receipt):
    lines = [
        marker_for(req["request_id"]),
        "**Bot Broker 回执** · `%s`" % req["request_id"],
        "",
        "```json",
        json.dumps(receipt, indent=2, ensure_ascii=False),
        "```",
        "",
        "> 由 bot-pr-broker 以 github-actions[bot] 身份维护；"
        "run-name 仅为关联线索，不作终态证明。",
    ]
    return "\n".join(lines) + "\n"


def _get_all_pages(path, params, list_key=None):
    """D7/FND-21：手动翻页遍历。list_key 为 None 时期望顶层 list，
    否则从 dict[list_key] 取。任一页失败/结构异常 → ApiError（调用方按
    "查询不完整"处理，绝不据此推断可安全重发）。"""
    items = []
    page = 1
    while True:
        q = dict(params or {})
        q["per_page"] = "100"
        q["page"] = str(page)
        data = gh_api(path, q)
        if list_key is None:
            batch = data
        elif isinstance(data, dict):
            batch = data.get(list_key)
        else:
            batch = None
        if not isinstance(batch, list):
            raise ApiError("paginated lookup returned malformed data")
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 100:  # 安全上限：1 万条
            raise ApiError("paginated lookup exceeded page cap")
    return items


def find_receipt_comment(issue, request_id):
    """查找本 request_id 的有效回执评论。返回 (id, body) 或 (None, None)。

    有效性：作者 == github-actions[bot] 且正文含 marker。作者不对 → 视为
    不存在（不更新别人的评论，防误写；调用方同样只认该作者的回执）。
    D7：翻页遍历全部评论，marker 在第 N 页也能找到，不会重复建评论。"""
    try:
        comments = _get_all_pages(
            "repos/%s/issues/%d/comments" % (OWNER_REPO, issue), None)
    except ApiError as exc:
        fail("cannot list issue comments: %s" % exc)
    marker = marker_for(request_id)
    for c in comments:
        if not isinstance(c, dict):
            continue
        user = c.get("user")
        body = c.get("body")
        cid = c.get("number", c.get("id"))
        if (
            isinstance(user, dict)
            and user.get("login") == RECEIPT_AUTHOR
            and isinstance(body, str)
            and marker in body
            and isinstance(cid, int)
        ):
            return cid, body
    return None, None


def _parse_receipt_status(body):
    """从回执评论正文解析 ```json 代码块的 status；解析失败 → None。

    R5 FND-17：写回执前必须先读旧状态，判断是否为终态。解析失败视为
    "未知旧状态" —— write_receipt 对未知旧状态按 fail-closed 拒绝覆盖
    （见 write_receipt）。"""
    if not isinstance(body, str):
        return None
    start = body.find("```json")
    if start < 0:
        return None
    start += len("```json")
    end = body.find("```", start)
    if end < 0:
        return None
    try:
        rec = json.loads(body[start:end])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rec, dict):
        return None
    status = rec.get("status")
    return status if isinstance(status, str) else None


def write_receipt(req, receipt):
    """存在则更新，不存在则创建；绝不重复发多条。

    R5 FND-17：终态不可覆盖 —— 写之前先读旧状态；旧状态为终态
    （CREATED/REJECTED/FAILED）且新状态不同 → 拒绝写入并记录证据
    （stderr + 非零退出，不得静默）。同状态重写（幂等）与非终态流转允许。
    旧状态不可解析 → 同样拒绝（fail-closed：无法证明非终态）。"""
    body = render_body(req, receipt)
    cid, old_body = find_receipt_comment(req["issue"], req["request_id"])
    new_status = receipt.get("status")
    if cid is not None:
        old_status = _parse_receipt_status(old_body)
        if old_status in TERMINAL_STATUSES and new_status != old_status:
            print(
                "receipt REFUSED: comment %d already has terminal status %s; "
                "refusing to overwrite with %s (request %s)"
                % (cid, old_status, new_status, req["request_id"]),
                file=sys.stderr,
            )
            sys.exit(1)
        if old_status is None:
            print(
                "receipt REFUSED: comment %d has unparseable status; "
                "refusing to overwrite blindly (request %s)"
                % (cid, req["request_id"]),
                file=sys.stderr,
            )
            sys.exit(1)
    try:
        if cid is not None:
            gh_api(
                "repos/%s/issues/comments/%d" % (OWNER_REPO, cid),
                method="PATCH",
                fields={"body": body},
            )
            print("receipt updated comment %d status=%s" % (cid, receipt["status"]))
        else:
            created = gh_api(
                "repos/%s/issues/%d/comments" % (OWNER_REPO, req["issue"]),
                method="POST",
                fields={"body": body},
            )
            new_id = created.get("id") if isinstance(created, dict) else None
            print(
                "receipt created comment %s status=%s" % (new_id, receipt["status"])
            )
    except ApiError as exc:
        fail("cannot write receipt comment: %s" % exc)


def cmd_status(status):
    if status not in VALID_STATUSES or status in ("CREATED", "REJECTED", "FAILED"):
        fail("invalid --status value: %s" % status)
    req = load_request()
    receipt = build_receipt(req, status, {})
    write_receipt(req, receipt)


def read_deny_evidence():
    """读取 BROKER_DENY_FILE 的 deny JSON → (error_class, error_detail)。
    缺失/损坏 → (None, None)，由调用方按"无机器可读证据"处理。"""
    path = os.environ.get("DENY_FILE", "")
    if not path or not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        if not isinstance(rec, dict):
            return None, None
        return rec.get("ac"), rec.get("reason")
    except Exception:  # noqa: BLE001
        return None, None


def _find_matching_pr(req):
    """D4：在 --finalize 中做 PR 对账（state=all，翻页），判断"创建是否已发生"。

    返回匹配的 PR dict 或 None。匹配条件与校验器的归属核验一致
    （state==open、作者==冻结 bot、head.repo/base.repo==目标仓库、
    head.ref==branch、head.sha==完整 sha、base.ref==main）。"""
    try:
        prs = _get_all_pages(
            "repos/%s/pulls" % OWNER_REPO,
            {"head": "SkyCjq:%s" % req["branch"], "state": "all"})
    except ApiError as exc:
        fail("cannot reconcile PRs in finalize: %s" % exc)
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        try:
            _VALIDATOR._check_pr_attribution(pr, req["branch"], req["sha"])
            return pr
        except _VALIDATOR.Deny:
            continue
    return None


def cmd_finalize():
    """终态决策（A+ 协议，R4 D4 修订）：
    - CREATE_PR_NUMBER 非空 → CREATED（绑定本次创建/竞态恢复的编号）
    - VALIDATE_DECISION == approve_idempotent 且有编号 → CREATED（幂等命中）
    - VALIDATE_OUTCOME == failure → 仅当有可信 deny 证据时写 REJECTED；
      无证据 → 不写回执（调用方按"无终态回执"走对账/超时路径）
    - 校验通过但无 CREATED 证据 → 做 PR 对账：找到匹配 PR → CREATED（绑定，
      覆盖"建 PR 成功但编号解析失败"的场景）；找不到 → 不写任何终态回执
      （R4 FND-20：禁止把"未确认"写成 FAILED；调用方判 UNKNOWN/HOLD）"""
    req = load_request()
    create_pr = os.environ.get("CREATE_PR_NUMBER", "").strip()
    val_decision = os.environ.get("VALIDATE_DECISION", "").strip()
    val_outcome = os.environ.get("VALIDATE_OUTCOME", "").strip()
    val_pr = os.environ.get("VALIDATE_PR_NUMBER", "").strip()

    extra = {}
    status = None
    if create_pr:
        status = "CREATED"
        extra["pr_number"] = int(create_pr) if create_pr.isdigit() else create_pr
        extra["pr_url"] = "https://github.com/%s/pull/%s" % (OWNER_REPO, create_pr)
    elif val_decision == "approve_idempotent" and val_pr:
        status = "CREATED"
        extra["pr_number"] = int(val_pr) if val_pr.isdigit() else val_pr
        extra["pr_url"] = "https://github.com/%s/pull/%s" % (OWNER_REPO, val_pr)
    elif val_outcome == "failure":
        ac, reason = read_deny_evidence()
        if ac is None:
            print("finalize: validate failed but no trusted deny evidence; "
                  "not writing REJECTED (caller will go UNKNOWN/HOLD)")
            return
        status = "REJECTED"
        extra["error_class"] = ac
        extra["error_detail"] = reason or "validator denied without detail"
    else:
        # 校验通过但无 CREATED 证据：PR 对账决定写 CREATED 还是什么都不写。
        pr = _find_matching_pr(req)
        if pr is not None:
            status = "CREATED"
            extra["pr_number"] = pr["number"]
            extra["pr_url"] = "https://github.com/%s/pull/%d" % (OWNER_REPO, pr["number"])
            extra["error_detail"] = ("recovered via PR reconciliation: create step "
                                     "did not report a number")
        else:
            print("finalize: create attempted but unconfirmed and no matching PR; "
                  "not writing a terminal receipt (caller will go UNKNOWN/HOLD)")
            return
    receipt = build_receipt(req, status, extra)
    write_receipt(req, receipt)


def cmd_rejected_if_derivable():
    """D3：校验失败路径的 REJECTED 回执。

    R5 FND-17 修订 —— 写入门槛收紧，只有"格式合法但政策拒绝"的请求才配写
    REJECTED：
    1. sender 不在白名单 → 不写（workflow 首步已拦截，此处为防御性复验）。
    2. 仅当可从 PAYLOAD 派生出合法 request_id（issue 正整数、sha 40hex、
       声称 ID 格式合法且 == 派生值）时才考虑写；否则静默退出 0。
    3. 读 DENY_FILE 的拒绝 AC：AC04（契约层：字段/格式/operation 非法等）或
       AC05（身份层）→ **不写**（非法请求不得污染合法派生 marker；调用方将
       走向 UNKNOWN/HOLD，fail-closed）。无 deny 证据（ac is None）→ 也不写
       （只有可信的 deny 证据才构成 REJECTED，见 FND-20）。
    4. 只有 AC06+（分支绑定/Issue 状态/T2 路径/幂等冲突等"格式合法但政策拒绝"）
       才写 REJECTED（marker 用派生 ID）。

    deny 证据缺失/不可派生 → 静默退出 0（调用方按"无终态回执"走对账/超时）。"""
    repo = os.environ.get("REPO", "")
    if repo != OWNER_REPO:
        print("rejected-if-derivable: REPO mismatch; skipping receipt")
        return
    # R5 FND-17：sender 防御性复验（workflow 首步已拦截）。
    sender = os.environ.get("SENDER_LOGIN", "")
    if sender not in _VALIDATOR.ALLOWED_SENDERS:
        print("rejected-if-derivable: sender not in allowed set; skipping receipt")
        return
    req = load_request_lenient()
    if req is None:
        print("rejected-if-derivable: request_id not derivable; skipping receipt")
        return
    ac, reason = read_deny_evidence()
    if ac in ("AC04", "AC05"):
        # 契约/身份层非法 → 不得碰合法派生 marker（R5 FND-17 实测：
        # 非法 operation 的 REJECTED 曾被写到同一 marker，造成审计污染）。
        print("rejected-if-derivable: deny at contract/identity layer (%s); "
              "not writing to marker" % ac)
        return
    if ac is None:
        # 无可信 deny 证据 → 不写（只有可信证据才构成 REJECTED）。
        print("rejected-if-derivable: no trusted deny evidence; skipping receipt")
        return
    extra = {
        "error_class": ac,
        "error_detail": reason or ("validator denied the request but the deny "
                                   "evidence had no detail; see run logs"),
    }
    receipt = build_receipt(req, "REJECTED", extra)
    write_receipt(req, receipt)


def main():
    argv = sys.argv[1:]
    if "--finalize" in argv:
        cmd_finalize()
        return
    if "--rejected-if-derivable" in argv:
        cmd_rejected_if_derivable()
        return
    if "--status" in argv:
        i = argv.index("--status")
        if i + 1 >= len(argv):
            fail("--status requires a value")
        cmd_status(argv[i + 1])
        return
    fail("usage: bot-broker-receipt.py --status PENDING|RUNNING | --finalize | --rejected-if-derivable")


if __name__ == "__main__":
    main()
