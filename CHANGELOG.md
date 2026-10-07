# Changelog

All notable changes to the CBA-KB Engine are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.2] - 2026-10-07

### Added
- Apache-2.0 License, NOTICE, DATA_LICENSE.md, and THIRD_PARTY_DATA.md.
- Issue and PR templates, CONTRIBUTING.md with DCO and Engine boundary rules, SECURITY.md.
- Structured docs reorganization (`guide/`, `operations/`, `architecture/`, `reference/`, `history/`).
- Bilingual operational guide (`docs/operations/OPERATIONS.md` and `.zh-CN.md`).
- Offline probe tests and CI checks for link validity and CLI command existence.

### Changed
- Synchronized package version to `2.0.2` across `pyproject.toml`, `src/cba_kb/__init__.py`, and README.
- Sanitized historical records and archived legacy validation assets into `docs/history/`.
- Pruned stale branches and hardened repository governance.

## [2.0.1] - 2026-10-06

### Added
- Drive placement enforcement guard and validation tests (`REQ-202-DRIVE-PLACEMENT-ENFORCEMENT-01`).
- Bounded repair rounds R1-R4 and D1-D6 for placement safety.

## [2.0.0] - 2026-10-05

### Added
- Statement and Claim extraction and verification layer (`REQ-190`).
- Source intake normalization contract v1.
- Read-only Research View synthesis (`cba-kb research-view`).
- Fail-closed consumer closure and profile verification.

## [1.8.1] - 2026-09-27

### Added
- Consumer closure repair and external consumer surface model (`REQ-181`).
- Identity projection and single-source consistency validation.

## [1.8.0] - 2026-09-24

### Added
- Multi-lane document evidence processing and deterministic xlsx build facts.
- Stable player identity registry schema and mention resolution.

## [1.6.1] - 2026-09-13

### Changed
- Current-state document lineage and migration controls.

## [1.5.3] - 2026-09-12

### Added
- Event closure verification and registration semantics enforcement.

## [1.5.2] - 2026-09-11

### Added
- Deterministic candidate synchronization and source discrepancy audit.

## [1.5.1] - 2026-09-11

### Added
- Initial baseline verification and automated ingestion controls.
