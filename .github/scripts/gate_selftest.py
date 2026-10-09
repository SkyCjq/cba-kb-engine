#!/usr/bin/env python3
"""Mode 2.1 门禁负面测试（rev5）。

对应独立安全复审 rev2/rev3/rev4 报告的 P0 漏洞，每个都是"旧行为必须失败"式的
差异性证伪：用复审报告中的攻击输入跑真实代码，断言被阻断。
rev5 新增：决策语义（CHANGES_REQUESTED 后 COMMENTED）、权限 API（mock）、
测试文件内容绑定、bundle 文件清单。

运行：python3 .github/scripts/gate_selftest.py
（可信层用 base 版运行；隔离层也运行一份做快速反馈。）
"""
import json
import base64
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# policy_gate 模块级要求环境变量：测试前先给 dummy 值
for k in ("GH_TOKEN", "REPO", "PR_NUMBER", "HEAD_SHA", "BASE_SHA",
          "PR_AUTHOR", "BASE_REF"):
    os.environ.setdefault(k, "dummy")
os.environ.setdefault("TRUSTED_AUTHORS", "dummy")

import tiering

# policy-gate.py 带连字符，不能直接 import：按路径加载
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "policy_gate", os.path.join(HERE, "policy-gate.py"))
policy_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy_gate)

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# ---------- 1. glob 语义矩阵（确定性） ----------
GLOB_CASES = [
    ("src/release/a.py", "src/**/release/**", True),
    ("src/foo/release/a.py", "src/**/release/**", True),
    ("src/other/a.py", "src/**/release/**", False),
    ("src/cba_kb/foo.py", "src/**", True),
    ("README.md", "*.md", True),
    ("docs/a/b.md", "*.md", True),
    ("AGENTS.md", "**/AGENTS.md", True),
    ("docs/x/AGENTS.md", "**/AGENTS.md", True),
    ("tests/acceptance/test_foo.py", "tests/acceptance/test_*.py", True),
    ("tests/acceptance/sub/test_foo.py", "tests/acceptance/test_*.py", False),
    ("Dockerfile", "Dockerfile", True),
    ("deploy/Dockerfile", "Dockerfile", True),
    ("requirements.txt", "requirements*.txt", True),
    ("requirements-dev.txt", "requirements*.txt", True),
]
for path, pat, exp in GLOB_CASES:
    got = bool(tiering.glob_to_regex(pat).match(path))
    check(f"glob {pat!r} vs {path!r} == {exp}", got == exp)

# ---------- 2. Vuln 1：混合 PR 降级 ----------
tiers = tiering.load_tiers(os.path.join(HERE, "..", "risk-tiers.yml"))
WASH_CASES = [
    (["Dockerfile", "README.md"], "T2"),
    (["config/new.yml", "Dockerfile"], "T2"),
    (["src/release/a.py"], "T2"),
    (["src/foo.py"], "T1"),
    (["README.md"], "T0"),
    (["automation/git_io.py"], "T1"),
    (["automation/verify.py"], "T2"),
    (["tests/fixtures/contract/x.csv"], "T2"),
    (["docs/a/AGENTS.md"], "T2"),
    ([], "T2"),
]
for files, exp in WASH_CASES:
    got = tiering.compute_tier(files, tiers)
    check(f"tier {files} == {exp}", got == exp)

# T2 硬约束（rev4 阻断项 2）
check("T2 被硬阻断",
      len(policy_gate.enforce_no_t2("T2")) > 0)
check("T1/T0 不阻断",
      policy_gate.enforce_no_t2("T1") == [] and policy_gate.enforce_no_t2("T0") == [])

# ---------- 3. 审批：决策语义 + 授权主体（rev5 P0-2） ----------
def _rev(login, state, sha="abc", assoc="OWNER", ts="2026-10-09T01:00:00Z"):
    return {"user": {"login": login, "type": "User"}, "state": state,
            "commit_id": sha, "author_association": assoc, "submitted_at": ts}

# 权限查询 mock 前置：rev6 起真实 get_repo_permission 失败即 fail-closed，
# 以下决策语义测试先 mock 掉网络调用（文件末尾恢复）
_orig_perm = policy_gate.get_repo_permission
policy_gate.get_repo_permission = lambda u: "write"

# 决策语义：CHANGES_REQUESTED 后再 COMMENTED，仍然是要求修改（rev5 P0-2b）
st = policy_gate.review_stance([
    _rev("bob", "CHANGES_REQUESTED", ts="2026-10-09T10:01:00Z"),
    _rev("bob", "COMMENTED", ts="2026-10-09T10:02:00Z")])
check("CHANGES_REQUESTED 后 COMMENTED 仍是要求修改",
      st is not None and st["state"] == "CHANGES_REQUESTED")
st = policy_gate.review_stance([
    _rev("bob", "CHANGES_REQUESTED", ts="2026-10-09T10:01:00Z"),
    _rev("bob", "APPROVED", ts="2026-10-09T10:03:00Z")])
check("之后 APPROVED 则转为批准", st is not None and st["state"] == "APPROVED")
st = policy_gate.review_stance([_rev("bob", "COMMENTED")])
check("仅 COMMENTED 无决策", st is None)

# 复审场景：Bob CHANGES_REQUESTED 后 COMMENTED，Alice APPROVED -> 整体阻断
v, e = policy_gate.evaluate_approvals(
    [_rev("bob", "CHANGES_REQUESTED", ts="2026-10-09T10:01:00Z"),
     _rev("bob", "COMMENTED", ts="2026-10-09T10:02:00Z"),
     _rev("alice", "APPROVED", ts="2026-10-09T10:03:00Z")], "bot", "abc")
check("CHANGES_REQUESTED 后 COMMENTED 不能掩盖阻断", v == ["alice"] and len(e) > 0)

# 权限 API 路径（mock）：read 权限不计入，write 计入（rev6 P0-2a）
policy_gate.get_repo_permission = lambda u: "read"
v, e = policy_gate.evaluate_approvals([_rev("r1", "APPROVED")], "bot", "abc")
check("read 权限批准不计入", v == [] and e == [])
policy_gate.get_repo_permission = lambda u: "write"
v, e = policy_gate.evaluate_approvals([_rev("r1", "APPROVED")], "bot", "abc")
check("write 权限批准计入", v == ["r1"] and e == [])
# rev6 P0-2：权限查询失败必须 fail-closed（抛异常），绝不回退放行
def _boom(u):
    raise RuntimeError("HTTP 403")
policy_gate.get_repo_permission = _boom
try:
    policy_gate.evaluate_approvals([_rev("r1", "APPROVED")], "bot", "abc")
    check("权限查询失败时 fail-closed（抛异常）", False)
except RuntimeError:
    check("权限查询失败时 fail-closed（抛异常）", True)
except SystemExit:
    check("权限查询失败时 fail-closed（抛异常）", True)
policy_gate.get_repo_permission = lambda u: None  # 权限未知
v, e = policy_gate.evaluate_approvals([_rev("r1", "APPROVED", assoc="OWNER")], "bot", "abc")
check("权限未知时不计入（fail-closed）", v == [])
policy_gate.get_repo_permission = lambda u: "write"  # 以下测决策语义：先给授权
# 注：文件末尾恢复 _orig_perm

# 同一审查人同 SHA：先 APPROVED 后 CHANGES_REQUESTED -> 整体阻断
v, e = policy_gate.evaluate_approvals(
    [_rev("r1", "APPROVED", ts="2026-10-09T01:00:00Z"),
     _rev("r1", "CHANGES_REQUESTED", ts="2026-10-09T02:00:00Z")], "bot", "abc")
check("失效审批被拒绝（APPROVED 后又 CHANGES_REQUESTED）", v == [] and len(e) > 0)
# 他人 CHANGES_REQUESTED 阻断整体（即使另有人批准）
v, e = policy_gate.evaluate_approvals(
    [_rev("r1", "APPROVED"), _rev("r2", "CHANGES_REQUESTED")], "bot", "abc")
check("他人要求修改则整体阻断", v == ["r1"] and len(e) > 0)
# 旧 SHA 的 APPROVED -> 拒绝
v, e = policy_gate.evaluate_approvals([_rev("r1", "APPROVED", sha="old")], "bot", "abc")
check("旧 SHA 审批被拒绝", v == [] and e == [])
# 作者自批 -> 拒绝
v, e = policy_gate.evaluate_approvals([_rev("bot", "APPROVED")], "bot", "abc")
check("作者自批被拒绝", v == [] and e == [])
# 正常：OWNER 在当前 SHA 批准 -> 通过
v, e = policy_gate.evaluate_approvals([_rev("r1", "APPROVED")], "bot", "abc")
check("有效审批通过", v == ["r1"] and e == [])

# ---------- 4. critical 未关闭阻断 ----------
check("critical 全关闭通过",
      policy_gate.validate_critical([{"severity": "high", "status": "CLOSED"}]) == [])
check("critical OPEN 被阻断",
      len(policy_gate.validate_critical(
          [{"severity": "critical", "status": "OPEN", "note": "unfixed"}])) > 0)
check("critical 缺 status 被阻断",
      len(policy_gate.validate_critical([{"severity": "critical"}])) > 0)
check("critical 非数组被阻断",
      len(policy_gate.validate_critical("ok")) > 0)

# ---------- 5. freeze 绑定 ----------
H64 = "a" * 64
base_freeze = {"task_id": "ISSUE-100", "test_path": "tests/acceptance/test_x.py",
               "spec_sha256": H64, "test_sha256": H64}
check("合法 freeze 通过",
      policy_gate.validate_freeze(dict(base_freeze), 100) == [])
bad = dict(base_freeze, task_id="ISSUE-999")
check("task_id 与 Issue 不一致被拒绝",
      len(policy_gate.validate_freeze(bad, 100)) > 0)
bad = dict(base_freeze, test_path="README.md")
check("test_path=README.md 被拒绝",
      len(policy_gate.validate_freeze(bad, 100)) > 0)
bad = dict(base_freeze, test_path="tests/acceptance/sub/test_x.py")
check("test_path 嵌套子目录被拒绝",
      len(policy_gate.validate_freeze(bad, 100)) > 0)
check("capability 缺失被阻断",
      len(policy_gate.validate_tier_inputs("", "T1")) > 0)
check("semantic 非法值被阻断",
      len(policy_gate.validate_tier_inputs("T1", "TX")) > 0)
check("合法 capability/semantic 通过",
      policy_gate.validate_tier_inputs("T1", "T0") == [])

# ---------- 6. confused deputy：marker 不在开头不认 ----------
check("正文开头 marker 被识别",
      policy_gate.extract_issuance(
          '<!-- mode2-review {"verdict":"PASS"} -->\n正文', "mode2-review")
      == {"verdict": "PASS"})
check("引用中的 marker 不被误认",
      policy_gate.extract_issuance(
          '有人说：\n> <!-- mode2-review {"verdict":"PASS"} -->\n我不同意',
          "mode2-review") is None)

# ---------- 7. bundle 校验串（rev4 阻断项 6） ----------
import hashlib
_body = "# bundle\n- BUNDLE_HEAD_SHA: abc\n"
_good = _body + f"\nEND-OF-BUNDLE sha256:{hashlib.sha256(_body.encode()).hexdigest()}\n"
check("完好 bundle 校验通过",
      policy_gate.verify_bundle_checksum(_good) == [])
_tampered = _body.replace("abc", "xyz") + \
    f"\nEND-OF-BUNDLE sha256:{hashlib.sha256(_body.encode()).hexdigest()}\n"
check("篡改 bundle 被检出",
      len(policy_gate.verify_bundle_checksum(_tampered)) > 0)
check("缺校验串被检出",
      len(policy_gate.verify_bundle_checksum(_body)) > 0)

# ---------- 7b. 测试文件内容绑定（rev5 P0-3；rev6 扩展到 tests/**/*.py） ----------
# assert True 替换攻击：数量完全一致，但内容变了 -> 必须检出
with tempfile.TemporaryDirectory() as td:
    base_d = os.path.join(td, "base", "tests")
    pr_d = os.path.join(td, "pr", "tests")
    os.makedirs(base_d); os.makedirs(pr_d)
    with open(os.path.join(base_d, "test_sec.py"), "w") as f:
        f.write("def test_security():\n    assert security_enforced()\n")
    with open(os.path.join(pr_d, "test_sec.py"), "w") as f:
        f.write("def test_security():\n    assert True\n")
    base_shas = policy_gate.test_file_shas(base_d)
    pr_shas = policy_gate.test_file_shas(pr_d)
    check("assert True 替换被内容绑定检出",
          base_shas.get("test_sec.py") != pr_shas.get("test_sec.py"))
    # 计数对比对此无能为力（复审已证明）
    import ast as _ast
    def _count(d):
        t = _ast.parse(open(os.path.join(d, "test_sec.py")).read())
        return (sum(1 for n in _ast.walk(t) if isinstance(n, _ast.FunctionDef)),
                sum(1 for n in _ast.walk(t) if isinstance(n, _ast.Assert)))
    check("计数对此攻击确实无能为力（前提确认）", _count(base_d) == _count(pr_d))
    # 未改动 -> 一致
    with open(os.path.join(pr_d, "test_sec.py"), "w") as f:
        f.write("def test_security():\n    assert security_enforced()\n")
    check("未改动文件哈希一致",
          policy_gate.test_file_shas(pr_d) == base_shas)

# rev6 P0-1：tests/helper.py（非 test_*.py）也被内容绑定覆盖
with tempfile.TemporaryDirectory() as td:
    base_d = os.path.join(td, "base", "tests")
    pr_d = os.path.join(td, "pr", "tests")
    os.makedirs(base_d); os.makedirs(pr_d)
    with open(os.path.join(base_d, "helper.py"), "w") as f:
        f.write("def secure():\n    return verify_real_security()\n")
    with open(os.path.join(base_d, "test_sec.py"), "w") as f:
        f.write("from helper import secure\ndef test_security():\n    assert secure()\n")
    with open(os.path.join(pr_d, "helper.py"), "w") as f:
        f.write("def secure():\n    return True\n")
    with open(os.path.join(pr_d, "test_sec.py"), "w") as f:
        f.write("from helper import secure\ndef test_security():\n    assert secure()\n")
    base_shas = policy_gate.test_file_shas(base_d)
    pr_shas = policy_gate.test_file_shas(pr_d)
    check("helper.py 纳入内容绑定",
          "helper.py" in base_shas and "helper.py" in pr_shas)
    check("helper.py 被替换能检出",
          base_shas.get("helper.py") != pr_shas.get("helper.py"))
    check("test_sec.py 未动仍一致",
          base_shas.get("test_sec.py") == pr_shas.get("test_sec.py"))

# ---------- 7d. CI run 选择（rev6 P0-3） ----------
# 复审场景：#11 成功但 updated_at 更晚，#12 更新但 in_progress -> 必须等待，不用 #11
_runs = [
    {"id": 11, "status": "completed", "conclusion": "success",
     "created_at": "2026-10-09T10:00:00Z", "updated_at": "2026-10-09T12:00:00Z",
     "run_attempt": 1},
    {"id": 12, "status": "in_progress", "conclusion": None,
     "created_at": "2026-10-09T11:00:00Z", "updated_at": "2026-10-09T11:20:00Z",
     "run_attempt": 1},
]
check("有在飞执行时不采用旧成功（返回 None 等待）",
      policy_gate.select_latest_run(_runs) is None)
_runs_ok = [
    {"id": 11, "status": "completed", "conclusion": "success",
     "created_at": "2026-10-09T10:00:00Z", "updated_at": "2026-10-09T12:00:00Z",
     "run_attempt": 1},
    {"id": 12, "status": "completed", "conclusion": "success",
     "created_at": "2026-10-09T11:00:00Z", "updated_at": "2026-10-09T11:20:00Z",
     "run_attempt": 1},
]
sel = policy_gate.select_latest_run(_runs_ok)
check("全完成时取最新（按 created_at）",
      sel is not None and sel["id"] == 12)
check("无候选返回 None", policy_gate.select_latest_run([]) is None)

# ---------- 7e. DIFF_DIGEST（rev6 §四；rev8 并入 canonical_diff；rev9 FileChange） ----------
import canonical_diff as _cd2
def _FC(b, h, old=None, bm="100644", hm="100644"):
    return _cd2.FileChange(b, h, old, bm, hm)
_fc = {"a.py": _FC(b"old", b"new"), "b.py": _FC(b"", b"added")}
d1 = _cd2.compute_diff_digest(_fc)
d2 = _cd2.compute_diff_digest({"b.py": _FC(b"", b"added"), "a.py": _FC(b"old", b"new")})
check("DIFF_DIGEST 与顺序无关", d1 == d2)
_fc3 = {"a.py": _FC(b"old", b"tampered"), "b.py": _FC(b"", b"added")}
check("DIFF_DIGEST 能检出内容变化",
      _cd2.compute_diff_digest(_fc3) != d1)
check("DIFF_DIGEST 绑定旧路径",
      _cd2.compute_diff_digest({"n": _FC(b"b", b"h", "o1")}) !=
      _cd2.compute_diff_digest({"n": _FC(b"b", b"h", "o2")}))
check("DIFF_DIGEST 绑定文件模式",
      _cd2.compute_diff_digest({"n": _FC(b"b", b"h", hm="100755")}) !=
      _cd2.compute_diff_digest({"n": _FC(b"b", b"h", hm="100644")}))
check("DIFF_DIGEST 为 64 位 hex", len(d1) == 64)

# ---------- 7c. bundle 文件清单（rev5 P0-4） ----------
_b = "## 变更文件\n```\na.py\nb.py\n```\n"
check("清单齐全无缺失",
      policy_gate.find_missing_bundle_files(_b, ["a.py", "b.py"]) == [])
check("清单缺失被检出",
      policy_gate.find_missing_bundle_files(_b, ["a.py", "b.py", "c.py"]) == ["c.py"])
check("无清单节被检出",
      policy_gate.find_missing_bundle_files("no section", ["a.py"]) == ["a.py"])

# ---------- 7f. rev7：404/大文件/canonical diff ----------
import urllib.error as _urlerr
_orig_urlopen = policy_gate.urllib.request.urlopen

class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self): return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _fake_urlopen_404(req, timeout=None):
    raise _urlerr.HTTPError(req.full_url, 404, "Not Found", {}, None)

def _fake_urlopen_bignone(req, timeout=None):
    # Contents API 对大文件返回 content="" + encoding="none"，再经 blob 取字节
    if "/contents/" in req.full_url:
        return _FakeResp({"type": "file", "encoding": "none",
                          "sha": "abc123", "content": ""})
    if "/git/blobs/abc123" in req.full_url:
        return _FakeResp({"content": base64.b64encode(b"big-real-bytes").decode()})
    raise AssertionError(req.full_url)

policy_gate.urllib.request.urlopen = _fake_urlopen_404
check("404 → b\"\"（新增/删除文件不报错）",
      policy_gate.github_file_bytes("new.py", "abc") == b"")
policy_gate.urllib.request.urlopen = _fake_urlopen_bignone
check("大文件 encoding=none 经 blob 取真实字节",
      policy_gate.github_file_bytes("big.bin", "abc") == b"big-real-bytes")
policy_gate.urllib.request.urlopen = _orig_urlopen

# canonical_diff：确定性、二进制、空、section 往返
import canonical_diff as cd
_fc1 = {"a.py": _FC(b"old\n", b"new\n")}
_fc2 = {"a.py": _FC(b"old\n", b"tampered\n")}
check("canonical diff 确定性", cd.canonical_diffs_text(_fc1) == cd.canonical_diffs_text(_fc1))
check("canonical diff 检出变化", cd.canonical_diffs_text(_fc1) != cd.canonical_diffs_text(_fc2))
check("二进制文件占位", "Binary file" in cd.canonical_file_diff("x.bin", _FC(b"\xff\x00", b"\xff\x01")))
check("空变更无输出", cd.canonical_diffs_text({"a.py": _FC(b"x\n", b"x\n")}) == "")
_sec = cd.diff_section(_fc1)
check("diff section 往返", cd.parse_diff_section("prefix\n" + _sec + "suffix\n") == cd.capped_diff(cd.canonical_diffs_text(_fc1)))
check("缺 section 返回 None", cd.parse_diff_section("no markers") is None)
# verify_bundle_diff 的 diff 文本验证：构造假 diff 应被检出（mock API）
_orig_gfb = policy_gate.github_file_bytes
_orig_gtm = policy_gate.github_tree_modes
_BASE, _HEAD = "b"*40, "a"*40
policy_gate.BASE_SHA, policy_gate.HEAD_SHA = _BASE, _HEAD
policy_gate.github_file_bytes = lambda p, r: (b"old\n" if r == _BASE else b"new\n")
policy_gate.github_tree_modes = lambda r: {"a.py": "100644"}
_fake_bundle = ("## 变更文件\n```\na.py\n```\n- BUNDLE_HEAD_SHA: " + "a"*40 + "\n"
                "- base: " + "b"*40 + "\nDIFF_DIGEST: " +
                _cd2.compute_diff_digest({"a.py": _FC(b"old\n", b"new\n")}) + "\n"
                + cd.diff_section({"a.py": _FC(b"old\n", b"FAKE\n")}) + "\n"
                "END-OF-BUNDLE sha256:" + "c"*64 + "\n")
# 注意：这里需要 BASE_SHA/HEAD_SHA；直接测 verify_bundle_diff 的 diff 部分
_fo = [{"filename": "a.py", "status": "modified", "previous_filename": None}]
errs = policy_gate.verify_bundle_diff(_fake_bundle, _fo)
check("伪造 diff 文本被检出", any("展示的 diff" in e for e in errs))
_good_bundle = ("## 变更文件\n```\na.py\n```\n- BUNDLE_HEAD_SHA: " + "a"*40 + "\n"
                "- base: " + "b"*40 + "\nDIFF_DIGEST: " +
                _cd2.compute_diff_digest({"a.py": _FC(b"old\n", b"new\n")}) + "\n"
                + cd.diff_section({"a.py": _FC(b"old\n", b"new\n")}) + "\n"
                "END-OF-BUNDLE sha256:" + "c"*64 + "\n")
check("真实 diff 通过", policy_gate.verify_bundle_diff(_good_bundle, _fo) == [])
policy_gate.github_file_bytes = _orig_gfb
policy_gate.github_tree_modes = _orig_gtm

# ---------- 7g. rev8：重命名 / 换行 / reverify fail-closed ----------
# 重命名分级：旧路径 T2 → 新路径 T0，最终必须仍是 T2
_ren = [{"filename": "docs/agent_instructions.txt", "status": "renamed",
         "previous_filename": "AGENTS.md"}]
_tps = policy_gate.tier_paths(_ren)
check("重命名双路径参与分级",
      "AGENTS.md" in _tps and "docs/agent_instructions.txt" in _tps)
import tiering as _tiering
_tiers = _tiering.load_tiers(".github/risk-tiers.yml")
check("重命名 T2→T0 仍判 T2",
      _tiering.compute_tier(_tps, _tiers) == "T2")
# 非重命名不受影响
_norm = [{"filename": "docs/a.md", "status": "modified", "previous_filename": None}]
check("普通文件单路径", policy_gate.tier_paths(_norm) == ["docs/a.md"])

# 重命名 diff 展示旧路径
_rd = cd.canonical_file_diff("new.txt", _FC(b"x\n", b"x\ny\n", old="old.txt"))
check("重命名 diff 含 from/to",
      "rename from old.txt" in _rd and "rename to new.txt" in _rd
      and "a/old.txt" in _rd and "b/new.txt" in _rd)

# 换行变化显式标注，不再消失
_nl1 = cd.canonical_file_diff("f.txt", _FC(b"a\n", b"a"))
check("末尾换行删除被标注", "文件末尾换行" in _nl1 and _nl1.strip() != "")
_nl2 = cd.canonical_file_diff("f.txt", _FC(b"a\r\n", b"a\n"))
check("CRLF→LF 逐行可见", "-a␍" in _nl2 and "+a\n" in _nl2)
_nl3 = cd.canonical_file_diff("f.txt", _FC(b"bad", b"good"))
check("无换行行不拼接", "-bad\n+good\n" in _nl3)

# A1：Phase C 组件（ghutil/reverify）已 shelve，相关测试移至 C 阶段
# rev10：build-review-bundle.py 真实入口集成测试（临时 Git 仓库）
import subprocess as _sp
import tempfile, shutil
_tmp = tempfile.mkdtemp()
try:
    _sh = lambda *a, **k: _sp.run(a, cwd=_tmp, capture_output=True, **k)
    _sh("git", "init", "-q"); _sh("git", "config", "user.email", "t@t")
    _sh("git", "config", "user.name", "t")
    # base：先提交脚本（真实场景 base=main 含骨架），再提交业务文件
    _dst = os.path.join(_tmp, ".github", "scripts")
    os.makedirs(_dst)
    for _f in ["canonical_diff.py", "build-review-bundle.py"]:
        shutil.copy(os.path.join(".github", "scripts", _f),
                    os.path.join(_dst, _f))
    open(os.path.join(_tmp, "job.sh"), "w").write("#!/bin/sh\necho hi\n")
    open(os.path.join(_tmp, "a.txt"), "w").write("hello\n")
    _sh("git", "add", "."); _sh("git", "commit", "-qm", "base")
    _base = _sh("git", "rev-parse", "HEAD").stdout.decode().strip()
    # head：chmod + 改内容 + 新增文件
    os.chmod(os.path.join(_tmp, "job.sh"), 0o755)
    open(os.path.join(_tmp, "a.txt"), "w").write("hello world\n")
    open(os.path.join(_tmp, "new.txt"), "w").write("new\n")
    _sh("git", "add", "."); _sh("git", "commit", "-qm", "head")
    _head = _sh("git", "rev-parse", "HEAD").stdout.decode().strip()
    _env = dict(os.environ, BASE_SHA=_base, HEAD_SHA=_head)
    _r = _sp.run([sys.executable, ".github/scripts/build-review-bundle.py"],
                 cwd=_tmp, env=_env, capture_output=True, text=True)
    _bpath = os.path.join(_tmp, "review-bundle.md")
    _out = open(_bpath).read() if os.path.exists(_bpath) else ""
    check("bundle 生成器真实入口成功",
          _r.returncode == 0 and "BUNDLE FAIL" not in _r.stderr
          and os.path.exists(_bpath))
    check("bundle 含 DIFF_DIGEST", "DIFF_DIGEST:" in _out)
    check("bundle 含 chmod 标注", "文件模式：100644 → 100755" in _out)
    check("bundle 含规范 diff 节", "<!-- MODE2-DIFF-START -->" in _out)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# rev10：混合换行位置变化可见（␍）
_mix = cd.canonical_file_diff("f.txt", _FC(b"a\r\nb\n", b"a\nb\r\n"))
check("混合换行位置变化可见",
      "-a␍" in _mix and "+b␍" in _mix and _mix.strip() != "")
# rev10：字面量反斜杠+r 与真实回车不混淆
_amb = cd.canonical_file_diff("f.txt", _FC(b"a\r\n", b"a\\r\n"))
check("字面量\\r 与真实回车可区分",
      "-a␍" in _amb and "+a\\r" in _amb and _amb.strip() != "")
# rev9：chmod 变化标注且参与 digest
_chm = cd.canonical_file_diff("job.sh", _FC(b"#!/bin/sh\n", b"#!/bin/sh\n",
                                           bm="100644", hm="100755"))
check("chmod 变化被标注", "文件模式：100644 → 100755" in _chm)

# ---------- 8. 完整性计数比对（二级信号；主防线是上面的内容绑定） ----------

_base = {"test_files": 10, "test_funcs": 27, "skips": 0, "xfails": 0, "asserts": 40}
check("指标持平通过",
      policy_gate.compare_integrity_metrics(dict(_base), dict(_base)) == [])
_pr = dict(_base, test_funcs=20)
check("测试函数减少被阻断",
      len(policy_gate.compare_integrity_metrics(_pr, _base)) > 0)
_pr = dict(_base, test_funcs=9999, asserts=99999)
check("指标虚增不阻断（双边皆可信计算，虚增无意义）",
      policy_gate.compare_integrity_metrics(_pr, _base) == [])
_pr = dict(_base, skips=2)
check("新增 skip 被阻断",
      len(policy_gate.compare_integrity_metrics(_pr, _base)) > 0)
_pr = dict(_base, asserts=30)
check("断言减少被阻断",
      len(policy_gate.compare_integrity_metrics(_pr, _base)) > 0)

# ---------- 9. 扫描器 CLI 集成（rev4 阻断项 1） ----------
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, "tests"))
    with open(os.path.join(td, "tests", "test_demo.py"), "w") as f:
        f.write("def test_a():\n    assert 1 == 1\n\n"
                "import pytest\n@pytest.mark.skip\ndef test_b():\n    pass\n")
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "integrity-scan.py"),
         "--json", "--root", os.path.join(td, "tests")],
        capture_output=True, text=True)
    try:
        m = json.loads(proc.stdout)
        cli_ok = (proc.returncode == 0 and m["test_funcs"] == 2
                  and m["skips"] == 1 and m["asserts"] == 1
                  and m["test_files"] == 1)
    except Exception:
        cli_ok = False
    check("扫描器 --json 接口与 schema 可用", cli_ok)

# ---------- 10. A1 审查修复：P3 symlink / P4 malformed rename ----------

# P4：重命名缺 previous_filename → fail-closed（SystemExit）
try:
    policy_gate.tier_paths([{"filename": "docs/x.txt",
                             "status": "renamed",
                             "previous_filename": None}])
    check("重命名缺 previous_filename 被阻断", False)
except SystemExit:
    check("重命名缺 previous_filename 被阻断", True)

# P4：previous_filename 为空字符串同样阻断
try:
    policy_gate.tier_paths([{"filename": "docs/x.txt",
                             "status": "renamed",
                             "previous_filename": ""}])
    check("重命名 previous_filename 为空被阻断", False)
except SystemExit:
    check("重命名 previous_filename 为空被阻断", True)

# P4：完整重命名元信息仍正常（AGENTS.md -> docs/x.txt 判 T2）
_p4_paths = policy_gate.tier_paths(
    [{"filename": "docs/x.txt", "status": "renamed",
      "previous_filename": "AGENTS.md"}])
check("完整重命名双路径参评",
      "docs/x.txt" in _p4_paths and "AGENTS.md" in _p4_paths)

# P3：git_blob_identity 在临时仓库中捕获 symlink 替换
with tempfile.TemporaryDirectory() as _td:
    _sp.run(["git", "init", "-q"], cwd=_td, check=True)
    _sp.run(["git", "config", "user.email", "t@t"], cwd=_td, check=True)
    _sp.run(["git", "config", "user.name", "t"], cwd=_td, check=True)
    os.makedirs(os.path.join(_td, "tests"))
    with open(os.path.join(_td, "tests", "test_guard.py"), "w") as f:
        f.write("def test_a():\n    assert True\n")
    _sp.run(["git", "add", "-A"], cwd=_td, check=True)
    _sp.run(["git", "commit", "-qm", "init"], cwd=_td, check=True)
    _base_id = policy_gate.git_blob_identity(_td, "tests/test_guard.py")
    check("base 普通文件身份为 100644", _base_id[0] == "100644")
    # 攻击：提交一个把 test_guard.py 换成 symlink 的 commit
    os.remove(os.path.join(_td, "tests", "test_guard.py"))
    with open(os.path.join(_td, "tests", "test_shadow.py"), "w") as f:
        f.write("def test_a():\n    assert True\n")
    os.symlink("test_shadow.py",
               os.path.join(_td, "tests", "test_guard.py"))
    _sp.run(["git", "add", "-A"], cwd=_td, check=True)
    _sp.run(["git", "commit", "-qm", "symlink-attack"], cwd=_td, check=True)
    _atk_id = policy_gate.git_blob_identity(_td, "tests/test_guard.py")
    check("symlink 替换后 mode 变为 120000", _atk_id[0] == "120000")
    check("symlink 替换被识别为类型变化",
          _atk_id[0] != _base_id[0])

# P3：旧 test_file_shas 跟随 symlink（记录已知局限，不作断言依据）
with tempfile.TemporaryDirectory() as _td2:
    os.makedirs(os.path.join(_td2, "tests"))
    with open(os.path.join(_td2, "tests", "test_shadow.py"), "w") as f:
        f.write("x = 1\n")
    os.symlink("test_shadow.py", os.path.join(_td2, "tests", "test_link.py"))
    _shas = policy_gate.test_file_shas(os.path.join(_td2, "tests"))
    check("test_file_shas 跟随 symlink（已知局限，PR 侧已改用 git 身份）",
          _shas.get("test_link.py") == _shas.get("test_shadow.py"))

# P3 回归（v1.1 复审）：完整 worktree 生命周期
# 复审发现 v1.1 把 git_blob_identity 写在 finally 删除 worktree 之后，
# 导致新增测试被误报"删除"。本测试模拟修复后的完整模式：
# try 内挂载→采集身份→finally 删除→try 外比对。
def _p3_lifecycle(pr_setup):
    """返回 check_test_identities 的结果（True=通过，False=被阻断）。"""
    with tempfile.TemporaryDirectory() as _td:
        _sp.run(["git", "init", "-q"], cwd=_td, check=True)
        _sp.run(["git", "config", "user.email", "t@t"], cwd=_td, check=True)
        _sp.run(["git", "config", "user.name", "t"], cwd=_td, check=True)
        os.makedirs(os.path.join(_td, "tests"))
        with open(os.path.join(_td, "tests", "test_guard.py"), "w") as f:
            f.write("def test_a():\n    assert True\n")
        _sp.run(["git", "add", "-A"], cwd=_td, check=True)
        _sp.run(["git", "commit", "-qm", "base"], cwd=_td, check=True)
        _base_sha = _sp.run(
            ["git", "rev-parse", "HEAD"], cwd=_td,
            capture_output=True, text=True, check=True).stdout.strip()
        pr_setup(_td)  # 构造 PR head 并提交
        _wt = os.path.join(_td, "wt")
        try:
            _sp.run(["git", "worktree", "add", "--detach", _wt, "HEAD"],
                    cwd=_td, check=True, capture_output=True)
            _pr_ids = {"test_guard.py":
                       policy_gate.git_blob_identity(_wt, "tests/test_guard.py")}
        finally:
            _sp.run(["git", "worktree", "remove", "--force", _wt],
                    cwd=_td, check=True, capture_output=True)
        _base_ids = {"test_guard.py":
                     policy_gate.git_blob_identity(_td, "tests/test_guard.py",
                                                   ref=_base_sha)}
        try:
            policy_gate.check_test_identities(_base_ids, _pr_ids)
            return True
        except SystemExit:
            return False

def _pr_add_only(td):
    with open(os.path.join(td, "tests", "test_new.py"), "w") as f:
        f.write("def test_b():\n    assert True\n")
    _sp.run(["git", "add", "-A"], cwd=td, check=True)
    _sp.run(["git", "commit", "-qm", "add-only"], cwd=td, check=True)

def _pr_symlink(td):
    os.remove(os.path.join(td, "tests", "test_guard.py"))
    with open(os.path.join(td, "tests", "test_shadow.py"), "w") as f:
        f.write("def test_a():\n    assert True\n")
    os.symlink("test_shadow.py", os.path.join(td, "tests", "test_guard.py"))
    _sp.run(["git", "add", "-A"], cwd=td, check=True)
    _sp.run(["git", "commit", "-qm", "symlink"], cwd=td, check=True)

def _pr_modify(td):
    with open(os.path.join(td, "tests", "test_guard.py"), "w") as f:
        f.write("def test_a():\n    assert True  # modified\n")
    _sp.run(["git", "add", "-A"], cwd=td, check=True)
    _sp.run(["git", "commit", "-qm", "modify"], cwd=td, check=True)

def _pr_delete(td):
    os.remove(os.path.join(td, "tests", "test_guard.py"))
    _sp.run(["git", "add", "-A"], cwd=td, check=True)
    _sp.run(["git", "commit", "-qm", "delete"], cwd=td, check=True)

check("P3 生命周期：仅新增测试通过", _p3_lifecycle(_pr_add_only) is True)
check("P3 生命周期：symlink 替换被阻断", _p3_lifecycle(_pr_symlink) is False)
check("P3 生命周期：修改测试被阻断", _p3_lifecycle(_pr_modify) is False)
check("P3 生命周期：删除测试被阻断", _p3_lifecycle(_pr_delete) is False)

print()
print(f"通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
policy_gate.get_repo_permission = _orig_perm  # 恢复
if FAIL:
    print("失败项：")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL NEGATIVE TESTS PASS")
