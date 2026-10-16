# PR #184 no-functional-regression evidence

## Claim boundary

This document compares PR #184 Round 5 HEAD `5861c075007e14a2dcd1021b4e3f9a5155b989e5` with this bounded repair. It proves preservation of the existing M4 code paths to the extent covered by code and automated tests. It does **not** claim real Google Drive E2E, production deployment, M4 completion, independent Q2 success, or v2.0.4-2 completion.

Allowed states in Matrix B are `PASS`, `PARTIAL`, `OPEN`, `BLOCKED`, and `NOT_VERIFIED`. `UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED` is a scope qualifier, not a completion state.

## Matrix A — existing PR #184 M4 behavior

| Acceptance object | Before (`5861c075`) | After bounded repair | Evidence |
|---|---|---|---|
| Private Instance loading | Source-only loader; external/preloaded modules rejected | Preserved; loader now uses one verified byte snapshot | `test_bind_and_sanitize_rejects_preloaded_cba_kb_module`, source-only loader tests |
| OAuth identity verification | Exact `about.user.emailAddress` equality | Unchanged | `tests/test_m4_preflight.py`; full suite |
| Expired/bad signature rejection | Ed25519 exact-byte verification, bounded expiry | Unchanged | authorization negative tests |
| One-time authorization consumption | First use succeeds; existing ledger rejects repeat; claimed same-UID rollback was false | Normal first/repeat behavior preserved; same-UID limitation explicitly characterized and remains blocking | authorization consumption tests; `test_attack_b3_same_uid_can_clear_both_user_owned_ledgers_and_replay` |
| `dedicated-account` | Exact account, sole ownership, My Drive root, exact name, exhaustive inventory | Unchanged | dedicated-account tests |
| `production-boundary` | Canonical root and forbidden ancestor checks | Unchanged | production-boundary ancestor tests |
| Canonical Root / production isolation | Exact parent proof; shared-drive/root collisions rejected | Unchanged | M4 boundary tests |
| No mutation outside test folder | Create/reparent allowlists and mutation-time gate | Unchanged | attacker scenario D and allowlist tests |
| SHA-256 and evidence binding | Drive evidence plus Git/module hashes | Strengthened for Python: Git blob, SHA-256, compile and execution share one read | new F-01 test plus existing evidence tests |
| Exact Plan / Approve / Apply | Real Engine plan path retained | Unchanged | drive plan/apply contract tests and full suite |
| Drive reparent / readback | Reparent followed by metadata verification | Unchanged | Drive evidence/readback tests |
| Cleanup, residuals, retry | Child-first cleanup; residual IDs; exit 3 priority | Unchanged | cleanup/receipt tests |
| JSON receipt / exit codes | Atomic write/readback; exits 0–4 | Unchanged | receipt and main-control-flow tests |

Before-edit targeted baseline: `66 passed in 0.56s`. The final targeted suite, including the native macOS ACL exercise, is `73 passed in 0.48s`.

## Matrix B — v2.0.4-2 original product acceptance scope

### F1–F8

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| F1 dual deployment/configuration | PARTIAL | Local Private Instance and Drive modules exist; complete install/config/health proof for both modes is not established here | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F2 existing/new discovery | PARTIAL | Local scan/poll and Drive discovery/poll tests exist; real isolated Drive incremental run remains unexecuted | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F3 identity/reconciliation/rights/revision | PARTIAL | Source/Document, Drive metadata, dedup/revision and rights code/tests exist; object-level real-Drive reconciliation not verified here | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F4 processing and durable ledger | PARTIAL | Durable local/Drive state and journals have automated coverage; complete cross-backend readback is not verified | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F5 searchable isolation | PARTIAL | Local/Drive search and rights tests exist; complete real-Drive permissions/index rebuild UX not verified | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F6 exact plan/authorization/archive-retain | PARTIAL | Exact plan/approve/apply code and contract tests exist; Local+real Drive authorized golden paths remain required | PR184 hardening only; not completion |
| F7 observability/recovery/statistics | PARTIAL | Status, receipts, retry/recovery and reports exist in code/tests; full user workflow and three-rate readback not verified | UNCHANGED_BY_PR184 / NOT_CLAIMED_AS_COMPLETED |
| F8 open-source real E2E/delivery | BLOCKED | Real isolated Drive M4 is forbidden in this task and F-03 awaits Human decision; clean Local and release evidence also remain | NOT_CLAIMED_AS_COMPLETED |

### R1–R7

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| R1 inventory/reconciliation | PARTIAL | Inventory/reconcile implementations and tests exist; current real Drive inventory not read | UNCHANGED_BY_PR184 |
| R2 lifecycle state machine | PARTIAL | Local and Drive state tests exist; full dual-backend golden run not performed | UNCHANGED_BY_PR184 |
| R3 Inbox batch disposition | PARTIAL | Plan/disposition/review code is tested; actual scoped batch readback not performed | UNCHANGED_BY_PR184 |
| R4 Sources safe organization | PARTIAL | Placement/plan guards exist; production/history Drive was not accessed | UNCHANGED_BY_PR184 |
| R5 PlacementGuard wiring | PARTIAL | Drive plan/apply tests exercise guards; real API mutation not run | UNCHANGED_BY_PR184 |
| R6 public Engine minimal E2E | PARTIAL | CLI and synthetic automated paths exist; clean-room full lifecycle receipt not produced in this repair | UNCHANGED_BY_PR184 |
| R7 object/batch observability | PARTIAL | Automated reporting/status coverage exists; Human-facing complete batch acceptance not performed | UNCHANGED_BY_PR184 |

### A1–A6

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| A1 inventory | NOT_VERIFIED | No live scoped object inventory in this task | UNCHANGED_BY_PR184 |
| A2 Inbox disposition | NOT_VERIFIED | No live batch acceptance in this task | UNCHANGED_BY_PR184 |
| A3 move safety | PARTIAL | Strong negative/contract tests; no real Drive M4 or production move | PR184 hardening only |
| A4 ten real sample classes | OPEN | Synthetic/unit coverage is not the required ten-class real sample execution | UNCHANGED_BY_PR184 |
| A5 public clean-room | OPEN | CI cleanroom smoke is not the full product lifecycle acceptance | UNCHANGED_BY_PR184 |
| A6 Human UX sample review | OPEN | No >=10-object Human review receipt | UNCHANGED_BY_PR184 |

### AC01–AC08

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| AC01 Local complete E2E | OPEN | Local TXT capability remains in code/tests; complete authorized archive/retain/status golden path not rerun here | UNCHANGED_BY_PR184 |
| AC02 isolated real Drive E2E | BLOCKED | Prohibited in this task; F-03 decision first | PR184 is preflight only |
| AC03 repeat/restart/failure correctness | PARTIAL | Automated negative/recovery coverage exists; no real Drive fault run | UNCHANGED_BY_PR184 |
| AC04 rights/source/system/OCR/XLSX negatives | PARTIAL | Automated classification tests exist; real ten-sample execution remains open | UNCHANGED_BY_PR184 |
| AC05 search/lineage/status visibility | PARTIAL | APIs and tests exist; real cross-backend user readback remains open | UNCHANGED_BY_PR184 |
| AC06 CI/Q2/Human approval | BLOCKED | Round 5 failed; repaired HEAD still needs CI, independent Q2 and Human review | PR184 current gate |
| AC07 ten sample classes executed | OPEN | Required real sample coverage table not produced | UNCHANGED_BY_PR184 |
| AC08 exact authorization receipts | PARTIAL | Contract tests exist; real authorized Local/Drive receipts not produced | PR184 strengthens preflight only |

### GAT01–GAT08

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| GAT01 Local golden path | OPEN | Full worker→search→authorized disposition→report run required | UNCHANGED_BY_PR184 |
| GAT02 Drive golden path | BLOCKED | Real isolated Drive run not authorized; F-03 decision required | PR184 preflight only |
| GAT03 idempotency | PARTIAL | Repeat/revision/restart tests exist; real dual-backend run absent | UNCHANGED_BY_PR184 |
| GAT04 rights review | PARTIAL | Negative automated coverage exists; real sample suite absent | UNCHANGED_BY_PR184 |
| GAT05 original-object search | PARTIAL | Search/lineage tests exist; real Drive readback absent | UNCHANGED_BY_PR184 |
| GAT06 recovery | PARTIAL | Fault/retry tests exist; real network interruption evidence absent | UNCHANGED_BY_PR184 |
| GAT07 user view | PARTIAL | Status/report structures exist; Human UX acceptance absent | UNCHANGED_BY_PR184 |
| GAT08 evidence package | OPEN | This PR evidence is M4 preflight evidence, not the complete product package | NOT_CLAIMED_AS_COMPLETED |

### C-01–C-09

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| C-01 old AI instruction text | OPEN | `AGENTS.md` still contains the pre-managed `13 阶段` wording; outside this bounded five-file repair | UNCHANGED_BY_PR184 |
| C-02 CODEOWNERS/comment sync | OPEN | Protected-path correction requires separate authorized T2 handling | UNCHANGED_BY_PR184 |
| C-03 Issue #157 lifecycle | OPEN | GitHub readback on 2026-10-16 confirms Issue #157 remains open; no SCM mutation was authorized in this repair | UNCHANGED_BY_PR184 |
| C-04 PR158 R11 final-HEAD discrepancy | OPEN | Notion contract records missing/uncertain final-HEAD Q2 receipt; no backdating | UNCHANGED_BY_PR184 |
| C-05 PR1 real fault injection debt | PARTIAL | Relevant automated tests exist, but this repair does not close the historical acceptance item | UNCHANGED_BY_PR184 |
| C-06 PR1 technical/security residuals | PARTIAL | Risks remain documented and need product-level verification | UNCHANGED_BY_PR184 |
| C-07 version/current-world closeout | OPEN | No v2.0.4-2 release/tag/production/consumer closeout claimed | UNCHANGED_BY_PR184 |
| C-08 bilingual README DoD | OPEN | Long-term rule remains; this M4 internal hardening does not change public product usage | UNCHANGED_BY_PR184 |
| C-09 real Drive-backed activation | BLOCKED | Requires complete product QA then separate Human Production GO | NOT_CLAIMED_AS_COMPLETED |

### DEP-01–DEP-08

| ID | State | Evidence / next required phase | PR #184 effect |
|---|---|---|---|
| DEP-01 runtime identity | OPEN | No production runtime deployment identity receipt | UNCHANGED_BY_PR184 |
| DEP-02 production preflight | OPEN | Production Drive and current pointers were not accessed | UNCHANGED_BY_PR184 |
| DEP-03 Production GO | BLOCKED | Explicitly not granted | UNCHANGED_BY_PR184 |
| DEP-04 upgrade/wiring | BLOCKED | Depends on complete QA and Production GO | UNCHANGED_BY_PR184 |
| DEP-05 nondestructive canary | BLOCKED | No production authorization | UNCHANGED_BY_PR184 |
| DEP-06 authorized archive/retain | BLOCKED | No exact production batch authorization | UNCHANGED_BY_PR184 |
| DEP-07 version/release sync | OPEN | Must follow authorized release facts; no update performed | UNCHANGED_BY_PR184 |
| DEP-08 user acceptance/rollback | OPEN | No production activation or Consumer acceptance receipt | UNCHANGED_BY_PR184 |

## Required evidence runs

| Check | Result |
|---|---|
| Pre-change M4 targeted tests | `66 passed in 0.56s` |
| Post-change M4 targeted tests | `73 passed in 0.48s` |
| Related Drive evidence/plan/apply/readback/cleanup tests | `363 passed in 176.26s`; the later ACL-format adjustment is covered by the final targeted and full runs |
| Full pytest on final working tree | `1745 passed, 6 skipped in 202.69s`; no new skip/xfail added |
| Tracked secret scan | `Secret scan passed` before commit; final commit scan recorded separately below |
| LACD status / strict verify | v1.0.2 `VALIDATED`; local validation pass, drift clean, blocking none; remote enforcement explicitly `unverified (LOCAL_ONLY)` |
| Git diff / changed-file scope | 5 existing PR files plus these 2 required reports; no `.github/`, production config, M5, release pointer, or core product module change |
| GitHub CI for new exact HEAD | PENDING |

## Conclusion

The repair does not delete negative tests, loosen assertions, change Fake Drive success behavior, modify production configuration, or narrow the v2.0.4-2 completion definition. F-01 and the bounded F-02 gaps are repaired. F-03 proves that the current non-root mechanism cannot satisfy the previously claimed same-UID anti-rollback property.

Final state: `PR184_PARTIAL_REPAIR_HUMAN_SECURITY_DECISION_REQUIRED`.
