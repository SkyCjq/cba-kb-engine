# v1.5 实施记录

日期：2026-09-10。状态：IN_PROGRESS，未上线。

已核对 live MASTER：3,451 行、20 列、单 MASTER tab、3,451 唯一键；source_registry 89 来源。详细哈希和赛季数见 baseline_2026-09-10.json。
数据内容保持原字节不变，1,019 条官方 API 行以官网 URL 追溯，未补造 source_file_id。
16 份 Drive 原文件已导入并核对长度，代码导入清单见 import_inventory.json。

本地实现已通过 29 项测试：既有 9 项，以及 MASTER 导出、正常发布、重复发布、原 ID 回退、冲突阻断、响应丢失重试、部分失败恢复、候选防篡改、类型保护、单写者要求、锁与路径保护、发布状态恢复，以及 native Docs 受控前缀的范围和回退测试。
故障测试当前为离线模拟，不等于 Drive 生产环境验证。

已创建并核实私有仓库 [SkyCjq/cba-kb-engine](https://github.com/SkyCjq/cba-kb-engine)，GitHub CLI 已授权，初始代码已推送。第二 AI 选择 Gemini。

工程已安装于 `/Users/skychengneo/Agent/CBA_kb`，Python 3.11.15 独立虚拟环境已就绪。首批本地候选包含完整 CSV/JSONL、10 个赛季阅读版、INDEX、provenance 与 validation 共 15 个文件，MASTER 原文件字节不变。

native Docs 适配原型仅改写受控的当前发布前缀，原正文保留在“历史内容”分界下，可通过删除前缀回退；范围测试通过。已接入发布器。通过当前 AI 的 Drive 连接在独立沙盒 INDEX 完成真实插入、样式修正和删除回退；回退前后 tabs/body 结构完全相同，仅 revisionId 变化。此结果不代表本地 OAuth 通道已验收。

待完成：

1. 用户创建 Desktop OAuth 客户端 JSON；本地浏览器同意授权。
2. 真实本地 Drive 拉取、OAuth refresh、来源可达性测试。
3. Drive 沙盒已创建（config/sandbox.json），待本地 publish/verify/restore 真机演练。
4. 原生 Docs 适配已完成离线和连接器验证，待本地 OAuth 验证。
5. 全量发布清单、代码镜像、上下文入口及生产切换。
6. ChatGPT/Codex + Gemini 验收与 v1.5.0 release tag。

当前生产版本仍为 v1.1 FINAL。不得把部署中标记成 IMPLEMENTED。

发布清单生成器会冻结代码副本、记录提交号，并保留已知旧文件 ID；未分配 ID 的新增产出物仍需发布前解析。沙盒演练步骤见 SANDBOX_RUNBOOK.md。
