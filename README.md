# CBA-KB | Basketball research you can trace back to evidence

[简体中文](README.zh-CN.md) · [Quick start](#5-minute-quick-start) · [Operations guide](docs/operations/OPERATIONS.md)

**A headline is not a verified fact.** CBA-KB is an open-source, evidence-first knowledge engine for research on the Chinese Basketball Association (CBA). It helps researchers and developers organize reports, rosters, official notices, and documents without losing their sources, identities, rights, or verification status. The question behind every result is simple: **“Where did this come from?”**

> **Current production release: v2.0.2.** The `main` branch also contains parts of **v2.0.4-2 under development**. Full Google Drive archiving, real two-backend end-to-end acceptance, and production activation are **not complete**. This README is not a production-release announcement.

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
| Plan archiving | Intended workflow: exact plan, approval, guarded operation, independent readback | Real Drive file moves are still undergoing integration review and E2E work |
| Serve people and AI | Produce research data with identities, evidence, and rights context | Users remain responsible for validation and copyright compliance |

**CBA-KB is not a chatbot or a service that automatically crawls the whole web and declares basketball facts.** It is a reusable engine; you choose appropriate data sources, run it in your environment, and define human-review processes.

## Understand the release status first

- **v2.0.2: released baseline.** Public Engine, data contracts, synthetic fixtures, research and verification tooling.
- **v2.0.4-2: in development.** The `main` branch contains code for a local inbox, read-only Drive discovery/polling, permission gates, indexes, some plan approval, and WAL recovery. **Module presence is not proof that the complete workflow has passed acceptance.**
- **Integration [PR #176](https://github.com/SkyOneBranch/cba-kb-engine/pull/176): under review.** Guarded Drive move and unified workflow changes require independent Q2 against the final commit and a human merge before they reach `main`.
- **Not yet complete:** real isolated Google Drive E2E, full-scope acceptance, Production GO, deployment to an actual host, production Inbox activation, and user acceptance.

**You can experiment locally with synthetic data. Do not treat this as permission to move production Google Drive files.**

## 5-minute quick start

Requirements: Python **3.11+** and Git. Commands below target Linux/macOS or a compatible terminal. You do not need private CBA data or Google credentials.

```bash
# 1. Clone the public repository (main is the development branch)
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

To reproduce the **released v2.0.2 source**, explicitly check out the `v2.0.2` tag before installation. A default clone of `main` includes development code; it is not a v2.0.4-2 release.

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

## Google Drive: why not just move everything in one click?

With personal Drive files, the priority is not speed. It is **moving the right file, with the right permissions, and being able to verify what happened afterward**.

CBA-KB's intended path is: read exact file ID, original parents, intended destination, revision and rights evidence → prepare an exact plan → obtain human authorization → recheck before a write → execute a guarded operation → independently read the result from Drive. Missing required evidence should stop the action rather than be treated as approval.

The development branch includes read-only Drive discovery, change polling, search, indexing, and some guarded local planning. **Production Drive reparent/move has not passed full-version independent acceptance.** Real production moves, deletions, sharing, publishing, or pointer updates require separate Human Production GO and any confirmation required by the connected Google Drive tools.

Real OAuth credentials, private folder IDs, raw corpora, and user logs belong in a separate **Private Instance**, not this public GitHub repository.

## Who is it for?

- **Basketball researchers and curators** who want every claim connected to its source and verification status.
- **Developers** who want to reuse CBA data contracts, identity mapping, ingestion tools, and synthetic examples.
- **AI application builders** who want to keep rumors, source statements, and verified facts separate.

The repository provides a **Public Engine**. Real private material is operated separately in a user's **Private Instance** under applicable permissions and law. Code uses [Apache-2.0](LICENSE), docs use [CC BY 4.0](DATA_LICENSE.md), and synthetic fixtures use [CC0-1.0](DATA_LICENSE.md). Third-party reporting and photographs retain their owners' rights; see [third-party data](THIRD_PARTY_DATA.md).

## AI can write code. How do we know it is correct?

AI agents also help develop this project. Think of the human as a **product manager**, Muse or another capable agent as the **developer**, and CI plus independent review as **testing and acceptance**. The human still makes the final decision.

**New Requirement → Independent Q1 / Freeze → Agent implementation → GitHub PR → CI + independent Q2 → Human Approval → Merge**

- State what should be built and what counts as done. For complex work, review the specification independently (Q1) and freeze the agreed scope before coding.
- Muse, Grok, or another agent with suitable repository access can develop, test, and submit a PR; the primary coding agent can change between tasks.
- GitHub CI supplies repeatable checks. A separate web AI reviewer, such as ChatGPT when it can access the code and evidence, can inspect the **exact PR HEAD**, reproduce issues, and request repairs.
- A failed review means fix and re-review, not blindly merge. Human approval controls merging; **merging is not production deployment**.

This is a budget-conscious collaboration approach inspired by [LACD](https://github.com/SkyOneBranch/LACD), **not a claim that CBA-KB itself is an AI coding service**. AI providers may offer free usage tiers, but availability, quotas, prices, permissions, and review quality vary. Testing and independent review reduce risk; they do not guarantee zero defects.

## Documentation and contributing

- [Operations Manual](docs/operations/OPERATIONS.md) · [中文操作手册](docs/operations/OPERATIONS.zh-CN.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
- [Data and license policy](DATA_LICENSE.md) · [Third-party materials](THIRD_PARTY_DATA.md)
- [GitHub Issues](https://github.com/SkyOneBranch/cba-kb-engine/issues) · [Pull Requests](https://github.com/SkyOneBranch/cba-kb-engine/pulls)

**The aim is not to make AI sound more certain. It is to make basketball research easier to check against real evidence.**
