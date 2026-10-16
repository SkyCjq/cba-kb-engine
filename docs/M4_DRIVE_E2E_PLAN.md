# M4: 真实隔离 Drive E2E 准备清单

> 状态：`PR184_SCOPE_DECISION_APPLIED_AWAITING_INDEPENDENT_Q2` —— Human 已仅对
> M4 隔离测试执行器接受 `LOCAL_SAME_UID_LEDGER_ROLLBACK`；F-01/F-02 修复及所有
> Drive/授权边界保持不变。该裁定不是 Q2 PASS、Merge Approval 或真实 Drive TEST GO。

## 1. 可执行脚本

`scripts/m4_drive_e2e.py` —— 真实 Drive E2E 全链路脚本。

执行前置关卡（首次写入前的安全阻断，缺一即 fail-closed，保证首次创建前 0 Drive 写入）：
1. `--instance-root`：含有效 Drive OAuth 的 Private Instance（`token.json` 存在）。
2. `--test-folder`：隔离测试文件夹 ID；脚本验证其为 folder 类型。
3. `--test-go`：隔离 TEST 授权引用（**不是** production GO；两者互斥）。
4. `--expected-account`：获批测试邮箱，运行时与 Google `about.user` 精确匹配。
5. `--approval-record` + `--approval-signature`：Human 本机批准记录及其 Ed25519
   签名（64-byte 原始签名的单行 base64），绑定一次性 `authorization_id`、最长两小时的
   有效期、最终合并 Git SHA、账号、folder、生产边界摘要与隔离模式；Human 私钥不交给
   Codex，PEM 公钥信任锚须由 Human/系统管理员安装到固定路径
   `/etc/cba-kb/m4_approval_ed25519_public.pem`，且为非符号链接、root-owned、group/world 不可写。
6. 隔离模式二选一：`production-boundary` 要求真实 `--production-folders`；
   完全独立账号使用 `dedicated-account`，不得填写虚构生产 ID。
7. `--receipt`：必填；运行前探测可写，结束时原子写入并回读。

授权模型：
- TEST（`--test-go`）与 PRODUCTION（`--production-go`）互斥，不可同时存在。
- `allowed_folders` 在两种模式下均为必填；本次 M4 自动设为 `[test_folder, dest_id]`。
- TEST 模式下触碰 `production_folders` 中的任何文件夹 → 拒绝，零写入。

执行流程：
1. 使用受保护的 `/usr/bin/git` 成功 fetch 后，校验 Human 签名授权与当前时间、干净
   `main == origin/main`、账号及 folder 完全一致；验签与 JSON 解析使用同一份授权字节流。
2. 读取认证身份（`about().get(user)`）并与获批账号精确比较；不一致时零写入。
3. 以 `O_CREAT | O_EXCL` 原子记录一次性 `authorization_id`（记录仍存在时重放拒绝），预生成唯一 Drive ID 与 run marker；
   在每个创建及真实 `reparent` mutation 紧前重新验签并检查有效期与隔离范围。
4. 创建合成测试文件 `M4_E2E_SYNTHETIC_report.txt`（仅测试文件夹内，显式 allowlist）。
5. 创建目标子文件夹 `M4_E2E_SYNTHETIC_dest`（仅测试文件夹内）。
6. 捕获移动前元数据；经 `DriveEvidenceAdapter` 读取真实证据。
7. 构建 exact plan → 人工批准记录 → `apply_plan`（`test_go` + allowlist）→ 独立 readback。
8. 捕获移动后元数据；比对前后差异。
9. 先删除子文件、再删除文件夹，并按 run marker 独立枚举残留；**残留时 exit 3**。
10. 原子写入并回读 JSON 收据；写入失败优先返回 exit 4，不报告 PASS。

退出码：`0`=全过且清理完成；`1`=环境/授权/隔离失败；`2`=E2E 断言失败；
`3`=清理未完成（残留文件，需人工恢复）；`4`=正式收据无法持久化。

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
- `production-boundary` 模式要求非空的真实 `--production-folders`，并在首次写入前向上回溯测试文件夹的完整祖先链，严格断言测试文件夹及其任何父级祖先均不在 `production_folders` 中；
  `dedicated-account` 模式要求身份/所有权/My Drive 根目录/精确名称/空目录，
  并完整分页枚举确认账号除本次允许对象外无其他可访问的未删除 Drive 对象；
  Drive 返回 `incompleteSearch` 时一律拒绝执行。
- **账号隔离**：优先使用与生产数据隔离的测试 Google 账号或单独限制权限的测试环境。
  脚本启动时把认证用户邮箱与获批值精确比较。Full Drive scope
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
4. **明确批准本次 TEST 授权摘要**，并落盘时间有界、SHA/账号/folder 绑定的授权记录。
   注意：`test_go` 仅授权本次隔离测试，**不能**授权生产操作。
5. 非独立账号时提供真实生产 folder ID；独立账号模式不得用虚构 ID。
6. 可选：确认 `DEFAULT_EVIDENCE_MAX_BYTES = 100 MiB` 是否合适。

## 6. 与 M4 无关但已完成的离线验证

- `tests/test_drive_evidence_contract.py`（14 项）：模拟原始 Drive API 响应，覆盖 adapter 工厂、元数据转换、MD5≠SHA-256、原生文档 fail-closed、缺失证据拒绝、reparent 接口、readback。
- 真实 `googleapiclient` 未安装环境下的测试失败为**预先存在的环境缺失**，与本次改动无关。

## 7. 安全边界、威胁模型与残余风险

1. **操作系统与特权攻击者边界**：
   - 本安全设计基于非 root 普通用户执行模型。所有系统级信任锚（`/etc/cba-kb/m4_approval_ed25519_public.pem`）与独立账本（`/etc/cba-kb/consumed_authorizations`）依赖 `root:wheel` 属主、只读或 sticky bit 权限。
   - 系统不宣称能够抵御已取得本地 root/sudo 权限的攻击者（特权用户可通过 `sudo chflags nouchg` 或修改系统文件绕过防护）。
   - 当前也不能宣称抵御**同一普通执行 UID** 对消费记录的恶意回滚：文件所有者可清除自己文件的 `UF_IMMUTABLE` 并删除实例与独立账本记录。`O_EXCL` 证明正常重复消费拒绝，不证明系统级不可回滚。维持更强承诺需要普通执行 UID 无法修改的 root-owned 写入边界及其部署维护；本 PR 未获授权增加 root helper、sudoers 或 daemon。
2. **Drive 远程状态与 TOCTOU 风险**：
   - 远程 Google Drive 状态可能在检查与执行之间发生外部并发变化。本脚本通过在每次真实 mutation（创建/移动）紧邻前重新验签、重核有效期与父子边界来降低 TOCTOU 窗口，但分布式环境下的理论网络竞态无法无条件绝对消除。
3. **Python 运行时代码来源**：
   - 若 Python 进程在 sanitizer 启动前已被恶意代码预加载或注入，源码的 Git hash 无法证明已载入内存的对象可信。因此，本方案强制在业务代码载入前建立 `_SourceOnlyFinder` 边界，拒绝对预加载模块的事后 whitewash；loader 只读取一次源码，并用同一字节快照完成 Git blob 校验、SHA-256 登记、编译与执行。
4. **部分执行与未清理残留风险**：
   - 若在成功创建测试文件后发生网络中断、Google API 5xx、系统崩溃或收据磁盘写入失败，已创建的合成文件将残留在测试文件夹中。此时保证“零写入”是不符合事实的；脚本会尽力清理并输出包含残留 ID 的收据，操作者须根据收据进行手动核验与清理。
5. **验证环境与测试替身边界**：
   - **Linux CI**：证明代码跨平台语法、逻辑分支与回归测试的正确性，但 Linux 内核原生缺少 macOS 特有的 `os.chflags(UF_IMMUTABLE)` 与 macOS ACL，不能作为 macOS 原生文件系统防御能力的实机证据。
   - **Fake/Mock Drive**：证明调用序列、参数过滤与契约边界，但不能代替真实 Google Drive API 权限与网络行为。
   - **本地 macOS 证据边界**：本轮实机证明文件所有者可以清除自己的 `UF_IMMUTABLE`，因此不能据此宣称同 UID anti-rollback；ACL 解析与根目录覆盖由定向测试证明，尚未取得对实际 root-owned 账本目录的原生完整演练收据。
   - **真实隔离 Drive E2E**：只有在 Human 合并 PR 并在独立测试文件夹下实际运行后，才能证明真实端到端完整就绪。

## 8. Human F-03 威胁模型裁定（2026-10-16）

- 决策：仅对 PR #184 的 M4 隔离测试执行器接受
  `LOCAL_SAME_UID_LEDGER_ROLLBACK`。已获得同一 macOS 用户身份完整执行权限的恶意
  进程删除两份本地消费账本，属于本执行器模型外风险。
- 保留：Human TEST 签名、有效期、SHA/账号/folder/隔离模式绑定、精确操作白名单、
  Canonical Root 与生产隔离、exact plan/approve/apply、SHA-256、真实 reparent、独立
  readback、残留清理与 JSON 收据，以及所有越界/身份/证据错误的 fail-closed。
- 账本仍使用 `O_CREAT | O_EXCL`、双记录、fsync、`UF_IMMUTABLE` 与权限/ACL 审计，
  用于正常重复消费拒绝、防意外重复与审计；不得描述为可抵御恶意同 UID 回滚。
- 不引入 root helper、SUID、守护进程、sudoers 或新的系统级授权服务。
- 此裁定不适用于 Engine 其他生产授权路径，也不改变 F1–F8、R1–R7、A1–A6、
  AC01–08、GAT01–08、C-01～C-09、DEP-01～DEP-08 或真实 Drive Production GO 义务。
- 下一门禁：由 Human 指派的独立 Q2 对本次裁定后的 exact HEAD 审查；在新的 TEST GO
  之前不得执行真实 M4，且本裁定本身不构成 Q2 PASS 或合并许可。
