# v1.5 实施与部署记录

## v1.5.3 开发状态（2026-09-11）

当前生产仍为 `v1.5.2-2 / COMPLETE`，未执行 v1.5.3 production publish。
`codex/v1.5.3-event-closure` 已建立只读候选链路：六表候选为
`46 / 10 / 131 / 244 / 227 / 7`，legacy EVENTS 73 行对账为 87 edges，
未映射 0、未解释冲突 0，罗汉琛 relationship signal 存在。候选明确
`production_eligible=false`；详细边界见 [v1.5.3 实施记录](CBA-KB_v1.5.3.md)。

## v1.5.2 DRAFT-2 实际部署（2026-09-11）

维护状态：`CODE_FROZEN_BY_USER`。用户确认 PNG OCR 不需要，收尾后不再调整代码；其余未实施范围和 Gemini 未复验结论保留为已知限制。最终审计、范围决定和消费规则见 [最终收尾说明](v1.5.2-final-closeout-20260911.md)。`COMPLETE` 仅指已实现范围的发布事务完成。

- 生产发布：`v1.5.2-1 / COMPLETE`，发布代码提交 `0d28fb8`，125 个产出物独立回读通过；上一发布为 `v1.5.1-2`。
- 实际部署：`/Users/skychengneo/Agent/CBA_kb`，分支 `codex/v1.5.2-deploy`，运行版本 `1.5.2.dev0`。
- 部署验证：发布前完整测试 **91 passed, 1 skipped**；真实来源重新生成六表工作簿，工作簿 SHA-256 为 `4124c4e6f8e3f62d7353180a5bbd4baca51e3196e2b7fac6b06148042fce2849`。
- Drive 同步：600 条候选已同步为 [r3 Sheet](https://docs.google.com/spreadsheets/d/1PxOd9NJAwdYb-ay9aKR1zoKGyGucxHKBsHMfa0CAHXs/edit)，官方 XLSX 导出逐格回读一致；实施记录与验收 JSON 已保存到 `40_ai_投喂与索引`。
- 生产产物：[CBA_注册领域_六表.xlsx](https://docs.google.com/spreadsheets/d/1_dgbWXOkJeEjGRtEeZ0oOC9e-lqbXZeW/edit) 已进入 `20_data`，六表 600 条；MASTER、SNAPSHOTS、EVENTS 原 ID 保留。
- 生产边界：验收对象仍明确 `production_eligible=false`，表示只接受已实现六表范围，不代表完整 DRAFT-2。PNG、窗口生命周期、八一/MASTER 合并、媒体快照、旧取消事件合并和原生 Sheet 权威源集成仍未完成。
- 发布证据：[发布报告](https://drive.google.com/file/d/1vXjEfH8M8aXlLXJqw4pjUec9ejUIZko2/view)、[验收 JSON](https://drive.google.com/file/d/1qJS-Tr1r5XEYbNIwrfiYOMNVqcf8Km-4/view)、[release_status](https://drive.google.com/file/d/1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV/view)。

以下为 v1.5.0 建设阶段的历史记录，不代表当前生产状态。

已核对 live MASTER：3,451 行、20 列、单 MASTER tab、3,451 唯一键；source_registry 89 来源。详细哈希和赛季数见 baseline_2026-09-10.json。
数据内容保持原字节不变，1,019 条官方 API 行以官网 URL 追溯，未补造 source_file_id。
16 份 Drive 原文件已导入并核对长度，代码导入清单见 import_inventory.json。

本地实现已通过 36 项测试：既有 9 项，以及 MASTER 导出、正常发布、重复发布、原 ID 回退、冲突阻断、响应丢失重试、部分失败恢复、候选防篡改、类型保护、单写者要求、锁与路径保护、发布状态恢复，以及 native Docs 受控前缀的范围和回退测试。
故障测试当前为离线模拟，不等于 Drive 生产环境验证。

已创建并核实私有仓库 [SkyCjq/cba-kb-engine](https://github.com/SkyCjq/cba-kb-engine)，GitHub CLI 已授权，初始代码已推送。第二 AI 选择 Gemini。

工程已安装于 `/Users/skychengneo/Agent/CBA_kb`，Python 3.11.15 独立虚拟环境已就绪。首批本地候选包含完整 CSV/JSONL、10 个赛季阅读版、INDEX、provenance 与 validation 共 15 个文件，MASTER 原文件字节不变。

native Docs 适配原型仅改写受控的当前发布前缀，原正文保留在“历史内容”分界下，可通过删除前缀回退；范围测试通过。已接入发布器。通过当前 AI 的 Drive 连接在独立沙盒 INDEX 完成真实插入、样式修正和删除回退；回退前后 tabs/body 结构完全相同，仅 revisionId 变化。随后本地 OAuth 通道也完成独立验收，原文 tabs/body/styles 回退一致。

本地真实验收（2026-09-10）：

- Desktop OAuth 授权完成，令牌刷新成功；凭据仅保存在本机。
- 三份输入文件 SHA256 与既有基线一致，MASTER 为 3,451 行、20 列、无重复键。
- sandbox-oauth-001：原生 Docs 发布、回读、重复发布、原 ID 回退通过；原正文及样式恢复。
- sandbox-oauth-002：仅修改沙盒 XLSX 的 ZIP 注释、保持表格值不变，完成实际字节覆盖与回读、原 ID 回退。
- 官方来源 6a97f61ed5 返回 code 200，实际解析出 14 行；仅为抽样来源可达性，不代表全来源复核。
- 网络中断、响应丢失等故障注入仍只有离线测试证据。

待完成：

1. 全量生产发布清单、代码镜像、上下文入口与生产切换。
2. ChatGPT/Codex + Gemini 的候选消费验收；用户选择自行在 Gemini 按提示词测试并返回结果。
3. 生产切换后的两 AI 复验与 v1.5.0 release tag。

当前生产版本仍为 v1.1 FINAL。不得把部署中标记成 IMPLEMENTED。

发布清单生成器会冻结代码副本、记录提交号，并保留已知旧文件 ID；未分配 ID 的新增产出物仍需发布前解析。沙盒演练步骤见 SANDBOX_RUNBOOK.md。

## 封板前修复（生产冻结前记录）

恢复中状态、重复恢复、最终状态响应丢失重试、只读输入依赖、完整生产白名单、新对象暂存/发布/归档恢复已实现；36项测试通过。真实Drive closeout-fault-002验证ROLLING_BACK可见、最终响应丢失可恢复、重复恢复成功。初次closeout-fault-001因新建对象元数据变化被安全阻断，未覆盖测试目标。旧legacy uploader命令入口已禁用。

Gemini直接链接抽样通过；生产正式文件复验仍待执行。此记录不提前宣称v1.5已封板。迁移说明见MASTER_MIGRATION_NOTE.md，完整运行边界见OPERATIONS_V1_5.md。
