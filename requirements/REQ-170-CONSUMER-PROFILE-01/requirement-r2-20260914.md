# CBA-KB Requirement — REQ-170-CONSUMER-PROFILE-01
# v1.7.0 Consumer Projection + Facts-only Player Profile MVP

> 文档类型：单需求生命周期账本（Requirement Ledger）  
> revision：`r2-20260914-v170-final-critical-revision`  
> 状态：`FROZEN_SPEC / READY_TO_CODE`  
> 目标版本：v1.7.0  
> 本 revision supersedes：
> - `r1-20260914-v170-stage1-freeze`
> - 早期审计候选 `REQ-170-CONSUMER-PROJECTION-01 / r1-20260913-audit-candidate`
>
> 说明：本 revision 对已冻结 scope / acceptance / allowlist 做了实质修订，因此在用户重新批准 Freeze 之前，不得进入 Stage 2。当前未写产品代码、未改 production。

## 0. 元数据

```text
requirement_id: REQ-170-CONSUMER-PROFILE-01
version: v1.7.0
status: FROZEN_SPEC
lane: CODE_CONFIG
requirement_owner: CBA-KB maintainer

baseline_release_id: v1.6.1-1
baseline_code_commit: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
target_base_branch: main
baseline_development_sha: 4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538
baseline_development_tree: aeac4a4e6eaa304b99b39b701e53cacee13ec70f
feature_branch: feat/REQ-170-CONSUMER-PROFILE-01

production_release_id: N/A_IN_FEATURE_REQ
production_release_state: NOT_STARTED
project_closeout_state: OPEN

release_infrastructure_mode: OFF
execution_mode: CODEX_EXECUTOR
production_authority_contract: N/A

runtime_evidence_ledger:
  /Users/skychengneo/Agent/CBA_kb_instance/evidence/REQ-170-CONSUMER-PROFILE-01/

optional_overlays.release_performance.enabled: false

human_runbook.dev_workspace:
  /Users/skychengneo/Agent/CBA_kb_dev/REQ-170-CONSUMER-PROFILE-01
human_runbook.local_runtime_root:
  /Users/skychengneo/Agent/CBA_kb
human_runbook.private_instance_root:
  /Users/skychengneo/Agent/CBA_kb_instance
human_runbook.local_prepare_command: NOT_IMPLEMENTED
human_runbook.publish_command: NOT_IMPLEMENTED
human_runbook.rollback_command: NOT_IMPLEMENTED
```

### 0.1 Authority / baseline clarification

1. live `release_status.json` is production authority. Current verified state is `COMPLETE / v1.6.1-1 / code_commit=81bd581...`.
2. GitHub `main@4103ce2...` is the development base; it is protected and requires the `tests` check.
3. `main@4103ce2...` and production execution `81bd581...` resolve to the same source tree at this Freeze baseline; SHA roles remain distinct.
4. The current Notion roadmap callout still describes `v1.6.0-1` as the production baseline. That text is documentation drift; the same roadmap explicitly states live `release_status` is authoritative. This drift is not a v1.7 product blocker and must not be copied into runtime contracts.

## 1. Problem

v1.6.1 已经落地 consumer 基础设施：rights-aware Document Projection、event coverage、consumer result/failure-layer schema、10-question v1 bootstrap contract，以及 release/governance closeout。v1.7 不应重复这些能力。

v1.7 真正缺失的是一个“可被多个 AI 实际使用”的产品闭环：

1. 从当前多年 canonical MASTER 生成 **facts-only player profile / timeline**；
2. 将 profile、受控 Document Evidence、Event Coverage 与来源索引组合成 **同一 canonical consumer payload**；
3. 为 ChatGPT / Gemini Notebook / WorkBuddy 生成可追溯、完整、不静默截断的目标包；
4. 用同一组真实、预先冻结的问题做三端验收；
5. 在验收后进入真实 Usage Gate，再由失败分布决定是否需要 MCP / Identity / Stats / 下一版本。

本需求不引入稳定 `player_uid`，不修改 canonical Facts，不实现 MCP，也不承担 production publication。

## 2. In Scope / Deliverables

### D01 — Facts-only Player Profile

新增只读 `player_profile` 能力。输入只能来自冻结的 current MASTER snapshot 和显式 selector。

Profile 必须：

- 保留 `record_key / season / club_id / player`；
- 保留原始注册、合同、来源、verification 字段和 NULL；
- 携带 `release_id / as_of / source_master_sha256 / generator_sha`；
- 固定排序：`season → club_id → record_key`；
- 不推断精确日期、转会顺序、合同到期日、真人 identity 或未记录事实。

### D02 — Explicit Selector / Identity Boundary

v1.7 没有 Identity Registry，因此 profile 的 canonical selector **不是姓名**，而是 Private Instance 中预先冻结的 `record_key` 集合。

允许提供 exact-name candidate discovery，但它只能返回候选 rows：

```text
EXACT_NAME_ONLY
→ candidate record_keys
→ ambiguous / same-name / multi-club uncertainty remains explicit
→ no automatic person merge
```

若存在同名、多俱乐部或其他 identity ambiguity，必须 `REVIEW_REQUIRED`；禁止制造 `player_uid` 或 same-person assertion。

### D03 — Canonical Consumer Payload

构造唯一 canonical payload：

```text
release_scope
player_profile
selected_document_projections
event_coverage
source_index
coverage
```

其中：

- Document projection 复用 v1.6.1 已冻结的 rights-aware semantics；
- Event Coverage 复用 v1.6.1 已冻结的 deterministic semantics；
- v1.7 不修改 `consumer_projection.py` 的 v1 contract；
- Evidence 不进入 Facts，Profile 也不是新的 Fact Truth。

canonical payload 产生稳定 `consumer_payload_sha256`。

### D04 — Three Target Packages

为以下三个目标生成显式 package：

```text
ChatGPT
Gemini Notebook
WorkBuddy
```

三者必须引用同一个 `consumer_payload_sha256`。

每个 package 至少包含：

```text
README / intake instructions
package_manifest
player_profile
source_index
authorized document projection files
event coverage when selected by the frozen spec
coverage report
```

目标产品存在文件数量/大小限制时允许 deterministic sharding，但必须满足：

```text
declared_items == packaged_items + explicitly_blocked_items
missing = 0
unexpected = 0
duplicate = 0
silent_truncation = 0
```

target-specific 包装可以不同，但 canonical payload 内容不可漂移。

### D05 — Target Authorization

Private / copyrighted / unknown material 只有出现在 **per-target authorization manifest** 中才能进入该目标 package。

授权至少绑定：

```text
artifact/doc_id
target
allowed_scope
authorization_basis
frozen_at
```

“允许个人账户消费”不等于 `public_export_allowed=true`，不得改变 Document Lane 原 rights 分类。

未授权内容必须进入 `blocked/excluded` coverage，而不是被静默忽略。

### D06 — v1.7 Consumer Acceptance Contract v2

真实问题、expected answer、selector 和真实 consumer result 全部留在 Private Instance。Engine 只保存 schema、validator 和 synthetic fixtures。

固定 10 题：

```text
deterministic_exact: 4
source_tracing: 2
cross_source_synthesis: 2
identity_boundary: 1
unknown_gap: 1
total: 10
```

这比旧审计候选的 `4 exact + 3 tracing + 3 synthesis` 增加了 identity/unknown 安全边界，也比上一版 r1 的 `5 exact + 2 provenance + 2 profile_boundary + 1 unknown` 恢复了真正的跨来源综合能力。

问题集必须在任何 consumer 运行前：

```text
independently validated
frozen
content-hashed
```

看到 consumer 结果后不得静默修改 expected answer。

### D07 — Consumer Intake Precheck

`SOURCE_CONTRACT_PROBE` 对本 REQ 为 N/A，因为代码实现不依赖外部 API、官网 scraping 或 live schema。

但真实消费者验收前必须做独立的 `CONSUMER_INTAKE_PRECHECK`。每个目标记录：

```text
target
run_date
package_sha256
expected_files
accepted_files
rejected_files
observed format/size constraints
search/retrieval ready
warning/truncation observed
receipt/evidence_ref
```

这属于 Stage 4 runtime evidence，不是 Stage 1 source-contract blocker。

### D08 — 30-cell Acceptance Matrix

三个 consumer × 10 questions = 30 个预期 cells。

每格：

```text
PASS | FAIL | NOT_TESTED
failure_layer
evidence_ref
raw_answer_ref
```

`FAIL` 沿用 v1.6.1 failure layers：

```text
projection
retrieval
aggregation
counting
provenance
source_gap
identity
consumer_tool
```

rights 未授权、目标不可用等不应伪造为 PASS；可记录 NOT_TESTED reason，但 NOT_TESTED 永不计 PASS。

### D09 — MCP Trigger

如果任一 deterministic exact 题失败，且人工 root-cause review 确认为：

```text
retrieval | aggregation | counting
```

则输出：

```text
MCP_TRIGGER_REQUIRED
follow_up_requirement_id: TBD_NEW_REQ
```

MCP trigger 不等于本版通过，也不在本 REQ 内实施 MCP。

`projection` 属于本版软件缺陷，应在本 REQ 内修复；`source_gap / identity / provenance / consumer_tool` 按对应领域分类，不自动归因 MCP。

### D10 — Usage Gate

consumer acceptance 通过后输出 `USAGE_GATE_OPEN`，但 v1.8 不自动开始。

在 v1.7 项目最终 `CLOSED` 前，收集至少 10 条**非验收性质**的真实查询。10 是工程上的最小观察批次，不是统计显著性声明。

每条记录至少包含：

```text
consumer
question
answer/result
evidence_refs
failure_domain
manual_minutes
observed_at
```

failure domain：

```text
Document | Event | Identity | Stats | Consumer Tool | Other
```

在 10 条齐全前：

```text
project_closeout_state = EXIT_EVIDENCE_PENDING
```

齐全后才形成：

```text
NEXT_STEP_DECISION =
  CONTINUE_V1_8
  | SPLIT_NEW_REQUIREMENT
  | HOLD_AND_KEEP_USING
```

## 3. Out of Scope

- 修改 `MASTER.xlsx`、六表、Event、source_registry、Document raw/archive 或任何 canonical fact。
- Identity Registry / `player_uid` / alias 自动合并。
- Stats、Claim Ledger、Notes Lane、MCP server。
- 新 CBA 官网/API、scraping、微信抓取。
- consumer 产品 API、自动登录、自动上传或机器人控制 ChatGPT/Gemini/WorkBuddy。
- 修改 v1.6.1 `config/consumer_golden_questions_v1.yaml`。
- 修改 v1.6.1 `src/cba_kb/consumer_projection.py` 的现有 v1 semantics。
- 修改 release / restore / rollback / state machine / manifest / target allocation / production policy。
- production write；production publication 必须另立 `REQ-170-PROD-RELEASE-01`。
- 把真实球员 fixtures、真实 golden answers、真实 consumer answers、target authorization 或 credentials 提交到公开 Engine repo。

## 4. Semantic / Governance Rules

1. **Snapshot ≠ Event ≠ Relation ≠ Window ≠ Stat。**
2. Profile 是 derived read-only view，不是 Fact Truth。
3. Explicit `record_key` selector 是一次 profile scope，不是人物 UID。
4. EXACT_NAME_ONLY 只可用于 candidate discovery，不能自动做 same-person merge。
5. NULL / unknown 必须原样保留。
6. Document Evidence 不得因为 profile/synthesis 需求进入 canonical Facts。
7. 三端必须来自同一 canonical payload；package 差异只能是 target packaging。
8. selected documents 必须逐 target 授权；授权不会改变 public-export rights。
9. 真实 oracle、真实 profile、真实回答、授权和 receipts 属于 Private Instance/runtime evidence。
10. v1.6.1 consumer contract v1 保持不变；v1.7 使用独立 v2。
11. Git `task.yaml` 仅存 static contract，不持续写 CI/GO/consumer result/usage records。
12. 如实现方案新增 external API / auth API / scraping / live schema，STOP 回 Stage 1 做 `SOURCE_CONTRACT_PROBE` 并 re-freeze。
13. 如 scope 扩展到 production target/manifest/release orchestration，STOP 并拆 `REQ-170-PROD-RELEASE-01`。
14. GitHub Actions 是 full regression owner；Codex 只执行 allowlisted implementation + focused tests。

## 5. Acceptance Criteria

```text
A01 live baseline = COMPLETE / v1.6.1-1 /
    production code 81bd581fafbccb602f9ecaf9aaefca4533be69a4;
    release_status sha256 =
    f4fb563c90ed19231d17fa4f5e88e5898bd971d077c046f52f57ce8360a6900d;
    active artifacts = 207; previous_snapshot = 223.

A02 development base = main @ 4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538;
    tree = aeac4a4e6eaa304b99b39b701e53cacee13ec70f;
    main protected = true; required check = tests.

A03 current MASTER fixture is read-only, schema=20 columns, and matches the
    frozen current production hash. Any baseline/hash drift => STOP + re-freeze.

A04 profile is deterministic for identical:
    MASTER bytes + explicit selector + release_id + as_of + generator code.
    Output sort = season, club_id, record_key.

A05 canonical profile construction requires explicit record_key selector.
    Name-only ambiguity never silently merges people; no player_uid is fabricated.

A06 every included profile row preserves record_key, season, club_id, player,
    registered nullable fields, source_file_id/source_url and verification_level;
    no unknown value is inferred.

A07 canonical consumer payload deterministically combines profile,
    selected rights-aware document projections, selected event coverage,
    source index and coverage metadata; Evidence does not mutate Facts.

A08 all three target packages reference the same consumer_payload_sha256.
    declared == packaged + explicitly_blocked;
    missing=0, unexpected=0, duplicate=0, silent_truncation=0.

A09 private/copyrighted/unknown document content may enter a target package only
    when that exact item+target exists in frozen target authorization.
    Public-export semantics remain unchanged.

A10 v2 golden set = exactly 10:
    exact=4, source_tracing=2, synthesis=2, identity_boundary=1, unknown_gap=1.
    Real question/answer values stay outside Engine and are frozen+hashed
    before the first consumer run.

A11 every golden expected answer is independently validated against frozen
    canonical inputs before consumer execution. An unresolved oracle invalidates
    the question set and requires re-freeze; it is not scored as a consumer error.

A12 CONSUMER_INTAKE_PRECHECK exists for ChatGPT, Gemini Notebook and WorkBuddy
    before their real run; package/file acceptance and observed truncation/warnings
    are recorded as runtime evidence.

A13 result matrix contains exactly 30 cells.
    PASS/FAIL/NOT_TESTED are explicit; FAIL has valid failure_layer and evidence_ref;
    NOT_TESTED never counts as PASS.

A14 hard per-consumer gates:
    deterministic_exact = 4/4;
    source_tracing = 2/2;
    identity_boundary = 1/1;
    unknown_gap = 1/1;
    overall PASS >= 9/10.
    Aggregate PASS >= 27/30.
    Therefore only synthesis may consume the one tolerated miss per consumer.

A15 a target with incomplete execution or product unavailability cannot be
    described as "complete"; NOT_TESTED remains in the 30-cell denominator.

A16 confirmed exact failure caused by retrieval/aggregation/counting emits
    MCP_TRIGGER_REQUIRED + a new follow-up REQ ID. Triggering MCP does not make
    v1.7 acceptance PASS.

A17 projection defects are fixed inside this REQ.
    source_gap / identity / provenance / consumer_tool are classified to their
    real domain and do not falsely trigger MCP.

A18 canonical Facts/Event/registration products, source_registry semantics,
    Document immutable raw/archive and published fact products = ZERO DIFF.

A19 new v1.7 code performs no Drive/OAuth/HTTP/release write path during profile,
    package or evaluation commands. Production access remains forbidden.

A20 v1.6.1 consumer projection v1 and golden-question v1 behavior remain
    byte/semantic compatible; v1.7 code layers around v1 rather than rewriting it.

A21 public Engine contains zero real player selector, real golden answer,
    real consumer answer/result, target authorization, Private Instance path payload,
    Drive credential or secret. Synthetic fixtures are visibly synthetic.

A22 after A12-A17 PASS, emit USAGE_GATE_OPEN.
    Project cannot be CLOSED until >=10 non-acceptance real usage records exist
    with failure-domain classification and a next-step decision.

A23 production publication is N/A in this Feature REQ.
    Any production target/manifest/release integration is a separate
    REQ-170-PROD-RELEASE-01 with a fresh Stage 1 and release_infrastructure_mode=ON.

A24 GitHub Actions owns full regression.
    Codex runs focused tests only; CI RED never enters WEB-04;
    reviewed_PR_head == reviewed_CI_head before merge.

A25 Human Runbook contains no fictional v1.7 project command:
    local_prepare_command=NOT_IMPLEMENTED;
    publish_command=NOT_IMPLEMENTED;
    rollback_command=NOT_IMPLEMENTED.
```

### 5.1 Scope-specific numeric / hash gates

```text
Phase 0:
  current_release_id: v1.6.1-1
  state: COMPLETE
  production code_commit: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
  release_status_sha256:
    f4fb563c90ed19231d17fa4f5e88e5898bd971d077c046f52f57ce8360a6900d
  active_artifacts: 207
  previous_snapshot_artifacts: 223

Development:
  branch: main
  HEAD: 4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538
  tree: aeac4a4e6eaa304b99b39b701e53cacee13ec70f
  branch_protected: true
  required_check: tests
  production_tree_relation: SAME_TREE

MASTER:
  logical_key: MASTER.xlsx
  sha256:
    185cf58d69ced2ba38a1745cdb848ab21288a666d893d3d4bae3a3ddd1c2d588
  schema_columns: 20
  mutation_allowed: NO

Consumer acceptance:
  consumers: 3
  questions_each: 10
  cells: 30
  exact_cells: 12
  tracing_cells: 6
  identity_boundary_cells: 3
  unknown_gap_cells: 3
  minimum_total_pass: 27
  minimum_usage_gate_real_queries: 10
```

这些数字是本次 frozen baseline；任何 live baseline drift 在 implementation 开始前必须重新核验。

### 5.2 Release-critical fixtures

```text
PF01 live_release_status_v1.6.1-1
  required before local precheck: YES

PF02 current_master_private_snapshot
  expected sha256:
    185cf58d69ced2ba38a1745cdb848ab21288a666d893d3d4bae3a3ddd1c2d588
  public repo copy: FORBIDDEN

PF03 selected_profile_record_keys
  Private Instance only
  stable person UID: NO

PF04 target_authorization_manifest
  per-target item authorization
  Private Instance only

PF05 selected_document_projection_set
  generated with existing v1.6.1 rights-aware semantics
  raw private/copyrighted source copy in Engine: FORBIDDEN

PF06 consumer_golden_questions_v2
  10 real questions + validated oracle
  hash frozen before first consumer run
  Private Instance only

PF07 consumer_intake_receipts
  ChatGPT / Gemini Notebook / WorkBuddy
  required before each target run

PF08 consumer_result_matrix_v2
  expected cells: 30
  required before acceptance closeout

PF09 facts_zero_diff_hash_set
  canonical mutation: 0

PF10 usage_gate_real_query_log
  minimum real non-acceptance queries: 10
  required for project CLOSED, may remain pending after feature acceptance
```

### 5.3 SOURCE_CONTRACT_PROBE / Consumer Intake

```text
SOURCE_CONTRACT_PROBE: N/A
reason:
  no external API, official-site scraping or live schema is an implementation dependency.

CONSUMER_INTAKE_PRECHECK: REQUIRED_AT_STAGE_4
reason:
  target UI/file intake is operational acceptance evidence, not an Engine source contract.

invalidating condition:
  any implementation dependency on external API/auth API/scraping/live schema
  => STOP + Stage 1 probe + re-freeze.
```

### 5.4 Release Infrastructure Contract

```text
release_infrastructure_mode: OFF
Production Authority Contract: N/A
typed release SHA roles: N/A for this Feature REQ
release state machine: N/A
manifest coverage: N/A
Optional Performance Overlay: OFF

scope expansion to production:
  split REQ-170-PROD-RELEASE-01
  fresh Stage 1
  release_infrastructure_mode = ON
  Performance Overlay = ON
```

## 6. Deterministic Impact Review / Frozen Change Surface

### 6.1 exact allowed_paths

```text
requirements/REQ-170-CONSUMER-PROFILE-01/requirement-r2-20260914.md
requirements/REQ-170-CONSUMER-PROFILE-01/task-brief.md
requirements/REQ-170-CONSUMER-PROFILE-01/task.yaml
src/cba_kb/player_profile.py
src/cba_kb/consumer_package.py
src/cba_kb/consumer_acceptance.py
src/cba_kb/cli.py
tests/test_player_profile.py
tests/test_consumer_package.py
tests/test_consumer_acceptance.py
tests/test_cli_consumer_v17.py
```

### 6.2 focused_tests

```text
tests/test_player_profile.py
tests/test_consumer_package.py
tests/test_consumer_acceptance.py
tests/test_cli_consumer_v17.py
tests/test_consumer_projection.py
tests/test_master.py
tests/test_engine_instance_boundary.py
tests/test_security_guards.py
tests/test_current_history_separation.py
tests/test_document_lane.py::test_rights_taxonomy_and_public_export_guards
```

### 6.3 must_not_change

```text
.github/workflows/offline-tests.yml
requirements.lock
config/consumer_golden_questions_v1.yaml
src/cba_kb/consumer_projection.py
src/cba_kb/master.py
src/cba_kb/release.py
src/cba_kb/drive.py
src/cba_kb/transport.py
src/cba_kb/native.py
src/cba_kb/instance.py
src/cba_kb/document_lane.py
src/cba_kb/document_sources.py
src/cba_kb/source_watcher.py
src/cba_kb/canonical_registry.py
src/cba_kb/current_state.py
scripts/prepare_production.py
config/production.json
config/runtime.json
config/sandbox.json
config/drive_map.yaml
requirements/REQ-161-CLOSEOUT-01/**
canonical Facts/Event/registration products
source_registry semantics
Document Lane immutable raw/archive
live release_status / manifest / production target inventory
```

设计选择：v1.7 新增模块围绕已经稳定的 v1.6.1 consumer projection 构建，不原地扩写 v1 模块。这比上一版 r1 把 `consumer_projection.py` 放入 allowlist 更保守、更容易证明 backward compatibility。

## 7. Human Runbook

```text
execution_mode: CODEX_EXECUTOR

DEV_WORKSPACE:
/Users/skychengneo/Agent/CBA_kb_dev/REQ-170-CONSUMER-PROFILE-01

LOCAL_RUNTIME_ROOT:
/Users/skychengneo/Agent/CBA_kb

PRIVATE_INSTANCE_ROOT:
/Users/skychengneo/Agent/CBA_kb_instance

remote:
https://github.com/SkyCjq/cba-kb-engine.git

base_branch:
main

base_sha:
4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538

feature_branch:
feat/REQ-170-CONSUMER-PROFILE-01

local_prepare_command:
NOT_IMPLEMENTED

publish_command:
NOT_IMPLEMENTED

rollback_command:
NOT_IMPLEMENTED
```

### 7.1 Workspace bootstrap contract

Stage 2 开始前：

```bash
export REQ_ID="REQ-170-CONSUMER-PROFILE-01"
export BASE_BRANCH="main"
export BASE_SHA="4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538"
export FEATURE_BRANCH="feat/REQ-170-CONSUMER-PROFILE-01"
export LOCAL_RUNTIME_ROOT="/Users/skychengneo/Agent/CBA_kb"
export DEV_ROOT="/Users/skychengneo/Agent/CBA_kb_dev"
export DEV_WORKSPACE="$DEV_ROOT/$REQ_ID"

git -C "$LOCAL_RUNTIME_ROOT" fetch origin
test "$(git -C "$LOCAL_RUNTIME_ROOT" rev-parse "origin/$BASE_BRANCH")" = "$BASE_SHA" || exit 1
test ! -e "$DEV_WORKSPACE" || exit 1
mkdir -p "$DEV_ROOT"
git -C "$LOCAL_RUNTIME_ROOT" worktree add -b "$FEATURE_BRANCH" "$DEV_WORKSPACE" "$BASE_SHA"
cd "$DEV_WORKSPACE" || exit 1
test "$(git rev-parse HEAD)" = "$BASE_SHA" || exit 1
test "$(git branch --show-current)" = "$FEATURE_BRANCH" || exit 1
test -z "$(git status --porcelain)" || exit 1
```

若 base SHA 漂移，不自动改用 latest；STOP 回 Stage 1 re-freeze。

## 8. Rollback / Compatibility

```text
backward compatibility:
  - v1.6.1 consumer projection v1 unchanged;
  - golden-question v1 unchanged;
  - MASTER schema unchanged;
  - Document rights semantics unchanged;
  - no player_uid introduced.

feature rollback:
  - before any production release: revert feature PR / stop using generated run;
  - target packages are immutable run_id outputs; never overwrite a prior run.

production rollback:
  - N/A / not authorized in this REQ.
```

## 9. Stage Gates

### Stage 1 — current revision

```text
result: FROZEN_SPEC
reason:
  substantive acceptance + allowlist + fixture changes versus r1.
source_contract_probe: N/A
release_infrastructure_mode: OFF
Performance Overlay: OFF
```

本 r2 已获用户明确批准并重新 Freeze：

```text
READY_TO_CODE
```

### Stage 2

```text
Codex:
  allowlisted implementation only
  focused tests only
  production access forbidden
  full regression forbidden
  baseline/allowlist mismatch => STOP
```

### Stage 3

```text
GitHub Actions = full regression owner
CI RED -> minimal same-PR repair
CI GREEN -> Private Instance precheck -> WEB-04
reviewed_PR_head == reviewed_CI_head
```

### Stage 4

```text
Private Instance real precheck
deterministic profile/package checks
target authorization check
CONSUMER_INTAKE_PRECHECK x3
real 30-cell consumer acceptance
Facts ZERO DIFF
production write = 0
```

### Stage 5 — Feature Closeout

consumer acceptance gates PASS 后：

```text
feature_acceptance = PASS
USAGE_GATE_OPEN
project_closeout_state = EXIT_EVIDENCE_PENDING
```

至少 10 条真实非验收 query 完成并形成 next-step decision 后：

```text
project_closeout_state = CLOSED
```

production publication 若需要：

```text
follow-up = REQ-170-PROD-RELEASE-01
```

## 10. Final Definition of Done

- [ ] r2 已由用户重新 Freeze；r1 作为历史审计证据保留。
- [ ] base SHA 未漂移；dev worktree bootstrap 验证通过。
- [ ] facts-only profile deterministic，explicit selector，不制造身份。
- [ ] canonical consumer payload deterministic。
- [ ] 三个 target packages 同一 payload hash，coverage 100%，silent truncation=0。
- [ ] target authorization fail-closed。
- [ ] v1.6.1 consumer projection / golden v1 unchanged。
- [ ] v2 10题在任何真实 run 前冻结并独立验证 oracle。
- [ ] 三目标 Intake Precheck 有 evidence。
- [ ] 30 cells 全部有 PASS/FAIL/NOT_TESTED 记录。
- [ ] 每端 exact 4/4、tracing 2/2、identity 1/1、unknown 1/1、overall >=9/10。
- [ ] 总 PASS >=27/30。
- [ ] MCP trigger 按 root cause 精确执行。
- [ ] canonical Facts/Event/source registry/Document raw = ZERO DIFF。
- [ ] Engine repo 无真实 fixture/answer/result/authorization/credential。
- [ ] GitHub Actions GREEN，reviewed head == CI head。
- [ ] Feature acceptance 后 `USAGE_GATE_OPEN`。
- [ ] >=10 条真实非验收 queries 完成后才 `CLOSED`。
- [ ] 不自动进入 v1.8。
- [ ] production publication 若需要，已拆独立 release Requirement。


## 11. Freeze Record

```text
freeze_approved_by: user
freeze_date: 2026-09-14
freeze_revision: r2-20260914-v170-final-critical-revision
freeze_state: FROZEN_SPEC
stage2_authorized: YES
frozen_base: main@4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538
```
