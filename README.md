# CBA-KB Engine v1.5

本地执行，GitHub 管理代码，Drive 保存已发布知识库。当前工程处于部署中，生产仍为 v1.1 FINAL。

## 已实现

- 导入 Drive 现有脚本、真实 parser fixture 和 9 项既有回归，保留 legacy 同步保护。
- 只读 MASTER 校验、十个赛季阅读版、完整 CSV/JSONL 派生导出。
- 独立本地 OAuth、按 ID 拉取及变更检测。
- 普通文件沙盒发布器：冻结包、备份、逐文件回读、journal、幂等重试、原 ID 回退。
- 全部依赖锁定在 requirements.lock，Python 3.11。

## 初始化

```sh
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
make doctor
make test
```

生产数据不进 Git。部署时 bootstrap 数据位于 workspace/inputs/bootstrap；新机器需完成授权后 make pull。

```sh
make auth
make pull OUTPUT=workspace/inputs/run-001
make validate MASTER=workspace/inputs/run-001/MASTER.xlsx
make build MASTER=workspace/inputs/run-001/MASTER.xlsx OUTPUT=workspace/candidates/run-001 RELEASE_ID=run-001
```

build 要求代码已提交且工作树干净，输出目录必须是新目录。未同步候选、outbox 和 journal 不可随意删除。

## 部署门禁

生产写入目前禁用：尚需真实 OAuth 沙盒演练、现有原生 Docs 阅读版/INDEX 适配、完整发布清单与两 AI 验收。
低层普通文件发布器不接受 native Docs/Sheets。CLI 只允许 runtime.json 中配置的沙盒直接子对象，不能靠填写生产 ID 绕过。
不要直接运行 legacy/upload_drive.py；旧按名上传流程不是 v1.5 发布通道。

完整方案见 docs/CBA-KB_v1.5.md；授权步骤见 docs/SETUP_AUTH.md；实施状态见 docs/DEPLOYMENT_STATUS.md。
