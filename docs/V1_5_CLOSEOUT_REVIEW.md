# v1.5 收尾复审与 v1.5.1 衔接

日期：2026-09-10。核查代码：1cd8e05（本报告提交前）。结论：先修正生产发布边界与恢复状态，再封板 v1.5.0；按用户计划进入 v1.5.1。此次为复审，不执行生产切换，不导入新业务事实。

## 依据与范围

- [外部实施审查](https://drive.google.com/file/d/12_QIgmeuPun3pHVJ-Imue1nJJ8-m1DkN/view)
- [范围补充分析](https://drive.google.com/file/d/15HirdBcAW3iA3CL4bCFO_S9gzIum3Bub/view)
- [v1.5.1 计划](https://drive.google.com/file/d/1vQYwGHaqoWhjNQQBTofYityzKAd6X1uJ/view)

已读三份全文，对照本地已提交代码、既有 OAuth 沙盒报告，并只读下载两版 MASTER、外援 XLSX、八一 MD。未核验两份 PNG 的 OCR，也未读取外部审阅者声称完成但尚未并入本仓库的 parser/fixture。外部审查明确未能访问私有仓库，其旧镜像结论不可直接等同当前代码结论。

## v1.5.0 封板前必须完成

### P1：回退必须先公布恢复中状态，并支持重复恢复

证据：src/cba_kb/release.py:186–203。restore 在逐项恢复时不改公开状态，完成全部写入后才标 ROLLED_BACK。
用现有 FakeDrive 复现：恢复 b、a 时公开状态都为 COMPLETE；再次 restore 报 Cannot roll back another release。
风险：只读 AI 可能在回退中误认当前发布完整；状态写入成功但响应丢失时，重试缺少幂等完成路径。
修复要求：ROLLING_BACK 或等价非 COMPLETE 状态先落盘与回读；写者记录自身恢复进度，读取者可找到完整可信快照；成功/失败/响应丢失后均能恢复或幂等退出。补回归和真实沙盒断点演练。

### P1：冻结输入基线必须绑定发布计划

证据：master.py:59–60 输出 source_master_sha256；release.py:36–79 只冻结目标文件基线，不读取 provenance 中的输入依赖；catalog.py:29–41 只收集候选派生文件。
因此仅发布阅读版时，即便作为输入的 MASTER 在 build 后已被他人修改，只要目标阅读版本身未变化，当前发布器也不能据输入变化阻断旧派生数据发布。
修复要求：计划登记只读依赖（MASTER、registry、必要 config）的 ID/hash/version，发布前复核；正式清单覆盖事实、控制文件、阅读版、状态和代码来源；统一 release ID。不以手工把 MASTER 加入某次清单代替通用依赖校验。

### P1：补正式发布路径与新对象生命周期

cli.py:63–76 仍限制为沙盒直接子对象，production_enabled 仍为 false；这是有效保护，不是已经上线。catalog 的新增对象仍可能 id=None，而 prepare 要求既有 ID。
需实现显式生产映射/白名单、完整计划和新增对象 ID 固化；新对象在 COMPLETE 前不作为 current 事实，失败/回退时退出 current 清单并保留归档。不能简单删除沙盒判断或改填生产文件夹 ID。
v1.5 首次发布的新阅读版和代码镜像已需要此能力；v1.5.1 的 SNAPSHOTS/EVENTS 直接复用。

### P1：说明并限制并发窗口

现有发布有远端 version/modifiedTime/hash 预检，不是外部审查所述“完全无冲突检测”；native Docs 还有 requiredRevisionId。
但 release.py:149–155 的读取与写入存在间隔，drive.py 的普通文件 update 未绑定条件版本；native.py:72–78 使用写入函数重新读取的 revision，并非计划冻结 revision。其他写者在间隔内改动仍可能被覆盖。
v1.5 至少明确单写者维护窗口并确保其他写入流程停用，缩短和复核临写基线、做冲突注入测试；不能承诺任意多 AI 同时编辑均安全。更强跨客户端并发方案可单列后续增强，不能仅用本地 flock 宣称跨客户端互斥。

### P1：生产可发现性、最新代码镜像与 AI 验收

此次审阅读取到旧 50_scripts，说明仅把新代码 ZIP 放 90_archive 不足以实现可核查的代码镜像。
需在现有入口明确当前/历史代码、GitHub URL、commit、文件清单/哈希和可读镜像链接。保留用户“产出同步 Drive”的要求，不能只用 GitHub 链接取代 Drive 可读代码。
README、AI Context、INDEX、registry 路径、release_status 链接必须一致；INDEX 当前只列文件名，发布状态也无明确 URL，应由已分配 ID 生成直链。
Gemini 已证实直接给两个文件链接可读且答案正确；从 START_HERE 自动读依赖失败。记录为“直接链接抽样通过，入口依赖读取受限”，不能写全量自动同步或自动刷新通过。生产切换后用正式 ID 复验，覆盖历史记录、验证等级/空值规则、非 COMPLETE 时的读取策略。

## 两版 MASTER：实测结果与收尾注意

来源：归档 1w9bpj3pdefFh9qh52ZCn9GoScxlVyh-k 与生产 1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc。

- 归档：677,658 字节，单 tab registrations_2017_2027，3,451 行、43 列。
- 生产：314,762 字节，单 tab MASTER，3,451 行、20 列。
- 两者 record_key 集合完全相同；不能用体积变小推断丢行。
- 映射 registration_type→registration_stage、former_cba_club→former_club、disclosure_deadline→notice_deadline、notes→remarks 后，共有/映射字段差异为：sequence 全部由数值改为字符串；32 行 verification_level 由 official_roster 改为 auto_validated。其他可直接对应字段一致。
- 旧有 ISO 日期、raw_row_text、secondary_source_ref 等不在当前 20 列产品中，仍存在归档。需明确字段去向、归档保留及 32 行等级映射依据，不能把“行数相同”当作全部信息无损的证明。
- 保留当前单 MASTER XLSX，不在收尾时再次迁移事实载体。当前 builder 只读 XLSX、保留原字节；“每次必因 openpyxl 重写而重传”不适用于现流程。字节 hash 用于完整性，semantic hash 用于逻辑差异，二者不能互相取代。
- 现已验证 Drive 直接消费，不等于 NotebookLM 自动刷新。NotebookLM/其他产品若成为必需目标，应单独验收；不据未测试产品强行回滚为 native Sheets。

## 对外部建议的采纳与更正

| 建议/判断 | 处理 |
|---|---|
| pull、冲突检查、回退完全不存在 | 旧镜像结论；当前实现和真实沙盒已有证据，但仍有上述边界问题 |
| 必须实现多 tab Sheets 写入 | 不适用当前单 tab XLSX 基线；native Sheets 原始字节写入已拒绝 |
| make wechat 必然失败 | 属 legacy 入口；新 Makefile 未暴露 wechat。保留禁用，文档明确旧入口不可用；业务接入归 v1.5.1 |
| OAuth 必须抽离 | 当前 OAuth 已独立；CI 生产授权不是 v1.5 前置条件 |
| 立即加 player_uid 空列 | 不采纳，当前 20 列校验会拒绝新增列；按计划留 v2.1 |
| 只保留 CODE_MANIFEST、不镜像代码 | 不足以满足不同 AI 从 Drive 审查代码；采用清单+可读镜像/代码包 |
| 将外援 parser 并入 v1.5 | 按用户最新版本顺序留 v1.5.1，v1.5 不再扩大业务范围 |

## v1.5.1 计划应保留及细化的内容

保留：三个事实产品按关系/快照/事件分开；同源可输出 relation 与 event；注册窗口截止与球员公示截止分离；英文 raw/normalized 分离；严格赛季球队别名；player_uid 与验证双轴大迁移留 v2.1；v1.5.0 封板后才进行 v1.5.1 生产发布。

实查纠错：

1. 外援 XLSX：73 条在册，59 条包含取消注册的备注单元格（行77–135）；不是58。不要沿用补充分析的58条fixture预期。具体解析仍需真实全源测试，不能只计数就宣称 parser 验收。
2. 八一 MD：提取14个完整 record_key，与当前 MASTER 的 exact collision 为0；补充分析“已经都在表内、必撞键”的判断不成立。3,465 只是在执行时基线及语义核对仍成立的条件预期。
3. 不采用补充分析的 notice_deadline→disclosure_deadline 映射；遵循 v1.5.1 的 registration_window_deadline 语义。
4. 73+59 是两个不同粒度集合的计数和，不直接等于完整赛季注册总量或唯一球员数；两组名字无重叠也不能替代身份和全赛季覆盖证明。
5. event_key 中 sequence 不能用易变的解析行序号；优先稳定 source locator + 原始事件指纹，修订来源时定义关联/替换规则。同一人重复取消事件不按姓名简单去重。
6. 日期解析补充无法唯一判断年份、赛季外月份、跨日/缺时间和 timezone 的停止/人工复核规则；无时刻信息不要伪造精确 timestamp。
7. 一来源多产品时，registry 不能在一部分发布成功后就全源 VERIFIED；记录按产品计数和验收结果，manifest 不替代业务处理状态。
8. XLSX writer 需验证字符串、公式拒绝/处理、日期类型、非目标字段不变，不能只依赖当前宽泛基础类型检查。当前 master.inspect 允许 str/int/float/bool，尚无逐字段语义类型与完整verification词表校验。
9. 外部声称已实现的 club_aliases/parser/10项fixture测试需拿到真实代码和fixture后再审查、导入，不能仅据报告标为本项目已实现。
10. 四份新来源的登记必须走受控 registry 更新，登记不等于已导入；本复审不改 registry。

## 建议执行顺序

修复恢复状态/幂等性 → 绑定输入依赖 → 完整生产清单及新对象生命周期 → 明确单写者窗口和故障测试 → 发布入口/镜像与正式两 AI 复验 → v1.5.0 tag/封板 → v1.5.1 分支与真实来源开发。

本报告是工程审查，不是球员事实来源；不得作为 official_api 或人工核验证据写入事实表。记录的沙盒成功不能替代生产发布验收。
