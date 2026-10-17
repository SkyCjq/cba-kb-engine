# CBA-KB | Basketball research you can trace back to evidence

[简体中文](README.zh-CN.md) · [Quick start](#5-minute-quick-start) · [Operations guide](docs/operations/OPERATIONS.md)

**A headline is not a verified fact.** CBA-KB is an open-source, evidence-first knowledge engine for research on the Chinese Basketball Association (CBA). It helps researchers and developers organize reports, rosters, official notices, and documents without losing their sources, identities, rights, or verification status. The question behind every result is simple: **“Where did this come from?”**

> **v2.0.4-2 released on GitHub** (Engine Release, tag `74f8987`). Note the four distinct states: Python package version is still `2.0.2`; Drive data production release is still `v2.0.1-1`; production Drive has read-only run verification only — full write features are not enabled and Human final acceptance has not passed.

## Why CBA-KB exists

Imagine researching whether a player has transferred. One report says a move is rumored; a separate official roster still lists the previous club. A search result or an AI summary could easily turn that rumor into a fact.

CBA-KB is designed to keep the pieces separate:

- **Source:** Where did the information originate?
- **Document:** What was the original file, and has its revision changed?
- **Statement:** What did the source actually say?
- **Claim:** What assertion still needs independent verification?
- **Fact:** What conclusion has adequate authoritative evidence and a verification record?

**Core rule: Statement ≠ Fact. Evidence ≠ Fact. Unknown ≠ 0.** If identity, rights, or evidence cannot be established, the result should remain unresolved or go to human review—not be guessed.

## What can it do?

| Your goal | How CBA-KB helps | Current boundary |
| --- | --- | --- |
| Organize research | Link sources, documents, player identity, and provenance | Public Engine includes data models and CLI tools |
| Examine a report | Separate statements, claims, verified facts, and outstanding checks | AI-generated summaries never become facts automatically |
| Try a local inbox | Discover `.txt` files, register Source/Document, deduplicate, inspect status, and search | v2.0.4 development capability; requires a rights sidecar |
| Discover Google Drive material | Read-only discovery, change polling, revision tracking, gated search and index | Development capability, not activated production Drive |
| Plan archiving | Prepare exact plans, require approval, run guarded operations and verify results | Released in v2.0.4-2; production write features not yet enabled |
| Serve people and AI | Produce research data with identities, evidence, and rights context | Users remain responsible for validation and copyright compliance |

**CBA-KB is not a chatbot or a service that automatically crawls the whole web and declares basketball facts.** It is a reusable engine; you choose appropriate data sources, run it in your environment, and define human-review processes.

## Major versions and current status

| Version | Main changes | Status |
| --- | --- | --- |
| **v1.5.x** | Registration data model, reproducible Excel output and early Drive publication tools | Historical milestone |
| **v1.8.x** | Document evidence, stable player identities and consumer checks | Historical milestone |
| **v1.9.0** | Source intake, Statements, Claims, verification queues and research views | Historical milestone |
| **v2.0.2** | Public open-source Engine, licensing, sanitized repository, synthetic fixtures and reproducible tooling | Historical milestone |
| **v2.0.4-2** | Drive discovery/polling, rights-aware indexing, guarded plans, recovery, M4 E2E tooling | **Latest Engine Release** (package `2.0.2`, data `v2.0.1-1`) |

**v2.0.4-2 (released):** Includes local Inbox processing, Drive discovery and polling, rights-aware indexing, guarded plans and recovery. Real isolated Drive E2E (business path) verified; production Drive read-only verification done. Full write features and Human final acceptance are still pending.

## 5-minute quick start

Requirements: Python **3.11+** and Git. Commands below target Linux/macOS or a compatible terminal. You do not need private CBA data or Google credentials.

```bash
# 1. Clone the public repository
git clone https://github.com/SkyOneBranch/cba-kb-engine.git
cd cba-kb-engine

# 2. Install into an isolated Python environment
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .

# 3. Environment check and offline tests
make doctor
make test

# 4. Explore available commands
cba-kb --help
```

To reproduce the **v2.0.2 source**, explicitly check out the `v2.0.2` tag before installation. A default clone of `main` includes the released v2.0.4-2 code.

### Try 1: an entirely synthetic research workflow

```bash
# Keep private-instance environment variables out of the public smoke test
unset CBA_KB_INSTANCE_ROOT CBA_KB_SOURCE_POLICY_JSON CBA_STATS_AES_KEY GOOGLE_APPLICATION_CREDENTIALS

# Uses public synthetic fixtures, not your private source material
python scripts/run_cleanroom_smoke.py --output .cleanroom/ci-smoke
```

For individual Source, Statement, Claim, and Research View commands, see the [public operations guide](docs/operations/OPERATIONS.md).

### Try 2: local Inbox discovery and search (development capability)

```bash
# Keep instance data outside the repository
mkdir -p /tmp/cbakb-demo/config /tmp/cbakb-demo/inbox/documents

cat > /tmp/cbakb-demo/inbox/documents/demo.txt <<'TXT'
Synthetic CBA Bulletin
The Harbor Falcons won a fictional preseason game.
TXT

# Local TXT files need accompanying provenance/rights metadata
cat > /tmp/cbakb-demo/inbox/documents/demo.txt.source.json <<'JSON'
{
  "schema_version": 1,
  "title": "Synthetic CBA Bulletin",
  "origin": "synthetic_fixture",
  "source_url": null,
  "rights": {
    "classification": "private",
    "public_export_allowed": false,
    "evidence": []
  }
}
JSON

# Run a second scan after the stability interval
cba-kb --instance-root /tmp/cbakb-demo inbox-scan
sleep 2
cba-kb --instance-root /tmp/cbakb-demo inbox-scan

# Inspect status and try a keyword query
cba-kb --instance-root /tmp/cbakb-demo archive-status --all
cba-kb --instance-root /tmp/cbakb-demo archive-query --keyword "Harbor Falcons"
```

This demo does not move, delete, or publish originals. Actual results depend on the command output; invalid rights metadata, an unstable file, or a review requirement may prevent ingestion. See the [operations and safety details](docs/operations/OPERATIONS.md) before running with real content.

## Google Drive: organize files with approval

CBA-KB can discover and track Drive files, then prepare an explicit plan for archiving or retaining them. **A file move requires authorization and a check of the actual result.** If required file or revision evidence is missing, the operation must stop.

The guarded move implementation is released in v2.0.4-2 (M4 business E2E verified), but **full production activation is not complete**. Production writes require separate human authorization. Keep OAuth credentials and private folder information outside this public repository.

## Who is it for?

- **Basketball researchers and curators** who want every claim connected to its source and verification status.
- **Developers** who want to reuse CBA data contracts, identity mapping, ingestion tools, and synthetic examples.
- **AI application builders** who want to keep rumors, source statements, and verified facts separate.

The repository provides a **Public Engine**. Real private material is operated separately in a user's **Private Instance** under applicable permissions and law. Code uses [Apache-2.0](LICENSE), docs use [CC BY 4.0](DATA_LICENSE.md), and synthetic fixtures use [CC0-1.0](DATA_LICENSE.md). Third-party reporting and photographs retain their owners' rights; see [third-party data](THIRD_PARTY_DATA.md).

## Documentation and contributing

- [Operations Manual](docs/operations/OPERATIONS.md) · [中文操作手册](docs/operations/OPERATIONS.zh-CN.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
- [Data and license policy](DATA_LICENSE.md) · [Third-party materials](THIRD_PARTY_DATA.md)
- [GitHub Issues](https://github.com/SkyOneBranch/cba-kb-engine/issues) · [Pull Requests](https://github.com/SkyOneBranch/cba-kb-engine/pulls)

**The aim is not to make AI sound more certain. It is to make basketball research easier to check against real evidence.**
