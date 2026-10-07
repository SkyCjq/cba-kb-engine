# CBA-KB Engine

[English](README.md)

**CBA-KB** 是一个面向中国男子篮球职业联赛（CBA）的证据优先（evidence-first）数据与研究引擎。它把分散的注册资料、表格、文档、文章和人工核验证据整理为可追溯的结构化事实、身份关联的文档证据、Statement（陈述）、Claim（主张）、Verification Queue（待核队列）和只读 Research View（研究视图），并供人类与 AI 消费，同时避免把“证据”直接等同于“事实”。

当前生产发布：**v2.0.1-1 — COMPLETE**。

> 核心规则：**Statement ≠ Fact；Evidence ≠ Fact；Unknown ≠ 0。**

## 项目解决什么问题

CBA 研究资料长期分散在注册名单、Excel、文档、媒体文章、历史记录和人工判断中。CBA-KB 的目标不是简单堆文件，而是建立一条可复现的数据链路，同时保留来源（provenance）、不确定性、语义粒度（semantic grain）和数据权利边界。

本公开仓库保存可复用的 **Engine**：代码、schema、adapter、validator、确定性处理逻辑、synthetic/public-safe fixture 和公开安全的配置示例。

真实数据、凭据、Google Drive ID、Production allowlist、私有 source registry、人工修正和私有证据属于独立 **Private Instance**，不会提交到本仓库。

## v1.9.0 新增能力

v1.9.0 在既有注册事实、Document Evidence 和 Player Identity 基础上增加了证据感知的研究层：

- **Source Intake Contract v1**：统一接收 local file、Google Drive document、浏览器采集的微信材料、ima-derived export 与 synthetic fixture，并保留 provenance 与 rights metadata。
- **Statement 模型**：记录“谁在什么来源中说了什么”，保存 source reference、controlled excerpt、actor reference、attribution type 与 extraction status。
- **Claim MVP**：把研究主张和事实分离，并支持 unverified、corroborated、contradicted、superseded、review-required 等状态。
- **Actor / Identity reference**：能复用已存在的稳定 `player_uid`，同时允许 person-ready 或 unresolved actor，不强行创造身份。
- **Verification Queue v1**：把 unresolved actor、模糊 attribution、来源不确定、Claim 或 derived signal 显式进入待核队列，而不是静默猜测或丢弃。
- **Research View v0**：用只读方式明确分层展示 canonical fact、document、statement、claim、actor identity、verification item 和 unknown/evidence gap。
- **Rights-aware consumer materialization**：避免把私有或受限制证据错误输出为公开消费内容。
- **Fail-closed release / provenance controls**：通过显式 release state、source binding、readback、rollback/recovery 与 current-world/consumer closeout 保障发布边界。

v1.9.0 **不会**自动把 Statement/Claim 提升为 canonical fact，也不包含完整 Person Registry、完整 Stats layer，更不会把私有 Production 数据公开。

## 数据模型概览

```text
原始来源
   │
   ▼
Source Intake + provenance + rights
   │
   ├──────────────► Document / Evidence
   │
   ▼
Statement ────────► Claim
   │                  │
   ▼                  ▼
Actor / Identity   Verification Queue
   │                  │
   └──────────┬───────┘
              ▼
         Research View
              │
              ▼
   Rights-aware Consumer Output

Canonical registration facts 始终是独立 Truth Layer。
```

这种分层是故意设计的：一段真实引用可以是有效证据，但不因此自动成为已验证事实；一个无法确定身份的人也可以继续保持 unresolved，而不是被强行写入身份库。

## 项目架构

| 层 | 责任 |
| --- | --- |
| Engine | 公开可复用代码、schema、adapter、validator、CLI、release logic、synthetic/public-safe fixture |
| Private Instance | 真实 source registry、credentials、Drive mapping、real fixture、人工修正、production policy、私有证据 |
| Canonical facts | 带明确来源链路的注册/事件结构化事实 |
| Document evidence | 原始资料捕获与文档级 provenance |
| Identity | 稳定 Player Identity + 有边界的 unresolved/person-ready actor reference |
| Statement / Claim | v1.9.0 新增的证据研究语义层 |
| Verification Queue | 显式保存 unresolved / review-required 工作 |
| Research View | 面向研究和 AI 的只读多粒度综合视图 |
| Consumer outputs | rights-aware 派生输出，不反向成为上游事实 authority |

GitHub 是 Engine 的 Code Truth；Production 数据不保存在本仓库。

## 仓库结构

```text
src/cba_kb/          Engine 模块
tests/               离线回归与契约测试
config/              public-safe 配置与 taxonomy
scripts/             确定性 build/release 工具
docs/                历史设计、运维、发布与 Requirement 记录
requirements/        开发流程使用的 Requirement contracts
legacy/              保留的旧工具与兼容性测试
```

v1.9 的关键模块包括：

```text
src/cba_kb/source_intake.py
src/cba_kb/statement.py
src/cba_kb/claim.py
src/cba_kb/actor.py
src/cba_kb/verification_queue.py
src/cba_kb/research_view.py
```

## 环境要求

- Python **3.11+**
- 依赖锁定在 `requirements.lock`
- 可复现 candidate build 需要 Git
- 只有处理真实/私有输入或 Production Google Drive 时才需要 Private Instance

## 快速开始

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock

make doctor
make test
```

项目提供 `cba-kb` CLI：

```bash
cba-kb --help
```

当前具有代表性的命令族包括：

```text
validate / build / extract / facts
document-ingest / document-batch
identity-* / mention-*
source-intake
statement-extract
claim-extract
verification-queue
research-view
consumer-*
plan / publish / verify / restore
```

需要私有配置的命令必须通过 `--instance-root` 或 `CBA_KB_INSTANCE_ROOT` 指向 Private Instance。缺少所需私有状态时，系统应 fail closed。

## v1.9 研究链路

典型研究流程可以理解为：

```text
1. 用 source-intake 标准化来源
2. 保留 provenance 与 rights metadata
3. 提取 Statement
4. 能复用已知 Player Identity 就复用，否则保持 unresolved
5. Claim 始终作为独立语义层
6. 模糊项进入 Verification Queue
7. 生成只读 Research View
8. 只 materialize rights-safe Consumer Output
```

本仓库有意不提供“文档文本 → canonical fact”的自动捷径。

## 测试

标准离线回归：

```bash
make test
```

Focused tests 位于 `tests/`。普通离线回归不需要 Production credential。

项目优先采用 deterministic validation、显式 semantic contract、精确 source/provenance binding 和 fail-closed，而不是静默修复不明确状态。

## 数据与版权/权利边界

这是一个**公开 Engine 仓库**，不是 CBA-KB 私有知识库的数据公开下载站。

默认应认为：

- 部分上游文档和媒体内容受版权保护；
- 私有 source locator、Drive ID、credential、evidence 或人工修正可能不可再分发；
- 派生 Consumer Output 必须遵守来源的 rights classification；
- 缺少证据不等于数值为 0；
- AI 生成摘要、分类和分析属于派生信息，不是原始事实来源。

任何数据再分发之前，都应先核验对应来源的权利与 provenance。

## Production 与发布安全

Production write 与普通开发严格分离。发布链路使用显式 production policy、冻结输入、single-writer execution、immutable evidence、readback verification 和 rollback/recovery。

本地 build 成功或 CI GREEN 都不等于获得 Production publish 权限。

## 项目状态与路线

**v2.0.1-1** 是当前已完成的 Production release，包含 Statement/Claim 研究层、Source Intake Contract、Verification Queue、Research View 及其 Consumer Closure 控制。

后续可能继续发展 Person modeling、Stats、更加完整的 Claim/Research workflow 与自动化，但只有代码实际存在且完成正式发布后，才视为已实现能力。

## 贡献

Issue / Pull Request 必须保持 Engine / Private Instance 边界。

贡献时请遵守：

1. 不提交 credential、真实 private fixture、private Drive mapping 或 private evidence；
2. 不把 fact、document、statement、claim、unknown 混成一个语义层；
3. contract 变化需要同步增加/更新测试；
4. 对模糊或不安全状态保持 deterministic + fail-closed；
5. 仓库修改使用 feature branch + Pull Request。

## 文档

- `docs/` 保存历史架构、迁移、运维和发布记录。
- `cba-kb --help` 是当前已实现 CLI surface 的直接入口。
- 仓库中的代码和测试是 Engine 已实现行为的最终依据。

## License 与复用

当前仓库**没有发布明确的开源 LICENSE 文件**。仓库公开可见本身并不等于授予开源许可证。

如果计划复用或再分发代码，应先补充或确认明确的软件许可证；数据与来源材料的权利需要与代码许可证分开评估。
