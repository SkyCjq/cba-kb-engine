"""B1-2 验收测试：queue-runbook 实际执行耗时记录。

机械实现冻结规格 AC-1..AC-5（Q1 冻结 2026-10-09＋2026-10-10 修订：新增本文件）。
规格原文：docs 归档 ~/workspace/mode2-b1-2/B1-2_Q1_FREEZE_SPEC_2026-10-09.md
本文件仅断言规格原文，不含规格外断言。
注：FROZEN_CHAPTER 取 Q2 已验证的实现字节（bab18228fd46）；其中"原因说明"为标题独占一行、
段落另起一行——用户粘贴的规格文本中该处显示为单行，系转录换行 artifact，Q2 已按实现版判定逐字一致。
"""
from pathlib import Path

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "mode2" / "queue-runbook.md"

FROZEN_CHAPTER = """## 实际执行记录

以下为 2026-10-09 的 3 次人工合并实测耗时记录。

| 日期 | PR | 耗时 | 是否需要 update branch | 备注 |
|---|---|---|---|---|
| 2026-10-09 | #110 | 1 分钟 | 否 | 直接合并，无需 update branch 等待 |
| 2026-10-09 | #111 | 2-3 分钟 | 是 | 包含 update branch 等待时间 |
| 2026-10-09 | #112 | 2-3 分钟 | 是 | 包含 update branch 等待时间 |

**update branch 原因说明：**
分支保护规则启用了 `Require branches to be up to date`，要求 PR 分支与目标分支保持同步，才能满足合并条件。PR #110 无需更新分支，可以直接合并；PR #111 和 #112 在合并前需要执行 update branch，并等待分支更新及相关检查完成，因此实际耗时为 2-3 分钟，而非直接合并所需的 1 分钟。
"""

EXPECTED = [
    ("2026-10-09", "#110", "1 分钟", "否"),
    ("2026-10-09", "#111", "2-3 分钟", "是"),
    ("2026-10-09", "#112", "2-3 分钟", "是"),
]


def _text():
    return RUNBOOK.read_text(encoding="utf-8")


def _rows(text):
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("| 2026-10-09 | #"):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def test_ac1_chapter_position_and_title():
    text = _text()
    assert text.count("## 实际执行记录") == 1, "新章节标题必须恰好出现一次"
    assert text.index("## Web 调用健康") < text.index("## 实际执行记录")


def test_ac2_records_complete_and_accurate():
    rows = _rows(_text())
    assert len(rows) == 3, f"必须恰好 3 条数据行，实际 {len(rows)}"
    for row, (date, pr, dur, upd) in zip(rows, EXPECTED):
        assert (row[0], row[1], row[2], row[3]) == (date, pr, dur, upd), \
            f"记录不符: {row}"


def test_ac3_columns_and_remarks():
    text = _text()
    assert "| 日期 | PR | 耗时 | 是否需要 update branch | 备注 |" in text
    for row in _rows(text):
        assert len(row) == 5, f"必须 5 列: {row}"
        assert row[4] != "", "备注不得为空"


def test_ac4_cause_explanation():
    tail = _text().split("## 实际执行记录", 1)[1]
    assert "Require branches to be up to date" in tail
    assert "#111" in tail and "#112" in tail


def test_ac5_append_only_and_verbatim():
    text = _text()
    head, chapter = text.split("## 实际执行记录", 1)
    assert head.rstrip().endswith("- 连续失败 → hold，@Human"), "原有内容不得改动"
    assert ("## 实际执行记录" + chapter).strip() == FROZEN_CHAPTER.strip(), \
        "新增章节必须与冻结文本逐字一致"
