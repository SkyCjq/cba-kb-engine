# CBA-KB｜让每一条篮球结论都有出处

[English](README.md) · [快速开始](#5-分钟快速开始) · [运维手册](docs/operations/OPERATIONS.zh-CN.md)

**新闻说了什么，不等于事实已经得到证实。** CBA-KB 是面向中国男子篮球职业联赛（CBA）的开源知识库引擎：帮助研究者和开发者整理比赛报道、球员资料、联赛公告与文档，保留来源、身份、版权和核验状态，让人或 AI 查资料时可以追问：**“这条结论依据什么？”**

> **当前最新生效版本：v2.0.2。** `main` 已合入 **v2.0.4-2 开发整合代码**，但真实 Google Drive 端到端验收和生产激活尚未完成，不能视为新的正式发布。

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
| 安全整理和归档 | 明确计划、人工批准、受控执行、操作后回读 | 已合并开发代码；真实 Drive E2E 和生产激活尚未验收 |
| 给人和 AI 使用 | 生成带身份、证据和权限语义的研究数据 | 使用者仍需核验证据并尊重版权 |

**CBA-KB 不是聊天机器人，也不是自动抓取全网、自动判定所有篮球事实的服务。** 它是可复用的处理引擎，需要你选择合法数据来源、运行环境和审核流程。

## 主要版本与当前状态

| 版本 | 主要迭代功能 | 状态 |
| --- | --- | --- |
| **v1.5.x** | 球员注册数据模型、可复现的 Excel 导出、早期 Drive 发布工具 | 历史里程碑 |
| **v1.8.x** | 文档证据、稳定球员身份、消费端核验 | 历史里程碑 |
| **v1.9.0** | 资料来源摄入、Statement、Claim、核验队列和研究视图 | 历史里程碑 |
| **v2.0.2** | 公开开源 Engine、许可证与脱敏、合成测试样例及可复现工具 | **当前最新生效版本** |

**下一版本 v2.0.4-2：** `main` 已合入本地 Inbox、Drive 文件发现和轮询、权限感知索引、受控计划与故障恢复等开发代码。**真实隔离 Drive E2E、完整产品验收及生产激活尚待完成**，因此仍不是新的生效版本。

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

## Google Drive：先确认，再整理

CBA-KB 可以发现、跟踪 Drive 文件，并为归档或保留生成明确计划。**实际移动前需要人工授权，移动后还要核验结果。** 如果文件身份或版本证据不足，就停止操作。

受控移动代码已进入开发分支，但**真实 Drive E2E 和生产激活尚未验收**。生产写入仍需单独的人类授权；OAuth 凭据和私人文件夹信息不得放入公开仓库。

## 谁适合使用？

- **篮球研究者 / 数据整理者**：想知道每个结论来自哪里、哪些仍待验证。
- **开发者**：想复用 CBA 数据契约、身份映射、解析工具和合成样例。
- **AI 应用构建者**：不希望模型把传闻、陈述和核验事实混为一谈。

公开仓库提供 **Public Engine**；私人真实资料应由使用者在独立的 **Private Instance** 中依法管理。代码采用 [Apache-2.0](LICENSE)，文档依 [CC BY 4.0](DATA_LICENSE.md)，仓库的合成样例依 [CC0-1.0](DATA_LICENSE.md)。第三方报道、照片等仍受原版权约束，详见[第三方数据说明](THIRD_PARTY_DATA.md)。

## 文档与参与贡献

- [运维手册（中文）](docs/operations/OPERATIONS.zh-CN.md) · [Operations Manual (English)](docs/operations/OPERATIONS.md)
- [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md)
- [数据和版权许可](DATA_LICENSE.md) · [第三方材料说明](THIRD_PARTY_DATA.md)
- [GitHub Issues](https://github.com/SkyOneBranch/cba-kb-engine/issues) · [Pull Requests](https://github.com/SkyOneBranch/cba-kb-engine/pulls)

**我们的目标不是让 AI 说出更确定的话，而是让每个值得相信的篮球结论都能找到证据。**
