# Mode 2.1 A1 阶段安全契约（冻结版）

- 版本：A1-contract v1.1（已冻结，2026-10-09）
- 状态：**已冻结**。为 A1 阶段的唯一验收依据；修改需 Human 批准并走修订流程
- 依据：Mode 2.1 v2.1 §3（控制器分阶段）、§18（门槛驱动）、独立流程复审（2026-10-09）
- 定位：A1 = 最小可信门禁。**不提供自动合并，不提供动态授权控制器。**

---

## 0. 一句话

A1 只保证"合并前的静态检查正确"；"合并那一刻的状态是否仍有效"由 Human 人工确认。
动态授权一致性是 Phase C 的问题，A1 不承诺、不断言、不实现。

---

## 1. A1 保证的安全性质（全部必须满足）

| # | 性质 | 验证方法 |
|---|---|---|
| P1 | 任何 PR 合并前，可信门禁必须在其当前 HEAD 上完整通过 | 真实 PR 测试：改 HEAD 后旧绿灯失效 |
| P2 | T2 路径的 PR 永不进入轻量流（硬阻断） | 单元测试 + 真实 PR（改 `.github/`） |
| P3 | 测试文件（`tests/**/*.py`）的任何字节修改都被拦截 | 内容哈希比对测试 |
| P4 | 风险等级只可上调不可下调（max 规则） | 重命名/未知路径测试 |
| P5 | 新 commit 自动作废旧审批与旧审查结论 | SHA 绑定测试 |
| P6 | 门禁定义来自 base，PR 不能篡改门禁自身 | 双层 workflow 架构审查 |
| P7 | 未知路径 fail-closed（判 T2） | glob 矩阵测试 |

## 2. A1 明确不保证（Phase C 范畴）

- ❌ 审批在门禁通过后被撤销 → 自动阻断（A1 由 Human 合并前人工检查）
- ❌ Issue 被打 `status:hold` → 自动阻断（A1 由 Human 合并前人工检查）
- ❌ 定时复验授权状态（无 reverify 调度）
- ❌ 独立授权检查（无 `pr-gate-authz / status`）
- ❌ T0 自动合并（所有 PR 全部 Human 手动合并）

## 3. Human 在 A1 的职责（不可委托）

合并 PR 前必须人工确认：
1. CI 在当前 HEAD 全绿（看 Actions 页面，不是听汇报）
2. 审批状态有效（无撤回、无 CHANGES_REQUESTED）
3. 无 `status:hold` 标签（PR 和关联 Issue）
4. 风险等级与改动内容相符（抽查）

**Human 就是 A1 的动态授权控制器。**

## 4. 接口契约（实现前冻结）

### 4.1 Tier（风险分级）
- 输入：PR 的完整文件列表（含重命名 `status`/`previous_filename`）
- 保证：重命名时新旧双路径参评取最大；未知路径 → T2；`changed_files` 超 3000 → 阻断
- GitHub API 异常：任何失败 → fail-closed

### 4.2 Freeze（冻结）
- 绑定：需求 Issue 号 + 规格 SHA + 测试文件集合 SHA + base SHA + 签发者身份
- 保证：任一对不上 → 阻断；测试文件被改 → 阻断

### 4.3 Review（审查）
- 绑定：`reviewed_head_sha == PR 当前 head`
- 保证：SHA 不一致 → 旧审查作废；无审查证据 → INSUFFICIENT（不许 PASS）

### 4.4 CI（隔离执行层）
- 绑定：workflow run 的 `head_sha`、`conclusion`、`pull_requests` 关联
- 保证：只认 completed + success；有 in-progress 时不等旧 success

### 4.5 GitHub API 错误分类
| 错误 | 处理 |
|---|---|
| 404（资源不存在） | 预期路径（如普通 Issue 查 PR）→ 走备用逻辑；非预期 → 阻断 |
| 403（无权限） | fail-closed，阻断 |
| 429（限流） | 重试 3 次 → 仍失败则阻断 |
| 500/超时 | 重试 3 次 → 仍失败则阻断 |
| `truncated: true` | fail-closed，阻断 |

## 5. 测试要求（A1 门槛）

### 5.1 真实入口测试（必须）
- `reverify.py` 不在 A1 范围（已 shelve）
- `build-review-bundle.py` 必须在临时 Git 仓库端到端跑通（含新增/修改/删除/重命名/chmod）
- `policy-gate.py` 的每个 `fail()` 路径必须有触发测试

### 5.2 集成测试（必须）
- 模拟 GitHub API 的 404/403/429/500/truncated，验证 fail-closed
- 交错场景：HEAD 变化时旧证据作废

### 5.3 单元测试（必须）
- 现有 108 项负面测试中属于 A1 范围的必须保留
- 属于 Phase C 的（authz check、reverify 调度、TOCTOU）移至 C 阶段测试集

## 6. 有限威胁模型（A1）

### 6.1 攻击者画像
- **主要**：恶意 PR 作者，试图在不满足条件的情况下合并代码
  - 能力：可改 PR 内容、可改 PR 描述、可评论、可撤回自己的审批
  - 不能：不能改 base 分支、不能改他人审批、不能拿 admin 权限
- **次要**：误操作的开发者（传错文件、忘跑测试）

### 6.2 明确不防
- GitHub 平台自身被攻破
- Human 恶意合并（Human 是信任根）
- 开发者本机被攻破（sandbox 假设）
- 社会工程学（骗 Human 点合并）

### 6.3 安全边界
- 可信层（`pull_request_target`）：不执行 PR 代码，只读 base
- 隔离层（`pull_request`）：可执行 PR 代码，无 secrets、最小权限
- 两层不共享写权限

## 7. A1 验收标准（全部通过才进入 B）

- [ ] §1 的 7 项安全性质全部有测试覆盖且通过
- [ ] §5 的真实入口 + 集成测试全部通过
- [ ] 真实 PR 红队测试：在测试仓库实际操作以下攻击，全部被阻断
  - 改 `.github/workflows/` 试图降级门禁
  - 重命名 T2 文件到 T0 路径
  - 修改测试文件伪造绿 CI
  - 用旧 SHA 的审查结论合并新 commit
  - 未知路径文件混入 PR
- [ ] Human 合并检查清单（§3）文档化并试执行 3 次
- [ ] `pull_request_target` 的 11-02 事件策略已确认

## 8. 从 rev10 的代码处置

| 代码 | 处置 |
|---|---|
| `tiering.py`、`risk-tiers.yml` | 保留（A1 核心） |
| `policy-gate.py` 核心检查（tier/freeze/review/CI/SHA） | 保留，移除 authz 发布 |
| `canonical_diff.py`、`build-review-bundle.py` | 保留（证据链） |
| 双层 workflows（简化版） | 保留，移除 reverify 相关 |
| `reverify.py`、`ghutil.py`（authz 部分） | **Shelve**（移至 `shelved/phase-c/`，不删除） |
| `pr-gate-reverify.yml` | **Shelve** |

## 9. 修订记录

- v1（2026-10-09）：初版。基于独立流程复审的四阶段方案。
- v1.1（2026-10-09）：Web bounded 审查 P3/P4 修复。P3：PR 侧测试文件绑定改用
  git 对象身份（mode + blob SHA），拦截普通文件→symlink 替换；P4：
  `status=renamed` 缺 `previous_filename` 时 fail-closed。
- v1.1.1（2026-10-09）：修复 P3 实现的 worktree 生命周期错误（v1.1 复审发现）：
  PR 侧身份采集必须在 `finally` 删除 worktree 之前完成；抽取纯函数
  `check_test_identities()`，自测新增完整生命周期回归测试。
