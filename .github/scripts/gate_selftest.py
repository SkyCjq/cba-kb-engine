#!/usr/bin/env python3
"""Mode 2.1 门禁负面测试（rev5）。

对应独立安全复审 rev2/rev3/rev4 报告的 P0 漏洞，每个都是"旧行为必须失败"式的
差异性证伪：用复审报告中的攻击输入跑真实代码，断言被阻断。
rev5 新增：决策语义（CHANGES_REQUESTED 后 COMMENTED）、权限 API（mock）、
测试文件完整性基线、bundle 文件清单。

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

import tiering

# policy-gate.py 带连字符，不能直接 import：按路径加载
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "policy_gate", os.path.join(HERE, "policy-gate.py"))
policy_gate = importlib.util.module_from_spec(_spec)
# dataclass 在合成模块名下需要 sys.modules 预先可见（否则 dataclasses 内部
# sys.modules.get(cls.__module__) 返回 None）。runner 中以 __main__ 运行不受影响。
sys.modules["policy_gate"] = policy_gate
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
# 退役断言（证伪）：risk-tiers.yml 必须已从仓库删除；在旧树上本断言 FAIL。
check("risk-tiers.yml 已退役（仓库中不存在）",
      not os.path.exists(os.path.join(HERE, "..", "risk-tiers.yml")))
tiers = tiering.get_tiers()  # 内联冻结分级表，不读文件
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
    (["lacd.yaml"], "T2"),
    ([".lacd/lock.json"], "T2"),
    ([".lacd/project-rules.yaml"], "T2"),
    ([], "T2"),
]
for files, exp in WASH_CASES:
    got = tiering.compute_tier(files, tiers)
    check(f"tier {files} == {exp}", got == exp)


# ---------- 3. LACD 身份绑定（validate_pr_identity） ----------
# 差异性证伪：事件伪造 / fork 混入 / HEAD 漂移 / 非 Bot 作者 / 草稿 / 非 main
# 必须全部被阻断；合法 Bot PR 必须通过。
_S40 = "a" * 40
_B40 = "b" * 40
_CFG = policy_gate.IdentityConfig(
    repo="SkyCjq/cba-kb-engine", pr_number=141, head_sha=_S40,
    base_sha=_B40, bot_author="cba-kb-muse-mode2-bot[bot]")

def _base_pr():
    return {
        "number": 141, "state": "open", "draft": False,
        "base": {"ref": "main", "sha": _B40,
                 "repo": {"full_name": "SkyCjq/cba-kb-engine"}},
        "head": {"sha": _S40,
                 "repo": {"full_name": "SkyCjq/cba-kb-engine"}},
        "user": {"login": "cba-kb-muse-mode2-bot[bot]"},
    }

def _identity_rejects(mutator):
    try:
        p = _base_pr()
        mutator(p)
        policy_gate.validate_pr_identity(p, _CFG)
        return False
    except SystemExit:
        return True

_id = policy_gate.validate_pr_identity(_base_pr(), _CFG)
check("合法 Bot PR 通过身份绑定",
      _id["state"] == "IDENTITY_PASS"
      and _id["author"] == "cba-kb-muse-mode2-bot[bot]")
check("PR 编号不一致被拒绝",
      _identity_rejects(lambda p: p.update(number=999)))
check("非 open 状态被拒绝",
      _identity_rejects(lambda p: p.update(state="closed")))
check("草稿 PR 被拒绝",
      _identity_rejects(lambda p: p.update(draft=True)))
check("fork PR（head 仓库不同）被拒绝",
      _identity_rejects(
          lambda p: p["head"]["repo"].update(full_name="evil/fork")))
check("base 仓库不一致被拒绝",
      _identity_rejects(
          lambda p: p["base"]["repo"].update(full_name="evil/fork")))
check("HEAD SHA 不一致被拒绝",
      _identity_rejects(lambda p: p["head"].update(sha="c" * 40)))
check("BASE SHA 不一致被拒绝",
      _identity_rejects(lambda p: p["base"].update(sha="d" * 40)))
check("非 Bot 作者被拒绝",
      _identity_rejects(lambda p: p["user"].update(login="SkyCjq")))
check("目标分支非 main 被拒绝",
      _identity_rejects(lambda p: p["base"].update(ref="develop")))
_bad_cfg = policy_gate.IdentityConfig(
    repo="SkyCjq/cba-kb-engine", pr_number=141, head_sha="not-a-sha",
    base_sha=_B40, bot_author="cba-kb-muse-mode2-bot[bot]")
try:
    policy_gate.validate_pr_identity(_base_pr(), _bad_cfg)
    check("非法 SHA 格式被拒绝", False)
except SystemExit:
    check("非法 SHA 格式被拒绝", True)
check("is_sha40 拒绝非 hex", not policy_gate.is_sha40("z" * 40))
check("is_sha40 拒绝短串", not policy_gate.is_sha40("abc"))
check("is_sha40 接受合法值", policy_gate.is_sha40(_S40))

# LACD 说明：T2 不再由 policy 硬阻断（硬阻断会使门禁自身维护只能走 bypass）。
# 路径分级仍报告，T2 准入授权移交 CODEOWNERS + 原生分支保护（Human 确认配置）。
check("T2 路径仍被正确分级（报告用）",
      tiering.compute_tier([".github/workflows/x.yml"], tiers) == "T2")

# ---------- 4. hold 标签与 Closes 解析 ----------
check("Closes #n 被解析",
      policy_gate.parse_closes_issue("xxx\nCloses #140") == 140)
check("fixes 大小写不敏感",
      policy_gate.parse_closes_issue("Fixes #7") == 7)
check("无关联返回 None",
      policy_gate.parse_closes_issue("no link here") is None)
check("空 body 返回 None",
      policy_gate.parse_closes_issue(None) is None)
# Q2-141-M02：多个不同 Closes 编号 → fail-closed（与 coordinator 一致）
try:
    policy_gate.parse_closes_issue("Closes #7\nCloses #8")
    check("多 Closes 被拒绝", False)
except SystemExit:
    check("多 Closes 被拒绝", True)
check("重复同一编号不误杀",
      policy_gate.parse_closes_issue("Closes #7\ncloses #7") == 7)
check("hold 标签被识别",
      policy_gate.has_hold_label([{"name": "status:hold"}]) is True)
check("无标签不误报", policy_gate.has_hold_label([]) is False)
check("None 标签不误报", policy_gate.has_hold_label(None) is False)
check("其他标签不误报",
      policy_gate.has_hold_label([{"name": "bug"}]) is False)

# ---------- 7b. 测试文件完整性基线（rev5 P0-3；rev6 扩展到 tests/**/*.py） ----------
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
    check("assert True 替换被完整性基线检出",
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

# rev6 P0-1：tests/helper.py（非 test_*.py）也被完整性基线覆盖
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
    check("helper.py 纳入完整性基线",
          "helper.py" in base_shas and "helper.py" in pr_shas)
    check("helper.py 被替换能检出",
          base_shas.get("helper.py") != pr_shas.get("helper.py"))
    check("test_sec.py 未动仍一致",
          base_shas.get("test_sec.py") == pr_shas.get("test_sec.py"))

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
# ---------- 7g. rev8：重命名 / 换行 / reverify fail-closed ----------
# 重命名分级：旧路径 T2 → 新路径 T0，最终必须仍是 T2
_ren = [{"filename": "docs/agent_instructions.txt", "status": "renamed",
         "previous_filename": "AGENTS.md"}]
_tps = policy_gate.tier_paths(_ren)
check("重命名双路径参与分级",
      "AGENTS.md" in _tps and "docs/agent_instructions.txt" in _tps)
import tiering as _tiering
_tiers = _tiering.get_tiers()  # 内联冻结分级表（原 risk-tiers.yml rev3 已退役）
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

# ---------- 8. 完整性计数比对（二级信号；主防线是上面的完整性基线） ----------

_base = {"test_files": 10, "test_funcs": 27, "skips": 0, "xfails": 0, "asserts": 40}
check("指标持平通过",
      policy_gate.compare_integrity_metrics(dict(_base), dict(_base)) == [])
_pr = dict(_base, test_funcs=20)
check("测试函数减少降为审查提示",
      policy_gate.compare_integrity_metrics(_pr, _base) == [])
_pr = dict(_base, test_funcs=9999, asserts=99999)
check("指标虚增不阻断（双边皆可信计算，虚增无意义）",
      policy_gate.compare_integrity_metrics(_pr, _base) == [])
_pr = dict(_base, skips=2)
check("新增 skip 降为审查提示",
      policy_gate.compare_integrity_metrics(_pr, _base) == [])
_pr = dict(_base, asserts=30)
check("断言减少降为审查提示",
      policy_gate.compare_integrity_metrics(_pr, _base) == [])

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
check("P3 生命周期：修改测试通过（留痕）", _p3_lifecycle(_pr_modify) is True)
check("P3 生命周期：删除测试被阻断", _p3_lifecycle(_pr_delete) is False)
# Q2-141-H02：check_test_identities 必须返回修改清单（供显著报告），
# 而不是静默通过——"允许但须审查"
check("修改清单被返回（非静默通过）",
      policy_gate.check_test_identities(
          {"a.py": ("100644", "b" * 40)},
          {"a.py": ("100644", "c" * 40)}) == ["a.py"])
check("无修改返回空清单",
      policy_gate.check_test_identities(
          {"a.py": ("100644", "b" * 40)},
          {"a.py": ("100644", "b" * 40)}) == [])

print()
print(f"通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL NEGATIVE TESTS PASS")
