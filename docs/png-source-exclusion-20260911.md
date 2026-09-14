# 两个 PNG source 的永久处理范围排除

任务基线：GitHub `codex/v1.5.2-draft2` 为 `a177df5`；本地已有文档收尾提交
`3752bb9`。本任务由该 Git 提交建立 feature branch，不使用 Drive 代码镜像反向补写。
默认分支、提交树、目标代码及测试已核验；基线无 GitHub Actions workflow。

启动时 Drive 为 `COMPLETE / v1.5.2-2`，pending=null，previous=v1.5.2-1。
生产状态记录六表 46 / 4 / 131 / 244 / 227 / 7，共 659 条；这是后续数据 patch 的
范围，不能套用历史 v1.5.2-1 的 600 条或把旧收尾报告当作当前状态。

用户永久排除的来源：

- `drive:1D72_i0dzR9TmeV_XH8KeBr_BTxxwFPIM`
- `drive:1WYVqi94bqVGOLsD7t1nmMXyTMTU4TOlr`

`source_policy.py` 按稳定 ID 排除，文件重命名或移动不会重新启用它们。
inbox 下载（含 `--all`）、递归 current inbox 扫描、直接登记、registry proposal 和
两个版本的 registry 合并均执行排除；旧 current CSV 和旧 proposal 也不能将其带回。
状态配置不再给这两个 ID 生成 `ocr_deferred` 待办。其他来源正常处理。

原始发现清单和历史 CSV 读取保留审计内容；archive、fixture、既有收尾审计不改写。
现存 `legacy/process_inbox.py` 仍是可执行登记入口，因此同样增加保护。
本任务不执行 production publish，不改变事实六表、schema、依赖或生产发布号。
部署目录继续停留在原收尾提交；feature branch 合并和后续受控维护须分别处理。

验证：新增回归修复前 14 failed / 1 passed；修复后相关测试 22 passed / 1 skipped。
离线全量 102 passed / 5 skipped；5 个跳过项依赖未提交的真实 MASTER、八一来源或
真实 DRAFT-2 候选，不记为通过。新增纯离线 Actions 执行专项及全量回归，无生产凭据。

执行方式：常规测试直接由 Actions 运行；Web ChatGPT 可做只读审查和维护任务说明，
不要编辑 Drive `50_scripts` 再回写 Git。生产资料差异先保存 readback 证据，交给本地
可信运行环境按维护流程解决，之后 Codex 只需核对结果。
