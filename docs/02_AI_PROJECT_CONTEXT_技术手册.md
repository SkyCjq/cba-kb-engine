# CBA-KB AI PROJECT CONTEXT — current routing

> **as_of:** 2026-09-11
>
> **current production:** `v1.5.2-2 / COMPLETE`
>
> **development:** `v1.5.3.dev0 / candidate, not production`

## Current query routing

- 某赛季或某公示时点的登记状态：读取 MASTER / Snapshot。
- 什么时候发生什么：读取 canonical `registration_status_events`。
- 为什么这么判断：读取 `10_sources_原始证据` 及事实行的 provenance。
- 人工解释、推导和待核实研究：读取 `30_notes_人工知识`。
- 快速导航和语义阅读：读取 `40_ai_投喂与索引`，不把派生正文当精确计数来源。
- 旧 `CBA_球员注册_EVENTS.xlsx`：只用于兼容和历史审计，不是 current truth。
- 不同 `event_domain` 不得直接相加；明确询问全部 event records 时，必须同时给出分域计数。
- `registration_method_official` 与 `research_movement_label` 不得互换。
- `auto_validated` 不等于 official / human verified；空值不是零。

v1.5.3 的候选控制、对账结果、命令和发布边界见
[`CBA-KB_v1.5.3.md`](CBA-KB_v1.5.3.md)。以下 v1.1 章节保留为历史基线说明；
若与当前 Drive `release_status` 或 v1.5.3 文档冲突，以当前 live state 和最新版本记录为准。

# CBA-KB AI PROJECT CONTEXT — v1.1 FINAL historical baseline

> **as_of:** 2026-09-09 15:00 UTC  
> **production baseline:** CBA-KB v1.1 — CLOSED / STABLE  
> **next target:** CBA-KB v1.5 PLANNED → v2.1 Unified Target  
> **data truth:** Google Drive `cba-kb`

## 0. 默认读取顺序

新 AI / 新对话先读：

1. `00_README_先读这个`
2. `02_AI_PROJECT_CONTEXT_技术手册.md`
3. `版本迭代/CBA-KB v1.1.md`
4. [版本迭代/CBA-KB_v1.5.md](https://drive.google.com/file/d/1App5yvwH7s9oLyjdVdUea1us233P5VgV/view)（本地部署与同步；v2.1 在其验收后按需读取）
5. `40_ai_投喂与索引/INDEX`

如果文档与 Drive live state 冲突，以 Drive live state 为准，并明确指出冲突。

---

## 1. 当前生产事实层

### 唯一当前结构化最终产物

`20_data_结构化事实/CBA_2017-2027_国内球员注册_MASTER.xlsx`

- 单文件
- 单 tab：`MASTER`
- 3,451 条数据记录
- 20 列
- `record_key` 3,451 / 3,451 唯一
- 覆盖 2017-2018 至 2026-2027
- 只覆盖国内球员注册/名单口径

赛季计数：

- 2017-2018：308
- 2018-2019：331
- 2019-2020：346
- 2020-2021：355
- 2021-2022：380
- 2022-2023：358
- 2023-2024：349
- 2024-2025：357
- 2025-2026：335
- 2026-2027：332

2024-2025 合并规则：

- 352 条当前 official API 记录优先；
- 另保留 5 条当前 official snapshot 未覆盖的历史记录；
- 不静默删除低等级历史证据。

贾昊最终修正：

- `record_key = 2024-2025|jilin_jiutai|贾昊`
- `notice_deadline = 2024年12月7日5:00`
- 此结论不再标记为 05:00/15:00 待证。

### 可信度规则

- `official_api`：当前 CBA 官网注册来源；
- `official_roster`：官方名单摘录，仅证明名单中明确出现的字段；
- `human_verified`：明确人工核验；
- `auto_validated`：机器/OCR/规则交叉核验，**不等于官方或人工核验**；
- `needs_review`：不得写成确定事实。

八一 2017-2018 / 2018-2019 的补充名单只证明 `player + season + club`；没有来源的注册方式、合同类别、合同期限等字段必须保持空白。

---

## 2. 当前目录语义

### `00_inbox_待处理/`
仅放“新到、尚未完成登记/处理”的资料。  
**当前为空。**

### `10_sources_原始证据/`
保存已处理来源和可追溯证据。当前主要结构：

- `historical_registrations_2017_2025/`
  - 2017-2018 至 2024-2025 八份历史 OCR/结构化工作簿
  - 2017-2018 八一补充名单
  - 2018-2019 八一补充名单
  - 2022-2023 江苏肯帝亚补充图片
- `official_cba/`
  - `snapshots/`：官网 probe/snapshot
  - `reference_exports/`：`CBA_2024-2027_国内球员注册数据库_官网核验版.xlsx`

### `20_data_结构化事实/`
**只保留最终生产事实产物：MASTER.xlsx。**

精确计数、筛选、跨赛季比较默认只读这里。

### `30_notes_人工知识/`
人工研究、观点、判断；不等同官方事实。

### `40_ai_投喂与索引/`
INDEX 和 AI 阅读优化文件；是派生层，不是精确计数真相源。

### `50_scripts_自动化脚本/`
采集、处理、同步、回归测试代码。代码不是知识来源。

### `60_config_配置与词表/`
当前控制层：

- `manifest.csv`：同步/hash/Drive file ID
- `source_registry.csv`：source processing truth
- `drive_map.yaml`：稳定 folder ID
- `taxonomy.yaml`：受控词表/分类

`source_registry.csv` 当前登记 89 个来源。判断 source 是否已处理时查它，不根据文件夹位置推断。

### `70_tools_辅助工具/`
辅助工具，不是事实来源。

### `90_archive_历史归档/`
中间表、QA/OCR artifacts、旧 native Sheet、审计报告、rollback snapshots。  
默认不参与当前事实查询。

---

## 3. v1.1 Register-first 规则

正式流程：

```text
new source
→ register
→ extract/process
→ validate/import
→ evidence archive
```

当前：

- `process_inbox.py --scan / --process / --extract-canary` 默认不修改 Drive parent；
- `--archive / --archive-canary` 才执行 parent mutation；
- `source_registry.csv` 的 canonical location 已调整为 `60_config_配置与词表/source_registry.csv`；
- `manifest.csv` 仍只做 asset/sync control。
- `build_bundles.py` 与旧 `make sync` 在 v1.1 FINAL 下已 **fail-closed**：不得再用 archive 中的 pre-MASTER 表重建/覆盖 `40_ai`；先由 v1.5 实现最小 MASTER-aware builder 并通过验收，再由 v2.1 扩展业务视图。

物理目录与处理状态是两个维度。

---

## 4. AI 回答硬规则

- 精确数字：以 `20_data_结构化事实/CBA_2017-2027_国内球员注册_MASTER.xlsx` 为准。
- 来源追溯：读取 `10_sources_原始证据/` 以及 MASTER 中的 `source_file_id / source_url / source_page / source_type`。
- source 处理状态：读取 `60_config_配置与词表/source_registry.csv`。
- `40_ai` 用于导航、解释、语义检索，不从正文手工数数。
- 跨赛季球队匹配使用 `club_id`。
- `registration_stage=变更` 不自动等于“转会”。
- 不把 `auto_validated` 写成官方/人工核验。
- 不把历史过程表、rollback 或 archive 中的记录数描述成当前生产状态。
- 不把 v1.5/v2/v2.1 planned 设计描述成已实施。

---

## 5. 当前 v1.1 六项 MASTER 验收

1. 总数据行 = **3,451** — PASS
2. `record_key` 唯一 — PASS
3. 10 个赛季计数与冻结基线一致 — PASS
4. 2024-2025 official precedence — PASS
5. 八一来源无证据字段保持空白 — PASS
6. 贾昊公示截止时间 = **2024年12月7日5:00** — PASS

---

## 6. v1.1 最终角色模型

```text
10_sources  = Evidence
20_data     = Final Fact Product
60_config   = Processing / Sync Control
40_ai       = Derived AI Views
90_archive  = Audit / Rollback / Working History
```

v1.1 已收尾。后续业务能力进入 `CBA-KB v2.1 Unified Target`，但 v1.1 MASTER 保持当前 production baseline。


## 7. v1.5 前置计划（2026-09-10）

实施顺序：v1.1 FINAL → v1.5 → v2.1。当前仍为 v1.1 生产状态。

v1.5 计划在 `/Users/skychengneo/Agent/CBA_kb` 本地运行，以 GitHub 私有仓库管理代码；事实、证据、AI 阅读版、代码镜像、报告等交付产出同步到 Drive。GitHub 仓库与 OAuth 尚未配置，本次仅完成方案。

MASTER file ID：`1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc`，MIME 为 XLSX；更新时保持原 ID 与格式，不能按 native Google Sheet 操作。归档 Sheet 不恢复为生产写入目标。

v1.5 将增加发布清单、失败恢复、release_status 与简短上下文入口；这些在实施验收前不是现有能力。各 AI 仍通过获授权的 Drive 工具读取，不能要求访问本地部署目录。跨产品刷新需实测，不因 file ID 不变就宣称自动刷新。

[完整 v1.5 方案与验收条件](https://drive.google.com/file/d/1App5yvwH7s9oLyjdVdUea1us233P5VgV/view)。
