# 暂定技术决策（2026-10-16，待 Human 最终确认）

> 本文档记录本次整合收尾 PR 中的暂定技术决策。所有决策均为**暂定**，
> Human 可在 Merge 前修改或否决。正式生效以 Human 裁定为准。

## D1. Google 原生文档：继续 HOLD

**决策**：Google 原生文档（Docs/Sheets/Slides）在无法提供必需证据时，
`drive_move` 继续 HOLD（fail-closed），不执行移动。

**依据**：
- Drive API 对原生文档不返回 `md5Checksum`；`drive.get()` 明确拒绝原生文档的原始下载。
- `DriveEvidenceAdapter.meta()` 对原生文档返回 `sha256=None`，下游 `_require_exact` 拒绝。
- **铁律**：禁止将 MD5 冒充为 SHA-256；禁止为放行而放宽 fail-closed。

**如原版需求需要支持原生文档**，以下为可验证的替代证据方案（需 Human 二选一）：

| 方案 | 证据组合 | 可验证性 | 精确影响 |
|---|---|---|---|
| A（维持 HOLD） | 无 | 不适用 | 原生文档永不参与 `drive_move`；如业务需要移动原生文档，需另立需求 |
| B（修订版本证据） | `headRevisionId` + `version` + `modifiedTime` 三元组 | 可经 Drive API 独立读取验证 | 需修改 `drive_move_plan.py` 的证据 schema（`sha256` 改为可选，新增 `revision_triple`）；需更新 `_require_exact` 逻辑；需独立 Q2 重审；**降低**了内容完整性保证（无哈希） |

**Muse 建议**：选 A（维持 HOLD），除非业务明确要求移动原生文档。
**禁止**：Muse 自行删除"支持原生文档"的需求（如原版需求中存在）。

## D2. DEFAULT_EVIDENCE_MAX_BYTES = 100 MiB（测试候选上限）

**决策**：`100 MiB` 暂作为**测试候选上限**，不宣告为生产默认值。

**资源边界证据**：
- `DriveEvidenceAdapter.meta()` 使用 `drive.get_bounded(file_id, max_bytes)`；
  超限时下载中止 → `sha256=None` → 下游 fail-closed（已由
  `test_adapter_meta_oversize_file_sha256_unavailable` 覆盖）。
- 内存使用：`get_bounded` 在内存中累积至多 `max_bytes + 1 chunk`
 （见 `drive.py:get_bounded` 文档）；100 MiB 上限对应单次证据读取至多约 101 MiB 内存。

**待 Human 确认**：
- 生产默认值（可小于 100 MiB，如 10 MiB）；
- 是否需要流式哈希（避免全量内存缓冲）作为后续优化。

## D3. Issue #175：建议关闭

PR #176 已合并（`de1df17`），独立 Q2 R5 PASS。建议 Human 关闭 Issue #175。
（详见 `docs/ISSUE_RECOMMENDATIONS_2026-10-16.md`）

## D4. Issue #157：建议按 PR1 历史交付完成关闭

**更新的建议**（取代 `ISSUE_RECOMMENDATIONS_2026-10-16.md` 中的"转下一批次"）：

Issue #157（PR1-LOCAL-TXT-LIFECYCLE）的本地 TXT 生命周期核心能力
（inbox scan/poll、49 函数的 `inbox_lifecycle.py`）已在 PR1 历史交付中完成。
建议按历史交付完成关闭，同时**保留 v2.0.4-2 全部剩余范围**
（统一调度器/worker、Drive inbox 批次等仍在 F2/F3 的 PARTIAL 中跟踪，
不因关闭 #157 而视为完成）。

## D5. C-04：最终 HEAD 差异的有界补审资料

**问题**：PR #158 历史 Q2 审查 SHA（`a804f15d…`）与最终合并 head
（`e2c8f82287f2081006ba0da526fa6136f267d08e`）不一致。

**有界补审资料准备**（Muse 可在 Human 授权后执行）：
```bash
git diff a804f15d e2c8f822 --stat
```
输出差异文件清单，Human 判断：
- (a) 仅文档/CI 修复 → 接受并记录；
- (b) 含代码变更 → 对增量做 scoped 独立 Q2；
- (c) 实质变更未审查 → 评估回滚/热修复。

**不建议**：Muse 自行定性历史问题。
