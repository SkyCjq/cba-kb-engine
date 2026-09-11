# CBA-KB v1.5.2 — DRAFT-2 实施记录

> **代码版本**：1.5.2.dev0
> **状态**：PRODUCTION_RELEASE_COMPLETE_SCOPED；production_eligible = false
> **验证日期**：2026-09-11
> **代码分支**：`codex/v1.5.2-draft2`
> **前置版本**：v1.5.1（DRAFT-1 发布历史保留）

## 目标与边界

本次提交提供 DRAFT-2 六类事实的来源解析、严格俱乐部匹配、结构校验和六标签页候选工作簿导出。原生 Google Sheet 的权威事实源读取、构建及生产发布集成尚未完成。v1.5.1 的既有构建/发布流程保留；身份主键、球员画像、统计与 Claim Ledger 仍属于 v2.1 范围。

本候选使用两份二次核验合并资料：

- 国内流动：[`CBA_赛季中国内球员人员流动_2019-2026_合并核验总表_v2.md`](https://drive.google.com/file/d/1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR/view)
- 外援权利/注册：[`CBA_外籍球员优先续约权_交易_注册信息_二次核验完整修正版_2019-2027.md`](https://drive.google.com/file/d/1eGrMDep9YMfPNy66M6-6Jwm9iYaF_gQj/view)

## 候选产物与数量

本次实际来源验证生成 `workspace/candidates/v1.5.2-verified-20260911/domain.xlsx`。其输入 SHA-256、工作簿 SHA-256、逐赛季控制量和验证结果见 [验证文件](validation/v1.5.2-source-acceptance-20260911.json)。

旧候选 `domain-v1.5.2-final.xlsx` 和 [Drive r2 Sheet](https://docs.google.com/spreadsheets/d/1y5Vx2UvBAoUvMGwbXRP1SrUlI_4-fY9j4wTVLYoiLzA/edit) 保留历史记录，但含本轮修复前的数据，不能作为本提交的验收依据。本轮已验证候选已同步为 [Drive r3 Sheet](https://docs.google.com/spreadsheets/d/1PxOd9NJAwdYb-ay9aKR1zoKGyGucxHKBsHMfa0CAHXs/edit)，验收 JSON 为 [Drive 副本](https://drive.google.com/file/d/1khF_OcCibJsAC82z6BEXGTRYHjvTxo8T/view)。Excel 31 字符限制下，`foreign_right_snapshots` 与 `foreign_right_transactions` 分别映射到完整实体名。

| 实体 | 行数 | 说明 |
|---|---:|---|
| domestic_registrations | 46 | 合并资料表格中的国内流动关系；含尚缺注册时间戳的记录 |
| domestic_transaction_windows | 4 | 从来源明确声明的窗口日期生成；未补齐的规则字段保留 pending |
| registration_status_events | 72 | 国内 53；2019-2020 外援暂停使用 19 |
| foreign_registration_snapshots | 244 | 2020-2021、2022-2023、2024-2025、2025-2026 等来源表 |
| foreign_priority_right_snapshots | 227 | 年度权利状态 166；动态权利快照 61 |
| foreign_priority_right_transactions | 7 | 带日期的历史确认交易；动态表重复行已去重 |

合计 600 条不同类型记录，不能相加解释为独立球员人数。严格模式下无未解析俱乐部。`/` 在动态权利表中保留为“该快照未显示权利转移”，不会生成交易记录，也不据此推断已续约。未知名称在严格模式直接失败；宽松模式只用于审查。

## 生产发布

`v1.5.2-1` 已把上述六表工作簿作为二进制 XLSX 发布到 `20_data`，同时保留 MASTER、SNAPSHOTS、EVENTS 的对象 ID。生产计划共 125 个目标，发布后独立回读 125/125；六表 SHA-256 仍为 `4124c4e6f8e3f62d7353180a5bbd4baca51e3196e2b7fac6b06148042fce2849`。`release_status` 当前为 `COMPLETE / current_release_id=v1.5.2-1 / previous=v1.5.1-2`。

这次发布是明确标记的已实现表范围发布，不是完整 DRAFT-2，也不是 STABLE。详情和链接见 `docs/v1.5.2-deployment-20260911.md`，独立验收见 `docs/validation/v1.5.2-production-verification-20260911.json`。

## 验证与保真

- 全量回归：**88 passed, 2 skipped**；完整来源验证：**PASS_IMPLEMENTED_TABLE_SCOPE**。两项跳过是缺少固定路径的 v1.5 初始 MASTER 和旧八一专项来源，不计为通过。当前两份 DRAFT-2 合并资料和原始外援 XLSX 已单独执行真实来源验证。
- Drive r3 从原生 Sheet 官方导出 XLSX 后逐格回读，六标签页、600 条记录、15,210 个数据单元格与本地候选一致，`semantic_sha256 = 102d19d4dbd03231926e428df556f183b440765f4300bd7f0d74450c96a82669`；同步报告见 `docs/validation/v1.5.2-drive-sync-20260911.json`。
- 六表主键无重复；来源行号和原始文本逐条核对；全部单元格回读一致；相同输入重复导出字节一致。旧版外援 XLSX 兼容验证仍为 20 家俱乐部、73 条注册快照、59 条取消注册事件、132 条观测记录。
- 修复日期区间、约数、截止上界被当成精确日期的问题；日期列优先于 Window 标签。黄荣奇官宣日保留为 2025-11-24，注册提交/完成日期仍为空且待证据。
- 合并的中英文姓名分别保存，保留罗马数字和原始译名；比较键不是永久球员身份。
- 补读无状态列的两组续约权名单 23 + 27 行，并纠正目标赛季；历史交易表取消此前多出的 7 条伪快照，因此权利快照从旧候选的 184 修正为 227。
- 交易去重保留历史表和动态表两处原始证据；不同日期的独立交易不合并。研究标签 release_then_claim 与官方认领方式分开。
- 窗口只读取明确给出的规则；原始 A/B 和核验声明保留为元数据，自动解析不会提升为 A1 官网直读。俱乐部源拼写“广州朗钛海本”归入 source_typo_variant。
- 外援 XLSX 的原始单元格文本在解析前拼接；不从规范化字段重建 `raw_row_text`。明确数字格式 00 才保留为 00；以等号开头的文字按字符串保存。固定 ZIP 元数据和核心修改时间，保证跨时刻重复导出一致。
- 对第三方提出的“JAMES NUNNALLY / CAMERON OLIVER 球衣号 00”问题，实际读取的原始 `foreign_original.xlsx` 两个单元格均为数值 `0`、`General` 格式，没有可证实的字符串 `00`。候选不擅自改成 `00`；该差异与本地快照证据一并保留，待人工提供更高优先级原件时再复核。

## 尚未完成项

1. `2020-2021` 和 `2022-2023` 外援 PNG 的 OCR 和单元格 QA 继续按用户决定暂缓；Markdown 转录不能替代 PNG 验收。
2. 国内窗口覆盖尚不完整，尤其 2024-2025、2025-2026；纠错、撤回、系统错误和叙述中的未完成事件生命周期还需实现。
3. 八一专项表的合同、官宣和逐条证据补充，以及原 domestic MASTER 的正式合并尚未接入本六表命令。
4. 2023-2024 媒体注册快照、两条外援启用事件尚未解析；旧适配器的 59 条取消记录尚未并入本次六表输出。
5. 原生 Google Sheet 作为权威事实源的读取、构建、兼容导出和发布校验尚待集成。
6. 最新修复已同步到 Drive r3 候选 Sheet，验收 JSON 已进入 Drive AI 目录；Gemini 消费复验仍待执行。
7. `v1.5.2-1` 已发布已实现六表范围；完整 DRAFT-2 消费验收和上述未完成项仍不随 release_status 的 COMPLETE 状态自动完成。

## 下一步

以上未完成项补齐后，仍需更新候选、完成消费验收并以新的发布 ID 完成发布验证，才可宣称 DRAFT-2 完整交付。本轮没有重新核验外部官网。

## 复现验证

在仓库根目录使用包含项目依赖的 Python 环境执行；真实输入与凭据不纳入 Git：

```sh
python -m pytest -q -rs
python scripts/verify_v1_5_2_sources.py \
  --domestic-source workspace/inputs/draft2/domestic_movement.md \
  --rights-source workspace/inputs/draft2/foreign_merged.md \
  --foreign-xlsx workspace/inputs/live/foreign_original.xlsx \
  --output workspace/candidates/<new-verification-run>
python scripts/sync_v1_5_2_candidate.py \
  --candidate workspace/candidates/v1.5.2-verified-20260911/domain.xlsx \
  --acceptance docs/validation/v1.5.2-source-acceptance-20260911.json \
  --sheet-folder-id 1bQybVHV_RRtZvvFuFhLpM-rXbVsNT2Dq \
  --sheet-name "CBA-KB v1.5.2 候选事实表（六标签页，600条，PNG待补，r3）" \
  --sheet-key candidate/v1.5.2/draft2-r3 \
  --report workspace/reports/v1.5.2-drive-sync.json \
  --root /Users/skychengneo/Agent/CBA_kb --apply
```
