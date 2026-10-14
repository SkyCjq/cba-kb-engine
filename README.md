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
| **Storage & Secrets**| No credentials committed; legacy non-secret Drive IDs are documented in [`DATA_LICENSE.md`](DATA_LICENSE.md) | Private Google Drive mappings, OAuth tokens |

## 4. Capability Evolution

- **v1.5.x**: Baseline registration data model, Excel export determinism, and basic Drive publishing.
- **v1.8.x**: Document evidence lane, stable player identity registry, and consumer closure controls.
- **v1.9.0**: Source Intake Contract v1, Statement model, Claim MVP, Verification Queue, and Research View synthesis.
- **v2.0.x**: Placement enforcement guard, fail-closed consumer profiles, and hardened release orchestration.
- **v2.0.2**: Public Engine open-source readiness, Apache-2.0 licensing, CC BY 4.0 docs, and sanitized history.
- **v2.0.4 (development, PR-1)**: Local-first TXT inbox lifecycle — polling auto-discovery, persistent Source/Document registry, rights-aware query, CLI status/report. Development capability, **not** a production release.

## 5. Roadmap

- **v2.1**: Expanded Person layer (coaches, referees, club staff) and historical transaction graphs.
- **v2.2**: Community Dataset distribution (ODC-By/ODbL) and automated benchmark suites.

## 6. 5-Minute Quick Start

```bash
# 1. Clone repository
git clone https://github.com/SkyOneBranch/cba-kb-engine.git
cd cba-kb-engine

# 2. Set up virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .

# 3. Verify environment
make doctor

# 4. Run offline tests
make test
```

The editable install creates the documented `cba-kb` command. To reproduce the
public workflow with CC0 synthetic data and no private configuration, follow the
[clean-room smoke procedure](docs/operations/OPERATIONS.md#15-clean-room-synthetic-smoke).

### Try the CLI:
```bash
cba-kb --help
```

### Local-First Inbox Lifecycle (v2.0.4 development, PR-1)

Current production release stays **v2.0.2 — COMPLETE**. The commands below
exercise the in-development PR-1 capability: automatic discovery of local
`.txt` files, Source/Document registration with deduplication, rights-aware
query, and lifecycle status. No Google credentials are needed; everything
runs against an external instance directory you choose.

```bash
# 1. Create an external instance (outside the engine tree)
mkdir -p /tmp/cbakb-demo/{config,inbox/documents}

# 2. Drop a synthetic TXT plus its rights sidecar
cat > /tmp/cbakb-demo/inbox/documents/synthetic-news.txt <<'EOF'
Synthetic CBA Bulletin
The Harbor Falcons won a fictional preseason game.
The team scored 88 points in this synthetic report.
EOF
cat > /tmp/cbakb-demo/inbox/documents/synthetic-news.txt.source.json <<'EOF'
{
  "schema_version": 1,
  "title": "Synthetic CBA Bulletin",
  "origin": "synthetic_fixture",
  "source_url": null,
  "rights": {
    "classification": "public",
    "public_export_allowed": true,
    "evidence": [
      {"type": "author_attestation", "license": "CC0-1.0",
       "description": "Original synthetic test content"}
    ]
  }
}
EOF

# 3. Scan twice: a file is processed only after it looks identical
#    in two consecutive scans at least 1 second apart (stability gate)
cba-kb --instance-root /tmp/cbakb-demo inbox-scan
sleep 2
cba-kb --instance-root /tmp/cbakb-demo inbox-scan

# 4. Or poll continuously instead of manual scans (Ctrl-C to stop)
cba-kb --instance-root /tmp/cbakb-demo inbox-poll --interval-seconds 30

# 5. Lifecycle status and rights-aware query
cba-kb --instance-root /tmp/cbakb-demo archive-status --all
cba-kb --instance-root /tmp/cbakb-demo archive-query --keyword "Harbor Falcons"
cba-kb --instance-root /tmp/cbakb-demo archive-query --title "Synthetic CBA Bulletin"
cba-kb --instance-root /tmp/cbakb-demo archive-query --visibility public --keyword "Harbor"
```

Security constraints (fail-closed by default):

- Every `.txt` needs a same-named `.txt.source.json` sidecar declaring
  title, origin, and rights. Missing/invalid/unreadable metadata (including
  structurally malformed values), non-UTF-8 bytes, a symlinked sidecar, or a
  public claim without verifiable evidence routes the file to
  `REVIEW_REQUIRED` — never silently ingested, never publicly searchable.
  An unresolved human review persists across rescans: re-ingesting identical
  inputs does not clear it.
- `local` visibility (default) is for the operator of that external
  instance; `public` visibility only returns attested synthetic fixtures
  with real rights evidence. A document shared by multiple sources is
  publicly searchable only while every linked source is review-clean; if any
  linked source enters review, the document is quarantined from public
  search (still visible locally via clean sources). This is a search filter,
  not a publication approval: there is no publish path in PR-1, and operators
  should still treat any public exposure as a residual risk to be reviewed
  (rights are re-evaluated on every metadata change and re-verified at query
  time, but no automated check replaces human rights clearance).
- Inbox originals are never renamed, moved, or deleted
  (`archive_status` stays `NOT_REQUESTED`).
- All state lives under `<instance>/data/`; the engine source tree is
  never written to. If `<instance>/data` (or the inbox) resolves outside the
  instance or inside the engine tree — e.g. via a symlink — the lifecycle
  refuses to start with `INBOX_LIFECYCLE_PATH_ESCAPE` before any write.

Not in PR-1 (remains v2.0.4 follow-up work): Drive-backed discovery and
adapters, guarded archive move/retain, full cross-backend recovery,
Production release.

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
