# v1.5 前置实施修订（2026-09-10）

> 当前生产：v1.1 FINAL / CLOSED。下一版本：v1.5 PLANNED；之后才进入 v2.1 Unified Target。
> 本节覆盖下方历史方案中相冲突的实施顺序、生产基线和运行环境要求；旧正文保留用于设计溯源，不代表当前状态。

实施顺序：**v1.1 FINAL → v1.5 → v2.1**。先读 [v1.5 完整方案](https://drive.google.com/file/d/1App5yvwH7s9oLyjdVdUea1us233P5VgV/view)。

- v1.5 将执行环境部署在 `/Users/skychengneo/Agent/CBA_kb`，GitHub 私有仓库管理代码，已验证产出同步到 Drive；不同 AI 通过 Drive 消费，不依赖本机服务。
- 当前事实产物为 `CBA_2017-2027_国内球员注册_MASTER.xlsx`，file ID `1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc`，不是旧 native 多标签 Sheet。v1.1 FINAL 记录 3,451 条、单 MASTER tab、registry 89 来源；实施时重新实读锁定。下文 2,737 / 3,404 / 75 等为历史快照。
- v1.5 先完成最小 MASTER-aware builder、类型正确的同步、原 ID 发布、版本状态/失败恢复、跨 AI Drive 消费验证；不提前实施 identity、stats 或 claims。
- 下文“Actions = Execution Truth”、云端优先 fallback、个人电脑仅应急以及直接从 v1.1 启动 v2.1 的要求，均改为承接 v1.5 本地运行和 Drive 发布审计。GitHub Actions 生产调度待后续明确迁移，不是前置门禁。
- v2.1 的业务目标继续保留，在 v1.5 验收后基于当前 MASTER 和既有发布链路增量实施；不得恢复已归档工作表为生产事实源。
- 新会话先读 README 的当前状态，再按任务读取 v1.5 或 v2.1，不必每次加载全部历史方案。
- 本修订是方案更新，不代表本地环境、GitHub 仓库或 v1.5 生产发布已完成。

---

# CBA-KB v2.1 — Unified Implementation Amendment

> **Amendment date**: 2026-09-09  
> **Status**: TARGET / 尚未进入生产实施  
> **Production baseline**: **CBA-KB v1.1 = STABLE / PASS_WITH_KNOWN_BOUNDARIES**  
> **Priority rule**: 本 Amendment 的当前状态、实施顺序和工程门禁，覆盖下方原 v2.1 正文中与其冲突的旧描述。原正文保留用于版本溯源。
>
> **Merge decision**: 原 `CBA-KB v2.md`（player-centric 逻辑模型）与原 v2.1（Web-AI runtime）不再作为两个连续生产版本实施。两者统一为 **CBA-KB v2.1 Unified Target**。`CBA-KB v2.md` 保留为历史设计快照，不删除。

## A. 当前启动顺序

新的 AI / Agent 开始开发前依次读取：

1. `00_README_先读这个`
2. `02_AI_PROJECT_CONTEXT_技术手册.md`
3. `CBA-KB v1.1.md` — 当前生产基线
4. `CBA-KB v2.1.md` — 统一后续实施方案
5. `40_ai_投喂与索引/INDEX` — 导航索引

`CBA-KB v2.md` 仅在需要追溯旧 v2 设计决策时读取，不再作为每次开发的必读文件。

如果文档与实际状态冲突：

```text
Drive live state = Data Truth
Private GitHub live state = Code Truth
GitHub Actions run/log = Execution Truth
Public GitHub merged snapshot = Published Snapshot
```

## B. v1.1 生产基线

v2.1 必须从以下已完成状态出发，而不是从旧的“只有 1,019 条注册数据”设计假设出发：

```text
cba_registrations
= 1,019 official_api
2024-2025 = 352
2025-2026 = 335
2026-2027 = 332

historical_registrations_2017_2025
= 2,737 historical structured rows
= 2,043 auto_validated
+   694 needs_review

registrations_all_2017_2027
= 3,404 GENERATED / READ-ONLY
= 2,385 historical
+ 1,019 official_api

source_registry.csv
= 75 initial historical sources
= 9 PDF + 3 XLSX + 63 images
```

v1.1 还已经完成：

- `process_inbox.py` 从 Move-first 改为 Register-first；
- `manifest.csv` 与 `source_registry` 职责分离；
- 物理文件位置与 processing state 解耦；
- CBA 官方真实 probe regression 固定解析 14 名球员；
- v1.1 regression suite 8 tests passed。

因此，v2.1 **不得重建或替换 v1.1 已验证事实层**，而应在原表上增量增加身份、统计、claims 和派生视图。

## C. v2.0 与 v2.1 合并结论

### 为什么可以合并

原 v2.0 解决：

```text
player_uid
→ identity
→ registrations
→ contracts
→ stats
→ claims
→ profiles
```

原 v2.1 解决：

```text
上述能力如何通过
Web AI + Private GitHub + GitHub Actions + Drive
长期开发、执行、审计和对外发布
```

两者都尚未成为独立 production release，而且业务模型与 runtime 强耦合。继续按：

```text
v1.1 → v2.0 → v2.1
```

实施，会造成：

- 两次 schema migration；
- 两套 README / AI context 状态切换；
- `player_uid` 逻辑先落本地/Drive 再迁 GitHub runtime；
- 两次测试基线重建；
- 新 AI 容易把“v2 已设计”误判为“v2 已上线”。

因此正式改成：

```text
v1.1 stable
→ v2.1 unified target
```

### 为什么仍保留 `CBA-KB v2.md`

它是设计溯源材料，不是待执行的独立版本。保留它可以回答：

- `player_uid` 为什么设计成内部永久主键；
- Claim Ledger / Facts / Claims 最初为何这样划分；
- Web-first 之前做过哪些技术取舍。

但它不再进入正常启动链。

## D. Player Identity 范围按 v1.1 重新定义

现有：

```text
486 player_uid seed
404 auto
82 needs_review
```

仅来自 **2024–2027 的 1,019 条 recent official registrations**，是 provisional registry，**不是 2017–2027 全历史球员全集**。

正式 Identity 分两段：

### D1. Recent Official Identity Bridge

```text
冻结 P0001–P0486
→ cba.net XHR discovery
→ 20–50 人 canary
→ external playerId / evidence review
→ 486 batch
→ cba_registrations + nullable player_uid
→ recent player_club_changes 改按 UID
```

禁止重排、复用 `P0001–P0486`。

### D2. Historical Identity Expansion

v1.1 新增 2,737 historical rows 后，必须增加独立阶段：

```text
historical rows
→ 能可靠链接到 P0001–P0486 的先链接
→ historical-only 球员追加 P0487+
→ OCR / needs_review 有歧义时 player_uid 保持空
→ 真实冲突进入 review_queue
→ 重建 registrations_all_2017_2027
```

目标是**正确、可追溯的身份覆盖率**，不是强求 100% UID 填充率。

## E. 四个 Truth 保持不变

```text
Google Drive   = Data Truth
Private GitHub = Code Truth
GitHub Actions = Execution Truth
Public GitHub  = Published Snapshot
```

新增一条派生规则：

```text
Private Git data_snapshot/
= GENERATED / READ-ONLY audit mirror
≠ Data Truth
```

它只用于 run-level 审计，永不手改，永不反向覆盖 Drive。

## F. 五条新增工程原则

### F1. Runtime Gate 必须验证真实外部依赖

Phase A 不只验证 Drive，还必须验证：

```text
OAuth refresh
Drive known-file read
Drive test write
文本/Markdown → Google Docs 转换
files.update 原地更新且 file ID 不变
readback 内容正确
cbaleague official API reachable
cba.net origin/page reachable
```

注意：

- `cbaleague` 已有真实 official API；
- `cba.net` 在 Phase A 只验证 origin/page reachability；
- 真正 roster/stats XHR endpoint 必须在 Phase B discovery 后再验证；
- 不得把“页面可达”写成“API 已验证”。

如果 GitHub-hosted runner 无法稳定访问源站，fallback 顺序：

```text
1. GitHub-hosted runner
2. region-controlled cloud/VPS self-hosted runner
3. approved stable egress/proxy
4. home self-hosted runner only for emergency/debug
```

不要重新把个人电脑变成 production dependency。

### F2. 禁止静默绿色空转

PR CI 与真实 runtime 分离：

```text
00_ci.yml
→ pull_request
→ unit/schema/fixture/static checks
→ NO production secrets
→ NO production Drive writes
```

Runtime：

```text
01_runtime_preflight.yml
→ schedule + workflow_dispatch
→ auth/read/network

02_runtime_gate_write.yml
→ manual / controlled periodic
→ convert/update/readback/file-ID test
```

共享业务流程：

```text
_pipeline.yml
→ workflow_call
→ required inputs

manual.yml
→ workflow_dispatch 显式传参

scheduled.yml
→ schedule 显式传参
```

禁止 scheduled job 依赖只在 `workflow_dispatch` 存在的 `inputs.*`。

Stage 必须是真实 DAG；例如 `stages=identity` 不得继续隐式执行 claims/profiles/bundles。

运行状态必须聚合为：

```text
success
partial_failure
failed
review_required
```

禁止长期使用 step-level `continue-on-error` 产生“看起来全绿”的假成功。

### F3. Drive 保持 Data Truth，同时补运行级审计轨迹

每次 live run 结束，Private Git 可生成：

```text
data_snapshot/
run_manifest.json
record_counts.json
source_hashes.json
diff_summary.md
schema_version
code_commit_sha
```

当前数据量较小时可以保存完整 CSV/Parquet snapshot。

约束：

- GENERATED；
- READ-ONLY；
- 不人工修改；
- 不反向同步 Drive；
- commit message 可带 run ID；
- 不构成第二 Data Truth。

### F4. Review 与 Public Release 必须 fail-closed、幂等、可人工审查

Review：

```text
review_queue = canonical review state
GitHub Issues = human review UI
```

Issue 创建：

```text
stable review key
→ search existing issue
→ update/reopen
→ only then create
```

默认单次新增 Issue 上限建议：

```text
--max-new 10
```

超限时输出 `review_required` 报告，不继续 Issue flood。

Public Release：

```text
manual trigger
→ allowlisted projection
→ license/product-policy gate
→ render public docs
→ final leak scan
→ create/update public release branch
→ create/update PR
→ human review
→ human merge
```

重复运行同一 release 必须更新已有 branch/PR，而不是重复创建。

### F5. Public / Private 边界从 schema 创建时就声明

新表/字段创建时即声明 exposure：

```text
public
internal
premium_private
needs_review
```

规则：

- 发布用字段白名单；
- 新字段默认 private；
- 未显式允许不得出仓；
- publication policy 与 canonical fact 分离。

Public repo 可以包含：

```text
approved data snapshots
README
public schema / data dictionary
optional consumer SDK / examples
```

默认不公开：

```text
ingestion adapters
fetch / XHR logic
Drive auth
OCR pipeline
identity evidence rules
verification / review workflow
publishing workflow
internal paths / IDs / secrets
```

第三方来源数据不能未经 rights review 自动写死为 `CC-BY-4.0` 等开放许可证；rights 未完成时使用 `license_status = needs_review` 或等价状态。

## G. OAuth / Credential Gate 补充

纯 Web 获取 refresh token 时，操作路径必须明确：

```text
Google Cloud Console
→ OAuth Client type = Web application
→ Authorized redirect URI:
  https://developers.google.com/oauthplayground
→ OAuth Playground
→ Settings
→ Use your own OAuth credentials
→ offline access / consent
→ refresh token
→ GitHub Secrets
```

Credential checklist 至少记录：

```text
OAuth publishing status
scopes
client ID
refresh token created_at
refresh token last-tested
secret rotation / expiry
PUBLIC_REPO_TOKEN expiry if used
```

Testing 状态下的 refresh token 生命周期必须明确纳入 preflight/运维检查。

## H. Public Export 三道门

### Gate 1 — Allowlist

只从 public projection 生成，不允许：

```text
cp internal_data/* public/
```

新字段默认 private。

### Gate 2 — Licensing + Product Policy

区分：

```text
licensing_status
product exposure policy
verification_status
source type
```

不要把“法律上可用”与“产品上要公开”混成一个字段。

### Gate 3 — Final Leak Detector

必须在 README / SCHEMA / CHANGELOG 等**外部文档已经生成之后**再运行。

建议：

```text
HARD BLOCK
- server.cbaleague.com
- prohibited private Drive URLs / IDs
- drive_file_id / ima_item_id
- internal folder paths
- secret/key patterns

WARNING / HUMAN REVIEW
- generic project/workflow vocabulary
- Tampermonkey
- non-secret implementation terms
```

不要把所有关键词都当 hard fail，避免大量 false positive。

## I. Public Repo 边界

Public GitHub 不等于“必须零代码”。

可以公开：

```text
approved data
schema
data dictionary
consumer-side loader / SDK
examples
```

但不公开内部采集、验证、身份判定和发布流程。

发布 workflow 只操作 public repo：

```text
clone public main
→ create/update release branch
→ replace generated outputs
→ commit
→ open/update PR
```

不把 private repo git history 推入 public repo；也不必每次创建 orphan history，否则会损失公共数据自身的连续 diff。

## J. v2.1 统一实施 Roadmap

### Phase A — Cloud Runtime Gate

必须通过：

```text
OAuth refresh
Drive read
Docs conversion
files.update / same ID
readback
cbaleague API reachability
cba.net origin/page reachability
no silent-green workflow
Private Git audit-snapshot rule
```

通过前不运行 identity batch / public publish。

### Phase B — Recent Official Identity Bridge

```text
20–50 canary
→ 486 recent seed
→ 1,019 official rows backfill player_uid
```

### Phase C — Historical Identity Expansion

```text
2,737 historical rows
→ existing UID linking
→ append historical-only UIDs
→ ambiguous OCR remains nullable
→ review queue
```

### Phase D — Official Stats

profile / season / career / game stats。

### Phase E — Claim Ledger

保留原 v2 核心：

```text
source
→ mention resolution
→ player_uid
→ claims
→ provenance
→ time anchoring
```

### Phase F — Player Profiles

```text
facts + claims + current time
→ build_profiles
→ 40_ai/profiles
```

### Phase G — Public Snapshot

schema exposure 已在前面持续声明；此阶段只实现投影、许可、泄漏检查和 PR 发布。

### Phase H — Optional B2

仍是对象归档，不是关系数据库，也不是当前 blocker。

### Phase I — MCP / REST

有真实跨客户端或商业实时需求后再做。

## K. 不可妥协的模型原则

以下来自原 v2 的核心继续保留：

1. `player_uid` 是内部永久主键。
2. `cba.net playerId` 等只能作为 external ID。
3. `P0001–P0486` 冻结，不因历史扩展重排。
4. Identity Resolution 与 Mention Resolution 分开。
5. Facts / Claims / Profiles 分层。
6. Claim Ledger 是故事/特点的 canonical structured layer。
7. Profile 是 generated view，不反写 Facts。
8. Claim 必须带 provenance 与时间锚定。
9. `source_registry` 与 `manifest` 分离。
10. `auto_validated` 不等于 official / human verified。
11. `needs_review` 不得为了覆盖率强行升级。
12. Drive folder/file ID 稳定规则继续有效。
13. v2.1 不破坏 v1.1 已验收的数据基线。

## L. 当前唯一执行起点

```text
v1.1 stable baseline
↓
Phase A Cloud Runtime Gate
↓
Phase B Recent Official Identity Bridge
↓
Phase C Historical Identity Expansion
```

当前不要先做：

```text
486 全量 batch
历史 UID 强行全覆盖
B2 全量迁移
MCP
REST API
复杂付费系统
```

---

# Original v2.1 Design — preserved for version traceability

> 以下为 2026-09-08 的原 v2.1 正文。若与上方 Amendment 冲突，以上方 Amendment 为准。

# CBA-KB v2.1

> **Purpose / 目的**：这是 CBA-KB v2.1 的 AI / 开发者总技术方案。任何新的 AI、Agent、ChatGPT Work、Claude Code Web 或其他开发会话，在修改项目前应先读本文件，再核实 Google Drive 和 GitHub 的实际状态。
>
> **状态日期**：2026-09-08
>
> **核心结论**：
>
> - **Google Drive = Data Truth / 业务数据真相源**
> - **Private GitHub = Code Truth / 代码真相源**
> - **GitHub Actions = Execution Truth / 正式执行环境**
> - **Public GitHub = Published Snapshot / 对外发布快照**
> - **Backblaze B2 = Optional Object Archive / 可选对象归档层**
>
> 本地 Python、本地 `token.json`、本地 OAuth **不再是正式依赖**；可作为调试手段，但不能成为项目可持续运行的前提。

---

# 0. AI / 开发者启动顺序

新的 AI / Agent 开始任务前，依次读取：

1. `00_README_先读这个`
2. `02_AI_PROJECT_CONTEXT_技术手册.md`
3. `CBA-KB v2.md`
4. `CBA-KB v2.1.md`（本文件，实施方式优先级最高）
5. `40_ai_投喂与索引/INDEX`

然后必须实际核查：

- Google Drive 根目录和目标子目录
- `20_data_结构化事实`
- `50_scripts_自动化脚本`
- `60_config_配置与词表/drive_map.yaml`
- `60_config_配置与词表/manifest.csv`
- Private GitHub repo 当前分支、PR、Actions、最近 workflow run
- 如果任务涉及公开发布：目标 Public GitHub repo 当前版本

如果文档和实际状态冲突：

> **数据事实以 Drive 实际状态为准；代码和 workflow 以 Private GitHub 实际状态为准；必须指出冲突，不能猜。**

---

# 1. v2.1 的目标

CBA-KB v1 解决：

```text
资料收集
→ 结构化 / 文本化
→ Drive
→ AI 可读
```

CBA-KB v2 解决：

```text
稳定 player_uid
→ identity
→ registrations
→ contracts
→ stats
→ claims
→ player profile
```

CBA-KB v2.1 进一步解决：

```text
这些能力如何在不依赖个人电脑的情况下，
通过 Web AI + GitHub + Drive 持续开发、执行、审计和对外发布。
```

最终目标：

```text
Web AI
   ↓
查 Drive / 改 GitHub / 触发 Actions
   ↓
稳定维护一个 player-centric CBA knowledge platform
   ↓
内部 AI 直接使用
   ↓
需要时人工发布一部分数据 / 能力到 Public GitHub
```

---

# 2. 当前已验证数据基线

## 2.1 注册事实

当前主事实层：

```text
20_data_结构化事实/cba_registrations
```

已验证：

```text
总记录：1,019
2024-2025：352
2025-2026：335
2026-2027：332
球队页：60 = 20 × 3
跨赛季球队变化：65
```

当前主表继续作为事实来源，不立即拆成第二张 `player_registrations`。

v2.1 的迁移策略是：

```text
cba_registrations
+ player_uid
```

而不是：

```text
cba_registrations
↓
另建一张新主表
↓
两个真相并存
```

---

## 2.2 provisional Player Registry

当前 seed：

```text
486 player_uid
404 auto
82 needs_review
```

风险分布：

```text
71 个两字名
11 个音译名
60 人有多队履历
```

必须注意：

```text
auto
≠
verified_external
```

当前 `cba_net_player_id` 尚未批量回填。

建议正式状态：

```text
identity_status:
- provisional
- verified_external
- human_verified
- needs_review
- disputed
```

---

# 3. 四个 Truth

## 3.1 Drive = Data Truth

Google Drive / `cba-kb` 继续保存：

```text
00_inbox
10_sources
20_data
30_notes
40_ai
60_config
90_archive
```

Drive 是：

- 结构化事实真相源
- 私有原始证据仓库
- AI 内部读取入口
- 人工编辑入口
- generated profile / bundle 的主要内部消费层

GitHub 中的 CSV / Parquet snapshot 不能取代 Drive。

---

## 3.2 Private GitHub = Code Truth

建议 Private Repo：

```text
cba-kb-engine
```

保存：

```text
src/
schemas/
tests/
fixtures/
config/
docs/
.github/workflows/
```

Private GitHub 是：

- 正式源码
- schema
- tests
- workflow
- PR / diff / review
- 代码版本历史

Drive 的 `50_scripts_自动化脚本` 逐步变成：

```text
镜像 / 导出 / 历史参考
```

而不是长期代码主版本。

---

## 3.3 GitHub Actions = Execution Truth

正式管线需要：

```text
Python
HTTP / XHR
Google Drive API
OCR
DuckDB / SQLite
hash
schema validation
profile build
public export
```

都由 Actions 执行。

一次 Web AI 沙箱运行：

```text
只能算实验
```

正式能力必须存在：

```text
commit
+ workflow
+ logs
+ artifacts/result
```

---

## 3.4 Public GitHub = Published Snapshot

公开仓库不是内部数据真相源。

Public repo 中的数据必须标：

```text
snapshot_as_of
schema_version
source_commit
canonical_source = private CBA-KB / Google Drive
```

对外发布不是 live sync，而是：

```text
Drive
↓
manual publish workflow
↓
allowlisted export
↓
public repo PR
↓
human review / merge
↓
published snapshot
```

---

# 4. Web AI 的正式角色

ChatGPT Work / Claude Code Web / 类似 Web AI：

```text
Development + Orchestration Client
```

允许：

- 读 Drive
- 查私有 GitHub（如果授权）
- 创建 branch
- 改代码
- 建 PR
- Review diff
- 触发 workflow
- 看 logs / artifacts
- 分析错误
- 读 Actions 写回 Drive 的 report

不应承担：

```text
唯一正式 runner
唯一项目 memory
唯一 secrets storage
唯一数据 truth
```

原则：

```text
AI conversation = disposable
Drive + GitHub = durable
```

---

# 5. Google Drive OAuth：v2.1 正式方案

## 5.1 不再使用 InstalledAppFlow 作为生产逻辑

旧逻辑：

```python
InstalledAppFlow.run_local_server()
```

不适合 GitHub Actions。

正式改成：

```text
OAuth client_id
OAuth client_secret
OAuth refresh_token
↓
GitHub Secrets
↓
GitHub Actions refresh
↓
Drive API
```

---

## 5.2 refresh token 可以纯 Web 获取

不要求本机 Python。

可通过浏览器完成：

```text
Google Cloud Console
↓
OAuth Client
↓
OAuth 2.0 Playground
↓
offline access
↓
refresh token
↓
GitHub Secret
```

因此项目正式目标是：

```text
zero local runtime dependency
```

而不是：

```text
zero browser setup
```

---

## 5.3 OAuth 发布状态

生产自动化不能依赖长期处于：

```text
Testing
```

的 OAuth 项目。

正式 setup checklist 必须记录：

```text
OAuth publishing status
scopes
client ID
secret rotation
refresh token creation date
last auth preflight
```

---

## 5.4 `drive_auth` preflight

所有会写 Drive 的 workflow，在做耗时任务前先：

```text
refresh token
↓
GET root / known file
↓
验证 read
↓
验证目标 folder permission
↓
验证 write capability
↓
再运行业务逻辑
```

失败：

```text
fail fast
```

不要跑完抓取 / OCR 后才发现 Drive 无权写。

---

# 6. Drive folder ID 规则继续保留

已有 P0 规则继续有效：

```text
folder role
→ drive_map.yaml
→ folder ID
```

禁止恢复：

```text
find folder by display name
→ not found
→ create
```

尤其禁止重新创建：

```text
data/
bundles/
scripts/
config/
```

Drive 自动化必须：

- folder ID 定位
- 已有 file ID 原地 update
- hash 未变 skip
- error 明确记录
- no silent duplicate folder

---

# 7. GitHub Repo 结构

建议：

```text
cba-kb-engine/
├── src/
│   ├── cba_core/
│   ├── drive/
│   ├── sources/
│   │   ├── cbaleague/
│   │   ├── cba_net/
│   │   └── ima/
│   ├── identity/
│   ├── claims/
│   ├── profiles/
│   └── publishing/
│
├── schemas/
│   ├── players_master.schema.json
│   ├── player_external_ids.schema.json
│   ├── source_registry.schema.json
│   ├── player_claims.schema.json
│   └── review_queue.schema.json
│
├── config/
│   ├── taxonomy.yaml
│   ├── season_map.yaml
│   ├── team_map.yaml
│   └── alias_types.yaml
│
├── tests/
├── fixtures/
├── docs/
└── .github/workflows/
```

Secrets 永不进入 repo：

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
B2_KEY_ID
B2_APP_KEY
IMA_*
PUBLIC_REPO_TOKEN（如需要）
```

---

# 8. Workflow 设计

建议：

```text
00_validate.yml
10_probe_sources.yml

20_identity_canary.yml
21_identity_batch.yml

30_sync_registrations.yml
40_process_inbox.yml

50_extract_claims.yml
60_build_profiles.yml
70_build_bundles.yml

80_audit_drive.yml
90_publish_public.yml
```

---

# 9. 不要把 manual 与 schedule 参数混用

共享逻辑建议写成 reusable workflow：

```text
_pipeline.yml
```

手动：

```text
manual.yml
→ workflow_dispatch
→ 显式传 stages / dry_run
```

定时：

```text
scheduled.yml
→ schedule
→ 显式传 stages=all / dry_run=false
```

不要让 scheduled job 依赖：

```text
inputs.*
```

---

# 10. Workflow 风险分级

## Tier A — 自动内部

可 schedule：

```text
source probe
schema validation
hash check
Drive integrity audit
profile rebuild
bundle rebuild
```

---

## Tier B — 手动触发内部

使用 `workflow_dispatch`：

```text
identity batch
OCR batch
bulk inbox move
mass migration
apply conflict resolution
```

---

## Tier C — 人工 Review 对外

```text
manual trigger
↓
candidate export
↓
validation
↓
public repo PR
↓
human merge
```

包括：

```text
free dataset
premium dataset
public schema snapshot
public Agent assets
```

---

# 11. 数据物理实现：先文件，不先 DB Server

当前规模：

```text
1,019 registrations
486 player seeds
```

无需 Postgres。

当前推荐：

```text
Google Sheet / CSV / Parquet in Drive
↓
Actions
↓
temporary DuckDB / SQLite
↓
query / validation / transform
↓
write result back to Drive
```

逻辑对象可以很多，但物理层保持简单。

---

# 12. v2.1 数据对象

现阶段建议：

```text
cba_registrations        # 直接增加 player_uid
players_master
player_external_ids
entity_aliases
entity_resolution_events
review_queue

player_contracts
player_team_history
player_season_stats

source_registry
player_claims
```

不要为迁移而迁移。

---

# 13. Player Identity 核心规则

## 13.1 `player_uid`

内部永久主键。

```text
P0001
...
P0486
```

规则：

- 永不复用
- 不因球队变化而变
- 不因姓名变化而变
- 不因外部 ID 变化而变
- merge / split 必须记录 event

---

## 13.2 External ID

例如：

```text
cba.net.cn playerId
```

只能进入：

```text
player_external_ids
```

不能：

```text
player_uid = external playerId
```

---

## 13.3 Identity Resolution != Mention Resolution

例如：

```text
方硕
identity_status = verified_external
mention_match_risk = high
```

是合理的。

不能因为两字名就自动创建 identity conflict。

---

# 14. Identity Bridge Canary

不要直接 486 人。

先 20–50 人，覆盖：

```text
三赛季连续同队
换队
两字名
音译名
单赛季
可能未出场
```

输出：

```text
candidate_player_ids.csv
identity_conflicts.csv
raw_response_samples/
summary.md
```

验证：

```text
cross-season playerId stability
missing rate
name mismatch
team mapping mismatch
one player -> multi playerId
one playerId -> multi player_uid
review rate
```

通过后才 batch。

---

# 15. `cba_registrations` 迁移方式

正式选择：

```text
原表 + player_uid
```

而不是立刻另建主事实表。

所有现有：

```text
bundles
INDEX
counts
diff
```

继续工作。

后续 `player_club_changes` 改成：

```text
GROUP BY player_uid
```

而不是姓名。

---

# 16. GitHub Issues = Review UI

v2.1 将 GitHub Issues 提前为 Web-only 重要能力。

但 Issue 不是长期数据。

长期状态：

```text
review_queue
entity_resolution_events
```

Issue 是：

```text
human review interface
```

只为真实 review 创建 Issue，例如：

```text
external playerId conflict
multiple candidates
team mapping ambiguity
same player -> multiple IDs
same ID -> multiple players
OCR unresolved
source conflict
claim conflict
```

不要：

```text
71 个两字名
→ 71 个 Issue
```

除非真实发生歧义。

---

# 17. Review 生命周期

```text
workflow detects review
↓
review_queue record
↓
create/update GitHub Issue
↓
human comment / label / close
↓
apply-review workflow
↓
entity_resolution_events
↓
update Drive structured record
```

人工决定必须可以回溯。

---

# 18. Source Registry 与 manifest 分开

`manifest.csv`：

```text
file sync
hash
Drive file ID
path
sync status
```

`source_registry`：

```text
source_id
source_system
publisher
source_url
published_at
source_authority
extraction_method
verification_status
licensing_status
drive_file_id
b2_object_key
content_hash
```

不要混成一张万能表。

---

# 19. Claims / Stories

微信 / 媒体：

```text
raw source
↓
mention resolution
↓
player_uid
↓
claim extraction
↓
player_claims
↓
build_profiles
↓
40_ai/profiles
```

媒体 claim 永不无标记变成 official fact。

---

# 20. Time Anchoring

所有 claim 至少：

```text
published_at
```

能识别时：

```text
event_date
valid_from
valid_to
observed_at
```

回答：

```text
“现在”
```

必须优先：

```text
latest official fact
+ current valid claims
```

旧文章必须标历史语境。

---

# 21. Profile 的位置

自动生成：

```text
40_ai_投喂与索引/profiles/Pxxxx_姓名.md
```

不是：

```text
30_notes_人工知识
```

Profile：

```text
generated
rebuildable
not fact truth
```

---

# 22. Backblaze B2

## 22.1 定位

B2：

```text
Object Archive
```

不是：

```text
Relational Database
```

---

## 22.2 当前不是 Phase A blocker

现在不要求先迁 B2。

只有出现：

```text
大量历史扫描
大图
OCR 原始件
Drive 容量 / 成本压力
不可变 archive 需求
```

再启用。

---

## 22.3 物理许可隔离

如果某些原始材料：

```text
内部要保存
但不希望出现在 Git / Public Export 链附近
```

可以只放 B2，并在 Drive 保留 metadata / pointer。

但不能一刀切把所有 `10_sources` 搬离 Drive，因为内部 Web AI 仍需要方便读取。

---

# 23. Public Export：三道 Gate

## Gate 1 — Allowlist

只允许明确 public schema / fields。

不能：

```text
cp internal data/* public/
```

---

## Gate 2 — Licensing

检查：

```text
licensing_status
premium_flag
verification_status
source type
```

---

## Gate 3 — Leak Detector

补充检查：

```text
mp.weixin.qq.com
js_content
known raw-source markers
private paths
secret patterns
```

Leak detector 是最后防线，不是唯一防线。

---

# 24. Public Publish

推荐：

```text
workflow_dispatch
↓
build candidate snapshot
↓
validate
↓
generate release manifest
↓
push publishing branch to public repo
↓
open Pull Request
↓
human review
↓
human merge
↓
optional GitHub Release
```

Public main 不允许 workflow 无审查直推。

---

# 25. 内部 AI 查询

普通问题：

```text
Web AI
↓
Google Drive
↓
20_data / 10_sources / 40_ai
↓
answer
```

不需要每次走 GitHub。

复杂问题：

```text
Web AI
↓
trigger analysis workflow
↓
DuckDB / Python
↓
report / artifact / Drive output
↓
Web AI reads result
```

---

# 26. MCP / REST 的优先级

当前不作为 blocker。

先完成：

```text
Identity
Stats
Claims
Profiles
Cloud Runtime
Public Snapshot
```

以后有跨客户端稳定工具调用需求：

```text
再做 MCP
```

有真实：

```text
commercial realtime
high concurrency
SLA
```

再做 REST / hosted DB。

---

# 27. GitHub Actions 失败语义

禁止长期：

```text
continue-on-error
→ everything green
```

推荐状态：

```text
success
partial_failure
failed
review_required
```

某 source adapter 失败：

```text
不更新该 source 下游
其他独立 source 可以继续
run summary 明确 partial_failure
```

---

# 28. Phase Roadmap

## Phase A — Cloud Runtime Gate

这是当前第一优先级。

完成：

```text
Private GitHub repo
↓
import current scripts / schemas / configs
↓
00_validate.yml
↓
drive_auth
↓
GitHub Secrets
↓
workflow_dispatch
↓
read one Drive test file
↓
write one test report to Drive
↓
verify log / permission / file ID
```

通过才表示：

```text
Web-only production execution 已成立
```

---

## Phase B — Identity Bridge

```text
discover cba.net XHR
↓
20–50 player canary
↓
review
↓
486 batch
↓
backfill player_uid to 1019 registrations
↓
UID-based club changes
```

---

## Phase C — Stats

```text
cba.net profile
season stats
career stats
game stats
```

---

## Phase D — Claims

```text
source registry
mention resolution
claim extraction
time anchoring
review
```

---

## Phase E — Profiles

```text
build_profiles
→ 40_ai/profiles
```

---

## Phase F — Public Snapshot

```text
public export model
publish workflow
public repo PR
manual merge
```

---

## Phase G — Optional B2

按容量 / 许可隔离真实需求启用。

---

## Phase H — MCP / API

有真实需求后再做。

---

# 29. 当前每个 Drive 文件夹在 v2.1 的职责

## `00_inbox_待处理`

人：

```text
把新 PDF / XLSX / DOCX / image 放这里
```

AI：

```text
pending / unverified
```

不能作为事实回答。

---

## `10_sources_原始证据`

人：

```text
保存原始 evidence
```

AI：

```text
用于 provenance / audit
```

微信原文不自动公开。

---

## `20_data_结构化事实`

核心：

```text
registrations
identity
contracts
stats
source registry
claims metadata
reviews
```

精确查询优先这里。

---

## `30_notes_人工知识`

只放：

```text
人工研究
人工判断
Obsidian notes
```

不放机器 Profile。

---

## `40_ai_投喂与索引`

```text
INDEX
bundles
profiles
generated AI views
```

可重建，不是真相源。

---

## `50_scripts_自动化脚本`

v2.1 过渡层：

```text
Drive 中历史 / 镜像代码
```

正式源码逐步迁入 Private GitHub。

---

## `60_config_配置与词表`

Drive 继续保存内部 control metadata：

```text
drive_map
manifest
taxonomy
```

Private GitHub 保存可版本化 schema / config source。

---

## `70_tools_辅助工具`

clipper / browser helper。

---

## `90_archive_历史归档`

snapshot / rollback / audit。

不参与当前自动执行。

---

# 30. AI Prompt：新开发会话

```text
你正在开发 CBA-KB v2.1。

先读取：
1. 00_README_先读这个
2. 02_AI_PROJECT_CONTEXT_技术手册.md
3. CBA-KB v2.md
4. CBA-KB v2.1.md
5. 40_ai_投喂与索引/INDEX

然后实际检查 Google Drive 和 Private GitHub。

实施规则：
- Drive = Data Truth
- Private GitHub = Code Truth
- GitHub Actions = Execution Truth
- Public GitHub = Published Snapshot
- 正式流程不依赖本地 Python / token.json
- 查询球员先 resolve player_uid
- current 486 UID 是 provisional seed
- facts / claims / profiles 分层
- 对外发布只能通过 public PR + human merge

不要把文档中的 planned 项写成已实现。
```

---

# 31. AI Prompt：Cloud Runtime Gate

```text
目标：验证 CBA-KB v2.1 的 Web-only 正式执行环境。

请：
1. 检查 Private GitHub repo 当前状态
2. 检查 drive_auth 实现
3. 检查 GitHub Secrets 所需变量名，不输出 secret 值
4. 运行 workflow_dispatch dry-run
5. 只读取 Drive 中一个指定测试文件
6. 写一个最小 test report 到指定测试位置
7. 验证 Drive file ID / parent / permission
8. 输出 workflow URL / commit / result summary

在这一步成功前，不运行 identity batch、inbox batch 或 public publish。
```

---

# 32. AI Prompt：Identity Canary

```text
使用 CBA-KB v2.1 规则做 identity bridge canary。

不要一次回填 486 人。

选 20–50 人，覆盖：
- 三季连续
- 换队
- 两字名
- 音译名
- 单季
- 可能无 playerId

输出：
player_uid
season
name
club_id
external playerId
match evidence
identity_status recommendation
review reason

任何真正歧义写入 review_queue；需要人工操作时创建/更新 GitHub Issue。
```

---

# 33. AI Prompt：内部球员查询

```text
请先 resolve 球员到 player_uid。

然后按顺序查询：
1. 20_data 的身份 / 注册 / 合同
2. 官方 stats（若已接入）
3. player_claims
4. 40_ai profile
5. 需要核验时回到 10_sources

回答分成：
- 当前事实
- 生涯 / 球队轨迹
- 统计
- 人物故事 / 特点
- 待核 / 冲突
- sources / as_of / coverage

媒体 claim 不得写成官方事实。
```

---

# 34. AI Prompt：对外发布

```text
准备 CBA-KB Public Snapshot。

禁止直接 push public main。

步骤：
1. 读取 Drive 指定 snapshot
2. 按 public allowlist 导出
3. 检查 licensing_status
4. 排除 internal_only / private / raw copyrighted content
5. 检查 premium_flag
6. 运行 schema validation
7. 运行 leak detector
8. 生成 diff / README / release manifest
9. 创建目标 Public GitHub repo PR
10. 停止，等待人工 review / merge

未人工 merge 前，不视为正式发布。
```

---

# 35. 不可妥协规则

1. Drive 是数据真相源。
2. Private GitHub 是代码真相源。
3. GitHub Actions 是正式执行环境。
4. Public GitHub 只是发布快照。
5. 本地 Python 不是生产依赖。
6. `player_uid` 是内部稳定实体主键。
7. 外部 `playerId` 不能替代 `player_uid`。
8. `cba_registrations` 当前直接增加 `player_uid`，不创建双主表。
9. Identity Resolution 与 Mention Resolution 分开。
10. GitHub Issues 是 Review UI，不是长期业务数据库。
11. Facts / Claims / Profiles 分层。
12. Source authority / extraction / verification 分开。
13. 回答“现在”必须考虑时间锚定。
14. OCR / vision 不能无标记进入官方事实层。
15. B2 不是关系数据库。
16. B2 当前是可选 archive，不是 Phase A blocker。
17. Private Drive 不自动镜像 Public GitHub。
18. Public 发布必须 PR + human merge。
19. 微信原文 / 私有 source archive 不进入公开数据包。
20. v2.1 开发不得破坏已验证的 1,019 条事实基线。

---

# 36. 当前唯一优先路径

当前不要先做：

```text
486 identity batch
B2 全量迁移
MCP
REST API
付费系统
复杂多 Agent
```

当前只做：

```text
Phase A Cloud Runtime Gate
```

即：

```text
Private GitHub
↓
Drive OAuth / refresh token
↓
Actions
↓
read Drive
↓
write test report
↓
logs / audit
```

通过后再进入 Identity Bridge Canary。

这一步是 CBA-KB v2.1 从“架构设计”变成“Web-only 可持续工程系统”的真正分水岭。

