# 生产交接：批量文件归档受控执行机制

> **范围**：本阶段只准备方案、脚本和可验证机制，**不触碰真实生产 Drive 文件**。
> 生产执行前需 Human 逐项确认授权绑定（见 §3）。

## 设计原则

生产文件处理优先由 CBA-KB Engine 的受控流程执行，而不是让 ChatGPT
对每个文件单独调用 Drive move。原因：

1. Engine 的 `drive_plan.py` 已实现 exact plan → 批准 → apply → readback 的完整链路。
2. 批量执行需要统一的授权范围、时间窗口和失败恢复，单文件调用无法保证。
3. 每个文件必须留下操作与验证收据，供独立审计。

## 1. Exact Migration Manifest 的生成

Manifest 是一个 JSON 文件，精确描述一个批次的所有操作：

```json
{
  "manifest_id": "MIG-2026-10-20-001",
  "created_at": "2026-10-20T10:00:00Z",
  "created_by": "human-operator-id",
  "batch": [
    {
      "file_id": "1abc...",
      "source_parent": "1src...",
      "destination": "1dst...",
      "expected_sha256": "abc123...",
      "expected_revision_id": "rev-001",
      "expected_mime_type": "application/pdf"
    }
  ],
  "manifest_sha256": "sha256-of-this-file"
}
```

生成步骤：
1. Human 提供批准的文件 ID 列表。
2. 脚本（`scripts/generate_migration_manifest.py`，待实现）读取每个文件的
   当前元数据（`drive.meta`），填充 `source_parent`、`expected_*` 字段。
3. 计算 `manifest_sha256`（整个文件的 SHA-256，不含该字段本身）。
4. Manifest 存入版本控制或签名存储，任何修改都会改变 `manifest_sha256`。

## 2. 批准绑定到批次

每个批次绑定以下要素（缺一不可）：

| 要素 | 说明 | 绑定方式 |
|---|---|---|
| 文件 ID | 已批准的文件 | Manifest `batch[].file_id` |
| 原目录 | 文件当前父目录 | Manifest `batch[].source_parent`（生成时快照） |
| 目标目录 | 归档目标 | Manifest `batch[].destination` |
| 身份证据 | 文件内容身份 | `expected_sha256`（执行前重新校验） |
| 版本证据 | 文件版本 | `expected_revision_id`（执行前重新校验） |
| 授权引用 | Human 的批准 | `production_go` 字符串 + 批准记录（Issue/PR 评论） |
| 时间窗口 | 授权有效期 | Manifest `valid_from` / `valid_until` |

执行前，脚本验证：
- 当前文件元数据与 Manifest 快照一致（`source_parent`、`revision_id` 未变）。
- 当前时间在 `valid_from`–`valid_until` 内。
- `production_go` 与批准记录中的引用一致。

任一不符 → 拒绝执行该文件（fail-closed），记录到收据。

## 3. 授权范围限定

`production_go` 的有效性依赖三重绑定：

1. **对象绑定**：`allowed_folders` 必须包含该批次的所有 `source_parent`
   和 `destination`。不在 allowlist 中的目录 → 拒绝。
2. **动作绑定**：`production_go` 仅授权 `drive_move` 操作，不授权删除、
   分享、权限修改。其他操作类型 → 拒绝。
3. **时间绑定**：超出 `valid_until` 的 `production_go` → 拒绝。

**重要**：代码中的非空 `production_go` 字符串**不等于** Human 有效批准。
正式生产执行前，必须确认：
- 该 `production_go` 与某次 Human 批准记录（Issue/PR/书面）对应；
- 批准范围（文件列表）与 Manifest 一致；
- 批准摘要（`manifest_sha256`）与执行时一致；
- 执行身份（OAuth 账号）与批准时指定的身份一致。

## 4. 逐批次执行与收据

一次只执行一个获批批次：

```bash
python scripts/apply_migration_batch.py \
    --instance-root /path/to/prod/instance \
    --manifest /path/to/MIG-2026-10-20-001.json \
    --production-go "PROD-GO-2026-10-20-001" \
    --allowed-folders "<SRC>,<DST>" \
    --receipt /path/to/receipt.json
```

每个文件生成收据条目：

```json
{
  "file_id": "1abc...",
  "op": "drive_move",
  "pre": {"parents": ["1src..."], "sha256": "abc123...", "revision_id": "rev-001"},
  "post": {"parents": ["1dst..."], "sha256": "abc123...", "revision_id": "rev-001"},
  "verified": true,
  "at": "2026-10-20T10:05:00Z"
}
```

## 5. 失败停止、恢复、对账、残留报告

- **失败停止**：任一文件 `apply` 失败或 `readback` 不符 → 立即停止本批次
  剩余文件（fail-closed），不继续。
- **恢复**：已移动的文件**不自动回滚**（回滚本身也是写操作，需单独批准）。
  收据记录每个文件的 `pre`/`post` 状态，供人工决策。
- **对账**：批次完成后，对比 Manifest 与收据：
  - 全部 `verified=true` → 批次完成。
  - 有 `verified=false` 或缺失条目 → 生成差异报告。
- **残留报告**：未执行的文件（因前序失败停止）列出清单，下次批次需重新批准
  （`manifest_sha256` 可能已变，需重新生成）。

## 6. ChatGPT 独立审核

### 迁移前审核

ChatGPT（独立会话，不共享执行上下文）审核：

1. 读取 Manifest，验证 `manifest_sha256`。
2. 对每个文件，独立调用 Drive API 读取当前元数据。
3. 验证：`source_parent` 与 Manifest 一致；`revision_id` 未变；
   `sha256`（如可计算）与 `expected_sha256` 一致。
4. 验证 `production_go` 与批准记录对应。
5. 输出审核结论：`APPROVED_FOR_BATCH` 或 `HOLD`（列出不符项）。

### 迁移后 Readback

1. 对每个收据条目，独立调用 Drive API 读取当前 `parents`。
2. 验证 `parents` 与收据 `post.parents` 一致。
3. 抽查 `sha256`（下载内容计算，或对比 `md5Checksum` 变化）。
4. 输出：`READBACK_VERIFIED` 或 `MISMATCH`（列出差异）。

## 7. Codex 接管执行的交接

如果由 Codex 接管生产执行，需要：

### 运行主机

- 能访问 Private Instance 的主机（OAuth token 在本机）。
- Python 3.11+，已安装 `requirements.txt`。
- 网络可达 `www.googleapis.com`。

### 凭证配置

1. Private Instance 目录（含 `credentials.json` / `token.json`，权限 600）。
2. Human 已在该主机执行 `make auth`（Codex 不触碰交互式授权）。
3. Codex 仅读取 token，不复制、不传输、不提交到 Git。

### 命令

```bash
# 1. 生成 Manifest（Human 提供文件列表后）
python scripts/generate_migration_manifest.py \
    --instance-root /path/to/prod/instance \
    --file-list /path/to/approved-files.txt \
    --output /path/to/MIG-2026-10-20-001.json

# 2. ChatGPT 独立审核（见 §6）

# 3. 执行批次（Human 批准后）
python scripts/apply_migration_batch.py \
    --instance-root /path/to/prod/instance \
    --manifest /path/to/MIG-2026-10-20-001.json \
    --production-go "PROD-GO-2026-10-20-001" \
    --allowed-folders "<SRC>,<DST>" \
    --receipt /path/to/receipt.json

# 4. ChatGPT 独立 readback（见 §6）
```

### 交接文件

| 文件 | 说明 | 敏感度 |
|---|---|---|
| `MIG-*.json` | 迁移 Manifest | 含文件 ID，可传输 |
| `approved-files.txt` | Human 批准的文件列表 | 可传输 |
| `receipt.json` | 执行收据 | 可传输（脱敏后） |
| Private Instance 路径 | 仅路径，不含 token 内容 | 路径可传输，token 不可 |
| `production_go` 引用 | 授权字符串 | 仅 Human 提供，不在聊天中传输 |

### 禁止

- Codex 不得索取生产 OAuth token 内容。
- Codex 不得在聊天中接收 `credentials.json` / `token.json`。
- Codex 不得修改 Manifest（`manifest_sha256` 会暴露篡改）。
- Codex 不得跳过 ChatGPT 独立审核直接执行。

---

## 当前状态

`PRODUCTION_HANDOFF_PREPARED` —— 机制文档完成；
`scripts/generate_migration_manifest.py` 和 `scripts/apply_migration_batch.py`
待 M5 阶段实现（需生产授权）。
**未触碰任何生产 Drive 文件。**
