# Mode 2.1 digest 证据记录格式 — rev3（回应 rev2 独立安全复审）

> 核心原则：**自述不是证据**。所有 digest 必须能从实际内容重新算出来，
> 且 freeze/review 记录必须由可信身份发布。policy-gate 只做"绑定验证"，
> 不做"内容判断"（审查质量由 Human 抽查保证）。

## 可信身份：防伪造 ≠ 证独立

`TRUSTED_AUTHORS = SkyCjq, cba-kb-mode2-bot[bot]`（trusted workflow env 传入）。

必须诚实区分两件事：
- **可信身份解决的是"伪造"问题**：随机攻击者无法冒充该集合内的身份发布
  freeze/review 记录。这是 GitHub 身份机制保证的。
- **可信身份不证明"独立审查"**：review 的独立性（审查者是否独立于实现者、
  是否认真）是**流程**保证的——Web 审查跑在全新隔离会话（关 Memory、
  单次发送、材料包原文），外加 Human 抽查。代码只验证"记录确由可信身份
  签发"，不验证审查质量。
- 因此 TRUSTED_AUTHORS 成员理论上可同时担任 PR 作者与审查签发人；
  独立性不依赖"换人"，依赖会话隔离 + Human 抽查。这是已知并接受的设计，
  不是漏洞。

## 签发格式（防 confused deputy）

marker 必须位于评论**正文开头**（去空白后），否则 policy-gate 不认：

```html
<!-- mode2-freeze {"task_id":"ISSUE-100","spec_sha256":"<64 hex>","test_sha256":"<64 hex>","test_path":"tests/acceptance/test_xxx.py","base_sha":"<40 hex>","capability":"T1","semantic":"T1"} -->
<!-- mode2-spec-start -->
<出题原文：规格 + 验收标准 + 范围外，全文>
<!-- mode2-spec-end -->
```

规则：
- 可信作者**永远把 marker 放在第一行**，绝不在正文中间引用/转述含
  `mode2-freeze` / `mode2-review` marker 的不可信文本。引用即可能被误认为
  签发（confused deputy），policy-gate 对"marker 不在开头"的评论一律忽略。
- 他人发布的同名记录：忽略（不阻断；冲突由 Human 在抽查中发现）。
  rev2 的"阻断并告警"改为"忽略"，与代码行为一致，避免文档与实现不符。

## freeze 记录（Issue 评论）

- `task_id` 必须 == `ISSUE-<n>`（n = PR body `Closes #n` 的 Issue 号），
  否则"哈希对但需求错"的记录无法过关。
- `test_path` 严格限定 `tests/acceptance/test_*.py`（单层目录，子目录不认）。
- `spec_sha256` = 规格文本区块（起止标记之间）的 sha256，由 policy-gate 重算核验。
- `test_sha256` = `test_path` 在 **PR HEAD** 上的文件字节 sha256，由 policy-gate
  经 GitHub Contents API（`?ref=HEAD_SHA`）重算核验。
  注意：验收测试由 PR commit 1 新建，base checkout 上不存在，必须走 API 取，
  不能读本地文件（rev2 代码曾误读本地，已修）。
- `capability` / `semantic`：出题模板 §7 的风险评估输出；缺失或非法值直接阻断
  （不再默认 T0）。任一 T2 → 转 13 阶段。
- 最终定级：`T_final = max(T_path, capability, semantic)`。

## review 记录（PR 评论，由可信身份发布）

```html
<!-- mode2-review {"reviewed_head_sha":"<40 hex>","verdict":"PASS","critical":[]} -->
<人类可读摘要（可选）>
```

- `reviewed_head_sha` 必须 == PR 当前 head，否则旧审查自动失效。
- `verdict` 三态：PASS / PASS_WITH_NOTES / CHANGES_REQUIRED / INSUFFICIENT。
  只有 PASS / PASS_WITH_NOTES 放行，其余 fail-closed。
- `critical`：发现的关键问题列表（数组）；Human 抽查时核对"试跑抓到真问题"。
- 审查材料（review-bundle.md）的完整性由 policy-gate 验证：
  bundle 来自可信绑定的 workflow run 的 artifact；含 TRUNCATED 标记则阻断转人工；
  bundle 内 BUNDLE_HEAD_SHA 必须 == PR HEAD。

## merge 前核验（policy-gate，全部机器执行）

1. freeze 签发者在可信身份集合内，且 marker 在评论开头；`task_id` 绑定当前 Issue
2. `spec_sha256`（评论内规格文本重算）、`test_sha256`（API 取 HEAD 版重算）一致
3. `reviewed_head_sha == PR head`；`freeze.base_sha == PR base`；事件 SHA 与 PR API 一致
4. `verdict` 为 PASS / PASS_WITH_NOTES
5. 隔离层：workflow-run API 精确绑定（workflow 名 + PR 编号 + 最新 completed 必须 success）
6. 测试完整性基线：base 版扫描器重算，PR 指标不得退化（funcs/files/asserts 不减，skips/xfails 不增）
7. `T_final != T2`；当前 head 有 Human 有效 approval（按审查人取最新状态）
8. 任一不满足 → required check 变红，阻断合并

## 已知边界（诚实标注）

- 审查的"质量"（审查人是否认真）代码无法验证 → Human 抽查 + 试跑"审查抓到真问题"验收。
- 可信身份不证明独立性（见上）；独立性由会话隔离流程 + Human 抽查保证。
- 未来 controller 独立服务化后，freeze 记录改由 controller 签发（分阶段，见执行手册 §3）。
