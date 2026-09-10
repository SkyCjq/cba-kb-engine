# CBA-KB v1.5 — 本地部署、GitHub 代码管理与 Drive 发布

> 修订日期：2026-09-10（Asia/Singapore）
> 状态：IN_PROGRESS / 本地 OAuth 与沙盒验收完成，生产切换及两 AI 验收待完成。
> 实施顺序：v1.1 FINAL → v1.5 → v2.1 Unified Target。
> 本地部署目录：/Users/skychengneo/Agent/CBA_kb
> 本次修订替代本文件旧草案；历史内容由 Drive 文件版本历史及修订前备份保留。

## 1. 目标与边界

在 v2.1 业务扩展前建立可恢复的本地运行环境、GitHub 代码版本管理和完整的 Drive 产出物发布链路。不同 AI 继续通过各自获授权的 Drive 工具查找、读取、引用知识库，不需要访问本机路径、开启本地服务或获得本机凭据。

| 位置 | v1.5 角色 | 管理规则 |
| --- | --- | --- |
| 本地 /Users/skychengneo/Agent/CBA_kb | 执行环境、输入快照、候选产物和重试队列 | 可加工数据；未发布内容不是当前生产事实 |
| GitHub 私有仓库 | 代码、测试、依赖锁定、配置模板、工程文档的版本源 | 已核实私有仓库 SkyCjq/cba-kb-engine |
| Drive 10_sources / 20_data / 60_config | 证据、已发布事实、处理与同步控制 | 保留既有 file ID，生产状态以已验证的发布结果为准 |
| Drive 40_ai | 不同 AI 的导航、阅读版和可检索派生数据 | 从同一 MASTER 快照生成，携带版本和来源，不另建可人工维护的事实表 |
| Drive 50_scripts | 已发布代码的阅读镜像 | 标明 GitHub URL、commit SHA；改动回 GitHub，不在镜像直接开发 |
| Drive 90_archive | 发布快照、审计、回退依据、需保留的中间结果 | 不参与默认当前事实回答 |

本地数据允许处理和修改；“Drive 是 Data Truth”指已发布事实的权威位置，不是禁止本地写入。
本地与 Drive 不做无条件双向目录镜像：输入拉取、候选计算、受控发布分开执行。
凭据、虚拟环境、缓存和调试临时文件不是项目交付产出，不上传 Drive 或 GitHub；其余应交付的项目产出都须纳入发布清单。

本版不扩展 player_uid、stats、Claim Ledger、profiles、MCP/REST、Backblaze 或公共 GitHub 数据发布。
GitHub Actions 可以做无凭据的代码检查，生产执行默认本地。以后迁移运行位置复用同一套 CLI，不以云端 runner 为 v1.5 前置条件。
本机休眠、关机或离线时不运行新任务；其他 AI 仍可读取最后成功发布的 Drive 版本。

## 2. 经核对的起点与旧草案纠正

本次已读取 README、技术手册、v1.1 最终 AB 节、v1.5 旧草案和 v2.1，并列举生产与配置目录。

当前 20_data 实际只包含以下原生 XLSX 文件，而非 native Google Sheet：
- 名称：CBA_2017-2027_国内球员注册_MASTER.xlsx
- file ID：1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc
- MIME：application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
- [当前 MASTER](https://docs.google.com/spreadsheets/d/1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc/edit)

v1.1 FINAL 文档记录的验收基线为：单一 MASTER tab，3,451 条记录、3,451 个唯一 record_key，10 个赛季计数依次为 308 / 331 / 346 / 355 / 380 / 358 / 349 / 357 / 335 / 332；source_registry 为 89 个来源。这些是已读取的历史验收记录，本次方案修订未重新下载逐行审计；实施 Step 0 必须重新锁定 live baseline。

保持 2024-2025 的 352 条 official API 优先与 5 条历史补充逻辑；八一缺少来源的合同/注册字段留空；贾昊公示截止时间按 FINAL 为“2024年12月7日5:00”。不恢复旧待证描述。
auto_validated 不等于官方或人工核验；仍保留字段质量边界。

旧 native Sheet 1XbNpuYD9wLFSPtD8260s3NT8_eLnybjkpjkLQRn6pzQ 已是归档工作历史，不得作为生产写入目标。
旧 make sync / build_bundles.py 的 fail-closed guard 保留，直到 MASTER-aware 替代流程通过测试。
原草案的多标签生产表、旧计数、统一 revisionId 假设、OAuth 一次授权永久有效、保证所有 AI 自动刷新等描述不再作为实施依据。
本地部署、GitHub 推送和 OAuth 沙盒已完成；最新实际结果见 DEPLOYMENT_STATUS.md。

## 3. 本地工程与 GitHub

建议目录如下；名称可随代码导入适配，部署根路径固定：

```text
/Users/skychengneo/Agent/CBA_kb/
  src/cba_kb/          auth、pull、process、validate、build、publish、restore
  tests/              v1.1 回归 + MASTER/同步/发布测试
  fixtures/           已有真实 parser fixture 与最小测试样本
  config/             drive_map、受控词表、运行配置模板
  docs/               版本方案、部署手册、恢复手册、AI 使用说明
  pyproject.toml      项目与依赖声明
  依赖锁定文件
  Makefile
  .gitignore
  .env.example
  .credentials/       仅本机；OAuth 客户端配置和 token
  workspace/
    inputs/            按 release/file ID 拉取的输入快照
    candidates/        尚未发布的输出
    outbox/            已冻结、待上传或重试的完整发布包
    cache/             可清理的下载和 OCR 缓存
    state/             同步基线、任务锁、发布 journal
    reports/           本轮验证报告
```

Git 跟踪 src/tests/允许入库的 fixtures/config 模板/docs/依赖文件；排除 workspace、.credentials、.env、token、日志、业务原始大文件和环境目录。真实配置中任何凭据均外置。
先建立忽略规则和凭据检查，再导入 Drive 现有脚本、Makefile、测试和真实 fixture。用 v1.1 回归验证导入，禁止用手写 fixture 冒充真实样本。
代码镜像应与 GitHub 已提交版本一致；MIRROR 标识可放伴随说明，避免破坏 shebang 或文件内容哈希。
每次发布记录 commit SHA、依赖版本、运行配置摘要；验收后创建 v1.5.0 tag。仓库 URL 未核实前标为待配置，不填虚构地址。

本地 Python OAuth 与当前 AI 的 Drive Connector 授权分离。需独立完成 OAuth、refresh 与目标目录读写验证；token 可能过期或被撤销，失败时清晰停止并提示重新授权。认证信息不得进入日志或同步包。

## 4. Drive 映射与同步范围

所有已知对象通过 file ID 定位，名称仅用于展示；改名不触发复制。未知 ID 必须先发现并确认映射，不按名称猜中后直接写。

| 对象 | 稳定 ID | 同步方式 |
| --- | --- | --- |
| 根目录 | 1bQybVHV_RRtZvvFuFhLpM-rXbVsNT2Dq | 保留结构 |
| 20_data | 1nL4xomHNQInbskeYdqa45hHfleHhExW- | 只保留当前 MASTER |
| MASTER.xlsx | 1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc | XLSX 原字节更新，保持 MIME/file ID |
| 40_ai | 1y-JlN327UARvZ4A8QN0EDDxg_0oggiUp | INDEX、赛季阅读版、需要的派生 CSV/JSONL |
| 50_scripts | 1oaJW9FDUjmIbFDafpt6aqtlvvzqQYhdC | GitHub 已提交代码/测试/依赖的阅读镜像 |
| 60_config | 1ccYmxy8NKt79pgyhDnyHUE14C6_wsn83 | 非秘密配置、registry、manifest、发布状态 |
| source_registry.csv | 106NV6lV4mPLNjohMcSLdOyPRct0uOUsx | 按 source_id 幂等更新 |
| manifest.csv | 1OsaC9ZQRC8pljd6VRMtub2YOVIv1QYKl | 同步状态/hash/file ID |
| drive_map.yaml | 1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf | 经核对的对象映射 |
| 90_archive | 1GjDMkAYs7JpIrI9_kQduxlqygZmxeb9M | 发布快照、审计、回退包 |

来源原件、采集 snapshot、需要保留的 OCR/抽取结果、人工研究、最终 MASTER、AI 阅读版、代码镜像、测试/审计报告、版本方案和恢复说明均应登记去向。新来源留在 00_inbox 或进入 10_sources，不因目录位置代替 registry 状态。
已存在 Drive 的大证据文件按需下载，无须重复上传；通过 file ID 与内容哈希引用。
失败运行的诊断报告在网络恢复后补传；本地 outbox 中未成功同步的交付物禁止清理。

## 5. MASTER-aware 处理和文件类型路由

pull → 冻结输入 → 处理候选 → 验证 → 生成派生文件 → 发布 → Drive 回读。

MASTER.xlsx 是普通二进制文件：下载为 XLSX，保留表结构、字段类型、文本日期、空值和 provenance；验证后的完整 XLSX 用同一 file ID 原地更新，不转换成 native Sheet，不上传 CSV 冒充 XLSX。
无业务变化的部署迁移应直接保持 MASTER 字节不变；需要重存时同时做逐单元格语义比较，避免 ZIP 元数据变化造成误判。

只有实际 MIME 为 native Google Sheets 的对象才路由到 Sheets API；如未来需要写其指定 tab，按范围写入、使用 RAW、保护其他 tab，并显式清理缩短范围后的残留行。v1.5 不写归档 Sheet。
Docs 内容用 Docs API；CSV/JSONL/Markdown 等普通文件按各自 MIME 更新。类型不匹配立即拒绝。

v1.5 必须实现最小 MASTER-aware builder，以保证原有 AI 消费能力可持续更新：读取同一 MASTER 快照，生成现有赛季阅读版、INDEX，必要时增加 40_ai 下只读机器数据副本。暂不实现 v2.1 的 profiles/claims。
每份派生文件注明 generated、release_id、source_master_file_id、source_master_sha256、generated_at、commit_sha。其数据不可人工维护或回写成为另一事实源。
manifest 与 source_registry 的职责保持分离；manifest 自身不计算递归自哈希，可由发布状态文件记录其最终哈希。

## 6. 并发、失败与一致发布

拉取时保存 file ID、Drive version、modifiedTime、可用的 headRevisionId，以及下载后 SHA-256；native 文件另保存规范化内容摘要。
Drive version 是变更序号，headRevisionId 仅对二进制内容可用，不能假定所有 Drive 文件都提供同一 revisionId 字段。[Drive 文件字段](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)

发布前比对所有目标的远端基线，发现变更即中止，生成差异报告，不自动覆盖或合并；重新 pull 和审核候选后再发布。
“先检查再写”本身不能消除检查后的竞争。v1.5 生产发布采用单一写入者及短维护窗口：本地发布锁防止本机重复任务，发布期间其他 AI/人只读生产文件。Drive 状态标记是协调信息，不是跨应用的强制锁；无法约束写入方时停止生产写入，不能声称绝对并发安全。

建议新增 60_config/release_status.json，包含 current_release_id、previous_release_id、state、started_at、published_at、commit_sha、artifact IDs/hashes、错误摘要和上一完整快照的 Drive 链接。状态包括 PREPARING、PUBLISHING、COMPLETE、FAILED、ROLLED_BACK。

发布步骤：
1. 冻结 outbox 与 release_id，生成全量交付清单；无变化则跳过。
2. 核验远端基线与权限；把变更前字节及元数据备份到 90_archive/releases/<release_id>/before。
3. 把候选包与清单存入同一 release 的 candidate 区并验证，不改变当前事实入口。
4. 标记 PUBLISHING，记录 previous/current 指针；按 journal 更新现有 file ID。
5. 每个对象写后下载回读：普通文件比对哈希，MASTER 再做语义检查，native 文档检查内容。
6. MASTER、registry、AI 产物和 manifest 全部通过后，更新 INDEX/上下文入口，最后标记 COMPLETE。
7. 任何失败标记 FAILED，保留 outbox 和 journal；按同一 release_id 幂等重试，或恢复已改对象的原字节到原 ID。回退完成再标记 ROLLED_BACK。

Drive 多文件写入不是原子事务。[原地上传接口](https://developers.google.com/workspace/drive/api/guides/manage-uploads)解决单文件更新，不提供整批事务。
遵守消费协议的 AI 在 PUBLISHING/FAILED 时读取上一完整快照，并说明当前发布未完成；直接打开文件的客户端可能看到暂时不一致，必须明确该边界。
恢复不能通过删除生产文件后把备份改名代替，否则原 file ID 断链。
新对象创建返回的 ID 应立即记入 journal；超时后先按 release 标识查找已创建对象，禁止盲目重建。
本地缓存可重建；未发布候选和 journal 不可当作缓存随意删除。异机恢复只保证 Drive 已发布数据及 GitHub 已提交代码，不承诺未同步工作的恢复。

## 7. 不同 AI 的 Drive 消费协议

v1.5 上线时建立简短上下文卡片并更新 README/INDEX，先读当前状态、发布编号、MASTER 链接、词义边界、最后更新时间；工程全文按需读取。
当前方案阶段仍从 README 与本方案进入，不把尚未创建的卡片或 release_status 写成已存在。

消费流程：
1. 用该 AI 已获授权的 Drive 连接读取入口及 release_status。
2. COMPLETE 时按 INDEX 读取同一 release 的数据；未完成时使用 previous 完整快照。
3. 精确人数、筛选和比较优先读取完整 MASTER；无法完整解析 XLSX 的工具可读取同版本生成的完整 CSV/JSONL 并校验来源摘要，不能从正文数数。
4. 追溯时引用 Drive 原件 file ID、source_url/source_page，保留 verification_level。
5. 回答注明数据版本或更新时间。只读 Drive，无须本地路径或本地服务。

同一 file ID 能维持链接，但不能保证不同产品自动刷新索引。NotebookLM/ima 或其他已导入来源可能需要各自刷新/重新同步；逐产品测试后记录。
验收至少覆盖用户实际使用的两个 AI，记录连接方式、读取格式、结果和刷新延迟。没有可用连接的产品记为 NOT TESTED，不能以一个 Connector 成功推定全部通过。
测试时本地运行程序停止，AI 仍应可完成：查询总数及分赛季计数、查贾昊记录、追溯一个来源、正确解释 auto_validated、识别一次未完成发布。

## 8. 分阶段实施与交付

下列为拟实现命令接口，尚未宣称当前可运行。

| 阶段 | 工作与交付 | 通过条件 |
| --- | --- | --- |
| P0 基线 | 盘点 live 文件/MIME/ID，下载 MASTER、registry、manifest、现有代码；保存 baseline 报告 | 确认单 MASTER、字段/行数/唯一键、来源与现有回归清单 |
| P1 环境 | 在指定根目录导入工程；核实/建立私有仓库；锁定依赖；配置本地 OAuth | make doctor、make test；认证和真实来源可达性有记录 |
| P2 只读同步 | make pull、make plan；保护本地未发布工作 | 两个新工作目录拉取同一冻结输入得到相同语义/哈希 |
| P3 构建 | MASTER-aware validate/build；保留旧 guard | 同一输入输出可重建，派生数字与 MASTER 一致 |
| P4 沙盒发布 | 独立测试对象上 make publish / verify / restore | ID/MIME 保持、冲突中止、失败恢复、无重复对象 |
| P5 生产启用 | 先低风险文档/代码镜像，再完整无事实变更发布 | 全部回读一致、更新入口、COMPLETE，数据不增删 |
| P6 验收 | 两个 AI 消费测试、恢复演练、最终审计 | 必需项通过后标为 IMPLEMENTED，tag v1.5.0 |
| 后续 | v2.1 从 v1.5 的代码和发布链路接续 | 不另建事实源，不回到已归档工作表 |

默认手动运行；如需要定时，可另配本机调度，记录 last_success/stale 状态和休眠补跑策略，不假定电脑全天开机。
可选 archive-canary 仅在需要迁移原件时使用单一样本，验证 ID/parent/registry 并能恢复；它不再是 v1.5 的强制主线或批量移动授权。

## 9. 验收与回退

必须完成的测试：
- 继承实际导入代码的 v1.1 regression；FINAL 文档记录 9/9，旧草案的“8 项”不能替代运行结果。真实 parser fixture 仍解析 14 行，首杨瀚森、末王奕博。
- MASTER 总行、唯一键、各赛季、所有非目标字段、provenance、空值、文本日期/前导零保持；旧归档表未写，20_data 不新增平行事实表。
- XLSX 不能误走 native API，native 文件不能被普通字节替换；类型错配测试必须失败。
- 同步二次运行无重复、改名不改变对象映射；外部改动被检测，冲突时不写。
- 发布中途断网/崩溃、上传成功但响应丢失、部分文件成功、校验失败均可幂等恢复；状态不可虚报 COMPLETE。
- 发布包、报告、代码镜像及必要中间产物全部在 Drive 有对应清单与可读链接，凭据不进入任何产出。
- rollback 恢复原 file ID 下的数据与相关控制文件、AI 视图；上一完整版本可由纯 Drive 客户端读取。
- 换一个干净工作目录从 GitHub + Drive 恢复已发布基线；不删除现有未同步工作做演练。
- 两个实际 AI 的查询/追溯/刷新测试有证据；未测项明示。

若生产发布失败，优先停止写入并保持上一发布可读；通过原 ID 回写备份恢复，不修改原始来源事实。若只是代码有误，git revert 后重新验证、重新发布，Drive 镜像记录新 commit。
验收报告包含 baseline、commit、release_id、输入/输出哈希、测试结果、Drive 链接、残留边界和恢复记录。

## 10. 与 v2.1 衔接及本次状态

v2.1 的 player-centric 业务目标保留；其旧“Actions 是唯一生产执行环境”“个人电脑只能应急”“v1.1 后直接进入 v2.1”的要求由本版本顺序覆盖。
v1.5 的最小 MASTER-aware builder 与发布协议先完成，v2.1 在此基础上增加身份/统计/claims/更丰富视图；云端调度另行评估。

本次已完成：方案修订、现状与历史口径校正、部署与同步设计、测试和回退定义。
本次未执行：建立 GitHub 仓库、安装本地运行环境、OAuth 配置、生产数据发布、AI 跨产品实测。
因此当前生产仍为 v1.1 FINAL；v1.5 为下一实施版本，不能标为上线。

依据：[v1.1 FINAL 文档](https://drive.google.com/file/d/1R6GncxTHg_lkSP5xgWbQJ3sRGHa2syxY/view)、[技术手册](https://drive.google.com/file/d/1Ckg-y3Ke4tHxGHPp1dY2A_cO39xzF9u4/view)、[v2.1 方案](https://drive.google.com/file/d/1eLEHuSgxMIe1jlRrLVojWdRqO3qJFGFu/view)及上述 Drive 目录实读。

