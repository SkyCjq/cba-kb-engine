# 本地授权准备

部署目录：`/Users/skychengneo/Agent/CBA_kb`。

## Google Drive OAuth

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，新建或选择自己的 CBA-KB 项目。
2. 在 APIs & Services → Library 启用 Google Drive API；原生 Docs 同步阶段还需要 Google Docs API。
3. Google Auth platform → Branding 配置应用名称、支持邮箱和联系邮箱。
4. 个人 Google 账号的 Audience 选 External；Testing 阶段把拥有项目 Drive 文件夹的账号加入 Test users。
5. Clients → Create client → Desktop app，命名 CBA-KB Local，下载客户端 JSON。
6. 把 JSON 保存为 `/Users/skychengneo/Agent/CBA_kb/.credentials/credentials.json`。不要提交 Git 或上传 Drive。
7. 在项目目录运行 `make auth`，在浏览器选择拥有该 Drive 知识库的账号并授权。
8. 运行 `make pull OUTPUT=workspace/inputs/first-oauth-pull`，核对下载与 bootstrap 基线。

程序把 token 保存在 `.credentials/token.json`，目录权限 700、token 权限 600。Drive Connector 登录不能替代这一步。
本地程序需访问现有项目文件，因此使用 Drive scope；授权覆盖面大于单个文件，程序通过稳定 ID 和沙盒门禁限制写入。
Testing 状态下授权可能需要重新进行；长期运行前应检查 OAuth 发布状态与 Google 的 token 生命周期要求。不要把一次授权描述为永久有效。

依据：[Google Python Quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)。

## GitHub

已选择：SkyCjq / cba-kb-engine，私有仓库。GitHub CLI 使用本机系统凭据存储。
重新授权命令：`/opt/homebrew/bin/gh auth login --hostname github.com --git-protocol https --web`。
不要把 PAT 或 OAuth token 放入仓库、文档或对话。

## Gemini 验收（发布后执行）

本地进程停止后，在 Gemini 通过其 Drive 连接读取项目入口，分别查询：

- 当前发布编号、总注册记录数、十个赛季计数；
- 贾昊 2024-2025 的公示截止时间和证据链接；
- 一条历史记录的来源；
- auto_validated 是否代表人工核验；
- 若发布未完成，能否识别状态并使用上一快照。

记录 Gemini 连接方式、实际使用的 Drive 文件链接、回答与刷新情况。尚未测试，不能预先标为 PASS。
