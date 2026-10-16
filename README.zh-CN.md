# CBA-KB｜让每一条篮球结论都有出处

[English](README.md) · [快速开始](#5-分钟快速开始) · [运维手册](docs/operations/OPERATIONS.zh-CN.md)

**新闻说了什么，不等于事实已经得到证实。** CBA-KB 是面向中国男子篮球职业联赛（CBA）的开源知识库引擎：帮助研究者和开发者整理比赛报道、球员资料、联赛公告与文档，保留来源、身份、版权和核验状态，让人或 AI 查资料时可以追问：**“这条结论依据什么？”**

> **当前正式发布版本：v2.0.2。** `main` 同时包含正在开发的 **v2.0.4-2** 部分功能。完整 Google Drive 归档、真实双后端端到端验收和生产上线尚未完成；本 README 不代表生产发布公告。

## 为什么需要 CBA-KB？

想象你在研究“某球员是否转会”。一篇新闻可能说“据悉即将加盟”，另一份名单却仍显示原球队。普通搜索容易把两句话混在一起；直接让 AI 总结，更可能把传闻写成确定事实。

CBA-KB 希望把它们分清楚：

- **Source（来源）**：消息来自哪篇文章、哪份公告、哪个文件？
- **Document（文档）**：原件是什么，版本有没有变化，能否回到出处？
- **Statement（陈述）**：来源原文到底说了什么？
- **Claim（主张）**：哪些判断仍然只是待验证的说法？
- **Fact（事实）**：哪些结论已有足够的权威证据和核验记录？

**核心原则：陈述 ≠ 事实；证据 ≠ 事实；未知 ≠ 0。** 当身份、权限或证据不清楚时，系统应保留未决状态或交给人工复核，而不是猜测。

## 它能做什么？

| 场景 | CBA-KB 的作用 | 当前边界 |
| --- | --- | --- |
| 整理研究资料 | 建立来源、文档、球员身份和可追溯记录 | 公开 Engine 已具备相关数据模型与 CLI |
| 检查新闻和观点 | 分离 Statement、Claim、Fact，保留待核验事项 | 不会自动把 AI 摘要认定为事实 |
| 本地 Inbox 试用 | 发现 `.txt`、登记 Source/Document、去重、状态查询和关键词检索 | v2.0.4 开发分支功能；需同名权利声明文件 |
| Google Drive 资料发现 | 只读发现、增量轮询、记录修订、搜索索引与审批记录 | 开发能力，不等于生产 Drive 已启用 |
| 安全整理和归档 | 以明确计划、审批、操作后回读为目标 | 真实 Drive 移动仍处整合审查及 E2E 阶段，不能当成已发布功能 |
| 给人和 AI 使用 | 生成带身份、证据和权限语义的研究数据 | 使用者仍需核验证据并尊重版权 |

**CBA-KB 不是聊天机器人，也不是自动抓取全网、自动判定所有篮球事实的服务。** 它是可复用的处理引擎，需要你选择合法数据来源、运行环境和审核流程。

## 先弄清楚版本状态

- **v2.0.2：正式发布基线。** 包括公开 Engine、数据契约、合成样例、研究与验证工具。
- **v2.0.4-2：正在开发。** `main` 中已有本地 Inbox、Drive 只读发现/轮询、权限控制、索引、部分计划审批和 WAL 恢复等代码；这些模块的存在**不代表完整业务链已验收**。
- **整合 PR [#176](https://github.com/SkyOneBranch/cba-kb-engine/pull/176)：仍在审查。** Guarded Drive move 和统一工作流等改动，必须经过最终代码版本的独立 Q2 与 Human 合并，才算进入 `main`。
- **还没有完成的事：** 真实隔离 Google Drive E2E、整版需求验收、Production GO、指定主机部署、生产 Drive Inbox 激活和用户实际验收。

**你可以在本地用合成数据体验开发中的功能；请不要把它理解为已经可以安全地批量移动自己的生产 Drive 文件。**

## 5 分钟快速开始

准备：Python **3.11+**、Git；以下命令适用于 Linux/macOS 或兼容的终端。无需任何真实 CBA 私有数据，也无需 Google Drive 凭据。

```bash
# 1. 获取公开仓库（main 是开发分支）
git clone https://github.com/SkyOneBranch/cba-kb-engine.git
cd cba-kb-engine

# 2. 安装到虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .

# 3. 环境检查和离线测试
make doctor
make test

# 4. 查看可以使用的命令
cba-kb --help
```

如果你想复现**正式 v2.0.2** 的源码，请在安装前明确切换到 `v2.0.2` Tag；上面的默认克隆得到的是开发分支，不是 v2.0.4-2 已发布版本。

### 体验 1：用虚构数据走一次研究流程

```bash
# 避免把私有环境变量混入公开合成测试
unset CBA_KB_INSTANCE_ROOT CBA_KB_SOURCE_POLICY_JSON CBA_STATS_AES_KEY GOOGLE_APPLICATION_CREDENTIALS

# 只使用公开的合成样例，不上传真实内容
python scripts/run_cleanroom_smoke.py --output .cleanroom/ci-smoke
```

它会运行仓库中定义的合成数据研究流程。需要分步查看 Source、Statement、Claim、Research View 等命令，可阅读[公开操作手册](docs/operations/OPERATIONS.zh-CN.md)。

### 体验 2：本地 Inbox 自动发现与搜索（开发功能）

```bash
# 运行在仓库外部的独立目录
mkdir -p /tmp/cbakb-demo/config /tmp/cbakb-demo/inbox/documents

cat > /tmp/cbakb-demo/inbox/documents/demo.txt <<'TXT'
Synthetic CBA Bulletin
The Harbor Falcons won a fictional preseason game.
TXT

# 本地 TXT 需要相应的来源和权利声明
cat > /tmp/cbakb-demo/inbox/documents/demo.txt.source.json <<'JSON'
{
  "schema_version": 1,
  "title": "Synthetic CBA Bulletin",
  "origin": "synthetic_fixture",
  "source_url": null,
  "rights": {
    "classification": "private",
    "public_export_allowed": false,
    "evidence": []
  }
}
JSON

# 稳定性检查：间隔后执行第二次扫描
cba-kb --instance-root /tmp/cbakb-demo inbox-scan
sleep 2
cba-kb --instance-root /tmp/cbakb-demo inbox-scan

# 查看状态与关键词检索
cba-kb --instance-root /tmp/cbakb-demo archive-status --all
cba-kb --instance-root /tmp/cbakb-demo archive-query --keyword "Harbor Falcons"
```

这里不会移动、删除或公开你的原始文件。实际处理结果以命令输出为准；若权利声明错误、文件未稳定或需要人工复核，系统可能拒绝摄入。正式使用前请先阅读[完整安全与操作说明](docs/operations/OPERATIONS.zh-CN.md)。

## Google Drive：为什么不能“点一下就全部归档”？

处理私人 Drive 文件，最重要的不是移动得快，而是**移动对文件、拥有权限、移动后还能核验**。

CBA-KB 的设计目标是：先读取准确的文件 ID、原父目录、目标目录、版本和权限证据 → 生成明确计划 → 人工批准 → 执行前再次核验 → 受控操作 → 再从 Drive 读取实际结果。中途缺少必要证据，应停止而不是猜测。

目前，开发分支已包含只读 Drive 发现、增量轮询、搜索、索引及部分受控计划能力；**生产 Drive reparent/move 尚不能视为已通过整版独立验收**。任何真实生产移动、删除、分享、发布或版本指针更新，都需要独立 Human Production GO，并服从 Google Drive 连接工具本身的授权确认。

真实 OAuth 凭据、私有文件 ID、原始语料和用户运行日志必须留在**Private Instance**，不能提交到公开 GitHub 仓库。

## 谁适合使用？

- **篮球研究者 / 数据整理者**：想知道每个结论来自哪里、哪些仍待验证。
- **开发者**：想复用 CBA 数据契约、身份映射、解析工具和合成样例。
- **AI 应用构建者**：不希望模型把传闻、陈述和核验事实混为一谈。

公开仓库提供 **Public Engine**；私人真实资料应由使用者在独立的 **Private Instance** 中依法管理。代码采用 [Apache-2.0](LICENSE)，文档依 [CC BY 4.0](DATA_LICENSE.md)，仓库的合成样例依 [CC0-1.0](DATA_LICENSE.md)。第三方报道、照片等仍受原版权约束，详见[第三方数据说明](THIRD_PARTY_DATA.md)。

## AI 帮忙开发，如何知道代码写得对？

本项目也使用 AI Agent 参与开发。可以把流程理解为：**你是产品经理，Muse、Grok 等 Agent 是开发者，CI 和独立审查是测试验收，最后由你决定是否合并。**

**新 Requirement → 独立 Q1 / Freeze → Agent 开发 → GitHub PR → CI + 独立 Q2 → Human Approval → Merge**

- 需求阶段写清楚“要解决什么”和“什么算完成”，复杂需求先独立审规格（Q1）并冻结范围。
- Muse 或其他具备仓库权限的 Agent 编写代码、测试、提交 PR；可以按任务切换开发主力。
- GitHub CI 检查可重复运行的测试；与开发过程隔离的 ChatGPT 等 Web AI 可以检查 PR 对应的**确切 HEAD**、复现缺陷并提出退修。
- 审查 FAIL 就修复后重审；最终仍由 Human 决定批准与合并。**合并不等于生产上线**。

这是一种参考 [LACD 协作模式](https://github.com/SkyOneBranch/LACD) 的低预算开发方式，**不是 CBA-KB 自带的 AI 编程服务**。不同 AI 工具可能提供免费额度，但额度、价格、权限和实际审查能力由服务商决定；多轮测试和独立审查能降低风险，不能保证代码零缺陷。

## 文档与参与贡献

- [运维手册（中文）](docs/operations/OPERATIONS.zh-CN.md) · [Operations Manual (English)](docs/operations/OPERATIONS.md)
- [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md)
- [数据和版权许可](DATA_LICENSE.md) · [第三方材料说明](THIRD_PARTY_DATA.md)
- [GitHub Issues](https://github.com/SkyOneBranch/cba-kb-engine/issues) · [Pull Requests](https://github.com/SkyOneBranch/cba-kb-engine/pulls)

**我们的目标不是让 AI 说出更确定的话，而是让每个值得相信的篮球结论都能找到证据。**
