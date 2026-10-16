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
1. 创建合成测试文件 `M4_E2E_SYNTHETIC_report.txt`（仅测试文件夹内）。
2. 创建目标子文件夹 `M4_E2E_SYNTHETIC_dest`（仅测试文件夹内）。
3. 经 `DriveEvidenceAdapter` 读取真实证据（sha256/revision_id/mime_type）。
4. 构建 exact plan → 人工批准记录 → `apply_plan`（production_go）→ 独立 readback。
5. 清理测试文件（`--keep` 可保留供人工检查）。

退出码：`0`=全过；`1`=环境/授权/隔离失败（未写入）；`2`=E2E 断言失败。

## 2. 测试数据

| 数据 | 内容 | 说明 |
|---|---|---|
| 测试文件 | `M4_E2E_SYNTHETIC_report.txt` | 合成文本，`b"M4_E2E_SYNTHETIC content 2026-10-16"`，SHA-256 本地可算 |
| 目标文件夹 | `M4_E2E_SYNTHETIC_dest` | 测试文件夹内的子文件夹 |
| 文件名前缀 | `M4_E2E_SYNTHETIC_` | 清理时识别；不得用于生产文件 |

不使用真实业务数据、不使用生产文件。

## 3. 隔离范围

- **允许操作的文件夹**：Human 指定的隔离测试文件夹（待提供 ID）。
- **禁止**：测试文件夹之外的任何 Drive 内容；特别是生产归档文件夹。
- 脚本内 Gate 3/4：验证 `--test-folder` 为 folder 类型；Human 须在运行前确认该文件夹为隔离测试用途。
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
3. **完成 `make auth`**（Human 本机），使 Private Instance 具备有效 token。
4. **给出本次 M4 运行的 Production GO 引用**（`--production-go` 参数值）。
5. 可选：确认 `DEFAULT_EVIDENCE_MAX_BYTES = 100 MiB` 是否合适（见下文 Human 裁定事项）。

## 6. 与 M4 无关但已完成的离线验证

- `tests/test_drive_evidence_contract.py`（14 项）：模拟原始 Drive API 响应，覆盖 adapter 工厂、元数据转换、MD5≠SHA-256、原生文档 fail-closed、缺失证据拒绝、reparent 接口、readback。
- 真实 `googleapiclient` 未安装环境下的测试失败为**预先存在的环境缺失**，与本次改动无关。
