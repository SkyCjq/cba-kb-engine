# CBA-KB Requirement — REQ-180-IDENTITY-01 Player Identity Registry & Record Links

> 文档类型：单需求生命周期账本（Requirement Ledger）  
> 状态：FROZEN_SPEC  
> 目标版本：v1.8.0  
> 创建/冻结日期：2026-09-17  
> Requirement URL：https://drive.google.com/file/d/1YJYKKBKPg5B7pTmFKTSZhkO7UPpHGiEJ/view?usp=drivesdk  
> 指导方针：https://app.notion.com/p/CBA-KB-vNext-AI-3ddad272535b811fa80de74db3e43445  
> 开发流程：https://app.notion.com/p/CBA-KB-Token-Codex-3d8ad272535b814f9b62f385cc3bd7ab  
> GitHub：https://github.com/SkyCjq/cba-kb-engine

## 0. 元数据

```text
requirement_id: REQ-180-IDENTITY-01
version: v1.8.0
status: FROZEN_SPEC
lane: CODE_CONFIG
release_risk: HIGH
requirement_owner: human
requirement_md_url: https://drive.google.com/file/d/1YJYKKBKPg5B7pTmFKTSZhkO7UPpHGiEJ/view?usp=drivesdk
source_evidence: live release_status + current MASTER + GitHub main + vNext guideline
baseline_release_id: v1.6.1-1
baseline_code_commit: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
baseline_development_sha: d2771afbecc33ad9ce91bf34a4adce4c333e5821
target_base_branch: main
feature_branch: feat/REQ-180-IDENTITY-01
release_infrastructure_mode: OFF
execution_mode: CODEX_EXECUTOR
production_access: forbidden
stage2_authorized: false
requirement_revision: r1-20260917-v180-identity-stage1
optional_overlays.release_performance.enabled: false

human_runbook.dev_workspace: /Users/skychengneo/Agent/CBA_kb_dev/REQ-180-IDENTITY-01
human_runbook.local_runtime_root: /Users/skychengneo/Agent/CBA_kb
human_runbook.private_instance_root: /Users/skychengneo/Agent/CBA_kb_instance
human_runbook.local_prepare_command: NOT_IMPLEMENTED
human_runbook.publish_command: NOT_IMPLEMENTED
human_runbook.rollback_command: NOT_IMPLEMENTED
runtime_evidence_ledger: /Users/skychengneo/Agent/CBA_kb_instance/evidence/REQ-180-IDENTITY-01/
```

## 1. Problem

v1.7 的 Player Profile 仍以显式 `record_key` 选择为权威，exact-name lookup 只返回候选并明确不建立身份，且当前 validator 明确禁止把 `player_uid` 放进 Profile。v1.8 需要建立独立的 Player Identity domain，使跨赛季、跨球队记录能够稳定关联，但不得改写 MASTER 或把“同名”自动提升为“同一人”。

## 2. In Scope

- 新增 `player`、`player_alias`、`player_record_link` 的确定性 schema、validator、序列化与私有实例存储逻辑。
- `player_uid` 为 opaque stable identifier，不由姓名、球队、赛季或 record_key 推导。
- `player_record_link` grain 固定为 `record_key × player_uid`，支持 `same | not_same | undecided`。
- exact-name / alias 只做 candidate discovery；任何自动链接策略必须 fail-closed。
- 提供只读/写私有实例的 CLI；Engine repository 仅保存代码、schema、validator 与 synthetic fixtures。
- 建立 real private canary 的验收契约，覆盖 vNext 指定风险类别；真实域不存在的风险类别允许记录 NOT_OBSERVED_WITH_EVIDENCE，不得伪造。

## 3. Out of Scope

- 不向 MASTER、Canonical Facts、Event 表增加 `player_uid`。
- 不做 `document_player_mention`、Profile v2、Stats、Claim Ledger、Personal Notes migration。
- 不接 production publish/restore/release orchestration。
- 不新增 release framework、SHA role、state machine 或权限 taxonomy。

## 4. Semantic / Governance Rules

- `player_record_link` 是 identity relation，不是事实表；MERGE/SPLIT 只能改 relation/redirect，不能改 canonical facts。
- 同名不是身份；alias 允许多 player candidate，冲突必须显式。
- 每个 `record_key` 最多只能有一个有效 `same` identity；`undecided` 合法且可见。
- `player_alias` 不得通过全局唯一约束把同名球员误合并。
- 私有人工映射、真实 `player_uid`、审核备注、canary 结果不得写入 Engine repo。
- production release state 与 project closeout state 分离。
- Static task contract 与 runtime evidence 分离。

## 5. Acceptance Criteria

```text
A01 player_uid 是 opaque stable ID；禁止从 name/team/season/record_key 可逆或可预测地产生。
A02 player / alias / record-link schema 可机器验证，重复主键与非法状态 fail closed。
A03 同一 record_key 最多一个 link_status=same；多候选必须保留 not_same/undecided 语义。
A04 exact-name/alias lookup 只返回 candidates，不自动建立 same。
A05 merge/split/redirect 不修改 MASTER 与 canonical facts/events。
A06 当前 MASTER.csv 3465 行、3465 unique record_key；实现前后 SHA-256 必须 ZERO DIFF。
A07 audited real canary 的 false merge = 0；所有 undecided 可被枚举，不 silent drop。
A08 canary 覆盖 stable_longitudinal / multi_club / name_variant / OCR_corruption / sparse_history；
    same_name_collision 若当前真实域未观察到，必须以 corpus audit 证明，不得构造为“真实案例”。
A09 synthetic tests 必须包含 same-name collision 与 ambiguous alias，证明系统 fail closed。
A10 输出 ordering/hash deterministic；相同输入重复运行字节一致。
A11 Engine 不出现真实 player_uid mapping、人工判断、credential 或 Private Instance 路径内容。
A12 CLI 在未提供 Private Instance 时 fail closed。
A13 `src/cba_kb/player_profile.py` 的 v1.7 contract 不在本 REQ 内改变。
A14 GitHub Actions required check `tests` GREEN 后才可进入 Web merge review。
```

### 5.1 Frozen numeric / hash gates

```text
Phase 0 live baseline:
  release_id: v1.6.1-1
  state: COMPLETE
  production_code_sha: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
  release_status_sha256: f4fb563c90ed19231d17fa4f5e88e5898bd971d077c046f52f57ce8360a6900d
  production_artifacts: 207
Current MASTER.csv:
  file_id: 1FsHkEbGeNSqLqcIqZuoUSzFZTVR6Wyue
  sha256: 0aafb6a17748f8d13b83736ac22b5ba3d5947a924e1f987d9ba249bcd6879e7f
  rows: 3465
  unique_record_key: 3465
  unique_player_text: 927
Tables/files that MUST NOT change:
  MASTER.csv / MASTER.xlsx / canonical fact workbooks / event products / source_registry / manifest
Expected business delta:
  0
```

### 5.2 Release-critical fixtures

```text
1. live_release_status_v1.6.1-1 @ https://drive.google.com/file/d/1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV/view?usp=drivesdk
2. current_MASTER.csv @ https://drive.google.com/file/d/1FsHkEbGeNSqLqcIqZuoUSzFZTVR6Wyue/view?usp=drivesdk sha256=0aafb6a17748f8d13b83736ac22b5ba3d5947a924e1f987d9ba249bcd6879e7f
3. private identity_canary_v1 (human-approved; runtime evidence only)
4. synthetic same-name/ambiguous-alias fixtures in tests
5. facts_zero_diff_hash_set
```

## 6. Rollback / Compatibility

Identity files are additive derived/private artifacts. Rollback means discard the v1.8 private identity artifact set and continue using v1.7 explicit-record-key profiles. No canonical data rollback is expected because canonical mutation is forbidden.

# PART B — Stage 1 Audit / Freeze

## 7. Stage 1 — Prepare / Audit / Freeze

### 7.1 Requirement Draft Review — WEB-01

```text
result: PASS
problem/scope complete: YES
semantic conflicts: NONE OBSERVED
acceptance gaps: NONE BLOCKING FOR CODE START
```

### 7.2 Code Truth Baseline / Impact Audit — WEB-03

```text
result: CODE_CHANGE_REQUIRED
repo accessible: YES
base branch: main
HEAD SHA: d2771afbecc33ad9ce91bf34a4adce4c333e5821
tree: 579cdf5fbd78ad46f273a1e9bd43f03cf7634081
relevant code:
  src/cba_kb/player_profile.py
  src/cba_kb/master.py
  src/cba_kb/instance.py
  src/cba_kb/cli.py
relevant tests:
  tests/test_player_profile.py
  tests/test_master.py
  tests/test_engine_instance_boundary.py
  tests/test_security_guards.py
relevant workflow:
  .github/workflows/offline-tests.yml (required check: tests)
config readers:
  existing Instance.data_root is sufficient; no new config file is required
private-material dependency:
  /Users/skychengneo/Agent/CBA_kb_instance/data/player_identity/
minimal change set:
  new identity module + CLI surface + focused tests only
lane: CODE_CONFIG / HIGH
```

### 7.3 Frozen Spec / Contract — WEB-02

```text
status: FROZEN_SPEC
frozen baseline release: v1.6.1-1
frozen baseline code commit: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
target base branch: main
baseline development SHA: d2771afbecc33ad9ce91bf34a4adce4c333e5821
source_contract_probe: N/A — no external API/site/scraping/live schema dependency
release_infrastructure_mode: OFF
optional performance overlay: OFF
```

#### exact allowed_paths

```text
requirements/REQ-180-IDENTITY-01/requirement-r1-20260917.md
requirements/REQ-180-IDENTITY-01/task-brief.md
requirements/REQ-180-IDENTITY-01/task.yaml
src/cba_kb/player_identity.py
src/cba_kb/cli.py
tests/test_player_identity.py
tests/test_cli_identity_v18.py
```

#### focused_tests

```text
tests/test_player_identity.py
tests/test_cli_identity_v18.py
tests/test_player_profile.py
tests/test_master.py
tests/test_engine_instance_boundary.py
tests/test_security_guards.py
tests/test_current_history_separation.py
```

#### must_not_change

```text
src/cba_kb/master.py
src/cba_kb/player_profile.py
src/cba_kb/document_lane.py
src/cba_kb/document_sources.py
src/cba_kb/consumer_projection.py
src/cba_kb/consumer_package.py
src/cba_kb/consumer_acceptance.py
src/cba_kb/release.py
src/cba_kb/drive.py
src/cba_kb/transport.py
src/cba_kb/native.py
src/cba_kb/instance.py
scripts/prepare_production.py
.github/workflows/offline-tests.yml
requirements.lock
config/production.json
config/runtime.json
config/sandbox.json
config/drive_map.yaml
config/taxonomy.yaml
canonical facts / event products / source_registry semantics / production manifest
```

### 7.4 Frozen Human Runbook

```text
execution_mode: CODEX_EXECUTOR
DEV_WORKSPACE: /Users/skychengneo/Agent/CBA_kb_dev/REQ-180-IDENTITY-01
LOCAL_RUNTIME_ROOT: /Users/skychengneo/Agent/CBA_kb
PRIVATE_INSTANCE_ROOT: /Users/skychengneo/Agent/CBA_kb_instance
local_prepare_command: NOT_IMPLEMENTED
publish_command: NOT_IMPLEMENTED
rollback_command: NOT_IMPLEMENTED
command_verification_evidence:
  Makefile@d2771afbecc33ad9ce91bf34a4adce4c333e5821 has no v1.8 identity/local-prepare/publish alias.
  Generic production make publish/restore exist but are NOT authorized for this requirement.
  No future alias is treated as executable today.
```

### 7.5 Human Approval / REQ-START

```text
READY_TO_CODE: YES
human_frozen_spec_approval: APPROVED_BY_USER_FOR_STAGE1_FREEZE
stage2_authorized: NO — this turn is Stage 1 only
feature_branch: feat/REQ-180-IDENTITY-01
task_contract_commit: TBD_STAGE2
base_sha_verified: YES (d2771afbecc33ad9ce91bf34a4adce4c333e5821)
```

## 8. Remaining stages

Stages 2–5 remain NOT_STARTED. Codex implementation, CI, merge, local acceptance and any production action are explicitly outside this Stage 1 run.

## 9. Final Stage 1 verdict

`READY_TO_CODE`

Reason: live baseline verified; GitHub base verified; impact allowlist closed; no external source contract applies; Private Instance boundary is explicit; runbook contains no invented command; release infrastructure is not touched.
