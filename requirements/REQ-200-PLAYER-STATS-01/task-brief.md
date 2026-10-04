# REQ-200-PLAYER-STATS-01 — Task Brief

## Goal
Implement Frozen v2.0 player-season Stats domain and Research View integration:
1. Canonical Stats domain model + validators (`src/cba_kb/stats.py`).
2. Provider adapter (`src/cba_kb/adapters/cba_stats.py`) for current public CBA provider contract (AES-128-ECB PKCS7 envelope, dynamic key discovery/runtime configuration).
3. Identity resolution fail-closed against existing registry (trusted external-id -> RESOLVED, name/birth/team candidate signals -> REVIEW_REQUIRED/UNRESOLVED; never automatic SAME).
4. Research View integration with non-overridable STATS grain (`src/cba_kb/research_view.py`).
5. Consumer safe materialization and projection (`src/cba_kb/consumer_integration.py` & `src/cba_kb/consumer_manifest.py`).
6. CLI surface for ingest and validation (`src/cba_kb/cli.py`).

## Invariants
- MVP semantic level: PLAYER_SEASON.
- Missing != zero; whole-season != team-split.
- Identity semantic delta on existing registry: ZERO.
- Production mutation authority: false.
- Release infrastructure & P2A mutation: false.
- Return gate: PR_READY_OR_HARD_BLOCKER.
