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
| 4 | TEST 授权记录与签名 | Human 批准的本机 JSON，绑定一次性 `authorization_id`、`test_go`、有效期、最终合并 Git SHA、账号、folder 与隔离模式；必须由 Human 控制的 Ed25519 私钥签名 | `m4-approval.json` + `.sig` |
| 5 | 隔离模式 | 独立账号用 `dedicated-account`；否则须使用 `production-boundary` 并提供真实生产 folder ID | `dedicated-account` |

## 确切运行命令

```bash
python scripts/m4_drive_e2e.py \
    --instance-root /path/to/private/instance \
    --test-folder <ISOLATED_TEST_FOLDER_ID> \
    --test-go "M4-TEST-2026-10-16-001" \
    --expected-account "m4-test@example.com" \
    --approval-record "/secure/path/m4-approval.json" \
    --approval-signature "/secure/path/m4-approval.json.sig" \
    --isolation-mode dedicated-account \
    --receipt /tmp/m4-receipt.json
```

## 测试账号与 Private Instance 配置步骤

1. （Human 本机）准备隔离测试 Google 账号（优先与生产数据隔离）。
2. 在 Private Instance 的 credentials 目录放置 Desktop OAuth client JSON（`credentials.json`）。
3. 运行 `make auth` 完成交互式授权，生成 `token.json`（权限 600）。
4. 确认 `token.json` 存在且 scope 包含 `https://www.googleapis.com/auth/drive`。
5. 将 Private Instance 路径告知执行者（仅路径，不传输 token 内容）。
6. 由 Human/系统管理员将 Human 的 Ed25519 公钥以 PEM SubjectPublicKeyInfo 格式安装到
   `/etc/cba-kb/m4_approval_ed25519_public.pem`；文件必须是非符号链接、root-owned，且
   group/world 不可写。Human 私钥必须受口令或硬件保护，不得交给 Codex。
7. Human 明确批准摘要后，使用对应 Ed25519 私钥对 `m4-approval.json` 的原始字节签名，
   将 64-byte 原始签名编码成单行 base64 保存为 `.sig`。例如使用 OpenSSL 3：
   `openssl pkeyutl -sign -rawin -inkey <HUMAN_PRIVATE_KEY> -in m4-approval.json -out m4-approval.sig.bin`
   后执行 `openssl base64 -A -in m4-approval.sig.bin -out m4-approval.json.sig`。Codex 不生成、
   持有或代替 Human 使用该私钥。

## 精确测试动作（脚本将执行）

1. 在任何写入前，使用固定系统信任锚核验 Human Ed25519 签名、认证邮箱、一次性
   `authorization_id`、授权有效期与 commit/folder 绑定；验签与 JSON 解析使用同一份文件
   字节。Engine 使用受保护的 `/usr/bin/git`，必须成功 fetch，且是无
   staged/unstaged/untracked 内容的 `main == origin/main`。
2. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_report.txt`（合成内容，白名单校验）。
3. 在测试文件夹内创建 `M4_E2E_SYNTHETIC_dest` 子文件夹。
4. 捕获移动前元数据；经 `DriveEvidenceAdapter` 读取真实证据并校验 SHA-256。
5. 构建 exact plan → 批准 → `apply_plan`（`test_go` + allowlist）→ 独立 readback。
6. 捕获移动后元数据；比对前后差异。
7. 使用预生成 ID 与 run marker 清理测试文件（先文件、后文件夹），再独立枚举残留；清理失败记录残留 ID（exit 3）。
8. 原子写入并回读 JSON 收据；写入失败返回 exit 4，绝不报告 PASS。

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

## 安全保证与边界说明

- 所有操作限制在 `--test-folder` 内；文件创建经显式白名单校验。
- `production-boundary` 模式触碰 `production_folders` 或祖先链包含 `production_folders` → 拒绝。
- `dedicated-account` 模式不要求虚构生产 ID；它要求账号精确匹配、文件夹由该账号单独拥有、位于 My Drive、名称精确且运行前为空。
- `dedicated-account` 还要求完整分页枚举 Drive 空间；`incompleteSearch` 会拒绝执行，且除测试 folder 与本次已创建对象外不得存在其他可访问的未删除对象。
- 授权在每个创建和实际 `reparent` mutation 紧前重新验签、解析同一字节流并检查有效期；一次性 `authorization_id` 以原子 `O_EXCL` 独立持久化账本（防回滚重放）消费，重放会被拒绝；过期后仅允许范围内清理。
- 授权窗口最长两小时；信任锚必须 root-owned，防止普通执行进程替换公钥后自签。
- `test_go` 永不能授权生产写入；与 `production_go` 互斥。
- 清理失败 → exit 3 + 残留 ID，不报告完整 PASS。

## 安全边界、威胁模型与残余风险

1. **威胁模型与信任假设**：
   - 防护假设操作系统内核、系统级只读目录及 root-owned 配置未被恶意接管。若攻击者具备 root 或内核级权限，本地所有文件标记及进程防御均可能被破坏。
   - 依赖 `/etc/cba-kb` 的 root 归属与 wheel 组权限，以及 `/var/run/cba-kb` 独立消费账本目录的所有权审计与 macOS ACL 审计（无额外 allow 写入规则）。
2. **Fail-Closed 故障停机原则**：
   - 依赖项缺失、Git 状态未完全同步干净、Python 运行环境受污染（如预先加载非源 loader 的模块）、缺少 `UF_IMMUTABLE` 特性或目录权限/ACL 不合规时，均立即中止，绝不发生 Drive 写入。
3. **残余风险与局限性**：
   - **中断后的残余文件**：若进程在写入测试文件后因网络故障或断电异常退出，测试文件仍留存于测试文件夹中。需通过 `--receipt` 输出的残留列表或人工登录 Google Drive 进行清理。
   - **Google Drive API 延迟与最终一致性**：Drive API 存在微小的元数据同步延迟。readback 机制在 mutation 后立即查询，但在极少数极端网络重试场景下，可能受到服务端一致性窗口影响。
   - **非跨平台账本不可变性**：`UF_IMMUTABLE` 是 BSD/macOS 原生文件系统特性。在无特权容器或不支持该特性的 Linux 环境中，系统将主动 fail-closed 拒绝写入。

## 当前状态

`SECURITY_HARDENING_PENDING_INDEPENDENT_REVIEW` —— 代码、测试、脚本、文档已完成定向安全加固与攻击自查演练；
**等待独立 Q2 复审**：待通过独立 Q2 PASS 并经 Human 决策合并到 main 后，方可签发测试授权并执行。

