# REQ-160-DOCUMENT-LANE-01 - v1.6 Document Lane

Requirement revision: `r2-20260913-v155-production-refresh`

Requirement URL: https://docs.google.com/document/d/14VMFN_HVc9eA_aEZFqaF9oUpGjzW_EQ_X8MnFbJTcpk/edit?usp=drivesdk

Exported requirement SHA-256:
`28fc2acd3c050a1df40905a2cd504eadafc88e76c3e487decc1e60e420506473`

## Freeze authority and baseline

- Production baseline: `v1.5.5-1`.
- Published code and frozen development base:
  `0b9c6e062616fc8d4349304ea483afdd917ce181`.
- Base branch: `codex/v1.5.2-draft2`.
- Feature branch: `feat/REQ-160-DOCUMENT-LANE-01`.
- Lane: `CODE_CONFIG`.
- `release_infrastructure_mode = OFF`.
- Optional Performance Overlay: `OFF`.
- Production access during this Requirement: `FORBIDDEN`.

Authority order is this frozen Requirement and `task.yaml`, then GitHub Code
Truth at the frozen base, then implementation details. Live `release_status.json`
remains production authority and is not modified.

## Goal

Build a private documentary-evidence lane on the v1.5.5 Engine/Private Instance
boundary. It archives source material without changing canonical facts or
release infrastructure.

Every canonical document record exposes:

`doc_id / canonical_url / published_at / captured_at / content_hash / capture_channel / rights`

`capture_channel` is one of:

- `wechat_browser_clip`
- `ima_file_export`
- `local_file`
- `google_drive_doc`

`captured_at` is required ISO-8601. `published_at` remains null when unknown.
`doc_id` is `doc_` plus the first 24 lowercase hex characters of SHA-256 over
normalized document text.

## Source scope

In scope:

1. Already captured WeChat/browser-clipped Markdown.
2. Caller-supplied local ima export files.
3. Local PDF, DOCX and TXT files.
4. Native Google Drive Docs through the existing private OAuth/Drive boundary.
5. Exact hash dedup, canonical-URL revision links, and deterministic
   near-duplicate review.
6. Controlled taxonomy validation.
7. Rights metadata and public-export blocking.
8. Immutable private archive, provenance, metrics and deterministic evidence.

Out of scope:

- Direct ima API body export.
- Server-side `mp.weixin.qq.com` scraping.
- Remote WeChat/CDN image downloading.
- OCR for scanned/image-only PDF.
- LLM free-form tags entering canonical taxonomy.
- Canonical Facts/Event/Stats/Claim/Profile generation.
- Public/community corpus export.
- Production release orchestration, manifest, reservation, rollback, or
  `release_status` changes.

If direct ima API or server-side WeChat fetching is requested later, stop with
`SOURCE_CONTRACT_REVISION_REQUIRED` and re-freeze before code changes.

## Archive contract

Private input root:

`$PRIVATE_INSTANCE_ROOT/inbox/documents/`

Private archive root:

`$PRIVATE_INSTANCE_ROOT/data/document_lane/archive/`

Each accepted archive contains:

- `record.json`
- `text.txt`
- `provenance.json`
- `dedup.json`
- `raw/`
- `attachments/`

Raw evidence is immutable. Derived text may be rebuilt but never replaces raw
evidence.

## Deterministic normalization and dedup

Normalization is UTF-8, Unicode NFKC, LF newlines, normalized horizontal
whitespace, paragraph-order preserving, and has no semantic rewriting.

`content_hash = SHA-256(normalized text UTF-8)`.

Canonical URLs lower-case scheme/host, remove fragments, remove only `utm_*`
parameters, preserve all other parameters, and sort the remaining parameters.
WeChat identity parameters are retained.

Dedup behavior:

- Identical `content_hash`: exact merge, idempotent, preserving every capture
  and attachment.
- Same normalized `canonical_url` and changed content: retain both immutable
  records and create a revision relation.
- Near duplicate: 5-codepoint character-shingle Jaccard `>= 0.92` and length
  ratio `>= 0.90`; create `review_required`, never silently delete or merge.
- Ambiguous collision: fail closed to review.

## Rights and taxonomy

`rights.classification` is one of `public / copyrighted / private / unknown`.

`rights.public_export_allowed` defaults to false. Defaults are:

- WeChat: `copyrighted`
- local file: `private`
- Google Drive Doc: `private`
- ima/unknown: `unknown`

Only an evidence-backed `public` classification may set public export to true.

Canonical tags must exist under `topic` in `config/taxonomy.yaml`. Unknown tags
are review candidates only and cannot mutate canonical taxonomy.

## Google Drive/Docs read contract

1. `Drive files.get(file_id)` for metadata and canonical URL.
2. `Docs documents.get(documentId, includeTabsContent=true)` for structure.
3. Traverse `Document.tabs` recursively in stable order and preserve tab ID,
   title, nesting, body text and complete structural snapshot.
4. Reuse the existing Private Instance Drive/Docs clients and transport retry
   reset behavior. Do not construct a second OAuth/API stack.
5. `NativeDocs.get` and `Drive.get_managed_doc` prefix helpers are not used as
   the multi-tab parser.
6. Normal read scale is two external calls per Doc and no folder-wide listing.
7. Retryable transport failures use the inherited bounded retry; partial,
   malformed or missing-body documents fail closed with no accepted archive.
8. No Drive/Docs write method is invoked.

## File parser contract

- TXT: UTF-8 or UTF-8-SIG only; unsupported encoding becomes review.
- DOCX: paragraphs and tables in source order; retain relationship/media
  provenance; raw DOCX remains authority.
- PDF: deterministic `pypdf` text extraction; no text layer becomes
  `OCR_REQUIRED`; raw PDF is retained.
- WeChat clip Markdown: UTF-8; preserve front matter, body, source URL and
  attachment references; no remote image requests.
- ima file export: caller-supplied local evidence; no ima auth or network.

## Facts isolation / ZERO DIFF

Document Lane must not write or transform `MASTER.xlsx`,
`CBA_注册领域_六表.xlsx`, `CBA_球员注册_EVENTS.xlsx`,
`CBA_外籍球员注册_SNAPSHOTS.xlsx`, or canonical Facts/Event/Transaction/Stats/
Claim objects. The ingestion kernel policy `canonical_write_allowed=False`
remains authoritative.

## Acceptance criteria

- A01 stable deterministic canonical metadata schema
- A02 idempotent exact hash dedup
- A03 canonical URL revisions retained
- A04 deterministic near duplicates require review
- A05 private/copyrighted/unknown public export blocked
- A06 free-form taxonomy cannot enter canonical taxonomy
- A07 WeChat Markdown imports without server-side fetch
- A08 ima file export imports without ima API access
- A09 deterministic TXT fixture
- A10 DOCX text/table/media provenance fixture
- A11 text PDF passes and image-only PDF returns `OCR_REQUIRED`
- A12 Drive Docs preserve tab topology and text provenance
- A13 Drive docs reuse read transport, read-only, fail closed
- A14 raw/text/provenance/dedup traceable from one `doc_id`
- A15 reruns byte-stable except explicit runtime metrics
- A16 no private IDs/content/credentials in tracked Engine files or CI logs
- A17 Facts-layer before/after hashes are ZERO DIFF
- A18 at least 50 real WeChat articles plus 20 local documents
- A19 at least 3 real native Drive Docs
- A20 per-document user capture action `<= 1`
- A21 duplicate source bodies/attachments remain traceable
- A22 public-export guard blocks private/copyrighted/unknown
- A23 token/cost/latency/review metrics recorded; no-LLM token/cost are zero
- A24 direct ima API and server-side WeChat scraping absent

## Exact allowed paths

- `requirements/REQ-160-DOCUMENT-LANE-01/requirement-r2-20260913.md`
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
- v1.5.5 CBA source-watcher source contract
- canonical business-fact semantics and published fact products

## Release-critical fixtures

- `private_instance_contract`
- `document_lane_private_manifest`
- `wechat_real_50`
- `local_documents_real_20`
- `drive_docs_real_min_3`
- `document_dedup_collision_set`
- `rights_public_export_guard_set`
- `facts_zero_diff_hash_set`

Conditional Local Precheck is mandatory after CI GREEN and before Web Merge
Review.

## Human Runbook

```text
DEV_WORKSPACE=/Users/skychengneo/Agent/CBA_kb_dev/REQ-160-DOCUMENT-LANE-01
LOCAL_RUNTIME_ROOT=/Users/skychengneo/Agent/CBA_kb
PRIVATE_INSTANCE_ROOT=/Users/skychengneo/Agent/CBA_kb_instance
```

`local_prepare_command = NOT_IMPLEMENTED`

`publish_command = NOT_IMPLEMENTED_FOR_REQ_160`

`rollback_command = NOT_IMPLEMENTED_FOR_REQ_160`

This Requirement performs no production write and freezes no v1.6 production
publication or rollback target.

## Freeze disposition

`READY_TO_CODE`
