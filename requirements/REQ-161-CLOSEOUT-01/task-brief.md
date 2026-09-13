# REQ-161-CLOSEOUT-01 Task Brief

- REQ_ID: `REQ-161-CLOSEOUT-01`
- Target version: `v1.6.1`
- Revision: `r1.1-20260913-v161-stage1-freeze-bookkeeping-fix`
- Lane: `CODE_CONFIG`
- Baseline release: `v1.6.0-1`
- Baseline production SHA: `4fab3d0e8eedc594fae982f12a507fef88958f15`
- Base branch: `codex/v1.5.2-draft2`
- Baseline development SHA: `80451930765442512d87db50621319bf94f39c26`
- Feature branch: `feat/REQ-161-CLOSEOUT-01`
- Execution mode: `CODEX_EXECUTOR`
- Release infrastructure mode: `ON`
- Performance Overlay: `ON`, activation reasons `H/J`

## Implementation Objectives

- Keep Git-tracked `task.yaml` static and move runtime observations to the
  Private Instance immutable evidence ledger.
- Implement typed SHA provenance, provenance DAG validation and
  release-critical tree attestation.
- Implement and test the release transaction:
  `PROJECTED -> RESERVED -> REPROJECTED -> FROZEN_PLAN -> PREPARED ->
  ARCHIVING -> ARCHIVE_COMPLETE -> PUBLISHING -> VERIFYING -> COMPLETE`.
- Enforce exact manifest set coverage with zero missing, unexpected, duplicate
  or unresolved targets.
- Support execution-scoped archive namespaces, append-only checkpoints,
  verified immutable reuse and idempotent resume.
- Expose retryable and non-retryable remote-I/O classes, bounded read backoff
  and journal/readback reconciliation before any write retry.
- Add stable logical key `CURRENT_VERSION_DOC` and require release/code
  identity consistency across release status, README, INDEX, CONTEXT_CARD and
  CURRENT_VERSION_DOC.
- Add the frozen consumer golden-question schema, rights-aware document
  projection, deterministic event coverage and consumer result/failure-layer
  schemas.
- Add the watcher operational-qualification ledger schema for seven planned
  cycles without manufacturing runtime cycles.
- Add explicit `v1.6.1-1` support to `scripts/prepare_production.py`.

## Frozen Paths

Allowed paths, focused tests and must-not-change paths are exactly those in
`task.yaml` and Requirement section 7.4.

## Release-Critical Fixtures

The live release status, live manifest, production private policy, current
control documents, facts-zero-diff hashes, Document Lane private archive
manifest, consumer rights guards, event coverage sources and watcher r5
contract are required during Stage 2/3 validation. Watcher seven-cycle,
credential-revocation, off-site restore and repository-policy evidence are
required before project closeout and may remain pending while production is
complete.

## Stage 2 Boundaries

Only implementation, focused tests, guards and GitHub Development Sync are
allowed. Production mutation, publish, restore, merge-readiness self-approval
and closeout self-approval are forbidden.

## Stop Conditions

Stop without widening scope on baseline drift, allowlist mismatch, frozen
contract conflict, source-contract drift, production-authority drift, or
unexplained dirty/diverged state.

