# CBA-KB Engine

[中文版](README.zh-CN.md)

**CBA-KB** is an evidence-first data and research engine for the Chinese Basketball Association (CBA). It turns heterogeneous source material into traceable structured facts, identity-aware document evidence, statements, claims, verification queues, and read-only research views that can be consumed by humans and AI systems without collapsing evidence into fact.

Current production release: **v2.0.2 — COMPLETE**.

---

## 1. What CBA-KB Is

CBA-KB provides a reproducible pipeline for Chinese basketball research. It extracts and models registration facts, player identities, document-level evidence, quoted statements, and research claims while preserving strict provenance, uncertainty, and rights boundaries.

The public repository contains the reusable open-source **Engine**: schemas, adapters, deterministic processing logic, synthetic test fixtures, and CLI tools.

## 2. Three North Stars

1. **Evidence-First Truth Hierarchy**:
   > Core rule: **Statement ≠ Fact. Evidence ≠ Fact. Unknown ≠ 0.**
   Verbatim quotes and claims are never silently collapsed into canonical facts without independent verification.
2. **Deterministic & Fail-Closed Operations**:
   Ambiguity, missing records, or unverified claims route directly to an explicit verification queue rather than guessing or silently dropping data.
3. **Strict Rights & Provenance Preservation**:
   Every fact, statement, and document retains an immutable cryptographic provenance trail linking back to its authoritative source.

## 3. Public Engine vs. Private Instance

| Dimension | Public Engine (This Repository) | Private Instance (Private Deployment) |
| :--- | :--- | :--- |
| **Code & Tools** | Open source under **Apache-2.0** | Operational configurations & private scripts |
| **Documentation** | Public documentation under **CC BY 4.0** | Private research notes & internal runbooks |
| **Data & Fixtures** | Synthetic & mock fixtures under **CC0-1.0** | Real raw corpora, copyrighted news, private Drive IDs |
| **Storage & Secrets**| Zero credentials, zero cloud IDs committed | Private Google Drive mappings, OAuth tokens |

## 4. Capability Evolution

- **v1.5.x**: Baseline registration data model, Excel export determinism, and basic Drive publishing.
- **v1.8.x**: Document evidence lane, stable player identity registry, and consumer closure controls.
- **v1.9.0**: Source Intake Contract v1, Statement model, Claim MVP, Verification Queue, and Research View synthesis.
- **v2.0.x**: Placement enforcement guard, fail-closed consumer profiles, and hardened release orchestration.
- **v2.0.2**: Public Engine open-source readiness, Apache-2.0 licensing, CC BY 4.0 docs, and sanitized history.

## 5. Roadmap

- **v2.1**: Expanded Person layer (coaches, referees, club staff) and historical transaction graphs.
- **v2.2**: Community Dataset distribution (ODC-By/ODbL) and automated benchmark suites.

## 6. 5-Minute Quick Start

```bash
# 1. Clone repository
git clone https://github.com/SkyCjq/cba-kb-engine.git
cd cba-kb-engine

# 2. Set up virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock

# 3. Verify environment
cba-kb doctor

# 4. Run offline tests
make test
```

### Try the CLI:
```bash
cba-kb --help
```

## 7. Semantic Principles

- **Separation of Concerns**: Statements represent what was said; Claims represent asserted hypotheses; Facts represent canonical audited league records.
- **Fail-Closed on Ambiguity**: When an actor cannot be uniquely matched to a player UID, they are held in unresolved state or routed to `verification-queue`.
- **Zero Hallucination / Zero Promotion**: An AI model or parser may never promote an extracted statement to a canonical fact without explicit human or official API verification.

## 8. Data and Rights Boundaries

- **Code License**: The Engine code is licensed under the [Apache License 2.0](LICENSE).
- **Documentation**: Licensed under [CC BY 4.0](DATA_LICENSE.md).
- **Fixtures**: Synthetic fixtures are dedicated to the public domain under [CC0-1.0](DATA_LICENSE.md).
- **Third-Party Data**: League notices, media articles, and photographs remain copyrighted by their respective owners. See [`THIRD_PARTY_DATA.md`](THIRD_PARTY_DATA.md).

## 9. Versioning and Release Specification

- Current version: **2.0.2**
- This project adheres to [Semantic Versioning 2.0.0](https://semver.org/).
- The version in `pyproject.toml`, `src/cba_kb/__init__.py`, and git release tags are strictly synchronized.

## 10. Documentation Map, Contributing & License

- **Operations**: [Operations Manual](docs/operations/OPERATIONS.md) ([中文](docs/operations/OPERATIONS.zh-CN.md))
- **Contributing**: Please review [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) before submitting pull requests.
- **License**: [Apache License 2.0](LICENSE) | [Notice](NOTICE) | [Data License Policy](DATA_LICENSE.md).
