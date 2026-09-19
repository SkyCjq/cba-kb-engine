# REQ-180-IDENTITY-COVERAGE-WEB-EVIDENCE-01

## Official Web Evidence Enrichment & Scalable Human Review

Revision: `r1-20260918-web-evidence-and-batch-review`

Lane: `CODE_CONFIG`

Risk: `HIGH`

Development baseline:
`02ad7f8061d184dfefd2fa802073154eb83fa9f5`

Upstream Requirement:
`REQ-180-IDENTITY-COVERAGE-01`

Upstream revision:
`r1-20260917-full-private-identity-coverage`

Prerequisite tooling:
`REQ-180-IDENTITY-COVERAGE-SUPPORT-01`

Prerequisite tooling state:
`MERGED`

Status: `FROZEN_SPEC`

Stage 2 implementation authorized: `false`

## Objective

Reduce the real human identity-review workload for the complete private MASTER
universe by adding deterministic, provenance-preserving official-web evidence
enrichment, candidate grouping, scalable human batch/group approval, and an
Excel-native human working projection.

This requirement does not change Identity v1.8 relation authority.

It does not authorize a web result, search ranking, exact name, team
continuity, LLM inference, or confidence score to create `same` automatically.

The frozen authority chain remains:

`MASTER + canonical registry`
`-> candidate-only evidence enrichment`
`-> immutable review_packet.json`
`-> explicit human authority`
`-> reviewed_decisions.json`
`-> candidate registry / coverage ledger / certificate`
`-> later controlled canonical apply`

The current `20260918T045146Z` review universe is audit history only after this
requirement is implemented. It must not be overwritten. A new enriched review
universe must be generated from the same verified MASTER/registry authorities.

## Why This Requirement Exists

The current deterministic review universe contains:

- MASTER record keys: `3465`
- already `RESOLVED_SAME`: `112`
- `EXISTING_IDENTITY_CANDIDATE`: `40`
- `NO_SAFE_CANDIDATE`: `3313`
- current review rows: `3353`

A design that requires the owner to research and decide all 3353 rows manually
is not operationally scalable.

The correct optimization target is not "remove human authority". It is:

1. automate evidence discovery and capture;
2. convert evidence into candidate-only structured claims;
3. group records that have strong common official evidence;
4. stratify ambiguous/conflicting records;
5. permit explicit human approval over an exact frozen batch/group membership;
6. expand that human authority back into row-level reviewed decisions.

## Non-Negotiable Identity Semantics

The following remain unchanged:

- `player_uid` is opaque and stable.
- existing UIDs must not be renumbered or regenerated.
- `candidate_group_id != player_uid`.
- exact name never auto-creates `same`.
- approved alias never auto-creates `same`.
- same team never auto-creates `same`.
- adjacent season never auto-creates `same`.
- jersey number never auto-creates `same`.
- fuzzy matching never auto-creates `same`.
- LLM inference never auto-creates `same`.
- web search ranking never auto-creates `same`.
- absence of a web result never proves a player does not exist.
- `not_same` remains negative relation evidence only.
- every final relation still requires explicit human authority.
- new identity creation still requires explicit human-approved opaque UID,
  canonical name, evidence, and UID-allocation attestation.
- unreviewed records in a candidate group must never be auto-linked.

## Official Web Source Policy

### Source Tier A0 — Association Registration Authority

Default allowed domains:

- `cba.net.cn`
- `www.cba.net.cn`
- `cbanetcdn.cba.net.cn`

Examples include:

- annual national athlete registration notices;
- official registration PDFs;
- official registration-management documents;
- official public notices that explicitly identify athletes or registration
  units.

### Source Tier A1 — CBA League Registration Authority

Default allowed domains:

- `cbaleague.com`
- `www.cbaleague.com`
- `image.cbaleague.com`

Examples include:

- season domestic-player registration information;
- league registration notices;
- official roster/registration PDFs;
- official free-agent and transaction notices when identity-relevant.

### Source Tier A2 — CBA League Player/Data Authority

Default allowed domains:

- `cbaleague.com`
- `www.cbaleague.com`

Examples include structured official player/data pages where the page or
underlying structured response is clearly associated with a player entity.

### Source Tier B0 — Curated Official Club Source

No club domain is trusted by default in r1.

A club website/domain may become `B0` only through a private,
human-reviewed source registry entry that records:

- canonical club identity;
- domain;
- reason the domain is official;
- approving human event;
- approval timestamp.

Adding a new B0 domain is source-policy maintenance and must not happen
silently during candidate generation.

### Source Tier D0 — Discovery Only

Search engines, mainstream media, encyclopedias, forums, social posts, search
snippets, and other secondary sources are discovery aids only in r1.

They may:

- suggest an official URL to fetch;
- suggest a query refinement;
- highlight a possible conflict for human attention.

They must not by themselves:

- become an identity evidence item;
- create a candidate relation;
- create or upgrade `same`;
- make a row batch-approval eligible.

If a discovery result points to an allowed official source, the official source
must be fetched, hashed, parsed, and independently represented as the evidence.

## Public-Web Access Boundary

Only public, unauthenticated web resources may be fetched.

Forbidden:

- credentials;
- logged-in sessions;
- cookies copied from a browser profile;
- CAPTCHA bypass;
- paywall bypass;
- private APIs;
- hidden user tokens;
- automated login;
- scraping a search engine as a data authority.

HTTP access is read-only.

Default network policy:

- methods: `GET`, `HEAD`;
- redirects allowed only to an allowed/validated destination;
- timeout required;
- bounded response size required;
- per-domain rate limit required;
- bounded retries required;
- response bytes hashed before parsing.

No new Python dependency is required. Existing project dependencies and Python
standard library are sufficient.

## Discovery Contract

Discovery and authority are separate.

Discovery may use:

- frozen query templates;
- official-site internal search;
- official links followed from already-fetched official pages;
- an external agent/browser/search tool that produces candidate URLs;
- existing source URLs already present in CBA-KB provenance.

A discovery record may contain:

- `query`
- `discovered_url`
- `discovered_domain`
- `discovered_at`
- `discovery_provider`
- `title_hint`

A discovery record has:

`authority = DISCOVERY_ONLY`

It has no identity-authority weight until the target official page is fetched
and converted into a validated evidence item.

## Web Evidence Artifact Contract

All real web evidence remains private.

Suggested private root:

`/Users/skychengneo/Agent/CBA_kb_instance/data/player_identity/web_evidence/`

Each run uses an immutable directory.

Required artifacts:

- `discovered_urls.json`
- `web_evidence_items.json`
- `web_evidence_manifest.json`
- `web_enriched_candidates.json`
- `web_evidence_conflicts.json`
- `review_evidence_summary.json`
- `review_batch_plan.json`

Raw snapshots remain private under a `raw/` subdirectory.

No raw web body enters Git or the machine handoff JSON.

### Evidence Item Minimum Fields

Each accepted official evidence item contains at least:

- `evidence_id`
- `source_tier`
- `source_kind`
- `source_url`
- `source_domain`
- `fetched_at`
- `http_status`
- `content_type`
- `content_sha256`
- `raw_snapshot_path`
- `published_at` or `null`
- `extractor_version`
- `claims`
- `extraction_warnings`

`evidence_id` must be deterministic from stable provenance inputs and must not
encode a player UID.

### Claim Contract

A claim contains at least:

- `claim_type`
- `raw_value`
- `normalized_value`
- `source_locator`
- `claim_confidence`
- `machine_extracted`
- `human_verified`

Allowed identity-relevant claim types in r1:

- `OFFICIAL_PLAYER_NAME`
- `OFFICIAL_BIRTH_DATE`
- `OFFICIAL_REGISTRATION_UNIT`
- `OFFICIAL_SEASON`
- `OFFICIAL_TEAM`
- `OFFICIAL_JERSEY_NUMBER`
- `OFFICIAL_SOURCE_DECLARED_PERSON_ID`
- `OFFICIAL_TRANSACTION_OR_REGISTRATION_EVENT`

Team, season, jersey number, and name alone are contextual claims, not unique
identity authority.

## External Identifier Contract

An external identifier may be treated as a strong identity claim only if the
official source clearly represents it as a player/person entity identifier.

The following are not automatically external person IDs:

- article/news IDs;
- page IDs;
- PDF filenames;
- URL slugs;
- registration notice IDs;
- arbitrary database row numbers;
- search-result IDs.

If the source semantics do not prove that the identifier is person-scoped, it
must remain an ordinary source locator.

External identifier namespace is mandatory, for example:

`CBA_OFFICIAL_PLAYER_ID:<value>`

The same raw value in two different namespaces is not equal authority.

## Candidate Enrichment Classes

Web evidence never creates a final relation. It assigns a candidate evidence
class.

### `W1_OFFICIAL_PERSON_ID_EXACT`

Requirements:

- exact official source-declared person identifier;
- allowed Tier A0/A1/A2 or approved B0 source;
- same identifier namespace;
- no conflicting official person identifier;
- no conflicting official birth-date evidence when birth date is available.

This is the strongest candidate class.

### `W2_OFFICIAL_BIO_MULTI_SOURCE`

Requirements:

- exact normalized player name;
- exact official birth date;
- at least one additional compatible official identity/context claim;
- at least two distinct official evidence artifacts from separate publication
  events or structured official entities;
- no conflicting official claim;
- exactly one candidate identity/group remains after conflict checks.

This is strong candidate evidence but still not automatic identity authority.

### `W3_OFFICIAL_CONTINUITY`

Examples:

- exact name plus official team/season continuity;
- exact name across official registration rosters;
- exact official alias continuity without unique person identifier.

This class remains ordinary manual-review evidence.

### `W4_DISCOVERY_SUPPORT`

Secondary/discovery-only support.

It may help a reviewer but must not independently create or upgrade an
identity candidate.

### `WX_CONFLICT`

Any material official-source conflict, including:

- incompatible birth dates;
- two distinct official person identifiers for a proposed same identity;
- simultaneous same-name records that cannot be safely separated;
- official evidence that points to different people;
- OCR/mojibake/name-corruption ambiguity affecting identity interpretation.

Conflict rows are never batch-approval eligible.

## Candidate Generation Rules

Web enrichment may transform a prior `NO_SAFE_CANDIDATE` into:

- `EXISTING_IDENTITY_CANDIDATE`;
- `NEW_IDENTITY_CANDIDATE`;
- `SOURCE_EXCEPTION_CANDIDATE` only when a source defect is explicitly
  documented.

The transformation remains candidate-only.

### Existing Identity Candidate

A web-enriched existing candidate must bind:

- explicit existing `candidate_player_uid`;
- evidence refs;
- evidence class;
- conflict status.

### New Identity Candidate Group

Web evidence may group multiple historical records into one
`NEW_IDENTITY_CANDIDATE` group when evidence supports a common proposed
identity.

The group must have:

- deterministic `candidate_group_id`;
- exact member review IDs / record keys privately;
- evidence refs;
- evidence class;
- conflict status.

The group ID must never be converted into `player_uid`.

## Search Exhaustion Contract for `NO_SAFE_CANDIDATE`

A `NO_SAFE_CANDIDATE` row may be tagged:

`OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH`

only if:

- every required Tier A collector applicable to that season completed;
- no collector failure is unresolved;
- no official candidate survived conflict checks;
- no suspicious text-encoding/OCR defect affects the key identity fields;
- all attempted official source URLs/queries are recorded in the private
  evidence manifest.

This tag means:

"no safe candidate was found under the frozen search policy"

It does not mean:

"the player has no identity" or "no matching person exists on the internet".

## Human Review Scaling Contract

Human authority remains mandatory.

This requirement adds two scalable human-authority mechanisms without changing
the resulting row-level decision semantics.

### 1. Human Group Approval

A human may approve one `NEW_IDENTITY_CANDIDATE` group in one explicit event
when:

- exact group membership is displayed;
- all member rows are covered by the event;
- evidence class is W1 or W2;
- conflict count is zero;
- the human supplies one approved opaque `player_uid`;
- the human supplies one approved canonical name;
- the human supplies UID-allocation attestation;
- the human supplies `reviewed_at`;
- the event explicitly states that every listed group member is approved as
  `PROPOSED_SAME`.

The system may then expand the one human group event into per-row reviewed
decisions for exactly those listed members.

This expansion is not auto-linking because the human event explicitly covers
the exact member set.

Unlisted or later-added group members remain unreviewed.

### 2. Human Batch Approval

A human may approve a machine-defined homogeneous batch only when the batch
predicate is frozen and every exact member is displayed.

Eligible r1 batch predicates:

#### `BATCH_EXISTING_W1`

- proposal type `EXISTING_IDENTITY_CANDIDATE`;
- evidence class `W1_OFFICIAL_PERSON_ID_EXACT`;
- exactly one existing candidate UID;
- zero conflict;
- no text-corruption warning.

#### `BATCH_EXISTING_W2`

- proposal type `EXISTING_IDENTITY_CANDIDATE`;
- evidence class `W2_OFFICIAL_BIO_MULTI_SOURCE`;
- exactly one existing candidate UID;
- zero conflict;
- no text-corruption warning.

#### `BATCH_NO_SAFE_SEARCH_EXHAUSTED`

- proposal type `NO_SAFE_CANDIDATE`;
- search status `OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH`;
- zero collector failure;
- zero text-corruption warning;
- zero identity conflict.

A batch approval event contains:

- `batch_approval_id`
- `review_packet_sha256`
- `web_evidence_manifest_sha256`
- `batch_predicate`
- exact private member review IDs
- exact member count
- human decision
- human note
- reviewed_at
- human authority attestation

The system may expand the human batch event into row-level human decisions
only for the exact listed members.

A batch must never include:

- `WX_CONFLICT`;
- unresolved collector failures;
- source exceptions;
- `PROPOSED_SEPARATE`;
- a new identity group requiring UID allocation;
- records with text/OCR corruption warning;
- rows whose machine proposal changed after the batch was prepared.

## Human Decision Expansion

Generated row-level decisions from group/batch authority must preserve the
existing support contract:

- each row has a valid `human_decision`;
- each row has `reviewed_at`;
- each row remains bound to the exact immutable `review_packet.json`;
- machine-owned fields remain byte/semantic-equal to the packet;
- `REJECT` never infers an opposite relation;
- `UNDECIDED` remains unresolved.

The row `human_note` must reference the human authority event ID.

The expansion tool must fail closed if:

- review packet hash changed;
- evidence manifest hash changed;
- batch/group membership changed;
- any row is missing;
- any row was already decided incompatibly;
- any eligibility predicate is no longer true.

## Excel-Native Human Working Projection

Excel compatibility is required because UTF-8 CSV auto-detection is not
reliable in common desktop spreadsheet workflows.

Add a non-canonical `.xlsx` human working projection using the already-present
`openpyxl` dependency.

Artifact:

`review_packet_workbook.xlsx`

The workbook is not identity authority.

Required sheets:

- `Review`
- `Groups`
- `Batches`
- `Evidence_Index`
- `Instructions`

### `Review`

Contains the canonical review rows plus human-editable fields.

Machine-owned cells are visually locked/protected and must be verified on
import.

### `Groups`

One row per eligible candidate group with:

- group ID;
- member count;
- evidence class;
- conflict count;
- evidence summary references;
- human group decision fields.

### `Batches`

One row per eligible batch with:

- batch ID;
- frozen predicate;
- member count;
- evidence class;
- conflict count;
- human batch decision fields.

### `Evidence_Index`

Read-only summary with clickable official source URLs and private evidence IDs.
It must not embed full copyrighted source bodies.

### `Instructions`

Explains authority boundaries and allowed edits.

## XLSX Round Trip

Add an importer that consumes:

- immutable `review_packet.json`;
- immutable `web_evidence_manifest.json`;
- edited `review_packet_workbook.xlsx`.

It must:

1. validate every machine-owned cell against the immutable authority;
2. validate group/batch membership and predicates;
3. validate human group/batch events;
4. expand authorized group/batch decisions to exact row-level human fields;
5. emit a canonical UTF-8 review CSV or equivalent intermediate accepted by
   the existing `identity-review-validate` flow;
6. never modify the original packet or workbook silently.

The existing `review_packet.csv` remains valid canonical human projection for
non-Excel workflows.

The XLSX path is an additional human UI, not a replacement identity authority.

## Encoding Safety

All JSON and text artifacts use UTF-8.

CSV validator compatibility must be explicitly tested for:

- UTF-8 without BOM;
- UTF-8 with BOM if the new importer emits or accepts it.

No implementation may silently normalize or repair mojibake in machine-owned
identity fields.

Suspicious text must be surfaced as a warning/conflict and returned for source
repair.

## LLM Boundary

LLM/agent assistance is allowed for:

- generating search queries;
- discovering official URLs;
- summarizing already-fetched evidence for human display;
- flagging likely conflicts;
- suggesting review priority.

LLM/agent output is not itself identity evidence.

An LLM must not:

- create final `same`;
- allocate a final UID;
- convert secondary-source text into Tier A/B authority;
- suppress a conflicting official claim;
- fill a missing human decision;
- fabricate evidence refs.

## Evidence Provenance and Caching

Every fetched official resource must be reproducible through:

- source URL;
- fetch timestamp;
- content SHA256;
- content type;
- raw snapshot path;
- extractor version.

If an upstream page changes, the old raw snapshot remains immutable and the
new fetch receives a new evidence item/version.

Candidate generation must bind to exact evidence-manifest hash.

## Private Data Boundary

Real identity enrichment outputs remain in the Private Instance.

Forbidden in Git:

- real record mappings;
- real player mappings;
- raw official page snapshots;
- real review decisions;
- real batch/group membership;
- real evidence bundle bodies.

Git tests use synthetic fixtures only.

Machine handoff may include only:

- private paths;
- artifact hashes;
- source-domain aggregate counts;
- source-tier aggregate counts;
- candidate-class aggregate counts;
- batch/group counts;
- conflict counts;
- failure counts.

## Required CLI Surface

Freeze these new commands:

- `identity-web-evidence-discover`
- `identity-web-evidence-collect`
- `identity-web-evidence-enrich`
- `identity-review-workbook-export`
- `identity-review-workbook-import`

Existing commands remain:

- `identity-coverage-inventory`
- `identity-coverage-candidates`
- `identity-review-prepare`
- `identity-review-validate`
- `identity-review-apply`
- `identity-coverage-reconcile`
- `identity-coverage-certify`

The new enrichment pipeline may feed the existing candidate/review flow but
must not bypass it.

## Determinism

Given identical:

- MASTER bytes;
- canonical registry bytes;
- frozen source registry;
- discovery URL set;
- fetched source bytes;
- extractor version;
- evidence policy version;

the following must be deterministic:

- evidence IDs;
- extracted normalized claims;
- conflict classification;
- enriched candidate grouping;
- evidence class;
- batch eligibility;
- group membership;
- batch membership;
- workbook machine-owned content.

Network discovery/fetch itself is time-varying and is therefore frozen by the
resulting fetched-byte manifest before candidate generation.

## Failure / Corridor Exit Conditions

Return to Web Audit and do not broaden scope if any occurs:

- official-source policy needs a new default domain;
- login/authentication is required;
- a CAPTCHA or anti-bot bypass would be required;
- a new external dependency is required;
- official sources conflict materially;
- evidence-class semantics need extension;
- auto-SAME would be required to meet the goal;
- candidate group membership is ambiguous;
- batch predicate needs extension;
- text/OCR corruption affects identity interpretation;
- source policy cannot be verified;
- private data would need to enter Git;
- production access would be required.

## Synthetic Test Contract

Repository tests must cover at least:

### Source policy

- A0/A1/A2 default domains accepted;
- unapproved club domain rejected as B0;
- D0 source cannot become authoritative evidence;
- redirect to unapproved domain rejected;
- login/cookie/private API path rejected;
- article/news ID not treated as person ID;
- explicit official person ID namespace accepted.

### Fetch/provenance

- content SHA deterministic;
- evidence ID deterministic;
- changed source bytes produce new evidence version;
- raw snapshots remain immutable;
- invalid UTF-8 or binary content handled safely;
- PDF and HTML fixtures supported;
- response-size and timeout guards enforced.

### Candidate enrichment

- W1 exact official ID;
- W2 exact name + DOB + multi-source evidence;
- W3 continuity remains manual;
- D0 remains discovery-only;
- official conflict -> WX_CONFLICT;
- no web result does not create negative identity;
- no automatic same;
- no automatic UID;
- prior NO_SAFE may become candidate-only existing/new proposal.

### New identity grouping

- deterministic group IDs;
- candidate_group_id never equals player_uid by transformation;
- ambiguous group membership fails closed;
- no unreviewed group row auto-links.

### Search exhaustion

- all required collectors complete -> eligible no-safe search status;
- one collector failure -> not eligible;
- encoding/OCR warning -> not eligible;
- official conflict -> not eligible.

### Batch/group human authority

- W1 existing batch eligible;
- W2 existing batch eligible;
- conflict row not batch eligible;
- new identity group requires human UID/name/attestation;
- exact membership bound to packet/evidence hashes;
- changed packet hash invalidates event;
- changed evidence hash invalidates event;
- missing member fails closed;
- group event expands only exact members;
- batch event expands only exact members;
- every expanded row contains human decision and reviewed_at;
- reject does not infer opposite relation.

### Workbook

- Unicode Chinese text round-trips through XLSX without mojibake;
- machine-owned cell edit fails import;
- human-owned edits import correctly;
- group/batch events import deterministically;
- workbook is non-canonical;
- evidence links preserved;
- no source body embedded;
- UTF-8 CSV no-BOM path remains valid;
- UTF-8 BOM compatibility is explicitly tested.

## Implementation Scope

Allowed Git paths:

- `requirements/REQ-180-IDENTITY-COVERAGE-WEB-EVIDENCE-01/requirement-r1-20260918.md`
- `requirements/REQ-180-IDENTITY-COVERAGE-WEB-EVIDENCE-01/task-brief.md`
- `requirements/REQ-180-IDENTITY-COVERAGE-WEB-EVIDENCE-01/task.yaml`
- `src/cba_kb/identity_web_evidence.py`
- `src/cba_kb/identity_coverage.py`
- `src/cba_kb/cli.py`
- `tests/test_identity_web_evidence.py`
- `tests/test_cli_identity_web_evidence.py`

No other Git path is authorized.

## Must Not Change

Do not modify:

- `src/cba_kb/player_identity.py`
- MASTER implementation or authoritative MASTER bytes
- Facts/Events
- Mention
- Profile
- document lane
- consumer package
- release orchestration
- production configuration
- canonical private registry directly
- existing frozen upstream Drive artifacts

No dependency change is authorized.

## Private Acceptance

Before this feature may be used to replace the current 3353-row review
universe, private acceptance must prove:

- official source policy enforcement;
- public-only network access;
- deterministic evidence manifest after fetch freeze;
- no automatic same;
- no automatic UID;
- candidate grouping with zero silent membership expansion;
- batch/group human authority expansion correctness;
- XLSX Chinese text round-trip correctness;
- no machine-owned workbook tampering accepted;
- no canonical registry mutation;
- no MASTER mutation;
- no private material in Git;
- no production write.

Private acceptance may use a bounded real-data canary before full enrichment.

## Success Criterion for This Requirement

The implementation is successful when the system can transform the current
manual-review problem from:

`3353 isolated rows requiring individual research`

into:

`machine-collected official evidence + grouped candidates + conflict strata +`
`explicit human batch/group authority + residual per-row review`

without changing final identity authority semantics.

No target percentage reduction is frozen in r1 because the obtainable official
evidence is an empirical property of the source universe.

## Safety

- automatic identity decision: forbidden
- automatic final UID: forbidden
- public web only
- private-data-to-Git: forbidden
- canonical identity direct write: forbidden
- production access: forbidden
- release infrastructure mode: `OFF`
- no new dependency
- existing 20260918T045146Z review universe: immutable audit history
