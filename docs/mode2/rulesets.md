# Mode 2.1 rulesets 配置（手动步骤 + #10 实测清单）

> 对应执行手册 §16、§19-10。目标：解决"T0 自动合并 vs 全局 1 approval"的死结（审计 P0-3）。
> DRAFT：以下为待实测验证的配置方案，#10 实测通过前不作为生效依据。

## 现状问题

经典 branch protection 的"required approving reviews: 1"对所有 PR 一刀切；
若设为 1，T0 无法零人工自动合并；若设为 0，T1/T2 失去人工闸门。

## 拟采用方案（待 #10 实测）

1. main 分支 ruleset（或经典 branch protection）：
   - Require a pull request before merging：**开**
   - Required status checks：`pr-gate-trusted / policy`、`pr-gate-untrusted / evidence`
     （必过；注意 check 名是 "workflow 名 / job id"）
   - **A1 无自动合并**：所有 PR 由 Human 手动合并；动态授权检查由 Human 执行
     （见 A1_SECURITY_CONTRACT.md §3）
   - Require branches to be up to date before merging：开
   - Restrict deletions / Require linear history：按需
   - **不**全局强制 human approval 数量（审批按 tier 差异化，见下）
2. 审批差异化（policy-gate.py 强制执行，required check 阻断）：
   - **在 #10 实测通过前，所有 tier（含 T0）均需 ≥1 个针对当前 head 的 Human approval**
     （回应 Web 独立审查"T0 零审批放行"：机制未被证明前不授予特权）
   - #10 通过后：纯 T0 可由 policy-gate App 审批满足（机器审批：基于已核验的证据，
     非 bypass，可审计；App：cba-kb-mode2-bot，App ID 5245332）
   - T2 路径：CODEOWNERS 要求 @SkyCjq review（Require review from code owners：开）
3. 禁止 bypass（含 admin）

## #10 实测清单（A1 验收；全部通过才进入 B）

- [ ] ruleset 按上述配置生效，`pr-gate-trusted` 为 required check
- [ ] T1 PR：无 Human approval 时 pr-gate-trusted 变红，无法合并
- [ ] T2 PR：CODEOWNERS 要求 Human review，无则阻断
- [ ] 试改 `.github/workflows/` 的 PR：可信层定义不受影响（来自 base）
- [ ] admin 尝试 bypass 被拒绝
- [ ] **负面验收**：用真实 PR 分别验证——未知路径混合提交
      （Dockerfile+README.md 必须判 T2）、过期审批（APPROVED 后
      CHANGES_REQUESTED 同 SHA 必须阻断）、伪造 freeze 评论（非可信作者/
      marker 不在开头必须忽略）、旧成功 CI + 新失败 CI（必须取最新一次）、
      CODEOWNERS 缺 review、管理员 bypass、首次骨架合并
- [ ] **隔离层绑定语义**：确认 `pr-gate-untrusted` 的 workflow run 的
      `head_sha` / `pull_requests` 字段与 policy-gate 的绑定逻辑一致
- [ ] **Actions 事件策略**：确认 `pull_request_target` 被允许
      （2026-11-02 强制策略前检查）
- [ ] **审批权限 API**：验证 GITHUB_TOKEN 能否调用
      `GET /repos/{R}/collaborators/{u}/permission`（失败即 fail-closed）
- [ ] **Human 合并检查清单**：按 A1_SECURITY_CONTRACT.md §3 试执行 3 次，
      记录耗时

> Phase C（动态授权控制器 + T0 自动合并）需另行验收，不在 A1 范围。

## 回退

任一实测不通过 → 回到"所有 PR 需 1 Human approval"，T0 自动合并暂缓（#3 授权暂停生效）。
