# M4: 真实隔离 Drive E2E 准备清单

> 状态：`PREPARED / EXTERNAL_AUTH_BLOCKED` —— 脚本与数据已就绪，等待 Human 提供隔离测试 Drive 授权后执行。

## 1. 可执行脚本

`scripts/m4_drive_e2e.py` —— 真实 Drive E2E 全链路脚本。

执行条件（缺一即 fail-closed，不写任何数据）：
1. `--instance-root`：含有效 Drive OAuth 的 Private Instance（`token.json` 存在）。
2. `--test-folder`：隔离测试文件夹 ID；脚本验证其为 folder 类型。
3. `--test-go`：隔离 TEST 授权引用（**不是** production GO；两者互斥）。
4. `--production-folders`：已知生产文件夹 ID 列表；**必填**；
   未提供时脚本拒绝执行（未知生产范围时拒绝真实 Drive 写入）。

授权模型：
- TEST（`--test-go`）与 PRODUCTION（`--production-go`）互斥，不可同时存在。
- `allowed_folders` 在两种模式下均为必填；本次 M4 自动设为 `[test_folder, dest_id]`。
- TEST 模式下触碰 `production_folders` 中的任何文件夹 → 拒绝，零写入。

执行流程：
1. 记录认证身份（`about().get(user)`），操作员核验为隔离测试账号。
2. 创建合成测试文件 `M4_E2E_SYNTHETIC_report.txt`（仅测试文件夹内，显式 allowlist）。
3. 创建目标子文件夹 `M4_E2E_SYNTHETIC_dest`（仅测试文件夹内）。
4. 捕获移动前元数据；经 `DriveEvidenceAdapter` 读取真实证据。
5. 构建 exact plan → 人工批准记录 → `apply_plan`（`test_go` + allowlist）→ 独立 readback。
6. 捕获移动后元数据；比对前后差异。
7. 清理测试文件；**清理失败时记录残留 ID 并返回 exit 3，不报告完整 PASS**。
8. 写 JSON 收据（含步骤、证据、残留 ID）。

退出码：`0`=全过且清理完成；`1`=环境/授权/隔离失败；`2`=E2E 断言失败；
`3`=清理未完成（残留文件，需人工恢复）。

部分执行说明：
- 测试文件可能在创建后、移动前失败（如证据不符），此时文件**已存在**于 Drive。
- 不再声称"任何失败都不会留下文件"或"失败前保证零写入"。
- 残留文件通过收据 `residual_file_ids` 报告；恢复步骤：从测试文件夹手动删除残留 ID 后重跑。

## 2. 测试数据

| 数据 | 内容 | 说明 |
|---|---|---|
| 测试文件 | `M4_E2E_SYNTHETIC_report.txt` | 合成文本，`b"M4_E2E_SYNTHETIC content 2026-10-16"`，SHA-256 本地可算 |
| 目标文件夹 | `M4_E2E_SYNTHETIC_dest` | 测试文件夹内的子文件夹 |
| 文件名前缀 | `M4_E2E_SYNTHETIC_` | 清理时识别；不得用于生产文件 |

不使用真实业务数据、不使用生产文件。

## 3. 隔离范围

- **允许操作的文件夹**：Human 指定的隔离测试文件夹（待提供 ID）及其脚本内创建的子目录。
- **文件创建白名单**：脚本内显式 `allowed_create_folders`（初始仅 test folder）；
  每次创建前校验目标在白名单内。`production_folders` 黑名单仅为纵深防御，
  不是主要控制手段。
- **禁止**：测试文件夹之外的任何 Drive 内容；特别是生产归档文件夹。
- 脚本内 Gate 1b/3/4：`--production-folders` 解析后必须非空；验证 `--test-folder`
  为 folder 类型且不与生产文件夹重合；Human 须在运行前确认该文件夹为隔离测试用途。
- **账号隔离**：优先使用与生产数据隔离的测试 Google 账号或单独限制权限的测试环境。
  脚本启动时记录认证用户邮箱，操作员必须核验为测试账号。Full Drive scope
  不等于允许访问整个账号——所有操作仍受文件夹白名单约束。
- 所有 API 调用均带 `supportsAllDrives=True`（与现有 `drive.py` 一致）。

## 4. OAuth Scopes 清单

| Scope | 用途 | 代码位置 |
|---|---|---|
| `https://www.googleapis.com/auth/drive` | Drive 文件读写（创建/移动/删除测试文件、读取元数据） | `src/cba_kb/drive.py: SCOPES` |

说明：
- 当前 `drive.py` 使用 full drive scope；M4 沿用，不另行收窄（收窄需 Human 决策并回归测试）。
- 凭证保存在 Private Instance（`credentials.json` / `token.json`），**不进 Git**（`scripts/check_secrets.py` 覆盖）。
- 交互式授权（`make auth`）仅在 Human 本机执行，Muse 不触碰。

## 5. 仍需 Human 的事项（M4 执行前）

1. **提供隔离测试文件夹 ID**（新建空文件夹，仅用于 M4）。
2. **确认该文件夹不含生产数据**，且与生产归档文件夹隔离。
3. **提供或确认隔离测试 Google 账号**（优先与生产数据隔离的测试账号）；
   **完成 `make auth`**（Human 本机），使 Private Instance 具备有效 token。
4. **给出本次 M4 运行的 TEST 授权引用**（`--test-go` 参数值）及有效期。
   注意：`test_go` 仅授权本次隔离测试，**不能**授权生产操作。
5. **提供已知生产文件夹 ID 列表**（`--production-folders`，必填）。
6. 可选：确认 `DEFAULT_EVIDENCE_MAX_BYTES = 100 MiB` 是否合适。

## 6. 与 M4 无关但已完成的离线验证

- `tests/test_drive_evidence_contract.py`（14 项）：模拟原始 Drive API 响应，覆盖 adapter 工厂、元数据转换、MD5≠SHA-256、原生文档 fail-closed、缺失证据拒绝、reparent 接口、readback。
- 真实 `googleapiclient` 未安装环境下的测试失败为**预先存在的环境缺失**，与本次改动无关。
