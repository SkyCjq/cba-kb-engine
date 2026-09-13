# REQ-160-DOCUMENT-LANE-01 Task Brief

Task: implement a deterministic, private-only Document Lane on top of
`codex/v1.5.2-draft2@0b9c6e062616fc8d4349304ea483afdd917ce181`.

Relevant paths:

- `src/cba_kb/document_lane.py`
- `src/cba_kb/document_sources.py`
- `src/cba_kb/cli.py`
- `src/cba_kb/instance.py`
- `Makefile`
- frozen tests under `tests/test_document_*.py` and the allowed boundary tests

Rules:

- No production access, publication, manifest, reservation, rollback or
  `release_status` change.
- Raw evidence is immutable; derived text never replaces it.
- Exact dedup is idempotent; URL revisions remain immutable; near duplicates
  require review.
- Private/copyrighted/unknown content cannot be publicly exported.
- Free-form tags cannot enter canonical taxonomy.
- Drive/Docs reads reuse the existing authenticated transport and never write.
- `canonical_write_allowed=False` remains authoritative.

Acceptance:

- focused tests cover A01-A17 and A21-A24;
- real Private Instance evidence must cover A18-A20 before merge review;
- facts-layer hashes must be unchanged;
- required CI must be green on the current PR head;
- any unavailable real fixture is reported as `ENVIRONMENT_BLOCKED`, never
  replaced by synthetic evidence.
