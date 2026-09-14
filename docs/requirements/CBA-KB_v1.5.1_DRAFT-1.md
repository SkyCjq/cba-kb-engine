# CBA-KB v1.5.1 — Registration Facts Extension / 注册事实扩展

> **文档版本**：v1.5.1 DRAFT-1\
> **方案制定时间**：2026-09-10\
> **状态**：PLANNED / PENDING USER APPROVAL / NOT EXECUTED\
> **当前生产基线**：v1.1 FINAL（3,451 行国内球员 MASTER）；v1.5 正在实施，尚未完成 production switch\
> **实施顺序**：v1.1 FINAL → v1.5.0（运行/发布基础设施收口）→ v1.5.1（本方案）→ v2.1 Unified Target\
> **本文件用途**：需求备份、技术实施依据、测试与验收依据、回退依据、版本溯源。\
> **重要约束**：本文件批准前，不修改 `20_data_结构化事实`、`source_registry.csv`、生产 MASTER、GitHub production branch 或 Drive 生产发布状态。

---

## 1. 背景与版本动机

v1.1 已把 2017–2027 国内球员注册资料收敛为：

- `20_data_结构化事实/CBA_2017-2027_国内球员注册_MASTER.xlsx`
- 单 tab `MASTER`
- 3,451 条记录
- 3,451 个唯一 `record_key`
- 当前事实 grain：**已收集并结构化的 `season + club + domestic player` 注册/名单关系**

v1.5 的主要任务是建立本地执行、Private GitHub Code Truth、MASTER-aware builder、受控候选发布、冲突阻断、原 file ID 更新、回读与回退。当前 v1.5 仍处于 IN_PROGRESS；生产仍以 v1.1 FINAL 为准。

2026-09-10 在 `00_inbox_待处理` 新增四份来源：

1. `八一球员_2021赛季中期转会核验.md`
2. `2024-2025赛季CBA联赛外籍球员注册信息（3月31日上午10点注册截止）.xlsx`
3. `2020-2021赛季CBA联赛外籍球员注册信息.png`
4. `2022-2023赛季CBA联赛外籍球员注册信息（截至4月5日）.png`

这些来源暴露了 v1.1/v1.5 现有事实模型的两个新维度：

- **赛季内注册事件（event）**：转会、认领、取消注册等带事件日期的事实；
- **外籍球员注册快照（snapshot）**：英文名、中文名、国籍、位置、球衣号，以及隐藏在备注区的取消注册事件。

因此本版本不是单纯的“新增 parser”，而是一次窄范围的 **Registration Fact Compatibility / 注册事实兼容扩展**。

---

# Part A — 版本需求（Requirements）

> 本节是需求真相源。需求与后文技术实现分开；技术方案可以调整，但不得静默改变本节需求。

## 2. 功能需求

### R1. 新来源必须进入现有 Register-first 管线

四份新增来源必须先登记 `60_config_配置与词表/source_registry.csv`，再抽取、校验、生成候选事实；不得仅凭“文件是否仍在 inbox”判断处理状态。

最低 source 状态：

`DISCOVERED → EXTRACTED → IMPORTED → VERIFIED`

物理移动到 `10_sources_原始证据` 只能发生在对应来源已完成登记和验收之后，且不作为抽取前置条件。

### R2. 国内赛季中期注册关系必须补齐，但不能丢失事件时间

`八一球员_2021赛季中期转会核验.md` 当前包含 14 条人工交叉核验记录。

需求：

- 对当前国内 MASTER 中不存在的 `season + club_id + player` 关系，允许作为新增国内注册关系候选；
- `transaction_date`、`club_announcement_date` 等事件日期不得压入 `remarks` 后丢失结构；
- 赛季关系与事件事实必须分别建模；同一来源可以同时产出 roster/registration relation 和 event fact；
- 执行时必须重新检查 14 个 `record_key` 与 live MASTER 的碰撞情况，不依赖 2026-09-10 规划阶段的检查结果。

### R3. 八一 `notice_deadline` 不得误映射为球员公示截止时间

该 MD 中 `2021-02-27` 的语义为 **该赛季中期国内球员注册窗口截止日**。

必须映射为：

`registration_window_deadline`

不得直接写入国内 MASTER 的 `notice_deadline` / `disclosure_deadline`，避免将联盟窗口截止日误写成某名球员的公示截止时间。

### R4. 外籍球员 roster 与取消注册 events 必须分开

2024–2025 实际 XLSX 已确认包含：

- 20 支球队；
- 73 条截止 2025-03-31 10:00 的在册外援快照；
- 59 条取消注册事件；
- 两组英文名无重叠，因此该来源至少观察到 132 个外援注册人次。

需求：

- 73 条进入 `foreign registration snapshot` 数据集；
- 59 条进入 `registration events` 数据集；
- 不得只导入前半部分而丢弃备注区事件；
- 不得将 snapshot date 推断成每名球员的 registration date。

### R5. 外籍球员字段必须保留原始值和规范化值

至少支持：

- `name_en_raw`
- `name_en_normalized`
- `name_zh_raw`
- `nationality`
- `position`
- `jersey_number`
- `club_source_name`
- `club_id`
- `snapshot_as_of`
- source/provenance 字段

英文名可能含 `JR`、`Ⅲ`、连字符、NBSP、多空格等；原始值不得被规范化结果覆盖。

### R6. 球衣号码必须按文本保留

例如 `01` 必须保持 `"01"`，不得被 Excel/Pandas 自动转换成 `1`。

### R7. 外援取消事件的年份推断必须显式留痕

2024–2025 来源备注只给“月/日”，不给年份。

规则：

- 8–12 月 → 2024
- 1–3 月 → 2025

且每条由赛季边界补出的年份必须标记：

`date_year_inferred = true`

不得把推导值伪装成源文件原文事实。

### R8. 球队名称必须统一映射到稳定 `club_id`

外籍来源使用赛季冠名商名称，和国内来源名称大量不同。必须建立/扩展 season-aware `club_aliases`，至少区分：

- `official_domestic`
- `official_foreign_sponsor`
- `media`
- `defunct/historical`

未知冠名商在 production / strict 模式下必须 fail-closed，禁止模糊匹配后静默入库。

八一规则：

- `bayi` 是历史有效 `club_id`；
- 状态为 `defunct`；
- 解散前历史赛季允许作为 roster club；
- 解散后不得创建新的 active-season roster；
- 不能把 `defunct` 解释成“历史上从未允许作为 club_id”。

### R9. v1.5.1 不建立永久球员身份主键

外援英文名规范化只能作为 provisional matching key；不得把英文名直接固化为永久身份主键。

永久内部身份仍由 v2.1 的 `player_uid` 解决。

### R10. verification 与 source authority 暂不做全库大迁移

v1.5.1 保持与 v1.1/v1.5 兼容：

- `verification_level` 表示当前核验状态；
- `source_type` / provenance 保留来源性质；
- 八一人工交叉核验可规范为 `human_verified`，同时保留真实 source URLs 和来源类型；
- 不把整份 MD 统一粗暴标成一个来源等级，因为其中来源强度不完全一致。

`verification_status × source_authority` 双轴正式标准化留给 v2.1。

### R11. 事实产品必须按 grain 分开

v1.5.1 发布后，`20_data_结构化事实` 采用“一种 grain 一个 Final Fact Product”，禁止生成一张大量空值、语义混乱的万能宽表。

目标产品：

1. `CBA_2017-2027_国内球员注册_MASTER.xlsx`
2. `CBA_外籍球员注册_SNAPSHOTS.xlsx`
3. `CBA_球员注册_EVENTS.xlsx`

三者均建议保持单文件、单 tab、明确 grain。

### R12. 发布必须复用 v1.5 的受控发布能力

生产写入不得绕过 v1.5 的：

- baseline freeze
- candidate build
- conflict detection
- publish journal
- stable Drive file ID（已有文件）
- readback validation
- rollback snapshot

若 v1.5.0 尚未完成 production switch，则 v1.5.1 只允许开发/staging，不允许写入生产 `20_data`。

---

## 3. 非目标 / Out of Scope

v1.5.1 不实施：

- `player_uid`
- cba.net external ID bridge
- official stats
- Claim Ledger
- Player Profiles
- MCP / REST API
- Public GitHub dataset publication
- Backblaze B2
- 全量 verification 双轴 schema migration

这些继续属于 v2.1 Unified Target。

---

# Part B — 技术方案（Technical Design）

## 4. 目标架构

```text
00_inbox_待处理
        ↓ Register-first
60_config/source_registry.csv
        ↓
SourceAdapter
├─ midseason_transfer_md
├─ foreign_registration_xlsx
└─ foreign_registration_image
        ↓
staging / candidates
        ↓
validation + strict club resolution
        ↓
┌─────────────────────────────────────────┐
│ 20_data_结构化事实                       │
│ 1. 国内球员注册 MASTER                  │
│ 2. 外籍球员注册 SNAPSHOTS               │
│ 3. 球员注册 EVENTS                       │
└─────────────────────────────────────────┘
        ↓
40_ai MASTER-aware derived views
```

`10_sources_原始证据` 继续保存最终已处理来源；`90_archive` 保存发布前快照、QA、OCR 过程材料和 rollback。

## 5. 数据模型

### 5.1 国内注册 MASTER

现有 `record_key` 继续：

`season|club_id|player`

八一 14 条处理规则：

- live MASTER 无同 key：新增 relation candidate；
- live MASTER 有同 key：禁止重复 append，改做字段/provenance 比对；
- `transaction_date` / `club_announcement_date` 不加入 roster key；
- `registration_window_deadline` 不写入现有 `notice_deadline`；
- 国内 MASTER 保留其原有 fact grain。

以当前 3,451 baseline 且 14 条继续全部无碰撞为前提，候选行数预计为 **3,465**；该数字仅为规划预期，不是验收前的生产事实。

### 5.2 外籍球员 SNAPSHOTS

建议字段：

```text
snapshot_key
season
snapshot_as_of
club_id
club_source_name
name_en_raw
name_en_normalized
name_zh_raw
nationality
position
jersey_number
status_at_snapshot
source_file_id
source_url
source_page_or_row
source_type
extraction_method
verification_level
raw_row_text
```

`snapshot_key` 建议：

`season|snapshot_as_of|club_id|name_en_normalized|nationality`

它是 snapshot 唯一键，不是永久 player identity。

### 5.3 Registration EVENTS

建议字段：

```text
event_key
season
player_type
club_id
club_source_name
player_name_zh
player_name_en_raw
player_name_en_normalized
nationality
event_type
event_date
date_year_inferred
from_club_id
from_club_source_name
registration_method
contract_category
contract_term_official
club_announcement_date
registration_window_deadline
registration_status
notes
source_file_id
source_url_primary
source_url_secondary
source_page_or_row
source_type
extraction_method
verification_level
raw_event_text
```

初始受控 `event_type` 至少：

- `registration_change`
- `registration_cancelled`

未来新增类型必须修改 taxonomy/config，不允许自由文本静默膨胀。

`event_key` 在 v1.5.1 暂采用可解释 provisional 组合键，例如：

`season|player_type|club_id|provisional_player_key|event_type|event_date|sequence`

其中 domestic provisional key 可用规范化中文名；foreign provisional key 使用 `name_en_normalized + nationality`。v2.1 引入 `player_uid` 后再迁移关联。

## 6. SourceAdapter 设计

统一接口建议：

```text
probe(source)
extract(source)
normalize(records)
validate(records)
emit_staging(records)
```

### 6.1 `midseason_transfer_md`

解析 Markdown 中的记录块，抽取：

- record_key
- season / club_id / player
- registration fields
- transaction_date
- club_announcement_date
- registration_window_deadline
- primary / secondary source URL
- verification metadata

字段兼容映射：

- `registration_stage` → 当前国内 MASTER 对应 registration-stage/type 字段
- `former_club` → 当前 former-club 字段
- `remarks` → notes/remarks
- `manually_verified` → `human_verified`

禁止映射：

- `notice_deadline` → `disclosure_deadline`（禁止）

### 6.2 `foreign_registration_xlsx`

必须支持：

- 两行合并表头；
- 球队合并单元格/空白行 forward-fill；
- roster 区与备注 event 区自动分段；
- `jersey_number` 文本保持；
- 英文名 Unicode/NBSP/多空格规范化且保留 raw；
- 取消注册自然语言 parser；
- season-aware year inference；
- `--strict-clubs`。

### 6.3 `foreign_registration_image`

2020–2021、2022–2023 PNG 接入现有 OCR 能力：

- PaddleOCR doc parsing / layout-table extraction；
- 对低置信度姓名、序号、球队和异常行做 targeted recognition；
- 复用 XLSX adapter 的 normalization / club alias / event segmentation；
- OCR 自动通过不得升格为 official/human verified。

## 7. `club_aliases` 配置

建议纳入 `60_config_配置与词表/club_aliases.yaml`，按 season 分层：

```yaml
clubs:
  ningbo_fubang:
    official_domestic:
      - 宁波富邦
    official_foreign_sponsor_by_season:
      2024-2025:
        - 宁波町渥

  beijing_shougang:
    official_domestic:
      - 北京首钢
    official_foreign_sponsor_by_season:
      2024-2025:
        - 北京北汽

  bayi:
    status: defunct
    historical_roster_allowed: true
    post_dissolution_new_roster_forbidden: true
    names:
      - 八一
      - 八一队
      - 八一富邦
```

要求：

- production 默认 strict；
- 未知名称报错并输出待补 alias；
- 禁止未经证据的高风险别名，例如不能把无关球队昵称映射到八一；
- alias 修改必须有回归测试。

## 8. source_registry / manifest

### source_registry

四份新增 source 在执行后应登记：

- source_id / drive file ID
- source title
- season
- source role
- extraction adapter
- current parent
- processing status
- records emitted by dataset type
- validation status
- processed_at

一份 source 可产生多个事实集，例如 2024–2025 外援 XLSX：

- 73 snapshot rows
- 59 event rows

### manifest

继续只负责：

- Drive file ID
- local/remote path
- content/semantic hash
- sync status
- published release

不得用 manifest 替代 source processing state。

## 9. v1.5 发布集成

v1.5.1 不新造一套 uploader。

发布顺序：

```text
pull/freeze live baseline
→ parse to staging
→ validate
→ build frozen candidates
→ compare remote baseline
→ archive before snapshot
→ publish existing/new fact products
→ download/readback
→ publish derived INDEX/40_ai
→ update registry/manifest/release_status
```

已有国内 MASTER 必须保持当前 Drive file ID 原地更新；新 `SNAPSHOTS` 与 `EVENTS` 首次创建后把 file ID 固化进 manifest/drive map。

---

# Part C — 测试方案（Test Plan）

## 10. 测试原则

- 真实来源优先于手写 mock；
- fixture 可用于单元测试，但不得把小型结构 fixture 的通过结果表述成 full-source acceptance；
- production 发布前必须对真实 XLSX / PNG / Markdown 跑完整验收；
- 所有 production gate fail-closed。

## 11. 单元测试

### T1. Markdown parser

真实/冻结 fixture：`八一球员_2021赛季中期转会核验.md`

断言：

- 14 records；
- 必填 `transaction_date` 可解析；
- `club_announcement_date` 可独立于 transaction date；
- `registration_window_deadline=2021-02-27`；
- 不写入 `disclosure_deadline`；
- `manually_verified` 规范为 `human_verified`；
- source URLs 保留。

### T2. XLSX header / forward-fill

断言：

- 两行表头正确合并；
- `KOUAT NOI / 库阿特·诺伊 / 澳大利亚 / PF / 23` 列不串位；
- 同队后续空白球队单元格正确 forward-fill。

### T3. text preservation

断言：

- `01` 仍为字符串；
- NBSP / 多空格规范化只影响 normalized 字段；
- raw 字段保持源值。

### T4. club resolution

断言：

- 已登记 sponsor 名全部 exact resolve；
- 未知 sponsor 在非 strict 可告警并留空；
- strict mode 直接失败；
- `bayi` 历史赛季允许，解散后新 active roster 禁止。

### T5. year inference

2024–2025：

- 8–12 月 → 2024
- 1–3 月 → 2025
- `date_year_inferred=true`

## 12. 真实来源验收

### A1. 2024–2025 外援 XLSX

必须精确得到：

- teams = **20**
- roster snapshots = **73**
- cancellation events = **59**
- observed registrations = **132**（仅该来源内的已观察人次口径）

数字不符即停止，不允许为了匹配预期而手工删行。

### A2. 八一中期转会 MD

必须得到：

- parsed records = **14**
- 执行时重新与 live domestic MASTER 做 exact `record_key` collision 检查；
- 若 live baseline 仍为 3,451 且 14 条继续全部未命中，则 candidate domestic MASTER = **3,465**；否则按真实 collision report 决定，不硬凑 3,465。

### A3. PNG OCR

2020–2021、2022–2023 分别生成：

- OCR/layout report
- low-confidence review list
- roster/event segmentation report
- source-level counts

在没有完成 source-specific QA 前，不在技术方案中预填最终人数。

## 13. 数据完整性测试

### D1. 国内 MASTER

- `record_key` 唯一；
- 原 3,451 baseline 行的非目标字段不被意外改变；
- 八一新关系的无证据字段保持空白；
- Jia Hao 05:00 回归仍通过。

### D2. 外籍 SNAPSHOTS

- `snapshot_key` 唯一；
- 每行 season / snapshot / club / English raw name / provenance 非空；
- jersey number 无 numeric coercion。

### D3. EVENTS

- `event_key` 唯一；
- cancellation event 必须有 event_date；
- inferred year 必须留痕；
- Bayi transaction date 与 club announcement date 分离；
- event 不与 roster snapshot 混作同一 grain。

### D4. Provenance

每个 fact 至少能回溯到：

- Drive source file ID 或官方/网页 source URL；
- source row/page/raw text 中至少一项；
- extraction method；
- verification level。

## 14. 发布与故障测试

复用 v1.5 release tests：

- stale remote baseline → BLOCK
- duplicate publish → idempotent/no duplicate
- partial write → FAILED / recoverable
- response lost → retry without duplicate
- candidate tamper → BLOCK
- type mismatch → BLOCK
- readback mismatch → rollback
- rollback restores prior MASTER bytes/semantic content and control files

生产发布必须有 release report。

---

# Part D — 实施步骤与 Gate

## 15. 实施前 Gate 0

**v1.5.0 未完成 production switch 时：**

允许：

- 新 branch / feature code
- parser/config 开发
- fixture/unit test
- local staging

禁止：

- 修改 production `20_data`
- 把 v1.5.1 标为 IMPLEMENTED
- 迁移 v2.1 player_uid

v1.5.0 完成并确认 release tag 后，才进入 v1.5.1 production rollout。

## 16. 执行顺序（用户批准后）

1. **Freeze baseline**：重新读取 MASTER、source_registry、manifest、v1.5 release status；记录 SHA/semantic hash、行数、file IDs。
2. **Register sources**：四个新文件登记为 `DISCOVERED`。
3. **Config**：创建/修正 `club_aliases.yaml`，跑 strict-club regression。
4. **Adapters**：完成 MD / XLSX / image adapters。
5. **Staging**：真实来源全量解析，不写 production。
6. **QA**：73+59、14 records、PNG source-specific QA、collision report、provenance report。
7. **Candidate build**：生成三个 fact products 的候选版本。
8. **Release gate**：run full tests；检查 remote baseline 未变化。
9. **Publish**：走 v1.5 publisher，先 snapshot 后写入。
10. **Readback**：逐文件下载回读、行数/键/provenance/semantic hash 验证。
11. **Archive sources**：已成功处理来源移动/整理到 `10_sources_原始证据`，registry 保留状态历史。
12. **Derived layer**：用 MASTER-aware builder 更新 INDEX/40_ai。
13. **Docs**：更新 README、AI Context、Notion 总方案和 v1.5.1 指南。
14. **Tag/closeout**：生成 v1.5.1 release report；通过后标 `v1.5.1 STABLE`。

---

# Part E — 回退预案（Rollback）

## 17. 回退对象

发布前必须冻结：

- 当前国内 MASTER 原字节 + semantic hash + file ID；
- `source_registry.csv`；
- `manifest.csv`；
- `drive_map.yaml` / `club_aliases.yaml`；
- 当前 `40_ai` 派生文件；
- release status；
- 新建 fact product 的预发布清单。

备份进入：

`90_archive_历史归档/releases/<v1.5.1-release-id>/before/`

## 18. 回退触发条件

任一情况触发停止/回退：

- live baseline 在 publish 前发生未预期变化；
- 真实 XLSX 不满足 73 + 59；
- strict club unresolved；
- `record_key` / `snapshot_key` / `event_key` 非预期重复；
- 原 3,451 国内 baseline 非目标字段被改写；
- readback hash/semantic comparison 失败；
- control files 与 fact products 发布不一致；
- source provenance 丢失。

## 19. 回退动作

- 国内 MASTER：用 before snapshot 原地恢复同一 Drive file ID；
- 新 SNAPSHOTS / EVENTS：不破坏性删除；标记 release `ROLLED_BACK`，移动到对应 archive/release 区或从 current manifest pointer 移除；
- 恢复 source_registry / manifest / config / INDEX；
- release_status 标记 `ROLLED_BACK` 并记录原因；
- 保留失败 candidate、diff、日志用于审计。

---

# Part F — 风险与控制

## 20. 已知风险

| 风险 | 影响 | 控制 |
|---|---|---|
| v1.5 尚未正式上线 | 两个未完成版本并行 | v1.5.1 production Gate 0 |
| 外援 sponsor 名每季变化 | club 错映射 | season-aware aliases + strict fail-closed |
| 备注事件无年份 | 事件日期失真 | season inference + `date_year_inferred` |
| 英文名 Unicode/后缀/空格 | 假重复或错人 | raw + normalized 双列；不当永久主键 |
| snapshot 与 event 混表 | 语义混乱 | one-grain-one-product |
| 八一 window deadline 语义误写 | 错误公示时间 | 独立 `registration_window_deadline` |
| OCR 图片误识别 | 外援姓名/号码错误 | PaddleOCR + targeted review + verification boundary |
| 旧脚本回写 pre-MASTER 结构 | 破坏新事实层 | 继续 fail-closed，使用 v1.5 MASTER-aware builder |
| 小 fixture 测试被误当真实验收 | 假绿 | real-source acceptance gate |

---

# Part G — 版本改动点与溯源

## 21. 相比 v1.5.0 的新增能力

v1.5.1 只新增注册事实兼容能力：

- season-mid registration event modeling；
- foreign registration snapshot modeling；
- cancellation event parsing；
- season-aware sponsor club aliases；
- `registration_window_deadline`；
- foreign raw/normalized name fields；
- `snapshot_key` / `event_key`；
- foreign XLSX + image adapters；
- real-source acceptance tests。

不改变四个 Truth，不提前实现 v2.1 identity/claims/stats。

## 22. 当前已知源数据基准（规划时）

- 国内生产 MASTER：3,451 rows / 3,451 unique record_key；
- 八一中期转会 MD：14 structured records；
- 2024–2025 外援 XLSX：73 active snapshot rows / 20 clubs / 59 cancellation events；
- 2020–2021 外援：PNG，最终人数待 source-specific OCR QA；
- 2022–2023 外援：PNG，最终人数待 source-specific OCR QA。

以上为 2026-09-10 方案制定时基准；执行前必须重新实读，不得直接当未来 live truth。

## 23. 决策记录

**Decision D1**：采用 v1.5.1，而不是把这批数据直接塞进 v1.5.0 或推迟到 v2.1。\
理由：新数据改变 registration facts 的 grain 与 subject 范围，但不需要 player_uid；独立窄版本可在 identity 前稳定 registration universe，同时避免扩大 v1.5.0 的基础设施收口范围。

**Decision D2**：v1.5.0 未收口前只做 staging，不生产发布 v1.5.1。\
理由：避免两个未完成 production release 并行。

**Decision D3**：不做超级 MASTER。\
理由：domestic roster relation、foreign snapshot、registration event 是不同 grain；一表硬合会产生大量空值和时间语义歧义。

**Decision D4**：永久 `player_uid` 后置 v2.1。\
理由：v1.5.1 只提供稳定事实输入和 provisional matching，不重复实现身份层。

---

# Part H — 完成定义（Definition of Done）

## 24. v1.5.1 可标记 STABLE 的条件

必须同时满足：

- v1.5.0 production infrastructure 已正式验收；
- 4 个 source 已登记并处理；
- 2024–2025 foreign XLSX 真实全量 = 73 roster + 59 cancel events + 20 clubs；
- Bayi 14 records 真实解析通过；
- PNG 两季均完成 source-specific OCR QA；
- 三类 fact product 通过唯一键、provenance、语义和 readback 验证；
- production publish/rollback 演练通过；
- README / AI Context / INDEX / Notion 文档同步完成；
- release report 保存到 `90_archive`；
- Git tag `v1.5.1` 创建并与 Drive release ID 对齐。

在以上条件完成前，状态只能是：

`PLANNED / IN_PROGRESS / CANDIDATE`

不得写成 `IMPLEMENTED / STABLE`。

---

# Part I — 用户确认点

本方案目前只完成设计与文档化，**尚未执行生产数据变更**。

用户确认后再执行，确认内容包括：

1. 同意增加 `v1.5.1` 作为 v1.5.0 与 v2.1 之间的窄范围兼容版本；
2. 同意 `20_data` 由 1 个最终事实产品扩展为 3 个、按 grain 分离；
3. 同意八一 14 条按“国内 relation + event”双输出处理；
4. 同意外援 73 roster + 59 cancellation events 分开建模；
5. 同意 v1.5.0 未收口前不向 production 发布 v1.5.1；
6. 同意 v1.5.1 不提前实施 `player_uid`，身份层继续留在 v2.1。
