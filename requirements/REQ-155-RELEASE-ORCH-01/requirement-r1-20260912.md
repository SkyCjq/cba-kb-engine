# CBA-KB Requirement - REQ-155-RELEASE-ORCH-01

> Revision: `r4-20260912-dependency-binding-recovery`
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
