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

- **Public Engine (This Repository)**: Contains exclusively open-source code (Apache-2.0), public documentation (CC BY 4.0), and synthetic test fixtures (CC0-1.0). No proprietary league data, private Drive identifiers, or credentials are included.
- **Private Instance**: Houses private credentials, Google Drive folder mappings, real source registries, and restricted research corpora. Those assets remain private and are governed by their respective operational agreements.

## 3. Synthetic Fixtures (CC0-1.0)

All synthetic sample records, mock fixtures, and test inputs located in `tests/fixtures/` and related synthetic test directories are dedicated to the public domain under the Creative Commons CC0 1.0 Universal Public Domain Dedication. You may copy, modify, distribute, and perform work using these fixtures without asking permission.

## 4. Documentation (CC BY 4.0)

All documentation, operational guides, architectural specifications, and requirement documents are licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0). You are free to share and adapt the material provided appropriate credit is given.

## 5. Third-Party Data & Primary Sources

Nothing in this license grants rights to copyrighted third-party texts, media, or proprietary data produced by the Chinese Basketball Association, member clubs, or news publishers. Refer to [`THIRD_PARTY_DATA.md`](THIRD_PARTY_DATA.md) for details on third-party provenance and fair use boundaries.
