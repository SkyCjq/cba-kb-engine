# REQ-181-CONSUMER-CLOSURE-01 — Task Brief

## Goal
Implement consumer closure across:
1. Current-State Document Closure (public GitHub wording)
2. Current-State Machine Consistency (CURRENT_VERSION_DOC as modern current surface for v1.8.1 and future releases)
3. Identity Consumer Projection / Consumption Closure (distinguishable SAME, NOT_SAME, UNDECIDED, UNAVAILABLE / NOT_MATERIALIZED semantic states without manufacturing relations; declared == actual machine counts)
4. Focused Consumer Acceptance (frozen contract v2 verification)

## Invariants
- identity_semantic_delta: ZERO
- Production mutation authority: false
- Return gate: CODEX_SUPERVISORY_REVIEW
