# CBA-KB Requirement — REQ-161-CLOSEOUT-01 v1.6.1 Governance, Reliability & Consumer Readiness Closeout

> 文档类型：单需求生命周期账本（Requirement Ledger）

> 状态：FROZEN_SPEC / Stage 1 complete

> 目标版本：v1.6.1

> 创建日期：2026-09-13

> 开发流程依据：CBA-KB开发流程方案.md（Mandatory Core）+ 开发与发布操作手册 + REQ_TEMPLATE_单需求开发.md

> 版本方案：https://app.notion.com/p/3daad272535b8188a349e10dd40aed22?pvs=204

> Notion Ledger 镜像：https://app.notion.com/p/3daad272535b814abad5c1f7f23f08ab?pvs=204

> 原项目演进指导方针：https://app.notion.com/p/CBA-KB-3d8ad272535b8175b059dc72f674c311

> **硬边界：本需求不修改原规划中的 v1.7 需求。**

---

## 0. 元数据

```text

requirement_id: REQ-161-CLOSEOUT-01

version: v1.6.1

status: FROZEN_SPEC

lane: CODE_CONFIG

requirement_owner: CBA-KB maintainer

requirement_md_url: https://docs.google.com/document/d/1IJQGnvlLY-D1K3oGGyyB6tOVK4rxiw3WMp-pZ8Uh7SM/edit?usp=drivesdk

baseline_release_id: v1.6.0-1

baseline_code_commit: 4fab3d0e8eedc594fae982f12a507fef88958f15

target_base_branch: codex/v1.5.2-draft2

baseline_development_sha: 80451930765442512d87db50621319bf94f39c26

target_release_id: v1.6.1-1

feature_branch: feat/REQ-161-CLOSEOUT-01

implementation_commit: TBD

pr_url: TBD

merge_commit: TBD

production_release_id: TBD

production_release_state: NOT_STARTED

project_closeout_state: OPEN

release_infrastructure_mode: ON

execution_mode: CODEX_EXECUTOR

production_authority_contract: this Requirement §5.3 + frozen task.yaml

runtime_evidence_ledger: /Users/skychengneo/Agent/CBA_kb_instance/evidence/REQ-161-CLOSEOUT-01/runtime-ledger/

optional_overlays.release_performance.enabled: true

optional_overlays.release_performance.proposal: REQ-RELEASE-PERF-01

optional_overlays.release_performance.activation_reason: release infrastructure + archive/checkpoint/recovery (H/J)

human_runbook.dev_workspace: /Users/skychengneo/Agent/CBA_kb

human_runbook.local_runtime_root: /Users/skychengneo/Agent/CBA_kb

human_runbook.private_instance_root: /Users/skychengneo/Agent/CBA_kb_instance

```

## 1. Problem

v1.6.0 已完成 Document Lane 与 production release，但当前项目仍存在一组阻塞“可信地进入 v1.7”的收口缺口：Roadmap/Requirement 验收语义漂移、GitHub 默认主线与真实 Code Truth 不一致且缺乏保护、Git task.yaml 仍混入运行态证据、SHA provenance/GO_PACKET/state machine/manifest coverage 尚未完整机器化、当前版本文档与 release report 链漂移、watcher 缺少 7-cycle 运营资格证据、凭证撤销与异地恢复演练无统一可审计证据，以及 Document/Event 资产尚缺 rights-aware consumer projection 与跨消费端 baseline。

本需求把这些事项合并为 v1.6.1 的 **closeout bridge**。它不重新定义 v1.7，不新增业务事实域。

## 2. In Scope

1. **Governance / Code Truth closeout**

- 冻结当前真实 development base；v1.6.1 closeout 时将默认 `main` 收敛到当前 Code Truth，并建立 PR + required CI 的 branch protection/ruleset。

- v1.6 的 software correctness 与 operational qualification 分开记录；不得用固定 corpus 数量冒充算法正确性，也不得让原运营目标静默消失。

- production release state 与 project closeout state 分轴。

2. **Release Infrastructure Contract v2**

- Git-tracked `task.yaml` 只保存 static contract。

- Runtime CI/Precheck/GO/project-reserve/archive/publish/readback/verify/closeout 证据进入 Private Instance immutable evidence ledger。

- typed SHA roles、provenance DAG、release-critical tree attestation、GO_PACKET 自动生成。

- 显式 release state machine 与 manifest 四集合覆盖一致性。

- archive checkpoint / immutable reuse / remote-I/O contract / mutation-boundary 证据化。

3. **Current-state / release evidence closeout**

- 新建稳定 logical key 的 `CURRENT_VERSION_DOC`，以后沿用同一 production identity。

- live `release_status`、README、INDEX、CONTEXT_CARD、CURRENT_VERSION_DOC release/code identity 必须一致。

- 补齐 v1.5.5 与 v1.6.0 closeout/release evidence；历史文档继续历史化，不伪装 current。

4. **Operational risk closeout**

- credential rotation/revocation attestation；验证旧凭证不可再用，且证据不含 secret。

- 与 Google Drive production 不同 failure domain 的 versioned backup + 真实 restore drill attestation。

- watcher r5 contract 不变；完成 7 个连续 planned cycles、无 manual repair 的 operational qualification ledger。

5. **Consumer Readiness（不等于 v1.7）**

- 冻结 10 道 golden questions（4 deterministic exact / 3 provenance / 2 cross-source synthesis / 1 unknown-gap handling）。

- 生成 rights-aware Document Projection：public/copyrighted 只投影允许的 metadata；private 默认不跨服务投影正文或敏感标题；unknown 默认 blocked。

- 生成 `event_coverage`；显式暴露 `OCR_REQUIRED / REVIEW_REQUIRED / body unavailable`，但本需求不实现 OCR。

- 冻结 consumer baseline result schema：PASS / FAIL / NOT_TESTED + failure_layer。

- 对 ChatGPT / Gemini / WorkBuddy 做 baseline；本版本只建立基线，不改变 v1.7 原验收阈值。

- 建立 usage/failure-layer telemetry schema，作为 v1.7 之后 Usage Gate 的输入。

## 3. Out of Scope

- 修改原 Notion Project Evolution Roadmap 的 v1.7 需求、Exit Criteria 或 MCP Trigger。

- Player Profile / Timeline 产品实现。

- MCP、Identity Registry、Stats、Claim Ledger、Notes Lane。

- 新 ingestion source、新 scraping topology、新官方 API parser。

- 修改 Document Lane ingestion/dedup/rights 业务语义。

- OCR 正文提取；这里只暴露 OCR_REQUIRED 状态。

- 重新梳理或改写 canonical Facts/Event/registration 数据。

- 为满足门禁而人工“凑够”特定 corpus 数量。

- production hot patch 或绕过 Web GO。

## 4. Semantic / Governance Rules

- GitHub Engine repository 是唯一 Engine Code Truth；Private Instance 不进入 Engine repo；Drive `50_scripts` 只读镜像。

- 本需求不改 v1.7 Roadmap 内容。

- canonical Facts/Event/Document/source-watcher 业务语义全部零变化。

- 未知事实/日期保持未知；不得为了 golden questions 或 baseline 猜值。

- consumer projection 不是新的 Fact Truth；它必须携带 release/as_of/source/provenance/rights scope。

- private material 默认不投影到跨服务 consumer；unknown rights fail closed。

- watcher 7-cycle 是 operational qualification，不把 observed change 自动提升为 business event。

- production release `COMPLETE` 不自动等于 Requirement `CLOSED`。

- release infrastructure mode ON 时：Production Authority Contract > Requirement/task > release state machine > Code Truth > implementation。

- 禁止无角色 SHA；禁止为了简单强制 `release_execution_sha == release_merge_sha`。

- Runtime evidence 不得回写 Git task.yaml 制造 SHA 自引用。

## 5. Acceptance Criteria

```text

A01 live baseline = v1.6.0-1 / COMPLETE / production SHA 4fab3d0e8eedc594fae982f12a507fef88958f15; 197 artifacts; previous_snapshot 197.

A02 development base = codex/v1.5.2-draft2 @ 80451930765442512d87db50621319bf94f39c26; production SHA is its ancestor; baseline drift => STOP + re-freeze.

A03 原 Roadmap v1.7 requirement 内容由本需求保持不变；任何 v1.7 变更必须另立决策/Requirement。

A04 default main 在 closeout 前收敛到正式 Code Truth，且启用 PR + required CI branch protection/ruleset；禁止未审 direct push。

A05 Git task.yaml 只含 static contract；所有 runtime evidence 写 Private Instance immutable ledger，ledger entry content-addressed/append-only。

A06 typed SHA provenance DAG 完整；reviewed_release_pr_head_sha == reviewed_ci_head_sha；production_execution_sha == release_execution_sha；release-critical tree attestation PASS。

A07 release transaction 明确并测试 PROJECTED→RESERVED→REPROJECTED→FROZEN_PLAN→PREPARED→ARCHIVING→ARCHIVE_COMPLETE→PUBLISHING→VERIFYING→COMPLETE。

A08 reservation 后旧 projection 必须失效并 re-project；planned/resolved/manifest/publish target sets 完全相等，missing/unexpected/duplicate/unresolved 均为 0。

A09 archive checkpoint/resume 可幂等恢复；verified immutable archive 可复用；write error 不盲重试，必须 journal/readback reconciliation。

A10 CURRENT_VERSION_DOC 使用稳定 logical key；发布后 release_status / README / INDEX / CONTEXT_CARD / CURRENT_VERSION_DOC 的 release_id + code identity 一致。

A11 v1.5.5 与 v1.6.0 closeout/release evidence 进入当前可审计链；不得把历史版本文档当 current。

A12 credential revocation attestation 完整：旧凭证已撤销或明确不存在；主动验证旧凭证失败；证据不含 secret/token。

A13 off-site versioned backup 与 production Drive 不同 failure domain；执行一次空目录 restore drill；核心 bundle hash/readback PASS。

A14 watcher r5 source contract 不变；7 个连续 planned cycles 完成且 manual_repair_required=false、canonical_publish_allowed=false；每周期证据进入 immutable ledger。

A15 golden questions 固定为 10 题且 schema/versioned；不得在看到 consumer 结果后静默改 expected answer。

A16 rights-aware documents projection 确定性；private body/敏感 title 默认不跨服务输出，unknown blocked；每条有 doc_id/availability/rights/body_status/source_ref。

A17 event_coverage 确定性输出 season/team covered/unresolved/critical_gap/source_status/as_of。

A18 OCR_REQUIRED / REVIEW_REQUIRED / body_unavailable 在 projection 中显式可见；本需求不要求 OCR 成功。

A19 ChatGPT / Gemini / WorkBuddy baseline 按 PASS/FAIL/NOT_TESTED 记录；FAIL 分类 projection/retrieval/aggregation/counting/provenance/source_gap/identity/consumer_tool。

A20 usage/failure-layer schema 冻结，后续 Usage Gate 可直接消费；不以“记录条数”替代 failure-layer coverage。

A21 current projection 与 release report generation 重跑相同输入产生相同 bytes/hash（时间字段需冻结/传入）。

A22 canonical business facts / published fact products / source_registry semantics / Document Lane raw archive = ZERO DIFF。

A23 Private Instance / credential / real private fixture 泄漏到 Engine repo 或 public projection = 0。

A24 GitHub Actions 是 full regression 唯一正常 owner；Codex 只跑 focused tests；CI RED 不进入 WEB-04。

A25 Performance Overlay ON；必须记录 wall-clock/retry/reconnect/recovery/archive reuse/refreeze/WEB05-not-ready 等指标，缺失写 NOT_AVAILABLE。

A26 production release 可在长期证据未齐时 COMPLETE，但 project_closeout_state 必须为 EXIT_EVIDENCE_PENDING；只有 A12/A13/A14/A19 等长期/人工证据齐全后才 CLOSED。

```

### 5.1 Scope-specific numeric / hash gates

```text

Phase 0 live baseline:

release_status_id: 1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV

release_status_sha256: 8930e353b90e3c5c05b8f9026844fcd0ee9a87ed34c689de9ccd410d6c2f9d7e

current_release_id: v1.6.0-1

state: COMPLETE

production_execution_sha: 4fab3d0e8eedc594fae982f12a507fef88958f15

active artifacts: 197

previous_snapshot: 197

Development baseline:

branch: codex/v1.5.2-draft2

HEAD: 80451930765442512d87db50621319bf94f39c26

production SHA relation: ancestor / ahead_by development = 2

default main: c4bebecd600711e0f00a56e968fec0b5e4349578 (stale relative to development)

main protected: false

repository rulesets: none

Current manifest:

id: 1OsaC9ZQRC8pljd6VRMtub2YOVIv1QYKl

sha256: 42ebeb31d62fbe14a3cb56660edc1c480191beb7b65115d0499b8357a667f576

Tables/artifacts that MUST NOT change:

canonical Facts/Event/registration products

source_registry semantics

Document Lane immutable raw/archive

watcher source contract r5 parser semantics

Expected business delta:

0

Candidate determinism:

generated projections/control docs identical for identical frozen inputs.

```

### 5.2 Release-critical fixtures

```text

1. live_release_status_v1.6.0-1 — id 1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV — required YES

2. live_manifest_current — id 1OsaC9ZQRC8pljd6VRMtub2YOVIv1QYKl — required YES

3. production_private_policy_snapshot — Private Instance production.json — required YES

4. current_control_documents — README/INDEX/CONTEXT/current-version identities — required YES

5. facts_zero_diff_hash_set — required YES

6. document_lane_private_archive_manifest — required YES

7. consumer_projection_rights_guard_set — required YES

8. event_coverage_source_set — required YES

9. watcher_r5_source_contract — required YES

10. watcher_7_cycle_operational_ledger — required for project CLOSED; may be pending at production COMPLETE

11. credential_revocation_attestation — required for project CLOSED

12. offsite_backup_restore_attestation — required for project CLOSED

13. repository_policy_snapshot — required for project CLOSED

```

### 5.3 Release Infrastructure Contract

```text

release_infrastructure_mode: ON

activation_reason:

- release tooling / projection / target allocation / manifest

- archive/checkpoint/recovery

- current control-document preservation

- runtime evidence / provenance

Production Authority baseline:

current_release_id: v1.6.0-1

state: COMPLETE

production_execution_sha: 4fab3d0e8eedc594fae982f12a507fef88958f15

active_targets: 197

release_status_id: 1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV

manifest_id: 1OsaC9ZQRC8pljd6VRMtub2YOVIv1QYKl

README_id: 1AqCZwlSKlZdfR9F0ftG_Dc36gffmz_NLs8v1FS99uu0

INDEX_id: 1VqnFtMRSlOV9K7eVCAJKQ5HngWMl60MibbopNclxN9c

CONTEXT_CARD_id: 16tE6ALa_MS6WAissYK99Eex6lZudjYnq

current historical version-doc id observed: 1ZebJR9YPKX37cMDdz0xznHDa45at_q65

typed_sha_roles:

baseline_development_sha: 80451930765442512d87db50621319bf94f39c26

product_candidate_sha: runtime

release_execution_sha: runtime

reviewed_release_pr_head_sha: runtime

reviewed_ci_head_sha: runtime

release_merge_sha: runtime audit node

production_execution_sha: runtime (= release_execution_sha)

release_critical_tree_attestation: runtime

release_state_machine:

PROJECTED → RESERVED → REPROJECTED → FROZEN_PLAN → PREPARED

→ ARCHIVING → ARCHIVE_COMPLETE → PUBLISHING → VERIFYING → COMPLETE

canonical_mutation_boundary:

before release_status enters PUBLISHING and before first canonical target write.

all authority/manifest/archive/provenance failures before this boundary => pre-mutation abort.

manifest_coverage_contract:

planned_targets == resolved_targets == manifest_targets == publish_targets

missing=0 unexpected=0 duplicate=0 unresolved=0

archive_namespace:

execution-scoped, keyed by release_execution_sha + release_id

archive_checkpoint_contract:

append-only progress; verified immutable objects reusable; resume idempotent.

remote_io_contract:

retryable: 408/429/5xx, timeout, reset/broken pipe/incomplete read

non_retryable: auth/permission/schema/authority/hash/MIME/semantic drift

read retries: bounded exponential backoff + fresh connection

write retries: no blind retry; journal/readback reconcile first

timeout/backoff: explicit and recorded

control_document_preservation:

release_status / README / INDEX / CONTEXT_CARD / CURRENT_VERSION_DOC / manifest all included in final plan and readback.

```

Machine gates:

```text

reviewed_release_pr_head_sha == reviewed_ci_head_sha

production_execution_sha == release_execution_sha

release-critical tree at release_execution_sha == attested tree in reviewed release PR head

planned_targets == resolved_targets == manifest_targets == publish_targets

missing = unexpected = duplicate = unresolved = 0

```

Provenance DAG:

```text

product_candidate_sha

→ release_execution_sha

→ reviewed_release_pr_head_sha

→ release_merge_sha

→ production_execution_sha (= release_execution_sha)

```

## 6. Rollback / Compatibility

```text

backward compatibility:

current Facts/Event/Document/source contracts remain readable and byte-stable.

current consumers may continue using previous current controls until v1.6.1 COMPLETE.

rollback expectation:

use frozen before snapshot + restore command; new targets return to staging/archive ownership or are excluded from current release after rollback.

rollback must restore previous release_id/code_commit/artifact set and readback hashes.

legacy migration/reconciliation:

no cleanup of legacy version-named scripts in this requirement.

register as future clean-room/bootstrap debt; do not expand current allowlist.

```

---

# PART B — 5 阶段执行日志

## 7. Stage 1 — Prepare / Audit / Freeze

### 7.1 Requirement Draft Review — WEB-01

```text

result: PASS

review date: 2026-09-13

problem/scope complete: YES

semantic conflicts: NONE after making v1.7 explicit must-not-change

acceptance gaps: CLOSED by A01-A26

```

### 7.2 Code Truth Baseline / Impact Audit — WEB-03

```text

result: CODE_CHANGE_REQUIRED

repo accessible: YES

repo: https://github.com/SkyCjq/cba-kb-engine

production code SHA: 4fab3d0e8eedc594fae982f12a507fef88958f15

base branch: codex/v1.5.2-draft2

HEAD SHA: 80451930765442512d87db50621319bf94f39c26

default main: c4bebecd600711e0f00a56e968fec0b5e4349578

default main protection: OFF

rulesets: none

impact:

release core: src/cba_kb/release.py

production orchestration: scripts/prepare_production.py

current-state controls: src/cba_kb/current_state.py

CLI/Make integration: src/cba_kb/cli.py, Makefile

new modules: consumer_projection.py, evidence_ledger.py, operational_qualification.py

tests: release/current-state/consumer/OQ/security/boundary

private dependencies: production.json, runtime.json, document archive, watcher ledgers, evidence root

minimal change set: frozen below

lane: CODE_CONFIG

```

### 7.3 SOURCE_CONTRACT_PROBE

```text

result: PASS_RECENT_REAL_PROBE_REUSED

contract revision: r5-20260912-detail-discovery

contract change in v1.6.1: NO

real evidence:

GitHub PR #13 Local Precheck

official_request_count: 64

2024-2025 child publications / teams: 20 / 20 PASS

2025-2026: 21 / 20 PASS; supplemental publication PASS; Jilin alias PASS

2026-2027: 20 / 20 PASS

schema/rate-limit/compatibility/zero-business-delta/private-leakage: PASS

2026-09-13 official CBA site still exposes the 2026-2027 registration entry.

freeze rule:

v1.6.1 MUST NOT edit adapters/cba_registration.py or source_watcher.py.

7-cycle OQ uses this frozen r5 contract; any live schema/topology/auth/rate-limit drift => STOP + new probe/re-freeze.

```

### 7.4 Frozen Spec / Contract — WEB-02

```text

status: FROZEN_SPEC

frozen baseline release: v1.6.0-1

frozen baseline production code: 4fab3d0e8eedc594fae982f12a507fef88958f15

target base branch: codex/v1.5.2-draft2

baseline development SHA: 80451930765442512d87db50621319bf94f39c26

Requirement revision: r1.1-20260913-v161-stage1-freeze-bookkeeping-fix

release_infrastructure_mode: ON

Production Authority Contract: §5.3

typed SHA roles: PASS

release transaction/recovery contract: PASS

remote I/O contract: PASS

canonical mutation boundary contract: PASS

Performance Overlay: ON (H/J)

```

Allowed paths:

```text

requirements/REQ-161-CLOSEOUT-01/requirement-r1-20260913.md

requirements/REQ-161-CLOSEOUT-01/task-brief.md

requirements/REQ-161-CLOSEOUT-01/task.yaml

Makefile

config/consumer_golden_questions_v1.yaml

src/cba_kb/cli.py

src/cba_kb/current_state.py

src/cba_kb/release.py

src/cba_kb/consumer_projection.py

src/cba_kb/evidence_ledger.py

src/cba_kb/operational_qualification.py

scripts/prepare_production.py

tests/test_consumer_projection.py

tests/test_release_evidence.py

tests/test_operational_qualification.py

tests/test_current_state_consistency.py

tests/test_release.py

tests/test_release_orchestration.py

tests/test_source_watcher.py

tests/test_cli_instance_config.py

tests/test_engine_instance_boundary.py

tests/test_security_guards.py

```

Focused tests:

```text

tests/test_consumer_projection.py

tests/test_release_evidence.py

tests/test_operational_qualification.py

tests/test_current_state_consistency.py

tests/test_release.py

tests/test_release_orchestration.py

tests/test_source_watcher.py

tests/test_cli_instance_config.py

tests/test_engine_instance_boundary.py

tests/test_security_guards.py

```

Must not change:

```text

v1.7 requirements in the Project Evolution Roadmap

src/cba_kb/facts.py

src/cba_kb/build_facts.py

src/cba_kb/registration_domain.py

src/cba_kb/event_closure.py

src/cba_kb/document_lane.py

src/cba_kb/source_watcher.py

src/cba_kb/adapters/cba_registration.py

src/cba_kb/aliases.py

src/cba_kb/drive.py

src/cba_kb/transport.py

config/canonical_products.yaml

config/club_aliases.yaml

config/taxonomy.yaml

.github/workflows/offline-tests.yml

requirements.lock

canonical business-fact bytes and published fact products

source_registry business/source semantics

Document Lane immutable raw/archive semantics

Snapshot/Event/Transaction/Stats/Claim semantic boundaries

unknown-date/unknown-fact fail-closed semantics

```

Release-critical fixtures:

```text

live_release_status_v1.6.0-1

live_manifest_current

production_private_policy_snapshot

current_control_documents

facts_zero_diff_hash_set

document_lane_private_archive_manifest

consumer_projection_rights_guard_set

event_coverage_source_set

watcher_r5_source_contract

watcher_7_cycle_operational_ledger

credential_revocation_attestation

offsite_backup_restore_attestation

repository_policy_snapshot

```

Static/runtime boundary:

```text

requirements/REQ-161-CLOSEOUT-01/task.yaml = STATIC CONTRACT ONLY

Private Instance /evidence/REQ-161-CLOSEOUT-01/runtime-ledger = RUNTIME EVIDENCE AUTHORITY

```

### 7.5 Frozen Execution Runbook

```text

execution_mode: CODEX_EXECUTOR

DEV_WORKSPACE: /Users/skychengneo/Agent/CBA_kb

LOCAL_RUNTIME_ROOT: /Users/skychengneo/Agent/CBA_kb

PRIVATE_INSTANCE_ROOT: /Users/skychengneo/Agent/CBA_kb_instance

local_prepare_command:

NOT_IMPLEMENTED_FOR_V1.6.1_AT_BASELINE

Evidence: scripts/prepare_production.py exists with project/reserve-staging/freeze,

but current RELEASE_SPECS only admits v1.5.5-1 and v1.6.0-1.

Stage 2 must add v1.6.1-1 support inside allowed paths and focused tests.

publish_command:

make publish INSTANCE_ROOT="\$PRIVATE_INSTANCE_ROOT" ARGS='--release "\$RUN_DIR/outbox" --single-writer'

verify_command:

make verify INSTANCE_ROOT="\$PRIVATE_INSTANCE_ROOT" ARGS='--release "\$RUN_DIR/outbox"'

rollback_command:

make restore INSTANCE_ROOT="\$PRIVATE_INSTANCE_ROOT" ARGS='--release "\$RUN_DIR/outbox" --single-writer'

archive/checkpoint:

current release.py has PREPARED→ARCHIVING→PUBLISHING→COMPLETE journal;

v1.6.1 must implement explicit ARCHIVE_COMPLETE/VERIFYING contract before Stage 4.

```

No future alias is represented as already executable.

### 7.6 Human Approval / REQ-START

```text

READY_TO_CODE: YES

human_frozen_spec_approval: this Stage-1 freeze requested by project owner; Stage 2 NOT executed in this turn

feature_branch: feat/REQ-161-CLOSEOUT-01

task_contract_commit: NOT_CREATED — Stage 1 performed read-only GitHub audit; frozen artifacts are prepared for deterministic sync

base_sha_verified: YES (80451930765442512d87db50621319bf94f39c26)

```

## 8. Stage 2 — Codex Implementation / Focused Tests / GitHub Development Sync

```text

status: NOT_STARTED

normal Codex calls target: 1

full/offline regression: FORBIDDEN for Agent

production access: FORBIDDEN

baseline/allowlist mismatch: STOP + REFREEZE_REQUIRED

```

## 9. Stage 3 — GitHub Actions / Web Review / Merge

```text

status: NOT_STARTED

full regression owner: GitHub Actions

CI RED => Codex minimal repair => same PR => Actions

CI GREEN + required Local Precheck PASS => WEB-04

reviewed_PR_head must equal reviewed_CI_head

```

## 10. Stage 4 — Local Trusted Runtime / Prepare Release

```text

status: NOT_STARTED

release-critical real fixtures: REQUIRED

reservation => mandatory re-project

runtime evidence ledger: REQUIRED

GO_PACKET completeness: REQUIRED

```

## 11. Stage 5 — GO / Publish / Closeout

```text

status: NOT_STARTED

WEB-05 GO required before publish.

production COMPLETE may coexist with project_closeout_state=EXIT_EVIDENCE_PENDING.

watcher 7-cycle / credential revoke / off-site restore / consumer baseline evidence must be complete before CLOSED.

```

## 12. Observability / Process Friction Ledger

Required fields include:

```text

normal_codex_calls

CI_repair_codex_calls

local_exception_codex_calls

CI_wall_clock_seconds

local_prepare_wall_clock_seconds

publish_wall_clock_seconds

context_expansion_count

refreeze_count

development_sync_count

web_merge_review_cycles

WEB05_not_ready_attempts

contract_semantic_defect_count

production_resilience_defect_count

recovery_cycle_count

pre_mutation_abort_count

archive_reuse_count

transport_reconnect_count

partial_write_failure_count

rollback_count

consumer_baseline_failure_layer_counts

watcher_qualified_cycles

```

Unavailable provider usage must be `NOT_AVAILABLE`, never guessed.

## 13. Final Definition of Done

- \[x\] Requirement/scope/acceptance/rollback frozen.

- \[x\] live baseline + Code Truth audit completed before Freeze.

- \[x\] source contract evidence reviewed; v1.6.1 source parser frozen.

- \[x\] exact allowed_paths/focused_tests/must_not_change/fixtures frozen.

- \[x\] Human Runbook contains only verified commands or explicit NOT_IMPLEMENTED.

- \[x\] Release Infrastructure Contract / typed SHA / state machine / manifest coverage frozen.

- \[x\] Performance Overlay ON with trigger evidence.

- \[ \] Stage 2 implementation committed/pushed to feature branch/PR.

- \[ \] required Actions GREEN.

- \[ \] Local Precheck PASS.

- \[ \] WEB-04 MERGE_READY.

- \[ \] Stage 4 GO_PACKET PASS.

- \[ \] WEB-05 GO.

- \[ \] v1.6.1 production release COMPLETE/readback/rollback evidence complete.

- \[ \] repository main/protection closeout complete.

- \[ \] credential revocation attestation complete.

- \[ \] off-site restore drill complete.

- \[ \] watcher 7-cycle qualification complete.

- \[ \] consumer baseline complete or explicit NOT_TESTED per consumer.

- \[ \] project_closeout_state = CLOSED only after all required long-horizon evidence is present.

---

## Stage 1 Final Output A–I

```text

A. production baseline

v1.6.0-1 / COMPLETE / code 4fab3d0e8eedc594fae982f12a507fef88958f15

artifacts 197 / previous_snapshot 197

release_status SHA256 8930e353b90e3c5c05b8f9026844fcd0ee9a87ed34c689de9ccd410d6c2f9d7e

B. development base

codex/v1.5.2-draft2 @ 80451930765442512d87db50621319bf94f39c26

default main @ c4bebecd600711e0f00a56e968fec0b5e4349578; stale relative to current dev line; unprotected; no ruleset

C. lane

CODE_CONFIG

D. frozen scope contract

allowed_paths / focused_tests / must_not_change / release_critical_fixtures = §7.4

E. source contract probe

PASS_RECENT_REAL_PROBE_REUSED; r5 unchanged; any live drift => re-probe/refreeze

F. release infrastructure

ON; Production Authority + typed SHA + state machine + manifest + recovery contracts frozen

Performance Overlay ON

G. Human Runbook

dev/runtime/private roots frozen

local prepare = NOT_IMPLEMENTED_FOR_V1.6.1_AT_BASELINE

publish/verify/restore commands verified present in current repo

H. Requirement revision

r1.1-20260913-v161-stage1-freeze-bookkeeping-fix

canonical authority: Google Doc revision + r1.1 bookkeeping amendment; r1 content snapshot hash retained only as prior audit evidence

I. decision

READY_TO_CODE

Stage 2 is not executed or authorized by this Stage-1-only task.

```
