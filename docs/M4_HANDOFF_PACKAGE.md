# M4 隔离测试交接包：Human 最小授权清单

> `scripts/m4_drive_e2e.py` 已就绪。以下信息到位后可立即执行。
> **不得索取**：生产文件夹 ID、生产 OAuth token、生产 GO。

## 需要 Human 提供的最小信息

| # | 信息 | 说明 | 示例 |
|---|---|---|---|
| 1 | 隔离测试 folder ID | 新建空文件夹，仅用于 M4；脚本验证其为 folder 类型 | `1abc...xyz` |
| 2 | 允许的合成样本 | 确认可使用 `M4_E2E_SYNTHETIC_` 前缀的合成文件（脚本自动创建/清理） | 确认即可 |
| 3 | OAuth 测试身份 | Human 本机执行 `make auth` 完成授权的 Private Instance 路径 | `/path/to/instance` |
| 4 | TEST 授权引用 | 本次 M4 运行的隔离测试授权字符串（`--test-go` 参数值） | `M4-TEST-2026-10-16-001` |
| 5 | 生产文件夹 ID 列表 | 用于 `--production-folders` 隔离 guard（仅 ID，不索取访问权限） | `1prod...` |
| 6 | 有效期 | 本次测试授权的有效时间窗口 | `2026-10-16 06:00–08:00 UTC` |

## 精确测试动作（脚本将执行）

1. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_report.txt`（合成内容）。
2. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_dest` 子文件夹。
3. 经 `DriveEvidenceAdapter` 读取真实证据并校验 SHA-256。
4. 构建 exact plan → 批准 → `apply_plan`（`test_go` + allowlist）→ 独立 readback。
5. 删除测试文件（`--keep` 可保留）。

## 安全保证

- 所有操作限制在 `--test-folder` 内；`allowed_folders=[test_folder, dest_id]`。
- TEST 模式触碰 `production_folders` → 拒绝，零写入（已测试）。
- `test_go` 与 `production_go` 互斥；TEST 引用永不能授权生产写入。
- 任何失败 → 非零退出；测试文件尽力清理。

## 当前状态

`M4_READY_FOR_AUTH` —— 代码、测试、脚本、文档全部就绪；
**唯一阻断**：上表 6 项 Human 信息。
