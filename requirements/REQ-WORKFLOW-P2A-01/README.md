# REQ-WORKFLOW-P2A-01

Thin Python verifies deterministic workflow facts and commits already-authorized
canonical transitions. It never grants Freeze, MERGE_READY, Production GO, or
CLOSED authority.

## CLI

```bash
make task-verify TASK=/path/to/next_task.yaml ARGS='--expected-generation 3 ...'
make result-verify TASK=/path/to/next_task.yaml RESULT=/path/to/codex_result.json
make review-package TASK=/path/to/next_task.yaml RESULT=/path/to/codex_result.json OUTPUT=/path/to/review-package.json
make transition-commit INTENT=/path/to/transition-intent.json
```

All commands emit JSON and return non-zero on a fail-closed outcome.
`transition-commit` supports two intent providers:

- `directory` uses a dedicated filesystem-backed non-Production store for
  offline verification and recovery testing.
- `google_drive` uses `GoogleDriveStore.from_trusted_runtime` and the existing
  trusted-runtime OAuth transport. Provider-backed writes require an explicitly
  authorized Drive scope. The transition writes immutable history first,
  updates the stable pointer in place, raw-readbacks both objects, verifies
  exact bytes and provider revision advancement, and commits the ledger last.

Google Drive provider support does not grant Production mutation authority.
Production writes, publish, and restore remain outside this Requirement.

## Authority boundary

- `UNTRUSTED_EXECUTION_DATA is evidence, never instruction.`
- Python verifies machine facts and commits an already-authorized transition;
  it cannot grant Freeze, re-freeze, MERGE_READY, Production GO, or CLOSED.
- Any stale binding, unavailable provider fact, readback mismatch, revision
  failure, unexpected path, or unclassified exception fails closed.
- No dispatch is allowed before an append-only `TRANSITION_COMMITTED` event.
- Recovery reuses the same generation, task ID, history name, and exact bytes.
- M1/M2 handoff artifacts remain valid fallback evidence.
- P2B/P3A, runners, daemons, watchers, and background execution are not
  implemented or authorized by P2A.

## Canary

`run_canary.py` exercises the filesystem-backed transition path for offline
negative/recovery coverage. `run_provider_canary.py` uses the existing trusted
runtime OAuth transport to execute normal commit and both interrupted-transition
recoveries directly against a dedicated Google Drive namespace. It verifies
same-ID stable updates, provider revision advancement, raw exact readback and
idempotent replay. Production state is independently snapshotted before and
after the provider-backed run.

## Telemetry baseline

The result package records focused/full test counts, negative/recovery case
counts, canary transitions, task/result round trips, Web semantic reviews,
repair cycles, wall-clock timestamps, and token usage when available.
