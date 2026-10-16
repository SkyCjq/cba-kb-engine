# 65 项需求核对更新 + Issue 关闭/补审建议

**日期**：2026-10-16
**基线**：`de1df173096dbbde4173dbeeb2b38849e6d795d4`（PR #176 已合并）
**原则**：不替 Human 裁定；只给建议 + 证据。

---

## 一、PR #176 合并后状态变更的关键条目

| ID | 旧状态（10-15） | 新状态 | 依据 |
|---|---|---|---|
| F6 | IMPLEMENTED_NOT_VERIFIED | IMPLEMENTED_NOT_E2E_VERIFIED | PR #176 合并；Q2 R5 PASS（head `19c25634`）；新增 `DriveEvidenceAdapter` + 14 项离线契约测试；真实 Drive E2E 仍待 M4 |
| F8 | NOT_IMPLEMENTED | PARTIAL | `scripts/m4_drive_e2e.py` + `docs/M4_DRIVE_E2E_PLAN.md` 已准备；执行仍待授权 |
| R7 | （待查） | IMPLEMENTED_NOT_E2E_VERIFIED | `ops_metrics.py` 三比率已合并（PR #176） |

其余 62 项未因本次合并改变状态；完整重核需独立任务。

---

## 二、Issue #175 关闭建议

**标题**：[2.0.4-2] FINAL INTEGRATION: Drive move wiring + unified CLI
**当前**：OPEN
**建议**：**CLOSE as completed**

证据链：
1. PR #176 已合并（merge SHA `de1df173096dbbde4173dbeeb2b38849e6d795d4`）。
2. 独立 Q2 R5 PASS（最终 HEAD `19c25634`，含 README 确定稿）。
3. CI 全绿（README 不影响测试；drive 相关 46 项全过）。
4. 范围内全部交付：M2（drive_move 接入）、M3（workflow-status CLI）、F-04（CLI `--production-go`/`--drive-adapter`）、F-05（ops_metrics 三比率）、F-06（workflow-status 状态推导）、F-01/F-02/F-03 修复。

**Human 动作**：在 Issue #175 下评论关闭（Muse 无 Issues:write 权限，无法代关）。

---

## 三、Issue #157 处置建议

**标题**：[v2.0.4] PR1-LOCAL-TXT-LIFECYCLE: local-first inbox scan/poll
**当前**：OPEN
**建议**：**保持 OPEN，转入 v2.0.4-2 后续批次；或 Human 决定拆分**

理由：
1. Issue 范围是 v2.0.4 的本地 TXT 生命周期（inbox scan/poll），与 PR #176 的 Drive move 接入无直接依赖。
2. `inbox_lifecycle.py`（49 函数）与 `drive_poll.py` 已存在，但统一调度器/worker 缺失（F2 仍为 PARTIAL）。
3. 不建议在当前整合分支上追加；建议 Human 决定：(a) 为其单独开 Issue 走 Bot Broker 下一 Slice；(b) 延期至 v2.0.4-2 下一阶段。

**不建议**：直接关闭（功能未完整验收）。

---

## 四、C-04（R11 HEAD 差异）补审建议

**问题**：PR #158 历史 Q2 审查 SHA（`a804f15d…`）与最终合并 head（`e2c8f82287f2081006ba0da526fa6136f267d08e`）不一致。

**建议**：**Human 裁定以下三选一**

(a) **接受**：差异为合并过程中的常规提交（如 README/CI 修复），无实质代码变更 → 记录接受理由后关闭。
(b) **补审**：对 `e2c8f822` 做一次 scoped 独立 Q2（仅 diff 增量），确认无引入新风险。
(c) **回滚重审**：如差异含实质代码变更且未经审查 → 评估回滚或热修复。

**Muse 可执行**：`git diff a804f15d e2c8f822 --stat` 输出差异清单供 Human 判断（需 Human 授权后执行）。

**不建议**：Muse 自行定性（历史问题，超出当前授权）。

---

## 五、仍需 Human 的最小授权与决策（汇总）

| # | 事项 | 类型 |
|---|---|---|
| 1 | 关闭 Issue #175（评论关闭） | 决策+执行 |
| 2 | Issue #157 处置（转下一批次 / 拆分 / 延期） | 决策 |
| 3 | C-04 三选一裁定 | 决策 |
| 4 | M4 隔离测试文件夹 ID + `make auth` + `--production-go` 引用 | 授权 |
| 5 | 原生 Google 文档版本证据策略（HOLD / headRevisionId+version 替代） | 决策 |
| 6 | `DEFAULT_EVIDENCE_MAX_BYTES = 100 MiB` 确认或调整 | 决策 |
| 7 | Production GO（M5） | 授权 |
