# M4 隔离测试交接包：Human 最小授权清单

> `scripts/m4_drive_e2e.py` 已就绪。以下信息到位后可立即执行。
> **不得索取**：生产 OAuth token、生产 GO。生产文件夹 ID 仅用于隔离 guard
> （禁止访问边界），不索取其访问权限。

## 需要 Human 提供的最小信息

| # | 信息 | 说明 | 示例 |
|---|---|---|---|
| 1 | 隔离测试 folder ID | 新建空文件夹，仅用于 M4；脚本验证其为 folder 类型 | `1abc...xyz` |
| 2 | 允许的合成样本 | 确认可使用 `M4_E2E_SYNTHETIC_` 前缀的合成文件（脚本自动创建/清理） | 确认即可 |
| 3 | OAuth 测试身份 | 隔离测试 Google 账号；Human 本机执行 `make auth` 完成授权的 Private Instance 路径 | `/path/to/instance` |
| 4 | TEST 授权引用及有效期 | 本次 M4 运行的隔离测试授权字符串（`--test-go` 参数值）+ 有效时间窗口 | `M4-TEST-2026-10-16-001` / `2026-10-16 06:00–08:00 UTC` |
| 5 | 禁止访问的文件夹边界 | 已知生产文件夹 ID 列表，用于 `--production-folders` 隔离 guard（仅 ID，不索取访问权限）；解析后必须非空 | `1prod...` |

## 确切运行命令

```bash
python scripts/m4_drive_e2e.py \
    --instance-root /path/to/private/instance \
    --test-folder <ISOLATED_TEST_FOLDER_ID> \
    --test-go "M4-TEST-2026-10-16-001" \
    --production-folders "<PROD_FOLDER_ID_1>,<PROD_FOLDER_ID_2>" \
    --receipt /tmp/m4-receipt.json
```

## 测试账号与 Private Instance 配置步骤

1. （Human 本机）准备隔离测试 Google 账号（优先与生产数据隔离）。
2. 在 Private Instance 的 credentials 目录放置 Desktop OAuth client JSON（`credentials.json`）。
3. 运行 `make auth` 完成交互式授权，生成 `token.json`（权限 600）。
4. 确认 `token.json` 存在且 scope 包含 `https://www.googleapis.com/auth/drive`。
5. 将 Private Instance 路径告知执行者（仅路径，不传输 token 内容）。

## 精确测试动作（脚本将执行）

1. 记录认证身份邮箱，操作员核验为测试账号。
2. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_report.txt`（合成内容，白名单校验）。
3. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_dest` 子文件夹。
4. 捕获移动前元数据；经 `DriveEvidenceAdapter` 读取真实证据并校验 SHA-256。
5. 构建 exact plan → 批准 → `apply_plan`（`test_go` + allowlist）→ 独立 readback。
6. 捕获移动后元数据；比对前后差异。
7. 清理测试文件；清理失败记录残留 ID（exit 3）。
8. 写 JSON 收据（含步骤、证据、残留）。

## 操作前后元数据比较（收据字段）

- `pre_move_metadata` / `post_move_metadata`：`parents`、`mimeType`、
  `headRevisionId`、`md5Checksum`。
- `evidence`：`sha256`、`revision_id`、`mime_type`（经证据适配器）。
- `source_sha256_local`：本地计算的合成内容 SHA-256（应与 `evidence.sha256` 一致）。

## 出错后恢复步骤

1. 查看收据 `residual_file_ids`。
2. 从测试文件夹手动删除残留文件 ID。
3. 检查收据 `steps` 确定失败点（证据/apply/readback/清理）。
4. 修复原因后重跑（`--keep` 可保留现场供检查）。

## 测试日志、JSON 收据与脱敏证据输出

- 收据（`--receipt`）：JSON，含步骤、证据、残留 ID、认证邮箱、scope。
- 脱敏：收据中的 `sha256` 仅打印前 16 位（日志）；完整值仅在收据文件内。
- 不记录：OAuth token 内容、生产文件夹内容。

## 安全保证

- 所有操作限制在 `--test-folder` 内；文件创建经显式白名单校验。
- TEST 模式触碰 `production_folders` → 拒绝（已测试）。
- `test_go` 永不能授权生产写入；与 `production_go` 互斥。
- 清理失败 → exit 3 + 残留 ID，不报告完整 PASS。

## 当前状态

`M4_READY_FOR_AUTH` —— 代码、测试、脚本、文档全部就绪；
**唯一阻断**：上表 5 项 Human 信息。
