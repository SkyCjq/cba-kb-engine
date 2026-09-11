# CBA-KB v1.5.3 — Registration Event Domain Closure

> **代码版本**：`1.5.3.dev0`
> **状态**：PRODUCTION_RELEASE_COMPLETE / v1.5.3-2
> **部署分支**：`codex/v1.5.3-deploy`
> **生产版本**：`v1.5.3-2 / COMPLETE`
> **发布代码提交**：`f9038fd9cc03e552abee0ca08ae51d047efd65af`
> **冻结日期**：2026-09-11

> **生产发布状态（2026-09-11）**：`v1.5.3-2 / COMPLETE`。149 个目标物独立回读通过；六表为 `46 / 10 / 131 / 244 / 227 / 7`，workbook SHA-256 `0c9812e5e49f0824b74966f3f3941d8eee3711fdafc19d88774084a83742e66c`。发布仅覆盖注册事件域收口，不表示完整 DRAFT-2 或 STABLE。

## 目标

v1.5.3 将六表中的 `registration_status_events` 明确为唯一 canonical registration
event product，并将 Snapshot、Event、日期语义、官方 registration method 与研究标签
永久分离。历史 `CBA_球员注册_EVENTS.xlsx` 保留为兼容与审计对象，不再是另一套独立
生产真相源。

本轮已完成代码合并、生产发布与独立回读。生产版本为 `v1.5.3-2 / COMPLETE`；
其中 `v1.5.3-1` 发布事实与代码，`v1.5.3-2` 只修正版本文档中的历史执行状态。

## Phase 0 冻结

当前 live 前置为 `COMPLETE / v1.5.2-2`。冻结输入：

| 输入 | SHA-256 |
|---|---|
| 六表工作簿 | `e37524480b392040d9ddc8a232a8bb561dfab51d29051082e087357a80bf71f6` |
| legacy EVENTS | `374472e4d0bb677abed446047b8c1eead3cce6a2291741bfd22d6f52e0be878a` |
| MASTER | `185cf58d69ced2ba38a1745cdb848ab21288a666d893d3d4bae3a3ddd1c2d588` |
| release status | `297362a0a3c86da59d2cb75af123feed104b5c945f5c3636ee1c9e156c53f9dc` |

Phase 0 实测：六表 `46 / 4 / 131 / 244 / 227 / 7`；legacy EVENTS 73 行；
`domestic_movement=53`、`foreign_registration=59`、`foreign_usage=19`。

## 候选结果

候选由 `scripts/prepare_v1_5_3.py` 生成，现已成为生产发布内容：

| 实体 | v1.5.2-2 | v1.5.3 production |
|---|---:|---:|
| domestic_registrations | 46 | 46 |
| domestic_transaction_windows | 4 | 10 |
| registration_status_events | 131 | 131 |
| foreign_registration_snapshots | 244 | 244 |
| foreign_priority_right_snapshots | 227 | 227 |
| foreign_priority_right_transactions | 7 | 7 |
| **合计** | **659** | **665** |

2024-2025 与 2025-2026 各有 3 个 transaction window entity。缺少来源日期的窗口保持
null / pending，不由球员事件日期反推。事件数量保持 131；既有 fact 不静默消失。

对账结果：legacy 73 行、mapping edges 87、mapped 73、unmapped 0、
unexplained conflicts 0。其中 14 条八一记录分别产生 relationship projection 与
event projection，59 条外援取消记录产生 event projection。

罗汉琛 golden case 已产生 `relationship_gap_signal`，提示
`former_cba_club = shenzhen_xinshiji`；signal 不含 event date，不创建深圳 Snapshot。

## AI 消费规则

- 某赛季登记状态：读取 MASTER / Snapshot。
- 什么时候发生什么：读取 canonical `registration_status_events`。
- 为什么这么判断：读取 `10_sources_原始证据`。
- 人工解释与研究判断：读取 `30_notes_人工知识`。
- 快速导航与语义阅读：读取 `40_ai_投喂与索引`，不要把派生正文当精确计数来源。
- 旧 `CBA_球员注册_EVENTS.xlsx`：兼容 / 历史审计输入，不是 current truth。
- 不同 `event_domain` 不得直接相加；只有明确询问全部 event records 总数时才汇总，
  并同时给出分域计数。
- `registration_method_official` 与 `research_movement_label` 不得互换。

## 本地命令

```sh
make reconcile-v1.5.3 ARGS="--domain <six.xlsx> --legacy <events.xlsx> \
  --master <master.xlsx> --domestic-source <domestic.md> \
  --output <new-report-dir> --expected-legacy-rows 73 --expected-edges 87 \
  --expected-event-domain domestic_movement \
  --expected-event-domain foreign_registration \
  --expected-event-domain foreign_usage --require-window-closure"

make prepare-v1.5.3 ARGS="--domain <six.xlsx> --legacy <events.xlsx> \
  --master <master.xlsx> --domestic-source <domestic.md> \
  --release-status <release_status.json> --source-registry <source_registry.csv> \
  --output workspace/candidates/v1.5.3-<run>"
```

两命令都只读输入并写新的候选目录；不会调用 Drive 或修改 production。

## 发布边界

GitHub CI、独立来源验收、candidate semantic readback、production GO-NO-GO 与
rollback snapshot 均已通过，生产已切换到 `v1.5.3-2 / COMPLETE`。该切换只覆盖
注册事件域收口与回归保护，不得表述为完整 DRAFT-2 或 STABLE。
