"""LACD v1.0 迁移验收测试（Issue #140）。

断言新门禁（.github/scripts/policy-gate.py，LACD v1.0）的核心不变量：
1. 旧签发协议解析已移除：不再解析 mode2-freeze / mode2-review 评论。
2. PR 身份绑定：Bot 作者、HEAD/BASE SHA、非 fork、非草稿。
3. status:hold 标签识别。

本文件被旧门禁的 freeze 协议哈希绑定（test_sha256），冻结后不得修改。
"""
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SCRIPTS = os.path.join(REPO_ROOT, ".github", "scripts")
sys.path.insert(0, SCRIPTS)

_spec = importlib.util.spec_from_file_location(
    "lacd_policy_gate", os.path.join(SCRIPTS, "policy-gate.py"))
pg = importlib.util.module_from_spec(_spec)
sys.modules["lacd_policy_gate"] = pg
_spec.loader.exec_module(pg)

GATE_SRC = open(os.path.join(SCRIPTS, "policy-gate.py"),
                encoding="utf-8").read()

BOT = "cba-kb-muse-mode2-bot[bot]"
S40 = "a" * 40
B40 = "b" * 40


def _cfg():
    return pg.IdentityConfig(repo="SkyCjq/cba-kb-engine", pr_number=140,
                             head_sha=S40, base_sha=B40, bot_author=BOT)


def _pr(**kw):
    d = {"number": 140, "state": "open", "draft": False,
         "base": {"ref": "main", "sha": B40,
                  "repo": {"full_name": "SkyCjq/cba-kb-engine"}},
         "head": {"sha": S40,
                  "repo": {"full_name": "SkyCjq/cba-kb-engine"}},
         "user": {"login": BOT}}
    d.update(kw)
    return d


def _rejects(mut):
    try:
        p = _pr()
        mut(p)
        pg.validate_pr_identity(p, _cfg())
        return False
    except SystemExit:
        return True


def test_no_legacy_freeze_parsing():
    # 无解析代码（docstring 允许提及"已退役"）
    assert "def check_freeze" not in GATE_SRC
    assert "extract_issuance" not in GATE_SRC


def test_no_legacy_review_parsing():
    assert "def check_review" not in GATE_SRC
    assert "validate_freeze" not in GATE_SRC


def test_no_python_approval_recheck():
    assert "evaluate_approvals" not in GATE_SRC


def test_identity_accepts_bot_pr():
    got = pg.validate_pr_identity(_pr(), _cfg())
    assert got["state"] == "IDENTITY_PASS"
    assert got["author"] == BOT


def test_identity_rejects_non_bot_author():
    assert _rejects(lambda p: p["user"].update(login="SkyCjq"))


def test_identity_rejects_fork():
    assert _rejects(
        lambda p: p["head"]["repo"].update(full_name="evil/fork"))


def test_identity_rejects_head_drift():
    assert _rejects(lambda p: p["head"].update(sha="c" * 40))


def test_identity_rejects_draft():
    assert _rejects(lambda p: p.update(draft=True))


def test_hold_label_detected():
    assert pg.has_hold_label([{"name": "status:hold"}]) is True
    assert pg.has_hold_label([{"name": "bug"}]) is False
    assert pg.has_hold_label([]) is False


def test_closes_issue_parsed():
    assert pg.parse_closes_issue("Body\nCloses #140") == 140
    assert pg.parse_closes_issue("nothing") is None
