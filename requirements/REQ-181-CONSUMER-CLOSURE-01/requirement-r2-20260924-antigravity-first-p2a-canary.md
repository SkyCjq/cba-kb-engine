# REQ-181-CONSUMER-CLOSURE-01 — FIRST_FREEZE r2

```text
requirement_id: REQ-181-CONSUMER-CLOSURE-01
revision: r2-20260924-antigravity-first-p2a-canary
status: FROZEN_SPEC
freeze_count: 1
re_freeze: NO
target_release_id: v1.8.1-1
fresh_base_sha: 17445e27ee98d7b58beb8c0e79ba97e7e3c89e08
repository: SkyCjq/cba-kb-engine
repository_visibility: public
policy_bundle_sha256: c628cc45e855090464eaaf1f4f8dae15636ddbc9451096654095759a3c2050b5
identity_semantic_delta: ZERO
canonical_executor: LOCAL_RUNTIME
execution_provider: GEMINI_ANTIGRAVITY
return_gate: CODEX_SUPERVISORY_REVIEW
production_mutation_authority: false
merge_authorized: false
publish_authorized: false
restore_authorized: false
```

## 1. Frozen product goals

Exactly four goals are in scope:

1. Current-State Document Closure.
2. Current-State Machine Consistency.
3. Identity Consumer Projection / Consumption Closure.
4. Focused Consumer Acceptance.

This Requirement MUST NOT expand into v1.9, P2B, or P3A.

## 2. Fresh baseline and predecessor facts

Freeze baseline is GitHub `main@17445e27ee98d7b58beb8c0e79ba97e7e3c89e08`. Repository visibility is intentionally `public`; it is not a security incident and MUST NOT be changed to private by this Requirement.

REQ-181 has no pre-existing conflicting branch and no open PR at Freeze. `REQ-WORKFLOW-P2A-01` is terminal CLOSED with P2A automation version `0.1.0`; its executor enum already recognizes `LOCAL_RUNTIME`. Production predecessor evidence remains `COMPLETE / v1.8.0-1`. Production is read-only for this Requirement.

Ordinary unrelated advancement of `main` is not by itself a Requirement defect. Before implementation, a fresh baseline change requires re-freeze only when it changes this Requirement's scope or semantics.

## 3. Frozen identity semantic boundary

All of the following are exact invariants:

```text
identity_semantic_delta: ZERO
new_identity_decisions: 0
new_same_decisions: 0
new_not_same_decisions: 0
machine_final_uid_decisions: 0

player_uid_mutation: 0
master_mutation: 0
identity_registry_mutation: 0
identity_authority_change: 0
```

Forbidden identity work:

- no new Identity algorithm;
- no new Web Identity research;
- no fuzzy merge;
- no same-name auto merge;
- no UID regeneration;
- no MASTER mutation;
- no Private Identity Registry mutation;
- no canonical business facts mutation.

Consumer closure MUST consume existing identity decisions without creating new relation truth.

## 4. Public GitHub wording closure

Managed current-state wording MUST no longer state `Code truth: private GitHub`.

Required concise semantics:

```text
Code truth: GitHub repository.
Repository visibility: public.
```

The closure MUST also preserve these separations:

```text
Public GitHub code
!= Public Production data
!= Public Private Identity Registry
```

Repository visibility MUST NOT relax Drive rights, Identity evidence authorization, private-source handling, or secrets policy. Secret guard remains a mandatory Gate.

## 5. Reuse existing implementation; no parallel subsystem

Implementation MUST preferentially reuse:

- `src/cba_kb/current_state.py`
- `src/cba_kb/consumer_projection.py`
- `src/cba_kb/consumer_package.py`
- `src/cba_kb/consumer_acceptance.py`

Existing contracts already provide player-profile-v2 / `player_uid` selection, target-specific authorization, rights-aware projection, package manifest, canonical consumer payload SHA, consumer navigation contract, and consumer acceptance v2.

Default prohibition:

- do not add `src/cba_kb/identity_consumer.py`;
- do not create a second consumer-manifest truth;
- do not create a second identity-acceptance truth;
- do not create a second current-state scanner.

If the existing contracts cannot express this frozen Requirement, STOP with:

```text
REQUIREMENT_SEMANTIC_GAP
WEB_RE_FREEZE_REQUIRED
```

## 6. Current-state validator closure

`current_state.py` is the only machine-consistency path.

The existing v1.6.1 migration special case MUST be closed so that:

```text
migration completed
-> CURRENT_VERSION_DOC remains the modern current surface
-> future releases continue using CURRENT_VERSION_DOC
```

Validation MUST NOT revert future releases to the legacy version-document surface merely because `release_id != v1.6.1-1`.

At minimum the unique validator path must verify common identity across:

- release_status
- README
- INDEX
- CONTEXT_CARD
- CURRENT_VERSION_DOC

Shared machine identity includes at least:

- `release_id`
- `code_commit`
- registry/current-state identity

The legacy migration helper may remain bounded to the historical migration event; the modern post-migration validation surface is not release-specific.

Technical Manual rule: fresh-read shows it is not currently part of `current_state.py`'s machine-managed surface. Do not invent a parallel validator for it. If the existing managed-generation chain already treats it as a managed current surface, include it in the same closure; otherwise only remove stale current-state wording through the existing document-generation/preparation path.

## 7. Consumer Identity consumption closure

Existing flow remains:

```text
player_identity
-> player_profile v2
-> consumer_projection
-> consumer_package
```

Frozen identity-navigation policy:

```text
identity_selector = player_uid
record_key_is_person_identity = false
automatic_merge = false
same_person_assertion_without_independent_evidence = false
```

Consumer-safe output MUST machine-distinguish these semantic states without manufacturing relations:

```text
SAME
NOT_SAME
UNDECIDED
UNAVAILABLE / NOT_MATERIALIZED
```

Existing internal `UNLINKED`/absence semantics may be projected to the frozen consumer-safe unavailable/not-materialized state, but MUST NOT be upgraded into SAME, NOT_SAME, or UNDECIDED.

## 8. Machine-count contract

Do not hard-code `246` as a v1.8.1 expected count. `246` is predecessor evidence only.

v1.8.1 must deterministically calculate from actual frozen inputs:

- production artifact count;
- player count;
- record-link count;
- same count;
- not_same count;
- undecided count;
- unavailable/not-materialized count.

Every declared count MUST be machine-derived and satisfy:

```text
declared == actual
```

LLM/manual counting of JSON or manifests is not acceptance evidence.

## 9. Consumer acceptance

Reuse `src/cba_kb/consumer_acceptance.py`.

Frozen existing contract:

```text
SCHEMA_VERSION = 2
GOLDEN_VERSION = v2
Consumers:
- ChatGPT
- Gemini Notebook
- WorkBuddy
```

Do not modify frozen golden questions in this Requirement.

If the current golden contract cannot test the frozen closure:

```text
STOP
REQUIREMENT_SEMANTIC_GAP
WEB_RE_FREEZE_REQUIRED
```

## 10. Gen1 implementation allowlist

Exact allowed paths:

```text
requirements/REQ-181-CONSUMER-CLOSURE-01/**
src/cba_kb/current_state.py
src/cba_kb/consumer_projection.py
src/cba_kb/consumer_package.py
src/cba_kb/consumer_acceptance.py
scripts/generate_context_card.py
scripts/prepare_production.py
tests/test_current_state_consistency.py
tests/test_consumer_projection.py
tests/test_consumer_package.py
tests/test_consumer_acceptance.py
tests/test_v1_8_1_consumer_closure.py
```

No silent allowlist expansion.

## 11. Gen1 must-not-change

```text
automation/**
tests/automation/**
Makefile P2A workflow targets

src/cba_kb/player_identity.py
src/cba_kb/identity_coverage.py
src/cba_kb/identity_web_evidence.py
src/cba_kb/identity_consumer.py

config/canonical_products.yaml
config/production.json

pyproject.toml
requirements.lock
.github/workflows/**

MASTER
six-table products
canonical facts
Production Identity Registry
Production Drive artifacts
Production release_status
```

Production mutation authority is false. Merge, publish, and restore are not authorized.

## 12. Focused acceptance and full regression

Focused implementation tests:

```text
.venv/bin/python -m pytest -q \
  tests/test_current_state_consistency.py \
  tests/test_consumer_projection.py \
  tests/test_consumer_package.py \
  tests/test_consumer_acceptance.py \
  tests/test_v1_8_1_consumer_closure.py

.venv/bin/python scripts/check_secrets.py --tracked
```

The focused closure tests MUST machine-prove:

- modern `CURRENT_VERSION_DOC` remains current for v1.8.1 and future non-v1.6.1 release IDs;
- all managed current surfaces share release/code/registry identity;
- public GitHub wording closure;
- existing profile-v2 identity boundary remains unchanged;
- SAME / NOT_SAME / UNDECIDED / UNAVAILABLE-or-NOT_MATERIALIZED are distinguishable;
- declared machine counts equal actual machine counts;
- identity decision/registry/MASTER zero-diff;
- no Production mutation path is exercised.

Full regression for Gen1:

```text
CBA_KB_SOURCE_POLICY_JSON='{"excluded_drive_ids":[]}' \
  .venv/bin/python -m pytest -q -rs \
  --deselect tests/test_prepare_v1_5_2.py::test_v1_5_2_registry_proposal_is_complete
```

A skipped/deselected test MUST be explicitly reported and classified; it is not silently counted as PASS.

## 13. Antigravity execution topology

Do not modify the P2A `EXECUTORS` enum.

Canonical P2A executor:

```text
next_executor: LOCAL_RUNTIME
```

Requirement-level provider authority:

```text
execution_provider: GEMINI_ANTIGRAVITY
```

Meaning:

- P2A verifier recognizes `LOCAL_RUNTIME`;
- the authorized AI implementation provider for this generation is Gemini Antigravity;
- no silent provider substitution to Codex.

Gen1:

```text
task_type: LOCAL_RUNTIME_ANTIGRAVITY_IMPLEMENT_TEST_PR
stage: V181_ANTIGRAVITY_FIRST_PRODUCT_CANARY
```

## 14. Generation 1 authority

Gemini Antigravity is authorized to:

- run task-verify;
- fresh-read baseline;
- require a clean worktree;
- create/use the frozen feature branch;
- implement only allowlisted changes;
- run focused tests;
- run identity zero-diff tests;
- run secret guard;
- run full regression;
- run scope audit;
- commit;
- push;
- create exactly one PR;
- produce P2A-compatible result evidence.

Then STOP.

Gemini Antigravity is forbidden to:

- merge;
- mutate Production;
- publish;
- restore;
- declare MERGE_READY;
- declare Production GO;
- declare CLOSED;
- update P2A authority by hand.

Return Gate:

```text
CODEX_SUPERVISORY_REVIEW
```

## 15. Codex separation after Gen1

Gen1 completion is:

```text
PR + P2A-compatible result
-> authority handoff
-> next Generation = CODEX_SUPERVISORY_REVIEW
```

Codex first review generation is read-only with respect to product code and only performs:

- exact diff review;
- path/scope review;
- architecture review;
- tests;
- CI;
- identity zero-diff verification.

If PASS:

```text
-> WEB_MERGE_READY
```

If ordinary code repair is required:

```text
-> explicit successor authority: CODEX_BOUNDED_REPAIR
```

No implicit code edits in the supervisory review generation.

## 16. Freeze authority summary

```text
requirement_id = REQ-181-CONSUMER-CLOSURE-01
revision = r2-20260924-antigravity-first-p2a-canary
status = FROZEN_SPEC
freeze_count = 1
re_freeze = NO
target_release_id = v1.8.1-1
fresh_base_sha = 17445e27ee98d7b58beb8c0e79ba97e7e3c89e08
policy_bundle_sha256 = c628cc45e855090464eaaf1f4f8dae15636ddbc9451096654095759a3c2050b5
identity_semantic_delta = ZERO
repository_visibility = public
canonical_executor = LOCAL_RUNTIME
execution_provider = GEMINI_ANTIGRAVITY
return_gate = CODEX_SUPERVISORY_REVIEW
Production mutation authority = false
```
