# tests/fixtures/contract/ — 首批 contract fixtures（合成/脱敏）

对应 Mode 2.1 §10 数据规则：注册六表、身份注册表、文档提及、人工审核契约。
供 v2.0.3 试跑的 clean-room 测试使用；**永不放入真实数据**。

## 文件（9 个）

| 文件 | 说明 |
|---|---|
| `domestic_registrations.csv` | 国内球员注册（合成） |
| `transaction_windows.csv` | 交易窗口（合成） |
| `registration_status_events.csv` | 注册状态事件（合成） |
| `foreign_registration_snapshots.csv` | 外援注册快照（合成） |
| `foreign_right_snapshots.csv` | 外援优先权快照（合成） |
| `foreign_right_transactions.csv` | 外援优先权交易（合成） |
| `identity_registry_contract.json` | 身份注册表契约 |
| `document_player_mentions.json` | 文档球员提及契约 |
| `identity_review_decisions.json` | 人工审核决策契约 |

## 读取约定

- CSV 首行可能是 `#` 开头的注释行（列说明），**不是列名行**；读取器必须显式
  跳过 `#` 开头行后再解析表头。
- 球员姓名均为合成值（含"合成"/"SYNTH"/"DOE"标记）；已通过 927 个真实姓名
  全量交叉比对 + 手机号/身份证号正则扫描（2026-10-09，零命中）。

## 红线

- 任何真实球员姓名、手机号、身份证号**永不**进入本目录（`check_secrets.py` + CI 扫描）。
- 新增/修改 fixture 触发 T2（`tests/fixtures/**` 在 risk-tiers.yml 中为 T2），
  须 Human 审查。
