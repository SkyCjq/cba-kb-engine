# Deterministic hardening failure-to-guard mapping

This file records test bindings only. The Frozen Requirement and release authority remain external canonical inputs.

| Observed failure class | Machine guard | Deterministic evidence |
| --- | --- | --- |
| Authority/output contradiction | `TASK_CONTRACT_INVALID` | `tests/automation/test_hardening.py`, canary N1/P1 |
| Q2 runtime or exact-HEAD mismatch | `Q2_RUNTIME_CONFORMANCE_FAILED` | `tests/automation/test_hardening.py`, canary N2/P2 |
| Repeated same-class local repair | `NON_CONVERGENT_REPAIR` | `tests/automation/test_hardening.py`, canary N3/P3 |
| Missing/unsupported release attempt | `RELEASE_IDENTITY_NOT_READY` | canonical `_release_spec`, canary N4 |
| Current World control-triad mismatch | `CURRENT_WORLD_CONTROL_TRIAD_MISMATCH` | canonical `validate_policy_reconciliation`, canary N5/P4 |
| Partial PREPARE without exact reuse binding | `PREPARE_RECOVERY_CONTRACT_INCOMPLETE` | canonical `validate_reservation`, canary N6/P5 |
| Active-target/retirement semantic drift | existing canonical release regression | `tests/test_release_orchestration.py` |
| ARCHIVING checkpoint/resume failure | existing journal/checkpoint regression | `tests/test_archive_read_retry.py` |
| CURRENT_ZONE / semantic-parent reverse-dependency risk | existing topology regression | `tests/test_release_topology_compatibility.py` |
