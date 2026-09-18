# REQ-180-IDENTITY-COVERAGE-SUPPORT-01 Task Brief

Revision: `r3-20260917-identity-coverage-tooling-final-contract`

This controlled r3 repair finalizes the Stage 1 support tooling contract. Code
implementation remains unauthorized.

The new `src/cba_kb/identity_coverage.py` layer will build coverage inventory,
candidate proposals, deterministic human-review packets, relation-aware
reviewed decisions, a candidate registry plus provenance sidecar, a complete
coverage ledger, reconciliation, and a computed coverage certificate.

`candidate_registry.json` is now a pure Identity v1.8 registry object with no
support metadata. Its base/current hashes, MASTER hash, review hashes, and
creation time live in `candidate_registry_manifest.json`.

The candidate manifest and coverage certificate use the same MASTER authority
representation: either a verified file SHA256 with `FILE_SHA256_VERIFIED`, or
`MASTER_sha256: null` with
`ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA`. Sentinel strings are
forbidden, and a null SHA never bypasses record-key reconciliation.

The review contract freezes:

- exact `proposed_relation` values
- proposal-type/relation compatibility
- decision-to-mutation semantics
- explicit `source_exception_reason`
- machine-owned versus human-owned fields
- JSON authority and deterministic CSV editing projection
- canonical `reviewed_decisions.json` output
- fail-closed machine-field tamper detection
- consistent approved UID and canonical name for one new-identity group
- at most one player created per approved group
- no automatic linkage of unreviewed rows in a group

UID allocation remains caller-supplied opaque. The support module validates
the opaque UID format, rejects direct equality to normalized semantic fields,
and rejects SHA-256 derivations of individual normalized semantic fields using
the exact NFKC, trim, whitespace-collapse, case-preserving UTF-8 procedure.
It requires an independent-allocation attestation and does not claim to prove
absence of every possible custom derivation.

Review decisions never infer the opposite relation. Reject creates no
mutation, and a rejected row without an independently reviewed final
disposition remains incomplete. No-safe and source-exception decisions create
coverage only, never fake registry relations.

`src/cba_kb/player_identity.py` remains unchanged. The canonical registry is
never directly overwritten. All real outputs remain inside the Private
Instance. Tests are synthetic only. Production access remains forbidden and
Stage 2 remains unauthorized.

The coverage certificate binds the exact review packet, reviewed decisions,
candidate registry manifest, and coverage ledger hashes. There is no separate
undefined review-manifest authority. `full_identity_resolution_complete` is
true only when every MASTER record is `RESOLVED_SAME`; any source exception,
no-safe state, or unresolved candidate makes it false.

The Stage 2 Git allowlist now names
`requirements/REQ-180-IDENTITY-COVERAGE-SUPPORT-01/requirement-r3-20260917.md`.
