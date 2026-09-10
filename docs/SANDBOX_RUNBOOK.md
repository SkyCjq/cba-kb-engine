# 本地 OAuth 沙盒验收

当前 Drive 沙盒已准备，ID 见 config/sandbox.json。连接器完成过 INDEX 插入/回退；以下步骤用于验证本地程序，尚未执行。

在 `/Users/skychengneo/Agent/CBA_kb` 完成 `make auth` 后执行：

```sh
make doctor
make pull OUTPUT=workspace/inputs/oauth-001
make validate MASTER=workspace/inputs/oauth-001/MASTER.xlsx
```

比较下载的 SHA256 和 docs/baseline_2026-09-10.json。如果正式 MASTER 已更新，先检查变化，不能强行用旧候选覆盖。

准备仅指向沙盒的混合类型测试：

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import json
from cba_kb.native import wrap
root=Path.cwd()
cfg=json.loads((root/'config/sandbox.json').read_text())
folder=root/'workspace/sandbox-inputs/oauth-001'
folder.mkdir(parents=True,exist_ok=False)
text=folder/'INDEX.txt'
text.write_text(wrap('CBA-KB sandbox acceptance\nLocal OAuth transport test.\n'))
entries=[
    dict(id=cfg['master_id'],name='SANDBOX_MASTER.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',mode='binary',path=str(root/'workspace/inputs/oauth-001/MASTER.xlsx')),
    dict(id=cfg['index_id'],name='SANDBOX_INDEX',mime='application/vnd.google-apps.document',mode='managed_doc',path=str(text)),
]
(folder/'entries.json').write_text(json.dumps(entries,indent=2))
PY
make plan ARGS="--entries workspace/sandbox-inputs/oauth-001/entries.json --release-id sandbox-oauth-001 --status-id 1sBiVPnkkK01jJECk36_gTs4xE4Kx_-jo --archive-id 1MonkA5LlxsVHbi27yzh48fi0YpBZeshq"
make publish ARGS="--release workspace/outbox/sandbox-oauth-001 --single-writer"
make verify ARGS="--release workspace/outbox/sandbox-oauth-001"
make publish ARGS="--release workspace/outbox/sandbox-oauth-001 --single-writer"
make restore ARGS="--release workspace/outbox/sandbox-oauth-001 --single-writer"
```

single-writer 表示演练期间没有其他程序或人编辑这些沙盒文件。完成后核对 journal 为 ROLLED_BACK、MASTER 哈希恢复、INDEX 前缀消失且原正文/格式保持、状态文件恢复上一发布号。verify 检查发布后的候选，不能当成回退检查命令。后续演练使用新的编号和目录。

以上不会写生产目录。OAuth refresh、网络中断重试和两 AI 消费仍需分别记录真实结果。
