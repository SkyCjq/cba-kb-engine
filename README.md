# CBA-KB Engine

[中文](README.zh-CN.md)

**CBA-KB** is an evidence-first data and research engine for the Chinese Basketball Association (CBA). It turns heterogeneous source material into traceable structured facts, identity-aware document evidence, statements, claims, verification queues, and read-only research views that can be consumed by humans and AI systems without collapsing evidence into fact.

Current production release: **v1.9.0-1 — COMPLETE**.

> Core rule: **Statement ≠ Fact. Evidence ≠ Fact. Unknown ≠ 0.**

## Why this project exists

CBA research data is fragmented across registration lists, spreadsheets, documents, articles, historical records, and manually reviewed evidence. CBA-KB provides a reproducible pipeline for organizing those materials while preserving provenance, uncertainty, semantic grain, and data-rights boundaries.

The public repository contains the reusable **Engine**: code, schemas, adapters, validators, deterministic processing logic, synthetic/public-safe fixtures, and public-safe configuration examples.

Real data, credentials, Google Drive object IDs, production allowlists, private source registries, human corrections, and private evidence belong in a separate **Private Instance** and are intentionally not committed to this repository.

## What v1.9.0 adds

v1.9.0 extends the project from registration facts, document evidence, and player identity into an evidence-aware research layer:

- **Source Intake Contract v1** — normalizes inputs from local files, Google Drive documents, browser-captured WeChat material, ima-derived exports, and synthetic fixtures while preserving provenance and rights metadata.
- **Statement model** — extracts attributable statements while keeping source references, controlled excerpts, actor references, attribution type, and extraction status.
- **Claim MVP** — represents research claims separately from facts and supports explicit states such as unverified, corroborated, contradicted, superseded, and review-required.
- **Actor / identity references** — reuses stable player identities where available and supports person-ready or unresolved actors without inventing identities.
- **Verification Queue v1** — routes unresolved actors, ambiguous attribution, uncertain sources, claims, and derived signals into a deterministic review queue instead of silently guessing or dropping them.
- **Research View v0** — produces a read-only synthesis that explicitly separates canonical facts, documents, statements, claims, actor identity state, open verification items, and unknown/evidence gaps.
- **Rights-aware consumer materialization** — keeps private or restricted evidence from being treated as public-safe consumer output.
- **Fail-closed release and provenance controls** — maintains explicit release state, source bindings, readback, rollback/recovery, and current-world/consumer closeout checks.

v1.9.0 does **not** turn statements or claims into canonical facts automatically, ship a complete person registry, provide a complete statistics layer, or make private production data public.

## Data model at a glance

```text
Raw sources
   │
   ▼
Source Intake + provenance + rights
   │
   ├──────────────► Documents / evidence
   │
   ▼
Statements ───────► Claims
   │                  │
   ▼                  ▼
Actor / Identity   Verification Queue
   │                  │
   └──────────┬───────┘
              ▼
        Research View
              │
              ▼
   Rights-aware consumer outputs

Canonical registration facts remain a separate truth layer.
```

The separation is deliberate. A quoted statement can be valid evidence without being a verified fact, and an unresolved person can remain unresolved without being forced into an identity record.

## Project architecture

| Layer | Responsibility |
| --- | --- |
| Engine | Public reusable code, schemas, adapters, validators, CLI, release logic, synthetic/public-safe fixtures |
| Private Instance | Real source registry, credentials, Drive mappings, real fixtures, human corrections, production policy, private evidence |
| Canonical facts | Structured registration/event products with explicit source lineage |
| Document evidence | Captured source material and document-level provenance |
| Identity | Stable player identity plus bounded unresolved/person-ready actor references |
| Statement / Claim | Evidence-aware research semantics introduced in v1.9.0 |
| Verification Queue | Explicit unresolved/review-required work |
| Research View | Read-only multi-grain synthesis for research and AI consumption |
| Consumer outputs | Rights-aware derived packages; not an authority over upstream facts |

GitHub is the code truth for the Engine. Production data is not stored in this repository.

## Repository layout

```text
src/cba_kb/          Engine modules
tests/               Offline regression and contract tests
config/              Public-safe configuration and taxonomy
scripts/             Deterministic build/release utilities
docs/                Historical design, operations, release and requirement records
requirements/        Requirement contracts used by the development workflow
legacy/              Preserved legacy tooling and compatibility tests
```

Key v1.9 modules include:

```text
src/cba_kb/source_intake.py
src/cba_kb/statement.py
src/cba_kb/claim.py
src/cba_kb/actor.py
src/cba_kb/verification_queue.py
src/cba_kb/research_view.py
```

## Requirements

- Python **3.11+**
- Dependencies pinned in `requirements.lock`
- Git for reproducible candidate builds
- A Private Instance only when working with real/private inputs or production Google Drive

## Quick start

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock

make doctor
make test
```

The package exposes the `cba-kb` CLI:

```bash
cba-kb --help
```

Representative command families include:

```text
validate / build / extract / facts
document-ingest / document-batch
identity-* / mention-*
source-intake
statement-extract
claim-extract
verification-queue
research-view
consumer-*
plan / publish / verify / restore
```

Commands that require private configuration must receive a Private Instance via `--instance-root` or `CBA_KB_INSTANCE_ROOT`. Missing required private state is designed to fail closed.

## v1.9 research workflow

A typical research-oriented flow is:

```text
1. Normalize a source with source-intake
2. Preserve provenance and rights metadata
3. Extract statements
4. Reuse a known player identity or keep the actor unresolved
5. Build claims only as a separate semantic layer
6. Route ambiguity to the verification queue
7. Render a read-only research view
8. Materialize only rights-safe consumer outputs
```

This repository intentionally does not provide a shortcut that converts document text directly into canonical facts.

## Testing

The normal offline regression suite is:

```bash
make test
```

Focused tests live under `tests/`. Production credentials are not required for ordinary offline regression.

The project favors deterministic validation, explicit semantic contracts, exact source/provenance bindings, and fail-closed behavior over silent repair.

## Data and rights

This is a **public Engine repository**, not a public dump of the private CBA-KB knowledge base.

You should assume that:

- some upstream documents and media are copyrighted;
- private source locators, Drive IDs, credentials, evidence, or manual corrections may not be redistributable;
- derived consumer outputs must respect their source-rights classification;
- absence of evidence is not evidence of zero;
- AI-generated summaries or classifications are derived information, not primary evidence.

Before redistributing any data produced with the Engine, verify the rights and provenance of the underlying sources.

## Production and release safety

Production writes are intentionally separated from ordinary development. The release path uses explicit production policy, frozen inputs, single-writer execution, immutable evidence, readback verification, and rollback/recovery controls.

Do not treat a successful local build or CI run as permission to publish production data.

## Project status and roadmap

**v1.9.0-1** is the current completed production release and includes the Statement/Claim research layer, source intake contract, verification queue, research view, and associated consumer-closure controls.

Likely future directions include deeper person modeling, statistics, richer claims/research workflows, and additional automation, but those are not implied to be implemented until they appear in code and a completed release.

## Contributing

Issues and pull requests should keep the Engine / Private Instance boundary intact.

When contributing:

1. do not commit credentials, real private fixtures, private Drive mappings, or private evidence;
2. keep semantic grains explicit instead of merging facts, documents, statements, claims, and unknowns;
3. add or update tests for contract changes;
4. preserve deterministic and fail-closed behavior for ambiguous or unsafe states;
5. use a feature branch and pull request for repository changes.

## Documentation

- `docs/` contains historical architecture, migration, operations, and release material.
- The CLI is the best source for currently implemented command surfaces: `cba-kb --help`.
- The repository code and tests are the authoritative description of implemented Engine behavior.

## License and reuse

No open-source license file is currently published in this repository. Public visibility alone does not grant an open-source license.

If you plan to reuse or redistribute the code, add or confirm an explicit software license first. Data and source-material rights must be evaluated separately from the code license.
