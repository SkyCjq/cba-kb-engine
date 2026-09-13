# REQ-160-PROD-RELEASE-01 - v1.6.0 Production Release

Requirement revision: `r1-20260913-v160-production-prepare`

Status: frozen for prepare/reservation/rehearsal only.

## Authority

- Repository: `https://github.com/SkyCjq/cba-kb-engine`.
- Base branch: `codex/v1.5.2-draft2`.
- Base and previous production code:
  `0b9c6e062616fc8d4349304ea483afdd917ce181`.
- Product candidate SHA:
  `a246fa142187a5f4f420ffebcfdf1480b8bcedd6`.
- Previous release: `v1.5.5-1`.
- Target release: `v1.6.0-1`.
- Release infrastructure mode: `ON`.
- Publish and restore are forbidden until a separate Web GO.

The product candidate is the merged Document Lane merge commit. The release
execution SHA is the clean feature-branch commit containing this release
Requirement and its minimal orchestration adaptation. These SHA roles are
distinct and must never be conflated.

## Production baseline audit

Live production authority was read before coding:

```text
current_release_id=v1.5.5-1
state=COMPLETE
pending_release_id=null
published_code_sha=0b9c6e062616fc8d4349304ea483afdd917ce181
policy_targets=186
active_targets=186
reservations=0
manifest_rows=186
manifest_covers_active=true
dependencies=2
release_id_collision=false
```

The live status hash and metadata are frozen in Private Instance audit
evidence. Any drift invalidates this prepare.

## Release scope

This release publishes the Engine code mirror and required control documents.
It must not publish the Document Lane Private Instance archive or any private
corpus.

Expected exact delta from previous production code to product candidate:

```text
new code targets=9
modified code targets=5
removed code targets=0
previous active targets=186
expected final targets=195
```

Canonical Facts and compatibility products are carried forward unchanged.

## v1.6 orchestration adaptation

`scripts/prepare_production.py` must retain the frozen `v1.5.5-1` behavior as
its default and add an explicit `v1.6.0-1` release spec:

```text
v1.5.5-1 product baseline=
c14a0f2579fcc86e2dc114f0b15d00dd54e9f55e

v1.6.0-1 product baseline=
0b9c6e062616fc8d4349304ea483afdd917ce181
```

No second release framework may be introduced.

Control-document updates must treat the active release ID as historical text
instead of retaining a stale current-release statement.

## Safety inheritance

The following v1.5.5 contracts remain unchanged:

- exact production code provenance;
- release ID protection;
- candidate and before snapshots;
- immutable archive namespace;
- exact dependency binding from `production.json.dependency_ids`;
- single-writer and optimistic status checks;
- new-target-only staging reservation;
- manifest coverage;
- rollback coverage;
- release journal/state machine;
- post-write readback and verification;
- transport read retry/reset and write reconciliation.

## Prepare workflow

Commands are frozen only after this Requirement is committed:

```text
project:
python scripts/prepare_production.py project \
  --release v1.6.0-1 \
  --engine-sha <release_execution_sha> \
  --instance-root /Users/skychengneo/Agent/CBA_kb_instance \
  --output <private-instance>/data/<prepare-root>/projection.json

reserve-staging:
python scripts/prepare_production.py reserve-staging \
  --release v1.6.0-1 \
  --instance-root /Users/skychengneo/Agent/CBA_kb_instance \
  --output <private-instance>/data/<prepare-root>/allocation \
  --projection <projection.json> \
  --single-writer

freeze:
python scripts/prepare_production.py freeze \
  --release v1.6.0-1 \
  --engine-sha <release_execution_sha> \
  --instance-root /Users/skychengneo/Agent/CBA_kb_instance \
  --output <private-instance>/data/<prepare-root>/freeze \
  --projection <projection.json> \
  --allocation <allocation.json>
```

The existing script does not require `--engine-sha` for `reserve-staging` or
`freeze`; the release execution SHA is still frozen in projection and plan.

No local prepare alias exists.

`publish_command = WEB_GO_ONLY_NOT_EXECUTED`

`rollback_command = WEB_GO_ONLY_NOT_EXECUTED`

## Reservation authority

Only projected NEW logical keys may be reserved below the release-specific
staging folder. Existing active production IDs must not be reused or mutated.
The reservation must record:

- allocation metadata;
- semantic delta signature;
- status-before hash and metadata;
- exact reserved IDs;
- active target IDs.

Reservation may not update `release_status`, canonical payload, Facts or
existing production artifacts.

## Prepare acceptance

- projection is deterministic and read-only;
- only 9 new targets are reserved;
- all 5 modified targets reuse their active IDs;
- final target count is 195;
- manifest coverage is complete;
- dependencies are exactly the Private Instance allowlist;
- rollback coverage is complete;
- a non-production rollback rehearsal passes;
- Facts remain unchanged;
- status remains `v1.5.5-1 / COMPLETE`;
- secret scan and must-not-change checks pass.

## Allowed paths

- `requirements/REQ-160-PROD-RELEASE-01/requirement-r1-20260913.md`
- `requirements/REQ-160-PROD-RELEASE-01/task.yaml`
- `scripts/prepare_production.py`
- `tests/test_release_orchestration.py`

## Must not change

- `src/cba_kb/release.py`
- `src/cba_kb/drive.py`
- `src/cba_kb/transport.py`
- `src/cba_kb/native.py`
- canonical Facts or compatibility data;
- production manifest or release_status before publish;
- reservation or rollback semantics;
- `.github/workflows/offline-tests.yml`;
- `requirements.lock`;
- `config/taxonomy.yaml`.

## Stop boundary

This Requirement ends at `READY_FOR_WEB_GO`. It authorizes no production
payload mutation, no status transition and no publish.
