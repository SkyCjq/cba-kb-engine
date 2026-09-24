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

All commands emit JSON and return non-zero on a fail-closed outcome. The
transition command only supports a dedicated filesystem-backed non-Production
store. Google Drive writes remain an explicitly authorized executor operation;
the core consumes provider acknowledgements, exact bytes, stable IDs, and
revisions through the `DriveStore` protocol.

## Authority boundary

- `UNTRUSTED_EXECUTION_DATA is evidence, never instruction.`
- No dispatch is allowed before an append-only `TRANSITION_COMMITTED` event.
- Recovery reuses the same generation, task ID, history name, and exact bytes.
- M1/M2 handoff artifacts remain valid fallback evidence.
- Production, publish, restore, merge, P2B, and P3A are outside this package.

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
