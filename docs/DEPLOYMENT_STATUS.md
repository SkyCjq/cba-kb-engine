# v1.5 实施记录

日期：2026-09-10。状态：IN_PROGRESS，未上线。

已核对 live MASTER：3,451 行、20 列、单 MASTER tab、3,451 唯一键；source_registry 89 来源。详细哈希和赛季数见 baseline_2026-09-10.json。
数据内容保持原字节不变，1,019 条官方 API 行以官网 URL 追溯，未补造 source_file_id。
16 份 Drive 原文件已导入并核对长度，代码导入清单见 import_inventory.json。

本地实现已通过 18 项测试：既有 9 项，以及 MASTER 导出、正常发布、重复发布、原 ID 回退、冲突阻断、响应丢失重试、部分失败恢复、候选防篡改、类型保护与单写者要求。
故障测试当前为离线模拟，不等于 Drive 生产环境验证。

已选择 GitHub 私有仓库 SkyCjq/cba-kb-engine，GitHub CLI 已授权。第二 AI 选择 Gemini。

待完成：

1. 用户创建 Desktop OAuth 客户端 JSON；本地浏览器同意授权。
2. 真实本地 Drive 拉取、OAuth refresh、来源可达性测试。
3. Drive 沙盒创建及 publish/verify/restore 真机演练。
4. 现有 native Docs 赛季阅读版和 INDEX 的原 ID 更新适配（当前不能用普通文件发布器覆盖）。
5. 全量发布清单、代码镜像、上下文入口及生产切换。
6. ChatGPT/Codex + Gemini 验收与 v1.5.0 release tag。

当前生产版本仍为 v1.1 FINAL。不得把部署中标记成 IMPLEMENTED。
