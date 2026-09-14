# REQ-160-DOCUMENT-LANE-01 Task Brief

Requirement revision: `r3-20260913-acceptance-gate-alignment`

Task: apply the approved r3 acceptance-gate alignment and tests-only repair on
top of `feat/REQ-160-DOCUMENT-LANE-01`, based on
`codex/v1.5.2-draft2@0b9c6e062616fc8d4349304ea483afdd917ce181`.

r3 reason: align merge gates with real corpus availability while preserving
deterministic correctness gates. No production behavior, data structure,
rights policy, algorithm or release infrastructure changes.

Relevant paths:

- `requirements/REQ-160-DOCUMENT-LANE-01/requirement-r3-20260913.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task-brief.md`
- `requirements/REQ-160-DOCUMENT-LANE-01/task.yaml`
- `tests/test_document_lane.py`
- `tests/test_cli_document_lane.py`

Approved repair:

- A03 is already covered by deterministic revision tests.
- Add A04 deterministic above/below/boundary and repeated-result coverage.
- Add A08 end-to-end local `ima_file_export` ingestion coverage and a CLI path.
- Do not modify production implementation.

Rules:

- No production access, publication, manifest, reservation, rollback or
  `release_status` change.
- Raw evidence remains immutable.
- Exact dedup, URL revision, near-duplicate review, rights gates, taxonomy,
  Drive/Docs read-only behavior and Facts ZERO DIFF remain mandatory.
- A18 validates all currently available real Private Instance corpus; a fixed
  WeChat count is not an Engine correctness invariant.
- No synthetic fixture is counted as real corpus evidence.

Completion gates:

- focused tests pass;
- secret and must-not-change checks pass;
- r3 commit is pushed to the existing PR branch;
- GitHub Actions is green on the new head;
- r3 Conditional Local Precheck reruns the real corpus and all hard gates.
