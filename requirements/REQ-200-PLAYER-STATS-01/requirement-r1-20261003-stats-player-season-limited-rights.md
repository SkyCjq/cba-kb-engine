# CBA-KB Requirement — REQ-200-PLAYER-STATS-01
## v2.0.0 Player Performance Data Domain + Research Integration
### FROZEN r1 — 2026-10-03

- status: FROZEN
- gate: READY_TO_CODE
- target_product_version: v2.0.0
- requirement_revision: r1-20261003-stats-player-season-limited-rights
- requirement_type: PRODUCT_FEATURE
- source_feasibility:
  - acquisition: GO
  - product: LIMITED
  - overall: LIMITED
- blocker_class: NONE_FOR_CODE_START
- return_gate: PR_READY_OR_HARD_BLOCKER

---

### 1. Authority and Baselines
- Current release id: v1.9.0-2
- Release state: COMPLETE
- Production code commit: b547ea386c49ed4e0dcd291bbef18664f76e3e3b
- Repository: SkyCjq/cba-kb-engine
- Base branch: main

### 2. Execution Topology
- Developer profile: E3_AGENTIC_SANDBOX@2
- Developer surface: ANTIGRAVITY
- Developer backend family: GOOGLE / Gemini Flash
- Assurance profile: A2_INDEPENDENT_QA@1
- Independent QA profile: Q2_INDEPENDENT_TECH_QA@1
- Independent QA surface: CODEX
- Independent QA backend: OPENAI / SOL
- Automation level: P2A_THIN_PYTHON

### 3. Frozen User Outcome
v2.0 adds a formal Player Performance Stats semantic grain so CBA-KB can answer player-season performance questions from canonicalized provider data while preserving:
- existing player_uid / Identity truth;
- Stats != Canonical Fact != Statement != Claim != Evidence;
- Unknown/missing != zero;
- provider/source provenance;
- rights/materialization boundaries;
- read-only Research View composition.

### 4. Frozen MVP Scope
- MVP semantic level: PLAYER_SEASON.
- Provider surfaces:
  - GET /api/com-code-tables/getSeason
  - GET /api/com-code-tables/getMatchType
  - GET /api/com-code-tables/getTeam?type=2&season={season}
  - POST /api/player-base-list?pageNumber={page}&pageSize={size}
- Advanced lists (offensive, defensive, shoot) remain out of v2.0 MVP product scope.

### 5. Canonical Stats Semantics
- Semantic grain label: PLAYER_SEASON_STATS (Research View grain: STATS).
- Invariants:
  - Missing != zero;
  - Zero is valid only when explicitly supplied by provider;
  - Rate/percentage explicit unit semantics;
  - Whole-season != team-split;
  - Transfer does not create duplicate whole-season truth;
  - Stats import produces zero mutation to MASTER/Fact/Identity registry.

### 6. Source Adapter Contract
- Envelope: AES-128-ECB + PKCS7.
- Key and envelope details are runtime/discovery details; fails closed on provider drift.
- Never hardcode key as eternal schema truth.

### 7. Identity Bridge
- Read-only against existing registry.
- Trusted provider external identifier link -> RESOLVED.
- Candidate signals (name, alias, birthDate, team) -> REVIEW_REQUIRED / UNRESOLVED; never automatic SAME.

### 8. Rights & Consumer Boundary
- Default: rights_classification=UNKNOWN_FOR_REDISTRIBUTION, public_export_allowed=false.
- Target-specific authorization required for consumer materialization.
- Fail closed if unauthorized.

### 9. Allowed Implementation Surface
- src/cba_kb/stats.py
- src/cba_kb/adapters/cba_stats.py
- src/cba_kb/research_view.py
- src/cba_kb/consumer_integration.py
- src/cba_kb/consumer_manifest.py
- src/cba_kb/cli.py
- tests/test_stats.py
- tests/test_stats_adversarial.py
- tests/test_stats_research_view.py
- tests/test_cli_v20.py
- requirements/REQ-200-PLAYER-STATS-01/**
