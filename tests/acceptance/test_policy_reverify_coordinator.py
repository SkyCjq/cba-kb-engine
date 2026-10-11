"""2.0.3-3-P0 验收测试：可信 policy 自动重验协调器的静态不变量（v2）。

冻结规格：SPEC_FROZEN-v1.1 §1（完整工作流）＋ 2026-10-11 re-freeze
（B-01/B-02 决议；交付文件增补：本文件）。
本文件仅断言冻结规格原文，不含规格外断言；freeze 用 test_sha256 绑定本文件字节。

v2 补强（Q2-FAIL round1 修复）：Q2 反向验证证明 v1 的字符串存在性检查
不足以保证守卫有效（掏空作者校验/复制 POST 行/改弱并发组仍 9/9）。
v2 增加：
  1. 冻结字节绑定：workflow 文件 SHA-256 必须等于规格 §1 第 19–322 行
     （18,509 字节）的摘要——该摘要经独立复算与候选文件一致，
     是规格原文的机械重述，不是规格外断言；
  2. 守卫语义检查：关键守卫必须是真实比较＋fail-closed，
     而不只是字符串出现；
  3. POST 至多一次与完整并发约束的精确断言。

断言目标（全部静态，不访问网络、不执行 PR 代码）：
  AC-P03 无假绿灯：协调器不得产生名为 policy/tests/evidence 的 job 或 check，
      不得调用 checks/statuses 写接口；
  接线完整性：触发器、权限、并发组、关键守卫语义均与冻结规格一致。
live 行为（N-01～N-06、P-01）由 GitHub Actions 演练 PR 按规格 §8 验收，不在本文件。
"""

import hashlib
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mode2-review-coordinator.yml"

# 规格 §1 第 19–322 行（18,509 字节）的 SHA-256；经独立复算与候选文件一致。
FROZEN_WORKFLOW_SHA256 = "3d254bcd59957741349514f9bf5fb47ddd40bd775c824be941a21356986b2e6f"

# 冻结规格 §1 env 块的可信常量（逐字精确值）。
TRUSTED_IDENTITIES = '\'[{"login":"SkyCjq","id":5900732,"type":"User"}]\''
TRUSTED_WORKFLOW_BLOB = "'419dd9d05a80a74779d577278078ca8a3f12f412'"

REQUIRED_JOB_NAMES = {"policy", "tests", "evidence"}

# 关键安全字符串：存在性是必要条件（守卫被删必红），语义检查见
# test_safety_guards_effective（存在性≠有效性，Q2-FAIL 教训）。
REQUIRED_SCRIPT_MARKERS = [
    "rerun-failed-jobs",          # 唯一写操作：重跑原 Run 的失败 job
    "pull_request_target",        # 原 Run 必须来自可信事件
    "pr-gate-trusted.yml",        # 原 Run 必须来自可信工作流定义
    "UNTRUSTED_AUTHOR",           # 非可信作者 HOLD
    "MARKER_INVALID_OR_STALE",    # 非法/过期 marker HOLD
    "PR_CONTEXT_REJECTED",        # 非 open/非 main/非本仓库 PR HOLD
    "NO_COMPLETED_UNAMBIGUOUS_RUN",  # 无唯一已完成原 Run HOLD
    "TARGET_NOT_INITIAL_POLICY_FAILURE",  # 目标非初始 policy 失败 HOLD
    "POST_UNCONFIRMED",           # POST 非 201 不重试 HOLD
    "AUTH_FAIL",                  # 401/403 明确阻断
    "ORIGINAL_POLICY_REVALIDATED",  # 唯一 PASS 原因
]


def _load():
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _window_before(text, anchor, size=600):
    """取 anchor 之前 size 字符的窗口，用于验证守卫的语义与其 hold 在同一逻辑块。"""
    m = re.search(anchor, text)
    assert m, f"未找到锚点 {anchor!r}"
    return text[max(0, m.start() - size):m.start()]


def test_workflow_file_exists_and_matches_frozen_digest():
    """冻结字节绑定：文件必须与规格 §1 的完整 YAML 逐字节一致。

    任何突变（掏空守卫、复制 POST、改弱并发组、改名 job）都会改变字节，
    摘要即失配。这是 Q2-FAIL 第三项的直接修复。
    """
    raw = WORKFLOW.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "workflow 文件不得带 BOM"
    assert b"\r" not in raw, "workflow 文件必须为 LF 换行"
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), "文件末尾必须恰好一个换行"
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == FROZEN_WORKFLOW_SHA256, (
        f"workflow 字节与冻结规格 §1 不一致: sha256={digest}"
    )
    text = raw.decode("utf-8")
    assert f"TRUSTED_IDENTITIES: {TRUSTED_IDENTITIES}" in text, "可信身份常量必须逐字精确"
    assert f"TRUSTED_WORKFLOW_BLOB: {TRUSTED_WORKFLOW_BLOB}" in text, "可信工作流摘要必须逐字精确"


def test_name_and_trigger():
    _, doc = _load()
    assert doc["name"] == "mode2-review-coordinator"
    on = doc[True]  # YAML 1.1 下 on 解析为 True
    assert set(on.keys()) == {"issue_comment"}, f"只允许 issue_comment 触发，实际: {sorted(on)}"
    assert on["issue_comment"].get("types") == ["created"]


def test_no_self_issued_gate_checks():
    """AC-P03：协调器不得自产 policy/tests/evidence 同名绿灯。"""
    _, doc = _load()
    assert doc["name"] not in REQUIRED_JOB_NAMES
    jobs = doc["jobs"]
    assert set(jobs.keys()) == {"coordinate"}, f"只允许一个 coordinate job，实际: {sorted(jobs)}"
    for job_id, job in jobs.items():
        assert job.get("name", job_id) not in REQUIRED_JOB_NAMES, (
            f"job 名 {job.get('name', job_id)!r} 与必需检查同名，属假绿灯"
        )


def test_minimal_permissions():
    _, doc = _load()
    assert doc.get("permissions") == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
        "actions": "write",
    }, "权限必须恰为冻结规格的最小集合（actions:write 仅用于 rerun-failed-jobs）"


def test_no_untrusted_execution():
    """不得 checkout/执行 PR 代码，不得使用外部 action，不得读 PR 内配置。"""
    text, doc = _load()
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            assert "uses" not in step, f"不允许外部 action: {step.get('name')}"
    lowered = text.lower()
    assert "actions/checkout" not in lowered
    assert "pull_request_target" in text  # 仅作为原 Run 来源的过滤条件出现


def test_no_check_write_endpoints():
    """AC-P03：不得写 checks/statuses，不得伪造检查结论。"""
    text, _ = _load()
    assert "check-runs" not in text, "不得调用 check-runs 写接口"
    assert "/statuses" not in text, "不得调用 statuses 写接口"


def test_safety_guards_effective():
    """关键守卫必须语义有效：真实比较＋fail-closed，而不只是字符串出现。

    Q2 反向验证：把作者校验掏空为保留字符串的空操作，v1 仍 9/9。
    本测试要求比较式与 hold 落在同一逻辑块，空操作突变必红。
    """
    text, _ = _load()
    missing = [m for m in REQUIRED_SCRIPT_MARKERS if m not in text]
    assert not missing, f"脚本缺失关键安全标记: {missing}"

    # 作者守卫：必须真实比较 id/login/type 三元组，且失败走 hold。
    window = _window_before(text, r"\|\| hold UNTRUSTED_AUTHOR")
    assert "any($trusted[]; .id==$u.id and .login==$u.login and .type==$u.type)" in window, (
        "作者守卫必须真实比较可信三元组，不能是保留字符串的空操作"
    )

    # marker 守卫：必须调用 marker valid($head;$issue;$pr) 校验，且失败走 hold。
    window = _window_before(text, r"\|\| hold MARKER_INVALID_OR_STALE")
    assert "marker | valid($head;$issue;$pr)" in window, (
        "marker 守卫必须真实校验 marker，不能是保留字符串的空操作"
    )

    # 可信工作流定义守卫：必须比对 blob 摘要，且漂移走 hold。
    window = _window_before(text, r"\|\| hold TRUSTED_DEFINITION_DRIFT")
    assert ".sha==$blob" in window, "可信定义守卫必须真实比对 blob 摘要"

    # POST 至多一次：rerun-failed-jobs 的实际 api 调用必须恰出现一次。
    # Q2 反向验证：复制该行 v1 仍 9/9；此处复制即红。
    posts = re.findall(r'api POST "actions/runs/\$original/rerun-failed-jobs"', text)
    assert len(posts) == 1, f"rerun-failed-jobs POST 必须恰一次，实际 {len(posts)} 次"

    # 授权失败必须 fail-closed：401/403 → AUTH_FAIL → exit 1。
    idx = text.find('[[ "$http" == 401 || "$http" == 403 ]]')
    assert idx != -1, "必须显式处理 401/403"
    tail = text[idx:idx + 400]
    assert "state=AUTH_FAIL" in tail and "exit 1" in tail, "401/403 必须 AUTH_FAIL 并非零退出"

    # 写操作不重试：POST 非 201 即 HOLD，不存在重试路径。
    assert '[[ "$rc" == 0 && "$http" == 201 ]] || hold POST_UNCONFIRMED' in text, (
        "POST 非 201 必须 HOLD 且不重试"
    )


def test_single_secret():
    """唯一允许的 secret 是 GITHUB_TOKEN；不得新增 secret。"""
    text, _ = _load()
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert secrets == {"GITHUB_TOKEN"}, f"只允许 GITHUB_TOKEN，实际: {sorted(secrets)}"


def test_concurrency_guard():
    """并发约束必须完整：按仓库＋PR 隔离、串行不取消、排队。

    Q2 反向验证：把组名改为固定值并删除 queue: max，v1 仍 9/9；此处必红。
    """
    text, doc = _load()
    conc = doc.get("concurrency", {})
    group = str(conc.get("group", ""))
    assert "github.repository_id" in group, "并发组必须按仓库隔离"
    assert "github.event.issue.number" in group, "并发组必须按 PR 隔离"
    assert conc.get("cancel-in-progress") is False, "同一 PR 的协调必须串行且不取消进行中的"
    assert re.search(r"queue:\s*max", text), "并发必须排队（queue: max），不得丢弃"
