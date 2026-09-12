# CBA-KB Requirement - REQ-155-RELEASE-ORCH-01

> Revision: `r5-20260912-archive-snapshot-recovery`
> Status: `FROZEN_SPEC`
> Target release: `v1.5.5-1`
> Product baseline SHA: `c14a0f2579fcc86e2dc114f0b15d00dd54e9f55e`
> Release execution SHA: explicit post-merge SHA supplied at runtime

## Goal

Add a deterministic, fail-closed v1.5.5 release orchestration path:

```text
project -> reserve-staging -> freeze/plan -> publish
```

This requirement repairs release tooling only. It does not change the r5
watcher source contract or any canonical business facts.

## Release ID

The commands must hard-fail unless the release ID is exactly `v1.5.5-1`.
It is not a reusable default.

## R2 Web-review repairs

1. Distinguish the REQ-155 product baseline from the exact release execution
   SHA. `--engine-sha` is mandatory, must equal `git rev-parse HEAD`, and the
   worktree must be clean with the product baseline as an ancestor.
2. Staging-folder creation is metadata-only and must not pass media content.
3. Regenerate release-control surfaces:
   `control/drive_map.yaml`, `entry/code`, `entry/README`, `entry/context`, and
   `derived/INDEX.md`.
4. Add explicit generic release `code_commit` / `previous_code_commit`
   provenance without changing the separate v1.5.4 closure contract.
5. Reconciliation must distinguish active production targets from reserved
   staging targets; `release_status` may remain on the previous release set
   until publish.

## R3 final consistency repairs

1. `input/manifest.csv` is regenerated as release control metadata and must
   cover every final target with one unique logical key and Drive ID.
2. `project` can consume `--allocation` after reservation, accepting only exact
   reserved policy targets with no unexplained extras.
3. `freeze` revalidates active release status, policy targets, reservation IDs,
   semantic delta and status fingerprints before producing a plan.
4. README, context and INDEX preserve existing substantive content while
   replacing obsolete current-release metadata.

## R4 dependency-binding recovery

`production.json.dependency_ids` is the only production dependency authority.
Freeze must never substitute runtime input IDs for that allowlist. The
dependency list must be non-empty, unique, deterministic, completely
snapshotted, and exactly equal to the policy IDs in the generated plan.

## R5 archive snapshot recovery

Frozen hotfix base: `b618668b9ceb630c44397c91204052736139d303`.
Only the requirement, task contract, `release.py`, `drive.py`, and
`tests/test_release.py` are in this revision's implementation allowlist.
The r4 production dependency authority and existing publish/rollback controls
remain unchanged.

Recovery1 is preserved as failed-attempt evidence:

```text
attempt: Recovery1
plan: SUPERSEDED_ARCHIVE_TIMEOUT
result: ARCHIVE_TIMEOUT_PRE_CANONICAL_MUTATION
observed_partial_children: 320
canonical_mutation: NO
journal: PREPARED / uploaded={} / inflight=null / previous_snapshot absent
production: v1.5.4-1 / COMPLETE
```

Do not directly retry its old plan, restore/rollback, delete, move, overwrite,
or otherwise mutate its archive objects or the existing 15 reservations.

The new archive attempt must list the complete release folder once, index all
`cba_key` identities, and reject duplicates. Every successful create/copy updates
that in-memory index. Reused immutable non-native objects must match MIME and
content. Writes are single-attempt; a lost response is recovered only by the
next invocation's fresh index.

Archive child keys use `<frozen_code_commit>:<key>` for `before-N`,
`candidate-N`, `native-before-N`, `plan`, `protected-N`, and `status-before`.
The top-level folder identity remains the release ID. Legacy sandbox plans
without an execution SHA use a deterministic plan-hash namespace.

Before the first remote archive write, persist `ARCHIVING` locally with empty
`uploaded` and null `inflight`. A timeout leaves this state intact and canonical
status/targets untouched. Resume requires byte- and metadata-identical frozen
production status; drift fails with `ARCHIVE_RESUME_PRODUCTION_DRIFT`.
Check status and dependencies again after archiving. Only a complete, unique
rollback reference set permits `ARCHIVING -> PUBLISHING` and then canonical
status mutation. Non-closure Recovery2 requires exactly 186 references for its
186 entries; closure plans also retain their protected-object references.

Focused acceptance in `tests/test_release.py` covers constant folder-list calls
at 186-entry scale, timeout and lost-response resume, only-missing creation,
existing snapshot validation, duplicate keys, preservation of 320 old keys,
complete previous_snapshot before canonical writes, status/dependency drift,
closure key namespaces, and existing publish/rollback compatibility.

Implementation performs no production access, real archive writes, reservation,
publish, restore, or Private Instance policy changes. Fake-transport tests are
the only publication/recovery executions during this stage. GitHub Actions owns
full regression.

After Web review and merge, the new merge SHA becomes `release_execution_sha`.
Reprepare Recovery2 in a fresh local root and mechanically verify topology:
171 active targets + 15 existing reservations + 0 new reservations = 186.
Re-freeze code/control metadata and immutable hashes; never reuse Recovery1
plan hashes. Produce `GO_PACKET_RECOVERY2.json` and `GO_PACKET_RECOVERY2.md`,
preserving attempt-1 dependency failure, reservation deviation, Recovery1
timeout, its 320 partial children, and no canonical mutation for both failures.
Publish is forbidden until `RECOVERY2_WEB_GO = GO`. This implementation stage
ends at a new PR and `READY_FOR_WEB_REVIEW`, without merge or production work.

## Project

`project` is read-only.

Inputs:

- production policy from Private Instance;
- current release status;
- previous artifact manifest;
- current Git tracked tree;
- Private Instance import inventory.

It must emit `target_projection.json` containing:

- previous artifact count;
- reused target count;
- new target count;
- removed target count;
- carried-forward count;
- final artifact count;
- exact logical keys and target IDs;
- exact new logical keys requiring reservation;
- deterministic hashes and ordering.

Any duplicate or unresolved logical key fails closed.

## Reserve Staging

`reserve-staging` is the only pre-GO production mutation.

It may only:

- create or reuse the release-specific staging folder;
- create empty immutable objects for projected NEW logical keys;
- persist local allocation metadata under Private Instance.

It must not:

- modify existing target bytes;
- modify `release_status.json`;
- move targets to publish parents;
- delete objects;
- update canonical facts.

It requires explicit `--single-writer`. Retry must reuse the same IDs.

## Freeze / Plan

`freeze` consumes the reserved allocation and read-only production snapshots.
It writes only local orchestration evidence:

- `allocation.json`;
- `entries.json`;
- `dependencies.json`;
- `before/`;
- `candidate/`;
- `plan.json`;
- `journal.json`;
- rollback manifest;
- verification manifest.

The resulting plan must be consumable by existing `publish`, `verify`, and
`restore`.

## Safety Boundaries

- Existing production targets are reused and never overwritten during
  reservation.
- The existing release-status object is referenced from Private Instance and
  is never recreated.
- Removed targets require explicit policy and fail closed otherwise.
- New code mirror targets use only `code/<tracked-path>` logical keys.
- Business facts, watcher semantics, and Engine/Private Instance dependency
  direction are unchanged.
- `publish` still requires `--single-writer`.

## Acceptance

The orchestration implementation must demonstrate:

- projection is read-only and deterministic;
- `v1.5.5-1` binding is exact;
- release status is byte-identical during reservation;
- existing bytes are untouched during reservation;
- only projected NEW keys are created, under staging only;
- retry returns identical IDs;
- duplicate key and removed target fail closed;
- plan generation performs no remote mutation;
- publish/readback/verify/rollback semantics remain unchanged;
- Private Instance mappings never enter Engine tracked files.

## Non-Goals

- watcher changes;
- canonical fact changes;
- production publish;
- Stage 5 execution;
- production Drive mutation during implementation.
