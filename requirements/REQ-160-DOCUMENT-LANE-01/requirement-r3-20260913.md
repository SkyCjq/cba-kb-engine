# REQ-160-DOCUMENT-LANE-01 - v1.6 Document Lane r3

Requirement revision: `r3-20260913-acceptance-gate-alignment`

Supersedes acceptance semantics in `r2-20260913-v155-production-refresh`.
The r2 artifact remains immutable historical evidence.

## Authority and baseline

- Production baseline: `v1.5.5-1`.
- Published code and baseline development SHA:
  `0b9c6e062616fc8d4349304ea483afdd917ce181`.
- Base branch: `codex/v1.5.2-draft2`.
- Feature branch: `feat/REQ-160-DOCUMENT-LANE-01`.
- Lane: `CODE_CONFIG`.
- `release_infrastructure_mode = OFF`.
- Optional Performance Overlay: `OFF`.
- Production access during this Requirement: `FORBIDDEN`.

Authority order: this r3 Requirement and `task.yaml`, then the unchanged r2
functional contract, then GitHub Code Truth at the frozen base.

## Revision reason

r3 aligns merge gates with actual project use:

- deterministic automated tests prove algorithm correctness;
- real corpus observations validate the available operational corpus;
- absence of a specific real-world occurrence does not imply incorrect code;
- no synthetic document is counted as real corpus.

The revision changes no production behavior, data structure, algorithm,
rights policy, archive layout, release infrastructure or canonical facts.

## Unchanged functional contract

All r2 requirements remain in force except for the A03, A04, A08 and A18
acceptance methods explicitly revised below:

- deterministic normalization, content addressing and canonical URL handling;
- exact hash dedup and idempotent reruns;
- immutable raw archive and derived text/provenance/dedup evidence;
- rights defaults and public-export blocking;
- controlled taxonomy;
- TXT, DOCX, PDF, Markdown, ima and Google Docs source behavior;
- Drive/Docs read-only transport and fail-closed errors;
- Facts ZERO DIFF;
- private data and secret boundaries;
- no direct ima API;
- no server-side WeChat scraping;
- no remote WeChat/CDN image download;
- no production write.

## A03 - Canonical URL revision behavior

The deterministic automated test suite MUST verify that documents sharing the
same normalized canonical URL but having different normalized content are
retained as separate immutable records with an explicit revision relationship.

Required behavior:

```text
same normalized canonical_url
+ different content_hash
= separate immutable records
+ revision relation
```

Real-corpus revision observations are recorded when naturally available but
are not required as a merge-blocking real fixture.

Automated hard gate:

`tests/test_document_lane.py::test_same_canonical_url_content_change_keeps_revision`

## A04 - Deterministic near-duplicate behavior

The algorithm remains frozen:

```text
Unicode 5-codepoint shingles
Jaccard >= 0.92
AND length ratio >= 0.90
=> review_required
```

The implementation MUST never silently merge or delete a near duplicate.
Ambiguous collisions fail closed to review.

The automated deterministic suite MUST cover:

- above-threshold positive behavior;
- below-threshold negative behavior;
- exact threshold and boundary behavior;
- exact duplicate versus near-duplicate separation;
- deterministic repeated evaluation.

Naturally occurring real-corpus near duplicates are observational evidence
only and are not required for merge eligibility.

Hard gate tests:

`tests/test_document_lane.py`

## A05-A07 - Rights, taxonomy and WeChat capture

Unchanged from r2:

- WeChat defaults to `copyrighted`;
- local files and Google Docs default to `private`;
- unknown sources remain blocked;
- only evidence-backed public classification may allow export;
- free-form tags remain review candidates only;
- WeChat clips are ingested without server-side fetching.

## A08 - ima file-export source contract

`capture_channel=ima_file_export` remains part of the canonical contract.

The deterministic automated suite MUST verify end-to-end ingestion of a
caller-supplied local ima export through Document Lane into the private
archive.

The implementation MUST contain no direct ima authentication, body-export API
or network dependency:

```text
caller-supplied local file only
NO ima auth
NO ima API
NO ima network body export
```

Required archive evidence:

- `record.json`;
- `text.txt`;
- `provenance.json`;
- `dedup.json`;
- `raw/`;
- `attachments/`.

Required record semantics:

- `capture_channel=ima_file_export`;
- default rights classification `unknown`;
- `public_export_allowed=false`;
- original raw bytes retained.

A real ima export becomes operational-validation evidence when the user's
workflow introduces one, but its current absence does not block the initial
v1.6 merge.

Hard gate tests:

- `tests/test_document_lane.py`
- `tests/test_cli_document_lane.py`

## A09-A17 - Source, archive, security and determinism gates

Unchanged from r2:

- TXT encoding behavior;
- DOCX paragraph/table/media provenance;
- PDF text extraction and `OCR_REQUIRED`;
- Drive Docs tab topology and read-only transport;
- archive traceability;
- deterministic reruns;
- private-data/security boundaries;
- Facts ZERO DIFF.

## A18 - Real Private Instance corpus validation

Before Web Merge Review, Document Lane MUST be executed against all currently
available real documentary inputs selected for the release precheck.

The evidence MUST report actual counts by capture channel and document type.
Synthetic fixtures MUST NOT be counted as real corpus evidence.

The real corpus MUST NOT be empty. A fixed minimum number of WeChat articles is
not a software correctness gate.

Corpus growth after release does not require a new Engine release solely
because the source count changed.

Current observed release evidence:

```text
35 real WeChat browser clips
33 real local documents
3 real native Google Docs
9 image-only PDFs returning OCR_REQUIRED
```

These counts are evidence for this run, not permanent minimums or Engine
invariants.

## A19 - Native Google Docs

Unchanged: at least three real native Google Docs must be validated through
the read-only Drive/Docs transport. The current evidence is `3/3`, but the r3
Local Precheck must execute the live read again.

## A20-A23 - Capture, provenance, export guard and metrics

Unchanged:

- one batch action remains sufficient for bulk capture;
- duplicate source bodies and attachments remain traceable;
- private/copyrighted/unknown public export is blocked;
- the deterministic no-LLM path records tokens and cost as zero;
- latency and review metrics are recorded.

## A24 - Forbidden network paths

The implementation and runtime evidence MUST contain no:

- direct ima API;
- server-side WeChat scraping;
- remote WeChat/CDN image downloading;
- Drive/Docs write calls.

## Release-critical fixtures

```text
private_instance_contract
document_lane_private_manifest
real_document_corpus
drive_docs_real_min_3
rights_public_export_guard_set
facts_zero_diff_hash_set
```

`real_document_corpus` means all currently available real Private Instance
documents used for operational validation, with counts reported rather than a
fixed WeChat minimum.

`wechat_real_50` and `document_dedup_collision_set` are removed as real fixture
hard gates. Their underlying correctness remains mandatory through automated
tests for A02, A03 and A04.

## Exact allowed paths

- `requirements/REQ-160-DOCUMENT-LANE-01/requirement-r3-20260913.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task-brief.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task.yaml`
- `src/cba_kb/document_lane.py`
- `src/cba_kb/document_sources.py`
- `src/cba_kb/cli.py`
- `src/cba_kb/instance.py`
- `Makefile`
- `tests/test_document_lane.py`
- `tests/test_document_sources.py`
- `tests/test_cli_document_lane.py`
- `tests/test_ingestion_kernel.py`
- `tests/test_engine_instance_boundary.py`
- `tests/test_cli_instance_config.py`
- `tests/test_security_guards.py`

The approved tests-only repair may modify only:

- `requirements/REQ-160-DOCUMENT-LANE-01/requirement-r3-20260913.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task-brief.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task.yaml`
- `tests/test_document_lane.py`
- `tests/test_cli_document_lane.py`

## Must not change

- `.github/workflows/offline-tests.yml`
- `requirements.lock`
- `config/taxonomy.yaml`
- `src/cba_kb/release.py`
- `src/cba_kb/drive.py`
- `src/cba_kb/transport.py`
- `src/cba_kb/native.py`
- `src/cba_kb/source_watcher.py`
- `scripts/prepare_production.py`
- `tests/test_release.py`
- `requirements/REQ-155-RELEASE-ORCH-01/**`
- production/sandbox/runtime/drive-map release semantics
- manifest/release_status/reservation/rollback contracts
- legacy `legacy/ingest_wechat.py`
- canonical business facts and published fact products
- all production implementation files for this repair

If a test exposes a production defect, stop with
`TEST_REPAIR_DISCOVERED_IMPLEMENTATION_DEFECT`. Do not hot-fix production code
inside this repair.

## Human Runbook

```text
DEV_WORKSPACE=/Users/skychengneo/Agent/CBA_kb_dev/REQ-160-DOCUMENT-LANE-01
LOCAL_RUNTIME_ROOT=/Users/skychengneo/Agent/CBA_kb
PRIVATE_INSTANCE_ROOT=/Users/skychengneo/Agent/CBA_kb_instance
```

`local_prepare_command = NOT_IMPLEMENTED`

`publish_command = NOT_IMPLEMENTED_FOR_REQ_160`

`rollback_command = NOT_IMPLEMENTED_FOR_REQ_160`

No production publication or rollback target exists for this Requirement.

## Completion gates

- r3 artifact and tests-only repair are committed and pushed.
- Frozen focused tests pass.
- GitHub Actions full regression passes on the new head.
- r3 Conditional Local Precheck reruns all currently available real corpus.
- Facts ZERO DIFF, secret scan and must-not-change checks pass.

No merge or production write is authorized by this artifact.
