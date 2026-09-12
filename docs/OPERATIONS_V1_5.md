# v1.5 运行与恢复

Engine repository 管理代码、schema、adapter、validator 和 synthetic fixture。真实配置、
Drive IDs、凭据、真实 fixture、人工修正和运行输出只存在于独立 Private Instance。
所有需要 Drive 或 watcher 私有输出的命令必须显式传入 `INSTANCE_ROOT`；缺失即 fail closed。

## 单写者维护窗口

发布/恢复期间暂停其他本地脚本、人工编辑和其他 AI 写入同一发布集。当前 flock 只约束本机；远端元数据与哈希预检不是跨客户端事务锁。禁止使用 legacy/upload_drive.py、旧 make sync/wechat 写入。生产必须通过 Private Instance `config/production.json` 的完整白名单，不能把 sandbox folder ID 改为生产目录绕过。

读取者每次先读 release_status.json。仅 COMPLETE 可使用 current artifacts。PUBLISHING、FAILED、ROLLING_BACK 时通过 previous_snapshot 读取已冻结快照，不能混用仍在逐项改写的原 ID 文件。ROLLED_BACK 时读取恢复后的上一发布；再次验证状态/发布号，若读取前后变化则重试。链接在生产 INDEX 和 README。

## 标准流程

make pull INSTANCE_ROOT="$INSTANCE_ROOT" OUTPUT=workspace/inputs/<new-run>
make validate MASTER=workspace/inputs/<new-run>/MASTER.xlsx
make build MASTER=workspace/inputs/<new-run>/MASTER.xlsx OUTPUT=workspace/candidates/<new-run> RELEASE_ID=<new-run>
make watch INSTANCE_ROOT="$INSTANCE_ROOT" ARGS='--output "$INSTANCE_ROOT/data/watcher/<new-run>"'

构建前代码必须已提交。Watcher 的 snapshot、diff、candidate、validation 和 run report 只是复核证据；observed change 不等于 business event，且不会直接 publish。发布计划冻结依赖 ID、hash、version；输入变化时重建，不能忽略冲突。使用完整条目和依赖执行 plan --environment production，之后 publish --single-writer、verify。readback 失败即保留 FAILED，不宣称成功；确认原因后重试 publish 或 restore --single-writer。

restore 先发布 ROLLING_BACK，之后逐项恢复字节/受控前缀；新增对象移回暂存区，最终 ROLLED_BACK；响应丢失可重试。outbox 与 before 不可删除。生产故障期间不要运行新发布。

## AI 使用边界

Gemini 已验证直接提供 INDEX 与赛季文件链接可读取；仅提供入口后自动递归读取依赖曾失败。应直接给问题相关文件链接；无法读取时明确报错，不从记忆补答案。未承诺 NotebookLM 自动刷新或任意产品自动同步。

原生 Docs 顶部为受控当前发布内容，历史正文保留。旧跨赛季阅读内容不用于 v1.5 当前事实。auto_validated 不是人工核验，空字段不是零，注册记录数不是独立球员人数。

## 版本边界

v1.5 不导入外援或八一新增材料；v1.5.1 在 v1.5.0 最终验收/tag 后实施。当前 20 列 MASTER 不加 player_uid。新增事实产品沿用稳定 ID、暂存→发布→回退生命周期。
