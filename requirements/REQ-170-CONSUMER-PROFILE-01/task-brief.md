# REQ-170-CONSUMER-PROFILE-01 — v1.7.0

Revision: r2-20260914-v170-final-critical-revision. This supersedes the earlier
r1 freeze and the pre-v1.6.1 audit candidate. Re-frozen by explicit user approval on 2026-09-14. Stage 2 is authorized.

Baseline: production v1.6.1-1 at 81bd581fafbccb602f9ecaf9aaefca4533be69a4;
development main at 4103ce2aaa6dba3e12ca59ff3c9a8da2d2db1538.
Use a separate dev worktree. Production access is forbidden.

Build a deterministic facts-only player profile from a frozen MASTER snapshot and
explicit Private Instance record_key selectors. Name lookup is candidate discovery
only; never fabricate player_uid or merge ambiguous people. Preserve NULL, source
locators, verification level and semantic grain.

Build one canonical consumer payload: player profile + selected rights-aware
Document projections + selected event coverage + source index. Generate explicit
ChatGPT, Gemini Notebook and WorkBuddy packages from the same payload hash.
Target-specific sharding is allowed only with 100% coverage and zero silent truncation.
Private/copyrighted material requires per-target authorization.

Freeze v2 before any real run: 10 questions = 4 exact, 2 source tracing,
2 cross-source synthesis, 1 identity boundary, 1 unknown-gap. Real questions,
answers, selectors, authorizations and consumer outputs stay in Private Instance.

Acceptance: 30 cells. Per consumer exact=4/4, tracing=2/2, identity=1/1,
unknown=1/1, total >=9/10; aggregate >=27/30. NOT_TESTED is non-PASS.
Confirmed exact failures from retrieval/aggregation/counting trigger a separate
MCP Requirement; projection bugs stay in this REQ.

After acceptance emit USAGE_GATE_OPEN. Collect >=10 non-acceptance real queries
before project CLOSED and decide CONTINUE_V1_8 / SPLIT_NEW_REQUIREMENT / HOLD.
Do not auto-start v1.8.

SOURCE_CONTRACT_PROBE=N/A. Stage 4 requires target intake receipts instead.
release_infrastructure_mode=OFF; Performance Overlay=OFF. Production publication,
if needed, is a separate REQ-170-PROD-RELEASE-01.

Do not modify v1.6.1 consumer_projection.py, MASTER schema, Document semantics,
release infrastructure, workflow, lockfile or canonical data. Run focused tests only;
GitHub Actions owns full regression.
