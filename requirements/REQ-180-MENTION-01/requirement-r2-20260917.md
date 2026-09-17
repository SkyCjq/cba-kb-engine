# CBA-KB Requirement — REQ-180-MENTION-01 Document Player Mention & Rights-aware Linking

> 文档类型：单需求生命周期账本（Requirement Ledger）  
> 状态：FROZEN_SPEC  
> 目标版本：v1.8.0  
> 创建日期：2026-09-17  
> 开发流程依据：`CBA-KB开发流程方案.md` + `REQ_TEMPLATE_单需求开发.md` + vNext CURRENT GUIDELINE

---

## 0. 元数据

```text
requirement_id: REQ-180-MENTION-01
version: v1.8.0
requirement_revision: r2-20260917-v180-mention-baseline-refreeze
status: FROZEN_SPEC
lane: CODE_CONFIG
release_risk: HIGH
requirement_owner: Web AI freeze / Codex implementation
requirement_md_url: https://drive.google.com/file/d/1jq8k0kJ-ghiKd_yszNkNJGOm6OXakeDO/view?usp=drivesdk

baseline_release_id: v1.6.1-1
baseline_code_commit: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
target_base_branch: main
baseline_development_sha: a001addabec3ca3d63ed136701b7c0c72480a216
upstream_identity_merge_sha: a001addabec3ca3d63ed136701b7c0c72480a216
feature_branch: feat/REQ-180-MENTION-01
release_infrastructure_mode: OFF
execution_mode: CODEX_EXECUTOR
production_access: forbidden
publish_allowed: false
restore_allowed: false
project_closeout_state: OPEN

optional_overlays:
  release_performance:
    enabled: false
    proposal: REQ-RELEASE-PERF-01
    activation_reason: null

runtime_evidence_ledger: /Users/skychengneo/Agent/CBA_kb_instance/evidence/REQ-180-MENTION-01/

human_runbook:
  dev_workspace: /Users/skychengneo/Agent/CBA_kb_dev/REQ-180-MENTION-01
  local_runtime_root: /Users/skychengneo/Agent/CBA_kb
  private_instance_root: /Users/skychengneo/Agent/CBA_kb_instance
  local_prepare_command: NOT_IMPLEMENTED
  publish_command: NOT_IMPLEMENTED
  rollback_command: NOT_IMPLEMENTED
```

## 1. Problem

v1.7 已能归档 Document Evidence 并对三类 AI target 做 rights-aware package，但“文档提到了谁”仍没有独立、可审计的关系 grain。若继续把文档与球员的关系塞进 Document 主记录或靠姓名即时猜测，会把“一篇文档可提多人”“主角与被提及者不同”“同名/别名不确定”“rights 与 identity 不是同一个判断”混在一起，造成错误聚合或 silent drop。

本需求建立独立 `document_player_mention(doc_id × player_uid)` Link Layer。它只表达文档与已存在 player identity 的关联，不改变 Document Evidence、MASTER、Facts、Event 或 rights truth。

## 2. In Scope

- 新增 `document_player_mention` 的确定性 schema/validator/serialization。
- 支持一文多人关系。
- 冻结字段：
  - `doc_id`
  - `player_uid`
  - `mention_status = same | not_same | undecided`
  - `mention_role = subject | mentioned`
  - `mention_method = exact_name | alias | manual | external_id`
  - `mention_confidence`
  - `evidence_ref`
- exact-name / alias discovery 只生成 candidate/`undecided`，不得仅凭姓名自动升级为 `same`。
- `undecided` 文档仍可按 document-level metadata 检索，但不得进入 confirmed player-centric 内容；Profile/coverage 必须显式暴露 unresolved mentions。
- 复用 v1.7 per-target authorization，不新增 rights taxonomy。
- 增加最小 CLI，用 Private Instance 文件作为输入/输出，Engine 仓库不得保存真实 mapping。

## 3. Out of Scope

- 不修改 canonical MASTER，不往 MASTER 增加 `player_uid`。
- 不修改 Document Lane 原始 archive、`doc_id` 算法或 rights classification。
- 不建设 Statement/Claim Ledger。
- 不建设 Personal Notes migration。
- 不新增外部 ID registry；`external_id` 作为枚举值可被 schema 接受，但本需求不主动产生此类 link，除非 Private Instance 已提供真实、可审计 external ID evidence。
- 不新增 release framework、MCP、fallback 或新的授权 taxonomy。
- 不执行 production publish/rollback。

## 4. Semantic / Governance Rules

1. `Identity Registry ≠ Mention Resolution`。`player_record_link(record_key × player_uid)` 与 `document_player_mention(doc_id × player_uid)` 是不同 semantic grain。
2. 一篇文档可以同时存在多个 `player_uid`；禁止在 Document 主记录增加单值 `mentioned_player_uid`。
3. `mention_role` 与 `mention_status` 独立：subject/mentioned 不代表身份已确认；`same/not_same/undecided` 才是 resolution status。
4. 姓名命中本身不是 identity evidence。exact-name/alias 自动发现默认 `undecided`；`same` 必须来自人工确认或冻结的、可追溯 deterministic evidence rule。
5. `undecided`、unauthorized、blocked 都必须进入 coverage；不得 silent truncation。
6. Rights authority 继续由 v1.7 的 rights + per-target authorization 决定。`PRIVATE_ACCEPTANCE_ONLY` 不改变 `public_export_allowed`，也不等于公开分发许可。
7. Engine 只保存 schema、validator、CLI、synthetic fixtures；真实 doc↔player mapping、真实 player_uid、授权证据保存在 Private Instance。
8. 本需求不得写 canonical Facts/Event；Document Evidence 也不得因 mention link 被重写。

## 5. Acceptance Criteria

```text
A01 schema grain 精确为 doc_id × player_uid；同一 pair 不得重复。
A02 同一 doc_id 可关联多个不同 player_uid。
A03 mention_status 仅 same/not_same/undecided；mention_role 仅 subject/mentioned。
A04 exact_name/alias discovery 不得直接输出 same；无足够证据时必须 undecided。
A05 undecided 文档仍保留 document-level reachability，但 confirmed player-centric selection 不得包含它。
A06 unresolved_mentions / unauthorized / blocked 均有显式 coverage count/list，不得 silent drop。
A07 rights/public_export_allowed/per-target authorization 语义 ZERO DIFF；PRIVATE_ACCEPTANCE_ONLY 不升级公开分发权。
A08 当前 DocumentLane normalize_text + doc_id contract 保持不变；真实 DOC-1 重新计算仍为 doc_0f98d91df284a9d42afda9da。
A09 MASTER / canonical Facts / Event 输出 ZERO DIFF。
A10 真实 player_uid、真实 mapping、credentials、真实授权体不得进入 Git repository。
A11 相同输入的 mention artifact bytes/hash deterministic。
A12 CLI 缺少 Private Instance 或 required mapping 时 fail closed。
A13 synthetic tests 覆盖 one-doc-many-player、same-name ambiguity、blocked authorization、undecided。
A14 GitHub required check `tests` GREEN 后才可进入 Web merge review。
```

### 5.1 Real corpus / rights canary

```text
DOC-1 source_file_id: 1gOTwWRk2w2SP7fWuXVDEv-vWlsAvGx2m
source_title: 2025-04-02 读特专访 | “大鸟”沈梓捷：回到深圳很亲切，第一件事先吃肠粉.txt
normalized_sha256: 0f98d91df284a9d42afda9da9ca5057b4dd208ca0c12aada0aa67027706265ec
doc_id: doc_0f98d91df284a9d42afda9da
rights: copyrighted
public_export_allowed: false
canonical_projection: METADATA_ONLY
approved private targets: ChatGPT / Gemini Notebook / WorkBuddy
role canary: subject=沈梓捷; article also mentions multiple other named people including 萨林杰、邹雨宸、陈国豪、贺希宁、闵鹿蕾、程帅澎、周鹏

approval_fixture:
  file_id: 1iN6SxHp2FLCk3gsRYRQMh4VVj_ZzFKiisPznvUzQjgM
  evidence: REQ-170_FINAL_HUMAN_FIXTURE_APPROVAL_人工审批表
  Q11: PAIR-1 approved
  Q15-Q17: DOC-1 PRIVATE_ACCEPTANCE_ONLY approved for all 3 targets

DOC-2:
  doc_id: doc_22a31c03d6d48211c392e6ee
  rights: private
  public_export_allowed: false
  canonical_projection: PRIVATE
  Q12/Q18-Q20: relation canary + all 3 private target authorizations approved
```

说明：上述审批证明“现有 rights/per-target authorization 能表达 v1.8 的私人消费语义”，不把审批中的 profile/document pair 当成永久 identity truth。v1.8 Mention resolution 仍必须通过本需求自己的 link status/evidence 规则。

## 6. Deterministic Impact Review

### code

- `src/cba_kb/document_lane.py`：现有 `doc_id`/rights/private archive authority；只读依赖，不改。
- `src/cba_kb/document_sources.py`：Document parse/source metadata；只读依赖，不改。
- `src/cba_kb/consumer_package.py`：现有三端 per-target authorization；只读兼容验证，不改。
- 新增 `src/cba_kb/document_mentions.py`：唯一 mention schema/validator/serialization 实现。
- `src/cba_kb/cli.py`：只增加最小 mention validate/build CLI。

### tests

新增 mention unit + CLI tests；回归 Document Lane、Document Sources、Consumer Package、Engine/Private boundary、secret guards、MASTER history separation。

### workflows

`.github/workflows/offline-tests.yml` 已对 PR 运行完整 offline regression；不改 workflow。

### release integration

本 REQ 不触碰 publish/restore/manifest/release_status/target allocation，因此 `release_infrastructure_mode=OFF`。

### config readers / private-material dependencies

不新增 public Engine 内真实配置。真实 `doc_id × player_uid`、rights approval、human resolution 全部从 Private Instance fixture/evidence 读取；缺失时 fail closed。

## 7. Stage 1 Freeze Contract

### allowed_paths

```text
requirements/REQ-180-MENTION-01/requirement-r2-20260917.md
requirements/REQ-180-MENTION-01/task-brief.md
requirements/REQ-180-MENTION-01/task.yaml
src/cba_kb/document_mentions.py
src/cba_kb/cli.py
tests/test_document_mentions.py
tests/test_cli_document_mentions_v18.py
```

### focused_tests

```text
tests/test_document_mentions.py
tests/test_cli_document_mentions_v18.py
tests/test_document_lane.py
tests/test_document_sources.py
tests/test_consumer_package.py
tests/test_player_identity.py
tests/test_cli_identity_v18.py
tests/test_engine_instance_boundary.py
tests/test_security_guards.py
tests/test_master.py
tests/test_current_history_separation.py
```

### must_not_change

```text
.github/workflows/offline-tests.yml
requirements.lock
src/cba_kb/master.py
src/cba_kb/facts.py
src/cba_kb/event_closure.py
src/cba_kb/player_profile.py
src/cba_kb/player_identity.py
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
config/production.json
config/runtime.json
config/sandbox.json
config/drive_map.yaml
config/taxonomy.yaml
canonical MASTER / Facts / Events
source_registry semantics
Document Lane immutable raw archive and doc_id algorithm
live release_status / manifest / production targets
REQ-180-IDENTITY-01 implementation scope
```

### release_critical_fixtures

```text
live_release_status_v1.6.1-1
current_MASTER_sha256_0aafb6a17748f8d13b83736ac22b5ba3d5947a924e1f987d9ba249bcd6879e7f
REQ-170_FINAL_HUMAN_FIXTURE_APPROVAL_人工审批表
real_DOC1_doc_0f98d91df284a9d42afda9da
real_DOC2_doc_22a31c03d6d48211c392e6ee
synthetic_one_doc_many_players
synthetic_undecided_and_blocked
facts_zero_diff_hash_set
```

### source_contract_probe

```text
external_api/site/live_schema: N/A
private_corpus_contract_probe: PASS
reason: v1.8 Mention consumes already-captured private Document Evidence; no new external acquisition contract is introduced.
proof: real DOC-1 raw bytes re-hash to the existing stable doc_id; REQ-170 human approval proves both copyrighted and private rights cases are expressible across all 3 targets with existing per-target authorization.
```

### release_infrastructure_mode

```text
OFF
```

### Optional Performance Overlay

```text
OFF
```


## 7.1 Stage 1 Baseline Revalidation / Re-freeze — 2026-09-17

```text
old_baseline_development_sha: d2771afbecc33ad9ce91bf34a4adce4c333e5821
new_baseline_development_sha: a001addabec3ca3d63ed136701b7c0c72480a216
upstream_change_1: PR #25 docs-only v1.5.3 release-history reconciliation
upstream_change_2: PR #24 REQ-180-IDENTITY-01 merged
Document Lane blob SHA: 69422006f2d7b0b5f084f58ed928866bb5595df0 (UNCHANGED)
Consumer Package blob SHA: 83110f29ee7f2e99e266f4218a61dcfc017d5343 (UNCHANGED)
Document Sources current blob SHA: c6dad3c04250d7cb920255f5cf2387432e6f5ec1
Identity contract now present: player_uid remains opaque; exact-name/alias is candidate-only; canonical facts are not mutated.
Scope change: NONE
Allowed product-code paths change: NONE
Rights / per-target authorization contract change: NONE
Document Lane / doc_id contract change: NONE
Focused-tests change: ADD tests/test_player_identity.py + tests/test_cli_identity_v18.py because src/cba_kb/cli.py is now a shared Identity/Mention surface.
Must-not-change refinement: src/cba_kb/player_identity.py explicitly protected as upstream authority.
Re-freeze reason: baseline SHA changed and the shared CLI acquired Identity commands; static execution contract must bind the new Code Truth and guard upstream behavior.
```

Revalidation verdict: `READY_TO_CODE` after this r2 re-freeze. Stage 2 still requires a separate explicit user instruction.

## 8. Frozen Human Runbook

```text
DEV_WORKSPACE=/Users/skychengneo/Agent/CBA_kb_dev/REQ-180-MENTION-01
LOCAL_RUNTIME_ROOT=/Users/skychengneo/Agent/CBA_kb
PRIVATE_INSTANCE_ROOT=/Users/skychengneo/Agent/CBA_kb_instance

local_prepare_command=NOT_IMPLEMENTED
publish_command=NOT_IMPLEMENTED
rollback_command=NOT_IMPLEMENTED
```

Command verification evidence: current repo Makefile/CLI has generic `plan/publish/verify/restore` and v1.7 consumer commands, but没有 REQ-180-MENTION-01 的 deterministic prepare/publish/rollback alias；本需求也不允许 production access，因此不得虚构命令。

## 9. Stage 1 Result

```text
Draft Review: PASS
Code Truth Audit: CODE_CHANGE_REQUIRED
baseline revalidated/refrozen: PASS (main@a001addabec3ca3d63ed136701b7c0c72480a216)
private corpus / rights contract probe: PASS
allowed_paths closed: PASS
dev workspace bootstrap parameters complete: PASS
Human Runbook invented commands: NONE
release_infrastructure_mode: OFF
Optional Performance Overlay: OFF
Requirement status: FROZEN_SPEC (r2 baseline re-freeze)
READY_TO_CODE: YES
Stage2 authorized by this document: NO — 当前用户请求仅完成 Stage 1；进入编码仍需明确启动 Stage 2。
```
