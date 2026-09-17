# REQ-180-IDENTITY-COVERAGE-SUPPORT-01

## Full Private Identity Coverage Tooling

Revision: `r3-20260917-identity-coverage-tooling-final-contract`

Lane: `CODE_CONFIG`

Risk: `HIGH`

Development baseline:
`1f1defe6999c33001ff5d67b7a7238ed870a6447`

Upstream Requirement:
`REQ-180-IDENTITY-COVERAGE-01`

Upstream revision:
`r1-20260917-full-private-identity-coverage`

Status: `FROZEN_SPEC`

Stage 2 authorized: `false`

## Objective

Add the deterministic orchestration and support layer required to execute the
frozen private identity coverage contract without changing Identity v1.8
relation authority.

The support layer covers full-MASTER inventory, candidate proposals,
deterministic review packets, relation-aware review ingestion, candidate
registry construction, coverage-ledger reconciliation, and coverage
certificate generation.

This controlled r3 repair closes the remaining implementation-affecting
contract ambiguities: certificate provenance, full-resolution computation,
MASTER authority representation, new-identity group consistency, exact UID
normalization, and final Git traceability. It does not authorize
implementation.

## Frozen Upstream Contract

Upstream hashes:

- Requirement:
  `065218c82485152cc9551f6ce2e4a17457f81db93d9fb47e199db0bda19cf2ba`
- Task Brief:
  `bccd2f8082f8ddc0ca7424d2eaa19d2f2b16f20aa718e6ce5d5c1f042b1ce043`
- task.yaml:
  `cc20888c943a68ddf4e27a0307947e8880c095735315454120ef473d57d4662e`

The upstream audit universe is 3465 MASTER record keys. Current state:

- players: `26`
- record links: `152`
- resolved same: `112`
- undecided: `40`
- not-same-only: `0`
- completely unlinked: `3313`
- estimated review rows: `3353`
- recommended review batch size: `200`
- initial batch estimate: `17`

The current collision count is only an audit snapshot and must be recomputed
after candidate generation.

## Architecture

New logic belongs in:

`src/cba_kb/identity_coverage.py`

The module orchestrates:

`MASTER + registry -> inventory -> candidates -> review packet -> reviewed decisions -> candidate registry + coverage ledger -> reconciliation -> certificate`

`src/cba_kb/player_identity.py` remains the relation authority and must not
change. The support module reuses existing registry validation, candidate
lookup, player/link transformation, serialization, and CAS storage APIs.

## Coverage Dispositions

Only these dispositions are valid:

- `RESOLVED_SAME`
- `UNRESOLVED_CANDIDATES`
- `NO_SAFE_CANDIDATE`
- `SOURCE_EXCEPTION`

Every MASTER record key has exactly one ledger disposition.

## Proposal Types and Relations

Review proposal types:

- `EXISTING_IDENTITY_CANDIDATE`
- `NEW_IDENTITY_CANDIDATE`
- `NO_SAFE_CANDIDATE`
- `SOURCE_EXCEPTION_CANDIDATE`

Frozen `proposed_relation` enum:

- `PROPOSED_SAME`
- `PROPOSED_SEPARATE`
- `KEEP_UNDECIDED`
- `NO_SAFE_CANDIDATE`
- `SOURCE_EXCEPTION`

No other value is valid.

Compatibility:

- `EXISTING_IDENTITY_CANDIDATE`: `PROPOSED_SAME`, `PROPOSED_SEPARATE`,
  `KEEP_UNDECIDED`; requires `candidate_player_uid`
- `NEW_IDENTITY_CANDIDATE`: `PROPOSED_SAME`, `KEEP_UNDECIDED`
- `NO_SAFE_CANDIDATE`: only `NO_SAFE_CANDIDATE`
- `SOURCE_EXCEPTION_CANDIDATE`: only `SOURCE_EXCEPTION`

## Candidate Rules

Allowed candidate sources are exact canonical name, approved alias,
source-declared identifier, legitimate external identifier, existing human
decisions, and existing identity relations.

Exact names, aliases, team, adjacent season, jersey number, fuzzy names, and
LLM inference must never automatically create `same`.

A `NEW_IDENTITY_CANDIDATE` may group multiple historical records into one
review-only `candidate_group_id`, but it must not create a player or relation.

`candidate_group_id != player_uid`. The support module must never
automatically copy or transform a `candidate_group_id` into an
`approved_player_uid`.

For multiple `NEW_IDENTITY_CANDIDATE` rows sharing one
`candidate_group_id`, every `APPROVE + PROPOSED_SAME` row must use one
consistent `approved_player_uid` and one consistent
`approved_canonical_name`. Conflicts fail closed with
`NEW_IDENTITY_GROUP_AUTHORITY_CONFLICT`.

One candidate group may create a player at most once. Each record relation
still requires its own reviewed row. Unreviewed rows in the group must never
be auto-linked.

## Decision to Mutation Semantics

### APPROVE + PROPOSED_SAME

Produces reviewed authority for `link_status = same` using the explicit
candidate or approved player UID.

### APPROVE + PROPOSED_SEPARATE

Produces `link_status = not_same` for the explicit candidate UID only. It does
not infer any other UID as `same`.

### APPROVE + KEEP_UNDECIDED

Produces coverage disposition `UNRESOLVED_CANDIDATES`. It must not create
`same` or `not_same`, and it must not fabricate a new undecided registry link.
An existing legitimate undecided link may remain unchanged.

### APPROVE + NO_SAFE_CANDIDATE

Produces only `coverage_disposition = NO_SAFE_CANDIDATE`. No player or
registry relation is created.

### APPROVE + SOURCE_EXCEPTION

Produces only `coverage_disposition = SOURCE_EXCEPTION`. No player or identity
relation is created.

### REJECT

Rejects the proposal only. It infers no opposite relation and causes no
registry mutation. A rejected proposal alone does not create a final alternate
disposition. If no independently reviewed final disposition remains, review is
incomplete and certification must fail closed for that record.

### UNDECIDED

Keeps the record unresolved, creates no `same` or `not_same`, and may produce
`UNRESOLVED_CANDIDATES` only when a legitimate candidate remains enumerable.

## UID Policy

`player_uid` remains opaque, stable, caller-supplied, and independent of
semantic data.

`uid_allocation_policy = CALLER_SUPPLIED_OPAQUE`

No automatic UID generator is authorized.

For an approved new identity, the support module must:

1. Require `player_identity.validate_player_uid()` to pass.
2. Reject direct equality to each normalized semantic field:
   canonical/display name, record key, team/club, and season.
3. Reject the SHA-256 hexadecimal digest of each individual normalized
   semantic field.
4. Require an allocation attestation that the UID was allocated independently
   of semantic source data.

Semantic normalization is exact:

1. Unicode NFKC.
2. Trim leading and trailing whitespace.
3. Collapse internal whitespace runs to one ASCII space.
4. Preserve resulting case.
5. Encode the normalized field as UTF-8 before SHA256.

Apply this independently to canonical/display name, record key, team/club,
and season. The forbidden digest check is exactly:

`sha256(normalized_field.encode("utf-8")).hexdigest()`

No concatenated-field hash rules or additional digest algorithms are
authorized without a future Requirement change.

The software may prove only this machine-checkable set. It does not claim to
prove the absence of every possible custom hash or encoding derivation.

Creating a new player requires human approval, a valid approved UID, an
approved canonical name, and evidence. The new player approval does not link
all same-name records; each record relation requires separate reviewed
authority.

## Human Review Schema

Machine-owned fields in `review_packet.json`:

- `review_id`
- `record_key`
- `proposal_type`
- `candidate_group_id`
- `candidate_player_uid`
- `candidate_name_display_only`
- `proposed_relation`
- `machine_suggestion`
- `machine_reason`
- `evidence_refs`

Human-owned fields:

- `human_decision`
- `human_note`
- `approved_player_uid`
- `approved_canonical_name`
- `source_exception_reason`
- `reviewed_at`

Machine-owned fields are immutable authority and may not be edited through the
CSV projection.

`SOURCE_EXCEPTION_CANDIDATE + APPROVE` requires a non-empty
`source_exception_reason`. For every other final decision,
`source_exception_reason` must be null. The reason must not be inferred from
`machine_reason` or `human_note`. An approved reason flows explicitly into
`coverage_ledger.source_exception_reason`.

## JSON and CSV Round Trip

`review_packet.json` is the immutable machine proposal authority.

`review_packet.csv` is a deterministic human-editable projection. Reviewers
may edit only human-owned columns.

`identity-review-validate` consumes:

- the original `review_packet.json`
- the reviewed `review_packet.csv`

It fails closed if any machine-owned CSV field differs from JSON. On success,
it creates canonical:

`reviewed_decisions.json`

`reviewed_decisions.json` is the human decision authority consumed by
`identity-review-apply`. The CSV is never canonical authority.

Deterministic hashes are recorded as:

- `review_packet_sha256`
- `reviewed_decisions_sha256`

## Candidate Registry

`candidate_registry.json` is a pure Identity v1.8 registry object. It has no
support-specific metadata and must pass
`player_identity.validate_registry()`.

Its canonical identity hash is `registry_sha256`.

Provenance lives in a separate private sidecar:

`candidate_registry_manifest.json`

Minimum sidecar fields:

- `schema_version`
- `base_registry_sha256`
- `candidate_registry_sha256`
- `MASTER_sha256`
- `master_authority_mode`
- `MASTER_rows`
- `MASTER_unique_record_keys`
- `review_packet_sha256`
- `reviewed_decisions_sha256`
- `created_at`

MASTER authority representation is fixed:

- `MASTER_sha256` is either a valid SHA256 or `null`.
- `master_authority_mode` is exactly
  `FILE_SHA256_VERIFIED` or
  `ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA`.
- A valid SHA requires `FILE_SHA256_VERIFIED`.
- A null SHA requires `ROWSET_RECONCILIATION_WITHOUT_RUNTIME_FILE_SHA`.
- Sentinel strings such as `NOT_AVAILABLE` are forbidden.
- `MASTER_rows` and `MASTER_unique_record_keys` remain required.

A null SHA never bypasses record-key reconciliation. The sidecar contains no
mappings beyond necessary provenance.

The support layer never directly overwrites the canonical registry. Actual
canonical write remains a later controlled Coverage Stage 2 apply gate using
the existing `IdentityStore` compare-and-swap authority.

## Review Outputs

All outputs remain private:

- `review_packet.json`
- `review_packet.csv`
- `reviewed_decisions.json`
- `candidate_registry.json`
- `candidate_registry_manifest.json`
- `identity_coverage_ledger.json`
- `identity_coverage_certificate.json`

Real outputs must not enter Git.

## Coverage Ledger

The deterministic ledger contains one object per MASTER record key:

- `record_key`
- `coverage_disposition`
- `same_count`
- `undecided_count`
- `not_same_count`
- `candidate_count`
- `review_required`
- `source_exception_reason`
- `evidence_refs`

Additional provenance may be present only when it does not blur frozen
semantics.

## Reconciliation

Reconciliation is fail-closed and compares MASTER unique record keys with
ledger unique record keys. It reports missing, unknown, duplicate, and silent
drop counts, plus all disposition counts.

## Coverage Certificate

The deterministic certificate records:

- `MASTER_sha256`
- `master_authority_mode`
- `MASTER_rows`
- `MASTER_unique_record_keys`
- base and final registry SHA256
- coverage-ledger SHA256
- `review_packet_sha256`
- `reviewed_decisions_sha256`
- `candidate_registry_manifest_sha256`
- disposition counts
- missing, unknown, duplicate, false-merge, multiple-same, and silent-drop
  counts
- `full_record_coverage_complete`
- `full_identity_resolution_complete`

There is no `review_manifest.json` authority and no review-manifest SHA field.

The caller may not supply either completeness boolean. Reconciliation computes
them. Full record coverage is true only when missing, unknown, duplicate, and
silent-drop counts are zero and ledger and MASTER key sets match.

`full_identity_resolution_complete` is true if and only if every MASTER
record key has `coverage_disposition = RESOLVED_SAME`. Any
`UNRESOLVED_CANDIDATES`, `NO_SAFE_CANDIDATE`, or `SOURCE_EXCEPTION` record
forces it false. `SOURCE_EXCEPTION` is not removed from the denominator under
r3.

MASTER SHA representation and the `master_authority_mode` enum are identical
to the rules frozen for `candidate_registry_manifest.json`.

## CLI Commands

- `identity-coverage-inventory`
- `identity-coverage-candidates`
- `identity-review-prepare`
- `identity-review-validate`
- `identity-review-apply`
- `identity-coverage-reconcile`
- `identity-coverage-certify`

All real input and output paths are constrained to the configured Private
Instance root.

## Synthetic Tests

Repository tests use synthetic data only and cover:

- resolved, undecided, not-same-only, and unlinked records
- existing and new-identity proposals
- multiple records proposed to one new-identity group
- no-safe and source-exception paths
- all approve, reject, and undecided relation semantics
- missing human authority and missing approved UID failures
- UID direct-equality and SHA256-derived-semantic-field failures
- valid caller-supplied opaque UID acceptance
- existing UID preservation
- deterministic packet, reviewed decisions, candidate registry, ledger, and
  certificate
- missing, unknown, duplicate, and silent-drop reconciliation failures
- private-path escape and canonical registry mutation guards
- candidate registry remaining a pure valid Identity registry
- candidate registry manifest carrying base and candidate hashes
- unsupported or incompatible proposal relations failing closed
- source exception without an approved reason failing closed
- machine-owned CSV tampering failing closed
- reject creating no opposite relation and no fabricated completion
- keep-undecided and no-safe decisions creating no fake registry relation
- certificate binding of exact `review_packet_sha256`
- certificate binding of exact `reviewed_decisions_sha256`
- certificate binding of exact `candidate_registry_manifest_sha256`
- absence of an undefined review-manifest concept
- full identity resolution true only when every record is `RESOLVED_SAME`
- `SOURCE_EXCEPTION` forcing full identity resolution false
- null MASTER SHA accepted only with the rowset authority mode
- null MASTER SHA not bypassing record-key reconciliation
- fake MASTER SHA sentinel strings rejected
- SHA and `master_authority_mode` mismatch rejected
- inconsistent approved UID in one new-identity group failing closed
- inconsistent approved canonical name in one new-identity group failing closed
- one new-identity group creating at most one player
- unreviewed new-identity-group rows not being auto-linked
- semantic normalization deterministic
- SHA256 of a normalized semantic field rejected as an approved UID

## Scope

Allowed paths:

- `requirements/REQ-180-IDENTITY-COVERAGE-SUPPORT-01/requirement-r3-20260917.md`
- `requirements/REQ-180-IDENTITY-COVERAGE-SUPPORT-01/task-brief.md`
- `requirements/REQ-180-IDENTITY-COVERAGE-SUPPORT-01/task.yaml`
- `src/cba_kb/identity_coverage.py`
- `src/cba_kb/cli.py`
- `tests/test_identity_coverage.py`
- `tests/test_cli_identity_coverage.py`

`src/cba_kb/player_identity.py` and all Profile, Mention, document-lane,
MASTER/Facts/Events, release, transport, production, and configuration paths
are must-not-change.

## Safety

- private data in Git: forbidden
- synthetic tests only
- canonical registry direct write: forbidden
- production access: forbidden
- release infrastructure mode: `OFF`
- Profile certificate-consumption follow-up: deferred
- Stage 2 authorized: `false`
