# CBA-KB Engine v1.5.1 (dev)

本地执行，GitHub 管理代码，Drive 保存已发布知识库。生产事实当前为 **v1.5.1-1**（release_status COMPLETE，100 个产出物独立回读通过）；Codex 侧正式 ID 验收 13/13 通过，v1.5.1 尚未封板，等第二 AI 回传结果后创建 tag。两季外籍球员 PNG 来源按用户决定不在 v1.5.1 实施 OCR，保持 `DISCOVERED`。

v1.5.0 已封板并发布（Git tag `v1.5.0`，提交 c4bebec，release_status COMPLETE，76 个产出物回读通过）。当前分支在 v1.5.0 基础上开发 v1.5.1 注册事实扩展：国内注册关系之外，新增外籍球员注册快照与注册事件两种 grain，来源解析只在本地 staging 进行，未写入生产 `20_data`。状态与证据见 docs/CBA-KB_v1.5.1.md，方案见 Drive CBA-KB v1.5.1。

## v1.5.1 新增

- 赛季感知 club 别名解析（`config/club_aliases.yaml`），未知冠名商在 strict 模式 fail-closed。
- 三个来源适配器：`midseason_md`、`foreign_xlsx`、`foreign_image`；外援英文名 raw/normalized 双列，球衣号按文本保留，取消注册事件按赛季边界补年份并标记 `date_year_inferred`。
- 三种 grain 分离的事实产品：国内注册关系 MASTER、`CBA_外籍球员注册_SNAPSHOTS.xlsx`、`CBA_球员注册_EVENTS.xlsx`。
- CLI `extract` / `facts`，真实来源验收 `make accept`，来源登记提案 `make registry`。
- 只读 Drive 调用的脱敏诊断、阶段日志与有界重试；`doctor` 输出真实 `production_enabled` 与版本 `1.5.1.dev0`。
- 生产发布走 `scripts/prepare_v1_5_1.py reserve|freeze` + v1.5 的 `plan → publish --single-writer → verify`；只读依赖已改为冻结输入副本，使 live MASTER/registry 可在同一发布内合法更新。

## 已发布产物（20_data）

- `CBA_2017-2027_国内球员注册_MASTER.xlsx`：3,465 条 = 3,451 基线 + 14 条八一中转关系。
- [CBA_外籍球员注册_SNAPSHOTS.xlsx](https://drive.google.com/file/d/1avAxhNUTgm14MIdkgkHhia6_adSDGwLD/view)：73 条快照（2024-2025）。
- [CBA_球员注册_EVENTS.xlsx](https://drive.google.com/file/d/1WtE63GIQxYPUBsfcRlhKCH8lJgyTZeGF/view)：73 条事件（59 条外援取消注册 + 14 条八一中转）。
- 发布状态：https://drive.google.com/file/d/1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV/view ；生产 INDEX 已带 v1.5.1 事实产品与来源覆盖说明。

## 已实现

- 导入 Drive 现有脚本、真实 parser fixture 和 9 项既有回归，保留 legacy 同步保护。
- 只读 MASTER 校验、十个赛季阅读版、完整 CSV/JSONL 派生导出。
- 独立本地 OAuth、按 ID 拉取及变更检测。
- 普通文件和受控原生 Docs 沙盒发布器：冻结包、备份、逐文件回读、journal、幂等重试、原 ID 回退。
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

生产写入目前禁用：尚需真实 OAuth 沙盒演练、完整发布清单与两 AI 验收。
低层普通文件发布器不接受 native Docs/Sheets。CLI 只允许 runtime.json 中配置的沙盒直接子对象，不能靠填写生产 ID 绕过。
不要直接运行 legacy/upload_drive.py；旧按名上传流程不是 v1.5 发布通道。

完整方案见 docs/CBA-KB_v1.5.md；授权步骤见 docs/SETUP_AUTH.md；实施状态见 docs/DEPLOYMENT_STATUS.md。
