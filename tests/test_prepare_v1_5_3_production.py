from scripts.prepare_v1_5_3_production import update_version_doc


def test_update_version_doc_preserves_body_and_marks_release():
    header = """# CBA-KB v1.5.3 — Registration Event Domain Closure

> summary

---


## 0. 文档元数据

- **文档状态**：IMPLEMENTED_CANDIDATE / PR_OPEN / NOT_PRODUCTION
- **方案制定日期**：2026-09-11
- **最近修订日期**：2026-09-11（implementation closeout / PR #2 / candidate validation）
- **实施日期**：2026-09-11（开发实现完成；PR 尚未合并；production 尚未发布）
- **前置发布版本**：v1.5.2-2
- **版本类型**：注册事件域收口 / semantics hardening / regression protection
- **本次写入范围**：需求与实现状态记录；代码实现已完成并进入 PR；Drive production 与 release_status 尚未切换
- **目标 Drive 路径**：`版本迭代/CBA-KB_v1.5.3.md`

# PART L — 当前执行状态

**IMPLEMENTED_CANDIDATE / PR_OPEN / NOT_PRODUCTION**

```text
规范版本：v1.5.3
开发实现：IMPLEMENTED_CANDIDATE / PR_OPEN
代码镜像：尚未同步 merge commit
生产版本：v1.5.2-2
```

# 正文保持
"""
    result = update_version_doc(header, 'a' * 40)
    assert 'PRODUCTION_RELEASE_COMPLETE / v1.5.3-1' in result
    assert 'IMPLEMENTED_CANDIDATE / PR_OPEN / NOT_PRODUCTION' not in result
    assert '> **生产发布状态（2026-09-11）**：`v1.5.3-1 / COMPLETE`' in result
    assert '**PRODUCTION_RELEASE_COMPLETE / v1.5.3-1**' in result
    assert '代码镜像：已同步发布 commit' in result
    assert '生产版本：v1.5.3-1' in result
    assert '# 正文保持' in result
    assert '`' + 'a' * 40 + '`' in result
