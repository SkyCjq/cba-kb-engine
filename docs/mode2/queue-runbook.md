# Mode 2.1 队列入口 runbook（cron + claim 协议）

> 对应执行手册 §11。试跑期轻量版：单 worker，不宣称解决并发；只防重复触发与中断恢复。

## cron 扫描（每 15 分钟）

| 队列 | 条件 | 动作 |
|---|---|---|
| 待出题 | Issue 无 `status:*` 标签 | 打 `status:spec`，进 spec |
| 待开发 | `status:ready` 且无关联 PR | 进 dev |
| CI 红待修 | PR 关联 `status:dev` Issue，且 pr-gate-untrusted 失败 | 自修（计数器） |
| 待合并 | PR 关联 Issue `status:review` 且 verdict PASS 且审批满足 | 合并前最终原子检查（见下）后按 tier 合并 |
| hold 复查 | `status:hold` 超过 24h 无 Human 决策 | @Human 提醒一次 |

分钟级延迟；无活不唤醒；不耗共享额度。

## claim 协议（防重复开工）

开工前必须在 Issue 先发 comment：

```html
<!-- mode2-claim {"worker":"muse-<ts>","lease_until":"<ISO8601>","task":"spec|dev|fix|review"} -->
```

- 开工前检查：已有未过期 claim → 跳过（不重复开工）
- lease 默认 60 分钟；超时未更新 → 其他 worker 可接管
- 完成/失败时 comment 关闭 claim（成功/失败原因）

## 故障分类与恢复

| 分类 | 例子 | 处理 | 计入 CODE 轮次？ |
|---|---|---|---|
| ENV | 网络/API 限流/Runner 故障/Web 会话失效 | 指数退避有限重试，恢复同一任务 | 否 |
| CODE | 测试失败、实现 bug | Muse 修复，重跑相关测试 | **是**（≤3 轮） |
| REQ | 验收标准冲突、测试本身不合法 | 停开发，回 spec 重出题、重冻结 | 否 |
| FLOW | SHA 不一致、证据缺失、状态冲突、claim 冲突 | 控制器阻断，重新取证/恢复状态 | 否 |
| SECURITY | 权限异常、可疑执行、敏感数据泄露 | 立即 HOLD，吊销相关凭证，交人工 | 否（走人工） |

熔断 = 同一根因实质修复超限（CI 3 轮 / 审查 2 轮）→ `status:hold`。另设总预算上限（轮次/时长/成本），超限即 hold。

## 合并前最终原子检查（回应 Web 审查： verdict 与审批之后、点合并之前的状态可能已变）

点合并（或 auto-merge 生效）前，控制器必须重新确认（任一失败 → 不合并）：
1. PR head SHA == 审查通过的 `reviewed_head_sha`（期间无新 push）
2. `pr-gate-trusted` / `pr-gate-untrusted` 在当前 head 上仍为成功（无过期）
3. Issue 不在 `status:hold`
4. T1/T2 的 Human approval 仍有效（针对当前 head，未被 dismiss）

执行机制（A1，2026-10-09 修订）：
- **可信事件边界**：trusted workflow 只订阅 `pull_request_target`
 （opened/synchronize/reopened/ready_for_review/**edited**），workflow 定义
  恒来自 base。`pull_request_review` 绝不用于产生 required check。
- **A1 无自动合并**：所有 PR 由 Human 手动合并。Human 合并前必须人工确认
  （见 A1_SECURITY_CONTRACT.md §3）：CI 在当前 HEAD 全绿、审批有效、
  无 `status:hold`、风险等级相符。**Human 即 A1 的动态授权控制器。**
- **动态授权控制器（shelve 至 Phase C）**：`reverify.py`、`ghutil.py`、
  `pr-gate-reverify.yml` 已封存。Phase C 启用独立授权控制器与 T0 自动合并时，
  需重新审查 TOCTOU、App 身份、并发控制。
- **diff 真实性**：bundle 的 diff 节为规范格式（`canonical_diff.py`，
  base 唯一来源），可信层逐字节重算核对；重命名显示 from/to 双路径；
  行内 `␍`（U+240D）表示真实回车；文件模式变化显式标注并参与 digest。
  审查人看到的 diff 文本即机器验证过的文本。
- 门禁失败后的恢复：**Actions 页面对本次运行点 "Re-run failed jobs"**。
  不要用 workflow_dispatch。

## 可信签发纪律（防 confused deputy）

- freeze / review 的 marker 必须位于评论正文第一行（policy-gate 强制）。
- 可信作者（Human 或 bot）**绝不在评论中引用/转述**含 `mode2-freeze` /
  `mode2-review` marker 的不可信文本；引用即可能被误认为正式签发。
- 可信身份只防伪造，不证独立性：审查的独立性由 Web 隔离会话流程 + Human
  抽查保证（见 digest-schema.md）。

## Web 调用健康

- 出题/审查/诊断调用失败 → ENV 分类 → 指数退避重试（最多 3 次）
- 仍失败 → 降级 Human relay（Muse 给 prompt 全文 → Human 粘贴 → Human 贴回原文）
- 连续失败 → hold，@Human
