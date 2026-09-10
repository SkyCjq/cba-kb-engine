# CBA-KB v1.5.1 — 注册事实扩展（实施记录）

> 状态：RELEASE_COMPLETE_NOT_STABLE。生产 release_status 为 COMPLETE，`current_release_id = v1.5.1-1`（提交 de9a6b4，100 个产出物，独立回读 100/100）。
> 未封板原因：两季外籍球员 PNG 尚未完成 OCR 验收，且尚未做 v1.5.1 的两 AI 消费验收与 tag。
> 方案依据：[CBA-KB v1.5.1 方案](https://drive.google.com/file/d/1vQYwGHaqoWhjNQQBTofYityzKAd6X1uJ/view)；范围与纠错见 docs/V1_5_CLOSEOUT_REVIEW.md。
> 本文件记录代码实现、真实来源验收证据和仍未完成的门禁；未完成项一律不得写成 IMPLEMENTED。

## 基线（2026-09-10 实读，冻结）

- 国内 MASTER：Drive `1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc`，3,451 行、20 列、3,451 唯一 `record_key`；字节 SHA256 `75a937bbb0f7696ba966b56aaafc4cc4d34b8e7751e9fb4a6995be44354a8aa7`，语义 SHA256 `6a9f4ed33efbc9f265d098dcee3ff2fabf07843a8137137e5e09cc43d2d9d3a8`（与 docs/baseline_2026-09-10.json 一致，且与本机部署拉取的 MASTER 逐字节相同）。
- 八一中期转会 MD：Drive `1C8BpmY_MMjFGnSOdllRsnVWlwPdfKViV`，17,057 字节，SHA256 `d7272639a8503f60c3608e7ad914c891e3bda455994968b305027fdbe3994781`。
- 2024-2025 外援 XLSX：Drive `16H0cxiItoSlGALeHce-E06pD_a3RFAmd`，18,212 字节，SHA256 `3e0a0751c12b11578cc477e7612ffaff91c9f9e738457bfcb78645395e2b67f0`。
- 2020-2021 外援 PNG：Drive `1D72_i0dzR9TmeV_XH8KeBr_BTxxwFPIM`，119,023 字节，SHA256 `6671dce3b02c4e953c5dcaf7165d9657beb1b9271dbd95106caf9d40c6babe89`。
- 2022-2023 外援 PNG：Drive `1WYVqi94bqVGOLsD7t1nmMXyTMTU4TOlr`，1,415,422 字节，SHA256 `1e37b29c4ddcc76a2c9aa2dddcb871254e46599a6169c3528fb1108688186201`。

## 已实现

- `config/club_aliases.yaml` + `src/cba_kb/aliases.py`：赛季感知的 club 解析，区分 `official_domestic` / `official_foreign_sponsor_by_season` / `historical_domestic` / `media`；未知名称在 strict 模式 fail-closed，宽松模式只登记待补别名；`bayi` 标记 defunct、`last_roster_season: 2019-2020`、禁止解散后新建 roster，但仍可作为事件来源 club。
- `src/cba_kb/facts.py`：三种 grain 的产品 schema 与键。`record_key`（国内关系）、`snapshot_key`（赛季+快照时点+俱乐部+球员+国籍）、`event_key`（赛季+player_type+俱乐部+provisional key+event_type+event_date+sequence）。字段集合、受控 `event_type`、`verification_level`、`date_year_inferred` 规则均在此校验。
- `src/cba_kb/adapters/`：`midseason_md`、`foreign_xlsx`、`foreign_image` 三个适配器，共用 `foreign.py` 的合并表头识别、球队 forward-fill、roster/事件分段、赛季边界年份推断、英文名 raw/normalized 双列、球衣号文本保持。
- `src/cba_kb/merge.py`：只追加 live MASTER 中不存在的 `season|club_id|player` 关系，按赛季+俱乐部续编 `sequence`，同键只做字段比对并记入 collision 报告，不重复 append。
- `src/cba_kb/facts` 候选构建（`src/cba_kb/build_facts.py` + `src/cba_kb/xlsx.py`）：写出 `MASTER.xlsx`、`CBA_外籍球员注册_SNAPSHOTS.xlsx`、`CBA_球员注册_EVENTS.xlsx` 三个单文件单 tab 产品，复用 v1.5 的 `master.build` 生成 MASTER.csv/jsonl、十季阅读版与 INDEX，并扩展 provenance/validation。XLSX writer 固定文档属性，同输入字节可复现。
- CLI：新增 `extract`（来源→staging）、`facts`（staging→候选产品）；`doctor` 现在读取 `config/production.json` 真实报告 `production_enabled`，并输出版本与三类产品名。
- 运行时可观测性（v1.5.0 封板遗留项）：`src/cba_kb/transport.py` 提供脱敏错误原因（去掉 URL query 与 token 字段）、阶段进度日志，并对**只读**调用做有界重试；写请求保持单次尝试，靠 journal 恢复。
- 版本标识：`pyproject.toml` 与 `cba_kb.__version__` 统一为 `1.5.1.dev0`（v1.5.0 冻结提交未追改）。

## 真实来源验收（plan A1/A2）

证据文件：`workspace/reports/v1.5.1-source-acceptance.json`，状态 `PASS_WITH_PENDING_SOURCES`。

| 检查 | 结果 |
| --- | --- |
| 2024-2025 外援球队数 | 20（= 预期） |
| roster snapshots | 73（= 预期），`snapshot_as_of=2025-03-31` |
| 取消注册事件 | 59（= 预期，源文件第 77–135 行） |
| 已观察注册人次 | 132；两组英文名 normalized 集合交集为空，确认不重叠 |
| 八一中期转会记录 | 14 条，14 个唯一 `record_key`，全部产出 event |
| `record_key` 碰撞 | 0（对冻结基线 3,451 行；候选 3,465 行） |
| `registration_window_deadline` | 全部 `2021-02-27`；`notice_deadline` 保持为空（R3） |
| verification | 八一记录规范为 `human_verified`，外援为 `auto_validated`，OCR 不得升格 |
| 同名/规范化 | `VLADYSLAV␠ KORENIUK`、`LEONARD RANDALLⅡ`、`GEORGE KELL Ⅲ` 的 raw 原样保留，normalized 为 `VLADYSLAV KORENIUK` / `LEONARD RANDALLII` / `GEORGE KELL III` |
| 球衣号 | 全部为字符串，`01`、`0` 未被数值化 |
| 未知球队 | strict 模式直接失败，宽松模式输出待补 alias 列表 |

单元与回归测试：59 项通过（v1.5 的 36 项 + v1.5.1 新增 23 项）；真实来源全量解析由 `make accept` 单独断言，避免把小型 fixture 当成 full-source 验收。

## 与方案的差异与说明

1. `EVENTS` 增加显式 `provisional_player_key`、`sequence` 两列：方案把它们只写在 `event_key` 组合里；显式成列后键可由行内字段复算，便于审计。两列仍是 provisional，不是身份主键。
2. 外援取消事件的 `sequence` 使用来源行号（稳定 source locator，如 `Sheet1!row 77`），不是解析顺序计数；同一天同一人同类型的重复事件不会互相覆盖。
3. 事件月份规则：8–12 月→赛季起始年，1–7 月→赛季结束年，`date_year_inferred=true`；其中 4–7 月额外进入 review 列表（2022-2023 PNG 为“截至 4 月 5 日”）。无时刻信息一律不伪造精确时间戳。
4. 八一 MD 的 `notice_deadline` 一律不写入国内 MASTER；窗口截止日只进 `EVENTS.registration_window_deadline`。现有 MASTER 中 2020-2021 的批次公示截止字符串不被改写，也不作为新增行的映射依据。
5. `source_type` 沿用来源自身标注（`web_cross_checked`、`gdrive`），并在 `config/taxonomy.yaml` 中补登记实际使用的取值；v1.5.1 不做 verification 双轴大迁移。
6. 来源登记只产出**提案**（`workspace/staging/registry-proposal/`）：staging 解析不等于生产导入，`60_config` 的 source_registry 必须走受控发布路径后才算登记完成（R1）。

## 运行方式

```sh
make extract ARGS="--source <staged source> --season 2024-2025 --output workspace/staging/<run> --source-id <drive id>"
make accept  ARGS="--staging workspace/staging --master workspace/inputs/<run>/MASTER.xlsx --report workspace/reports/v1.5.1-source-acceptance.json"
make registry ARGS="--sources workspace/staging/inbox/sources.json --staging workspace/staging --output workspace/staging/registry-proposal"
.venv/bin/python scripts/prepare_v1_5_1.py reserve --release <release id>   # 预留新对象与冻结输入副本，写 config/production.json
git commit -am "Freeze the v1.5.1 production allowlist"                      # freeze 要求干净工作树
.venv/bin/python scripts/prepare_v1_5_1.py freeze  --release <release id>   # 构建三类产品并冻结 entries.json
make plan    ARGS="--entries workspace/production/<release id>/entries.json --release-id <release id> --status-id 1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV --archive-id 1GjDMkAYs7JpIrI9_kQduxlqygZmxeb9M --dependencies workspace/production/<release id>/dependencies.json --environment production"
make publish ARGS="--release workspace/outbox/<release id> --single-writer"
make verify  ARGS="--release workspace/outbox/<release id>"
```

发布仍走 v1.5 的 `plan --environment production` → `publish --single-writer` → `verify`，不得绕过白名单、依赖冻结、journal、回读与回退。

## 生产发布记录：v1.5.1-1（2026-09-10）

| 项 | 值 |
| --- | --- |
| release_status | COMPLETE；`current_release_id = v1.5.1-1`；`previous_release_id = v1.5.0` |
| 代码提交 | `de9a6b48cdc89d793e896ed03de262438d546442`（分支 `v1.5.1`） |
| 产出物 | 100 个目标全部写入并独立回读通过（`verify: {"verified": 100}`） |
| 事实产品 | MASTER 3,465 条；SNAPSHOTS 73 条；EVENTS 73 条（59 外援取消 + 14 八一中转） |
| 国产 MASTER 完整性 | 前 3,451 行与冻结基线逐字段相同；新增行 `notice_deadline` 为空，`sequence` 按赛季+俱乐部续编（如 beijing_shougang 20/21） |
| registry | 89 → 93 行，四个新来源登记为 DISCOVERED（PNG 两个标 pending_ocr） |
| 入口 | [发布状态](https://drive.google.com/file/d/1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV/view)、[INDEX](https://docs.google.com/document/d/1VqnFtMRSlOV9K7eVCAJKQ5HngWMl60MibbopNclxN9c/edit)、[SNAPSHOTS](https://drive.google.com/file/d/1avAxhNUTgm14MIdkgkHhia6_adSDGwLD/view)、[EVENTS](https://drive.google.com/file/d/1WtE63GIQxYPUBsfcRlhKCH8lJgyTZeGF/view) |
| 归档 | `90_archive/v1.5.1-1/`（100 个 before 快照、candidate 副本、原生 Docs 备份、plan.json） |

结构性变更（相对 v1.5.0 冻结策略）：`config/production.json` 的 `dependency_ids` 由 live MASTER/registry 改为**冻结输入副本**（`1iehJ6BPHZeYqvhdk0606gIO0E0EkXnUX`、`1jYtukHUR4HnCoclny3oSDltCdHTpglMY`），因为本发布必须合法修改 MASTER 与 registry；否则 `check_dependencies` 会在写入后自阻断。新增目标为两个事实产品对象与 22 个代码镜像文件，全部通过 `scripts/prepare_v1_5_1.py reserve` 预留 ID。

故障记录：首次 `publish` 在归档快照阶段遇到网络超时并正确 fail-closed（journal 保持 PREPARED、release_status 保持 COMPLETE/v1.5.0、无目标被改写）；另有一次因新建空对象的 Drive `version` 在 plan 与 publish 之间自增而被 preflight 安全阻断。两者都通过重新 `plan` + 重跑 `publish` 幂等恢复，未使用回退。

## 未完成 / 下一步

1. **两季 PNG OCR QA（阻塞 DoD）**：本机没有 PaddleOCR 访问令牌与 CLI，`foreign_image` 适配器已就绪但尚未有真实 OCR 表。需要设置 `PADDLEOCR_ACCESS_TOKEN` 并安装 `paddleocr` 后，对 `workspace/staging/inbox` 的两张 PNG 执行 doc parsing，产出 OCR/版面报告、低置信度复核清单、roster/事件分段报告与来源级计数，再登记该赛季赞助商别名。
2. **来源状态推进**：已登记来源从 `DISCOVERED` 走到 `EXTRACTED`（2024-2025 XLSX、八一 MD 已达 `table_extracted` / `markdown_extracted`），PNG 完成后补事件计数与别名。
3. **两 AI 消费验收**：按正式 ID 复验三类产品（尤其 EVENTS 的 `date_year_inferred`、快照与事件不得混用、非 COMPLETE 时的读取策略）。
4. **封板**：验收通过后创建 GitHub tag `v1.5.1`、把发布报告归档到 `90_archive`、同步 README/AI Context/INDEX/Notion。

在以上完成前，v1.5.1 只能是 `PLANNED / IN_PROGRESS / CANDIDATE / RELEASE_COMPLETE`，不得写成 `STABLE`。
