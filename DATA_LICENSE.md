# Data and Content Licensing Policy

This document defines the license terms, rights boundaries, and data policies applicable to materials within the CBA-KB repository and derived artifacts.

## 1. Summary of Layered Licensing

| Asset Layer | Scope | License / Rights Terms |
| :--- | :--- | :--- |
| **Engine Code & Tooling** | `src/`, `scripts/`, `automation/`, build configs | **Apache-2.0** |
| **Documentation & Guides** | `README.md`, `docs/`, markdown specifications | **CC BY 4.0** (Creative Commons Attribution 4.0 International) |
| **Synthetic Fixtures & Schemas** | `tests/fixtures/`, synthetic JSON/YAML schemas | **CC0-1.0 Universal** (Public Domain Dedication) |
| **Primary Sources & Upstream Data** | Historical league notices, articles, third-party records | **Proprietary / Third-Party Copyright** (Not licensed by this repo) |
| **Derived Research & Consumer Outputs** | Structured facts, claims, research views | **Rights-Bounded** (Subject to upstream rights & fair use boundaries) |

## 2. Public Engine vs. Private Instance Boundary

- **Public Engine (This Repository)**: Contains exclusively open-source code (Apache-2.0), public documentation (CC BY 4.0), and synthetic test fixtures (CC0-1.0). It includes non-secret legacy Drive file identifiers used as immutable operational references in `src/cba_kb/release.py`, `src/cba_kb/master.py`, `src/cba_kb/current_state.py`, and historical preparation scripts. Some referenced files are owner-only Drive artifacts, but the identifiers are not credentials or access tokens and grant no access by themselves. No credentials are included.
- **Private Instance**: Houses private credentials, Google Drive folder mappings, real source registries, and restricted research corpora. Those assets remain private and are governed by their respective operational agreements.

The following legacy identifiers were verified on 2026-10-08. Drive permission metadata reported each file as owner-only (`shared: false`); they are retained because they identify non-secret operational artifacts and do not convey access:

| Identifier | Artifact nature | Repository location |
| :--- | :--- | :--- |
| `1sA2TxUYAOGtcLfr2AhTLR4moBGIu2hoF` | v1.8.1 release-readiness result JSON | `src/cba_kb/release.py` |
| `1H25ty3673TbSBTPs4S_lliPmZgRecxzhXD8i2lrl_CM` | v1.8.1 production GO 授权记录 | `src/cba_kb/release.py` |
| `1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc` | historical registration MASTER workbook | `src/cba_kb/master.py`, `scripts/accept_v1_5_1.py` |
| `1ZebJR9YPKX37cMDdz0xznHDa45at_q65` | legacy/current-version pointer document | `src/cba_kb/current_state.py` |

Historical requirement documents (requirements/), legacy scripts (scripts/accept_v*.py), and test fixtures may contain additional legacy Drive file IDs from prior releases (v1.5.x–v1.8.x era). These are non-secret operational references embedded in historical records, not credentials or access tokens. They are retained for traceability and do not convey access.

## 3. Synthetic Fixtures (CC0-1.0)

All synthetic sample records, mock fixtures, and test inputs located in `tests/fixtures/` and related synthetic test directories are dedicated to the public domain under the Creative Commons CC0 1.0 Universal Public Domain Dedication. You may copy, modify, distribute, and perform work using these fixtures without asking permission.

## 4. Documentation (CC BY 4.0)

All documentation, operational guides, architectural specifications, and requirement documents are licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0). You are free to share and adapt the material provided appropriate credit is given.

## 5. Third-Party Data & Primary Sources

Nothing in this license grants rights to copyrighted third-party texts, media, or proprietary data produced by the Chinese Basketball Association, member clubs, or news publishers. Refer to [`THIRD_PARTY_DATA.md`](THIRD_PARTY_DATA.md) for details on third-party provenance and fair use boundaries.
