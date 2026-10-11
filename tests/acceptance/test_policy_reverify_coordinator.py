"""2.0.3-3-P0 验收测试：可信 policy 自动重验协调器的静态不变量。

冻结规格：Q1《2.0.3-3-P0-FROZEN-v1》§1（完整工作流）＋ 2026-10-11 re-freeze
（B-01/B-02 决议；交付文件增补：本文件）。
本文件仅断言冻结规格原文，不含规格外断言；freeze 用 test_sha256 绑定本文件字节。

断言目标（全部静态，不访问网络、不执行 PR 代码）：
  AC-P03 无假绿灯：协调器不得产生名为 policy/tests/evidence 的 job 或 check，
      不得调用 checks/statuses 写接口；
  接线完整性：触发器、权限、并发组、关键安全字符串均与冻结规格一致。
live 行为（N-01～N-06、P-01）由 GitHub Actions 演练 PR 按规格 §8 验收，不在本文件。
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mode2-review-coordinator.yml"

REQUIRED_JOB_NAMES = {"policy", "tests", "evidence"}

# 冻结规格 §1 要求脚本中必须出现的关键安全字符串（阻力最小通过路径的堵点）。
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


def test_workflow_file_exists_and_is_clean_text():
    raw = WORKFLOW.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "workflow 文件不得带 BOM"
    assert b"\r" not in raw, "workflow 文件必须为 LF 换行"
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), "文件末尾必须恰好一个换行"


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


def test_safety_markers_present():
    """关键安全字符串缺失 = 守卫被删，测试必须红（防为绿灯删守卫）。"""
    text, _ = _load()
    missing = [m for m in REQUIRED_SCRIPT_MARKERS if m not in text]
    assert not missing, f"脚本缺失关键安全标记: {missing}"


def test_single_secret():
    """唯一允许的 secret 是 GITHUB_TOKEN；不得新增 secret。"""
    import re

    text, _ = _load()
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert secrets == {"GITHUB_TOKEN"}, f"只允许 GITHUB_TOKEN，实际: {sorted(secrets)}"


def test_concurrency_guard():
    _, doc = _load()
    conc = doc.get("concurrency", {})
    assert conc.get("cancel-in-progress") is False, "同一 PR 的协调必须串行且不取消进行中的"
    assert "m2c-pr-" in str(conc.get("group", "")), "并发组必须按 PR 隔离"
