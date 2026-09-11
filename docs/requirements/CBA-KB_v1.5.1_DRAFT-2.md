# CBA-KB v1.5.1 DRAFT-2 — Registration, In-season Movement & Foreign-player Rights Extension
# 注册、赛季中人员流动与外援权利事实扩展

> **文档版本**：v1.5.1 DRAFT-2\
> **原方案形成时间**：2026-09-10\
> **本文件补建时间**：2026-09-10\
> **文档性质**：需求/技术/测试基线补建，用于版本溯源\
> **历史说明**：DRAFT-2 当时已在 Notion 与方案讨论中形成，但没有以独立、唯一文件名写入 Drive；Drive 中同名 `CBA-KB_v1.5.1.md` 实际仍是 DRAFT-1。本文补建 DRAFT-2，不改写历史，也不声称 DRAFT-2 已经作为当时实际实施依据。\
> **版本边界**：本 DRAFT-2 目标是完整化 Registration / In-season Movement / Foreign-player Rights 事实域；`player_uid / identity / stats / Claim Ledger / profiles` 继续留给 v2.1。\
> **重要原则**：需求、技术方案、测试方案、实施状态必须分开。`planned` 不得写成 `implemented`。

---

# 0. 为什么需要 DRAFT-2

DRAFT-1 的核心输入是：

1. `八一球员_2021赛季中期转会核验.md`
2. `2024-2025赛季CBA联赛外籍球员注册信息（3月31日上午10点注册截止）.xlsx`
3. 2020-2021 外籍注册 PNG
4. 2022-2023 外籍注册 PNG

DRAFT-1 因此只定义了三类事实产品：

- 国内注册关系 MASTER
- 外籍注册 SNAPSHOTS
- 注册 EVENTS

随后新增两份更高层、跨赛季的合并核验资料：

- `CBA_赛季中国内球员人员流动_2019-2026_合并核验总表_v2.md`
- `CBA_外籍球员优先续约权_交易_注册信息_二次核验完整修正版_2019-2027.md`

它们暴露出 DRAFT-1 无法完整表达的事实类型：

- 国内赛季中交易窗口
- 自由球员认领、球员互换、解除关系后认领
- 未完成 / pending / system error / correction / retraction
- 外援多赛季注册快照与取消注册
- 2019-2020 复赛期间外援暂停/启用状态
- 外籍球员优先续约权状态
- 优先续约权真实转移
- “优先续约权交易信息”动态状态快照
- 跨赛季球队冠名和球员译名变体

因此 DRAFT-2 不再是“多加几个 parser”，而是把 **注册事实域** 的 grain 正式拆清楚。

---

# Part A — 版本需求（Requirements）

> 本部分是 DRAFT-2 的需求真相源。技术实现可以优化，但不得静默删减或改写本部分语义。

## 1. 总体目标

v1.5.1 DRAFT-2 必须能够无损表达：

1. 国内球员某赛季某俱乐部的注册/名单关系；
2. 国内赛季中的交易/认领窗口；
3. 国内与外援的注册、取消、流动、使用状态、纠错等事件；
4. 外援在某个官方/权威快照时点的注册状态；
5. 外援优先续约权在某目标赛季、某快照时点的持有与状态；
6. 外援优先续约权真实转移事件。

不得把不同 grain 强行塞入同一宽表。

## 2. R1 — Register-first 仍是所有新来源入口

任何新增 source：

```text
00_inbox_待处理
→ source_registry
→ extract / normalize
→ staging
→ validation
→ candidate
→ controlled publish
→ 10_sources_原始证据
```

规则：

- 文件物理位置不代表 processing status；
- `source_registry.csv` 是 source processing truth；
- `manifest.csv` 只负责同步/hash/file ID；
- staging 成功不等于 IMPORTED；
- production publish 完成后必须回写 source processing 状态。

## 3. R2 — 国内赛季中流动按“关系 + 事件”双层建模

对于完成新注册/认领且当前国内 registration relation 不存在的球员：

- 允许补入 `domestic_registrations`；
- 同时必须保存一条或多条 `registration_status_events`；
- 事件时间、官宣时间、提交时间、完成时间不得压进 remarks 后丢失结构；
- 不能用一次 roster relation 代替整个 event lifecycle。

原八一 14 条保留为 **parser fixture / supporting evidence**，但不再作为国内赛季中流动完整性总验收。

## 4. R3 — 国内交易窗口必须独立建模

新增 `domestic_transaction_windows`。

一行代表：

```text
season × window_no
```

至少支持：

- `season`
- `window_no`
- `window_start`
- `window_end`
- `allowed_methods`
- `ordinary_transfer_allowed`
- `loan_allowed`
- `rule_source_id`
- `verification_status`
- `completeness_status`

必须能区分：

- 官方确实没有窗口；
- 有窗口但无完成交易；
- 有窗口但数据尚不完整。

`0 event` 不得自动解释为“没有窗口”。

## 5. R4 — registration_status_events 必须支持“发生、未完成、纠错、撤回”

统一使用三层字段：

```text
event_domain
event_type
event_status
```

建议 `event_domain` 至少：

- `domestic_movement`
- `foreign_registration`
- `foreign_usage`
- `correction`

建议 `event_type` 至少支持：

- `free_agent_claim`
- `player_swap`
- `release_then_claim`
- `status_change_only`
- `rumor_not_completed`
- `registration_change`
- `registration_cancelled`
- `usage_suspended`
- `usage_activated`
- `status_correction`

建议 `event_status` 至少：

- `confirmed`
- `pending`
- `not_completed`
- `corrected`
- `retracted`
- `system_error`

多人互换必须支持 `transaction_group_id`，避免“四名球员事件”被统计成“四笔交易”。

纠错/撤回必须支持：

- `supersedes_event_key`
- `correction_reason`

不得通过删除历史事件来“修正”历史。

## 6. R5 — 日期字段与日期状态字段分离

禁止：

```text
registration_submission_date = pending_official_snapshot
```

正确做法：

```text
registration_submission_date = NULL
registration_submission_date_status = pending_evidence
```

日期状态建议：

- `known`
- `pending_evidence`
- `unknown`
- `not_applicable`

同样适用于：

- `registration_completed_date`
- `club_announcement_date`
- 其他可能存在待证据状态的日期

## 7. R6 — 八一窗口截止日语义保持独立

`2021-02-27` 在八一专项材料中表示：

```text
registration_window_deadline
```

不得映射为球员个人：

```text
notice_deadline
disclosure_deadline
```

## 8. R7 — 外籍注册快照独立建模

新增/保留 `foreign_registration_snapshots`。

一行代表：

```text
season × snapshot_as_of × club × foreign player
```

至少保留：

- `snapshot_key`
- `season`
- `snapshot_as_of`
- `club_id`
- `club_source_name`
- `name_en_raw`
- `name_en_normalized`
- `name_zh_raw`
- `nationality`
- `position`
- `jersey_number`
- `status_at_snapshot`
- provenance / verification fields

`snapshot_as_of` 只表示该时点状态，不得推断为球员个人 registration date。

## 9. R8 — 外援取消注册进入 EVENTS，不得丢失备注区第二数据集

外援来源中的取消注册备注必须解析为：

```text
event_domain = foreign_registration
event_type = registration_cancelled
```

2024-2025 实际 XLSX 的 source control：

- 20 clubs
- 73 active snapshots
- 59 cancellation events
- 73 + 59 = 132 observed registration occurrences

年份由赛季边界补出时，必须：

```text
date_year_inferred = true
```

## 10. R9 — 外援优先续约权是独立状态实体

新增 `foreign_priority_right_snapshots`。

它不是普通 registration event。

一行代表：

```text
right_target_season × snapshot × club × player
```

至少支持：

- `right_key`
- `source_registration_season`
- `right_target_season`
- `snapshot_date`
- `club_id`
- `club_source_name`
- `player_name_en_raw`
- `player_name_en_normalized`
- `player_name_zh_raw`
- `right_status`
- `renewal_completed`
- provenance / verification fields

`right_status` 至少区分：

- `exercised`
- `continued`
- `renewal_completed`

“继续行使”和“双方已完成续约”不得压成同一状态。

## 11. R10 — 优先续约权转移独立建模

新增 `foreign_priority_right_transactions`。

一行代表：

```text
one confirmed priority-right transfer
```

至少支持：

- `right_transaction_key`
- `right_target_season`
- player raw/normalized names
- `from_club_id`
- `to_club_id`
- `transaction_date`
- `official_update_date`
- `later_registration_status`
- provenance / verification fields

权利转移不等于球员已完成新俱乐部注册。

## 12. R11 — 动态权利页必须按 snapshot 语义处理

“优先续约权交易信息”动态页面不是完整历史流水。

规则：

```text
交易后获得优先续约权俱乐部 = /
```

只表示：

```text
该 snapshot 时点未显示发生转移
```

不得解释为：

```text
历史上从未发生权利交易
```

硬约束：

```text
later snapshot absence
≠ delete earlier confirmed transaction
```

球员续约/注册后从动态页面消失，也不得反向删除此前已确认的权利交易历史。

## 13. R12 — 2019-2020 复赛特殊外援管理进入事件模型

“暂停使用全部外援”“启用某外援”等状态进入：

```text
event_domain = foreign_usage
event_type = usage_suspended / usage_activated
```

不得误写成：

```text
registration_cancelled
```

如 source 明确存在“亚外”等规则标签，应保留 source raw classification；不要为了迎合二值 `domestic/foreign` 而丢失原始分类。

## 14. R13 — club_aliases 必须真正跨赛季

`club_id` 是稳定实体键。

alias 必须 season-aware，至少支持：

- `official_domestic`
- `official_foreign_sponsor_by_season`
- `historical_domestic`
- `media`
- `source_typo_variant`

未知 alias 在 production strict 模式：

```text
BLOCK
→ human/config review
→ add alias with source evidence
→ regression test
→ rerun
```

禁止 fuzzy matching 后静默入库。

`bayi`：

- 历史有效 `club_id`
- `status = defunct`
- 解散前允许 historical roster
- 解散后禁止新增 active-season roster
- 仍可作为 `former_club` / `from_club_id`

## 15. R14 — 外援姓名必须 raw + normalized，不能用英文名作为永久身份

至少保留：

- `name_en_raw`
- `name_en_normalized`
- `name_zh_raw`
- alias/variant evidence（如适用）

处理：

- Unicode 罗马数字 `Ⅲ / Ⅱ`
- ASCII `III / II`
- NBSP
- 多空格
- JR / III suffix
- 连字符

normalized 仅用于 provisional matching。

永久身份仍由 v2.1 `player_uid` 解决。

## 16. R15 — 球衣号必须保真，包括 `00`

号码必须按源显示语义保存为字符串：

- `00`
- `01`
- `0`
- `1`
- `10`

不得因为 Excel numeric coercion 把 `00` 变成 `0`。

此外必须保存真正的 source-literal/raw representation；不得从已转换后的 parsed value 反向拼出“raw”。

## 17. R16 — verification 与 source authority 对新实体开始双轴保存

由于现有资料至少存在三套证据词表，v1.5.1 DRAFT-2 新表应保存：

- `verification_status`
- `source_authority`
- `verification_raw`
- `source_authority_raw`

推荐 `source_authority`：

- `A1_official_direct`
- `A2_official_mirror`
- `B1_authoritative_media_reproduction`
- `B2_secondary_cross_check`
- `C_unverified`

但映射必须按**具体 source**判断，不得机械把 `A/B` 翻译成某个统一值。

旧 3,451 baseline/既有国内 MASTER 不要求在本版本做全量证据 schema 迁移。

## 18. R17 — 一个 grain 一个 canonical logical table

DRAFT-2 的 canonical logical entities 固定为六个：

1. `domestic_registrations`
2. `domestic_transaction_windows`
3. `registration_status_events`
4. `foreign_registration_snapshots`
5. `foreign_priority_right_snapshots`
6. `foreign_priority_right_transactions`

禁止把六种 grain 合并成一个超级宽表。

## 19. R18 — Canonical store 与导出物分离

DRAFT-2 推荐目标：

```text
Native Google Sheet / canonical store
└── 6 logical tabs
```

而：

```text
XLSX / CSV / JSONL
= GENERATED / READ-ONLY EXPORT
```

如果实施阶段暂时保留多个 XLSX 作为兼容物，必须明确它们是发布产物还是 canonical truth，不能形成多个互相漂移的真相源。

## 20. R19 — v1.5.1 不提前实施 v2.1

Out of Scope：

- `player_uid`
- cba.net identity bridge
- career/game stats
- Claim Ledger
- Player Profiles
- MCP / REST API
- Public GitHub dataset publication
- B2
- 全库 entity resolution

---

# Part B — 技术方案（Technical Design）

## 21. 目标数据流

```text
00_inbox
   ↓
source_registry
   ↓
SourceAdapter
   ├── domestic_movement_md
   ├── foreign_registration_xlsx
   ├── foreign_registration_image
   ├── foreign_rights_md
   └── rights_transaction_snapshot
   ↓
normalized staging
   ↓
schema validation
   ↓
strict club resolution
   ↓
source-specific acceptance
   ↓
cross-table invariants
   ↓
frozen candidate
   ↓
v1.5 controlled publish
   ↓
Drive readback
   ↓
release closeout
```

## 22. 六个逻辑表的建议 grain

### 22.1 `domestic_registrations`

```text
season × club_id × domestic player
```

继续兼容现有：

```text
record_key = season|club_id|player
```

### 22.2 `domestic_transaction_windows`

```text
season × window_no
```

### 22.3 `registration_status_events`

```text
one player-related factual event
```

建议 key 不依赖一个语义混杂的 generic `sequence`。

建议分开：

- `source_row_no`
- `seq_in_club`
- `event_occurrence_no`

### 22.4 `foreign_registration_snapshots`

```text
season × snapshot_as_of × club_id × provisional foreign identity
```

### 22.5 `foreign_priority_right_snapshots`

```text
right_target_season × snapshot_date × club_id × provisional foreign identity
```

### 22.6 `foreign_priority_right_transactions`

```text
one confirmed right transfer
```

## 23. 关键跨表规则

- 一个国内 movement 可以同时生成 relation + event；
- 一个外援 snapshot 不自动生成 registration date；
- 一个 rights snapshot 不自动生成 transaction；
- `"/"` 不生成 rights transfer；
- later snapshot absence 不删除历史 transfer；
- corrected/retracted event 保留历史链；
- source raw value 永远不由 normalized value 反推；
- provisional player key 永远不等于 `player_uid`。

---

# Part C — 测试方案（Test Plan）

## 24. Layer 1 — Parser Fixture Tests

### T1 八一专项 MD

固定：

```text
parsed records = 14
```

用途仅为 parser regression，不作为完整国内流动控制量。

必须验证：

- 14 relations candidate
- 14 events
- `registration_window_deadline=2021-02-27`
- 不写入 personal disclosure deadline
- event date / announcement date 分列

### T2 2024-2025 外援 XLSX

真实 full-source control：

```text
clubs = 20
snapshots = 73
cancellation events = 59
observed registrations = 132
```

必须验证：

- merged header
- club forward-fill
- raw/normalized names
- `date_year_inferred`
- `00` / `01` 保真
- notes/event section boundary

## 25. Layer 2 — Canonical Source Controls

### 国内赛季中流动

逐赛季控制，不使用一个虚假的固定总数：

```text
2019-2020 = 0 completed
2020-2021 = 22 completed
2021-2022 = 4 completed
2022-2023 = 1 completed
2023-2024 = 9 completed
2024-2025 >= 6（source completeness 未完全冻结）
2025-2026 = 3 confirmed + 1 pending-evidence
```

### 外援 registration snapshots

当前控制量：

```text
2020-2021 = 45
2022-2023 = 52
2024-2025 = 73
2025-2026 = 74
```

如 2023-2024 使用较低等级复核资料，应单独标证据等级，不能假装同等 official source。

### 外援优先续约权

当前 merged source control：

```text
12 / 23 / 27 / 17 / 15 / 18 / 27 / 27
total = 166
```

### 动态 rights status snapshots

当前控制：

```text
10 / 8 / 2 / 8 / 9 / 24
total = 61
```

### 已二次确认真实 rights transfers

```text
7
```

这些数字是 **source controls**，不是永远不变的 production truth；若官方 source 与合并 MD 冲突，按 source authority 生成 discrepancy report，不为“凑数”改事实。

## 26. Layer 3 — Cross-table Invariants

必须全部通过：

1. `record_key` 唯一；
2. `window_key` 唯一；
3. `event_key` 唯一；
4. `snapshot_key` 唯一；
5. `right_key` 唯一；
6. `right_transaction_key` 唯一；
7. unknown club alias strict fail-closed；
8. `00` 保持 `00`；
9. `01` 保持 `01`；
10. raw names 保持原字符；
11. inferred date 必须有 flag；
12. pending 不得写进日期列；
13. `"/"` 不生成 rights transfer；
14. later snapshot absence 不删除历史 transaction；
15. correction/retraction 链完整；
16. 多人 swap 的 `transaction_group_id` 一致；
17. relation/event 不重复统计；
18. `bayi` 历史 roster 合法，解散后 active roster 非法；
19. provisional key 不得被当成永久 identity；
20. 每条 production fact 可回溯 source。

## 27. Layer 4 — Production Release Tests

必须走既有 v1.5 受控发布：

```text
candidate
→ validation
→ plan
→ publish --single-writer
→ readback
→ verify
→ release_status COMPLETE
```

并验证：

- stable Drive file ID（已有对象）；
- before snapshot 存在；
- journal 可恢复；
- failure 时可 rollback；
- source_registry 与 production release state 同步；
- README / AI Context / INDEX / Notion / release report 状态一致。

---

# Part D — 实施 Gate

## G0. 文档与历史基线冻结

- 保留 DRAFT-1；
- DRAFT-2 独立文件名；
- DRAFT-2 不覆盖 DRAFT-1；
- 记录新上级 source 到达时间及其 supersedes / source-family 关系。

## G1. Source Registry

- 登记两份上级合并核验文件；
- 为已有 source 补 `source_family / supersedes / canonical_source_id`（如需要）；
- 不因同标题副本而默认删除 source。

## G2. Config

- 扩展 multi-season `club_aliases`；
- 建 evidence mapping；
- 建 event/right/status 受控词表；
- strict unknown alias fail-closed。

## G3. Adapter / Parser

- 国内 movement parser；
- foreign registration parser；
- PNG OCR adapter；
- rights snapshot parser；
- rights transaction parser；
- correction/retraction parser。

## G4. Staging Acceptance

- source-specific controls；
- discrepancy report；
- no production write。

## G5. Candidate Build

- 六个 logical tables；
- frozen fingerprints；
- provenance；
- validation report；
- diff summary。

## G6. Controlled Publish

- single writer；
- baseline freeze；
- precondition checks；
- journal；
- stable file IDs / controlled new objects；
- readback。

## G7. Production Validation

- 4 层 tests；
- source_registry 状态同步；
- release_status COMPLETE；
- AI consumption acceptance。

## G8. Closeout

- release report 非空；
- Git tag；
- README；
- AI Context；
- INDEX；
- Notion；
- archive before/after/diff；
- 版本状态明确。

---

# Part E — Rollback Plan

发布前必须冻结：

- canonical fact store / 当前 production fact products；
- source_registry；
- manifest；
- club_aliases / taxonomy；
- README / AI Context / INDEX；
- release_status；
- previous release pointer。

失败时：

```text
state = FAILED / ROLLING_BACK
→ stop writer
→ restore previous fact artifacts
→ restore control files
→ readback verify
→ state = ROLLED_BACK
```

原则：

- 不破坏性删除失败 candidate；
- 失败 candidate / logs / diff / discrepancy 永久留在 archive；
- rollback 后 source_registry 不得错误保留 IMPORTED；
- 任何人工修复必须形成新的 candidate/release，不直接手改 production。

---

# Part F — DRAFT-1 → DRAFT-2 变更摘要

DRAFT-2 相比 DRAFT-1 新增/修正：

1. 从 3 个 grain 扩展为 6 个 canonical logical entities；
2. 新增 `domestic_transaction_windows`；
3. EVENTS 从两种 `event_type` 扩展为 domain/type/status；
4. 增加 pending/not_completed/correction/retraction/system_error；
5. 增加 `transaction_group_id`；
6. 日期值与日期状态分离；
7. 新增 `foreign_priority_right_snapshots`；
8. 新增 `foreign_priority_right_transactions`；
9. 明确 rights 动态页是 snapshot，不是完整 history；
10. 明确 later snapshot absence 不撤销历史 transfer；
11. 新增 2019-2020 foreign usage status；
12. `club_aliases` 扩展为 multi-season；
13. `jersey_number` 明确覆盖 `00`；
14. raw provenance 不能由 parsed values 重建；
15. 新实体开始采用 `verification_status × source_authority` 双轴；
16. 八一 14 条降级为 parser fixture，而非国内 movement completeness control；
17. 国内流动完整性改为逐赛季控制；
18. release closeout 要求 source_registry / docs / Notion / release report 同步一致。

---

# Part G — 版本边界与历史真实性

## 28. DRAFT-2 的正确历史定位

本文件是 **补建的 DRAFT-2 需求基线**。

必须明确：

```text
Drive 原 CBA-KB_v1.5.1.md
= DRAFT-1

Notion / 对话中形成的完整 Registration Domain 方案
= DRAFT-2 concept

本文件
= 将该 DRAFT-2 concept 正式补写为独立 requirements document
```

如果实际代码/production 已按 DRAFT-1 实施，不得回写历史声称：

```text
“实际实施完全依据 DRAFT-2”
```

正确表述应是：

```text
v1.5.1-1 实际实施范围
与
DRAFT-2 目标范围
分开审计
```

## 29. 后续版本建议

若 v1.5.1-1 已经正式 production COMPLETE，则：

- DRAFT-2 不应再悄悄覆盖 v1.5.1-1 的已发布定义；
- correctness hotfix 可作为 `v1.5.1-2`；
- DRAFT-2 未实施的完整 registration domain 可转化为后续正式版本（例如 v1.5.2）；
- v2.1 继续只做 identity / stats / claims / profiles 等上层能力。

最终版本号由项目 owner 决定，但**历史文档必须保留 DRAFT-1 与 DRAFT-2 的独立身份**。

---

# Part H — Definition of Done（若未来按本 DRAFT-2 实施）

只有以下全部通过，才允许把“DRAFT-2 目标”标为 IMPLEMENTED / STABLE：

- 6 logical entities 均有 canonical storage；
- source-specific acceptance 全部完成；
- PNG OCR source 完成 QA；
- multi-season aliases 完成并 strict 通过；
- event/right/status schema 完整；
- `00` 等 source-literal fidelity 通过；
- evidence 双轴在新实体有效；
- source_registry 与 production 一致；
- production readback 通过；
- rollback 演练/验证通过；
- release report 非空；
- docs / Notion / INDEX 与 release 状态一致；
- Git release/tag 与 production release 对齐。

---

# Appendix — 推荐的新 AI 初始化说明

```text
请先区分“需求文档”“实施记录”“生产事实”。

版本资料：
1. CBA-KB_v1.5.1_DRAFT-1.md = 原始三产品方案；
2. CBA-KB_v1.5.1_DRAFT-2.md = 后续完整 Registration Domain 需求基线；
3. docs/CBA-KB_v1.5.1.md = 实际代码实施记录；
4. release_status.json = 当前 production release 状态。

规则：
- 不把 DRAFT-2 planned 写成 implemented；
- 不把 DRAFT-1 已发布范围写成完整 registration domain；
- 判断当前事实先看 release_status，再读 production fact products；
- 判断版本需求差异时同时比较 DRAFT-1、DRAFT-2 与 implementation record；
- 任何新 source 先 source_registry，再 staging，再 candidate，再受控发布。
```
