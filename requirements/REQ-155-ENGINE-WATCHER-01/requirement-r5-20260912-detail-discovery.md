# CBA-KB Requirement Revision - REQ-155-ENGINE-WATCHER-01

> Requirement ID: `REQ-155-ENGINE-WATCHER-01`  
> Revision: `r5-20260912-detail-discovery`  
> Status: `FROZEN_SPEC`  
> Supersedes source-contract assumptions in: `r4-FROZEN-20260912`

## 1. Revision Basis

A read-only source-contract review found that the three frozen index article IDs
remain valid official discovery roots, but the r4 assumption that each index
response contains a complete embedded registration table is no longer true.

Verified facts:

- Index API: `GET https://server.cbaleague.com/news_register/detail?id=<index_article_id>`
- Index response schema: `{code, message, data}`
- Index links to official child articles are in `data.detail_content`.
- Child API: `GET https://server.cbaleague.com/news_register/detail?id=<child_article_id>`
- Child response schema: `{code, message, data}`
- The registration HTML table is in child `data.detail_content`.
- Current child publication counts:
  - 2024-2025: 20 publications, 20 canonical teams.
  - 2025-2026: 21 publications, 20 canonical teams plus one supplemental update.
  - 2026-2027: 20 publications, 20 canonical teams.
- Official sources remain public, read-only, and unauthenticated.

## 2. Frozen Business Scope

No business-scope change. The watcher remains limited to the official CBA
domestic registration announcements for:

- `2024-2025`
- `2025-2026`
- `2026-2027`

## 3. Discovery Roots

The following IDs remain frozen discovery roots only:

| Season | Index article ID |
|---|---|
| 2024-2025 | `66b1de8bab` |
| 2025-2026 | `68932081bf` |
| 2026-2027 | `6a72fc344a` |

They must not be used as source identity for player rows.

## 4. Discovery Contract

For every season:

1. Fetch the frozen index endpoint and preserve the raw JSON.
2. Parse every official link matching
   `/news-register/detail/<child_article_id>` from `data.detail_content`.
3. Discover child article IDs dynamically. Current child IDs must not be hard-coded.
4. Normalize the discovery result deterministically.
5. Treat partial discovery or an unparseable discovery result as fail-closed.

## 5. Canonical Team Coverage

Each season must resolve exactly:

```text
canonical_team_count = 20
```

The child publication count may be greater than or equal to 20 because
supplemental, correction, and update publications are valid. The acceptance
condition must not use `child_article_count == 20`.

Every child publication must map to a known canonical team. Unknown or
ambiguous mappings require `REVIEW_REQUIRED` or fail-closed handling.

## 6. Alias Normalization

Alias resolution uses the project canonical team vocabulary in
`config/club_aliases.yaml`.

Verified required alias:

```text
吉林东北虎 -> 吉林九台农商行 -> jilin_jiutai
```

Unknown aliases must not be guessed. They require `REVIEW_REQUIRED` or
fail-closed handling.

## 7. Child Fetch Contract

For every discovered child article:

1. Fetch `https://server.cbaleague.com/news_register/detail?id=<child_article_id>`.
2. Preserve the raw JSON.
3. Require HTTP/JSON success and `{code, message, data}`.
4. Require `data.detail_content`.
5. Require one parseable official registration table.
6. Require the frozen logical schema.

Any child failure prevents the season cycle from being complete. Children must
not be silently skipped.

## 8. Logical Table Contract

Required logical fields:

- 运动员
- 注册类型
- 合同类别
- 合同原信息
- 原CBA俱乐部
- 公示截止时间
- 备注

The contract information field has two verified variants:

- 合同剩余年限
- 合同到期日

Both variants are valid. Any other unrecognized variation is `schema_drift` and
must fail closed.

## 9. Snapshot Identity

The r4 single-index-content hash identity is revoked.

The season snapshot identity must include at least:

- source contract revision
- season
- frozen index article ID
- index content SHA-256
- deterministic, order-independent list of
  `(child_article_id, child_content_sha256)`

Adding, deleting, or changing any child publication or its content must change
the season snapshot identity.

## 10. Change-Evidence Row Key

The r4 key is revoked:

```text
(season, index_article_id, raw_player_name)
```

The replacement source change-evidence key is child-local, for example:

```text
(season, child_article_id, row_index)
```

This key is not a canonical player or team business identity. Canonical semantic
projection belongs to a separate, later workflow.

## 11. Semantic Invariants

The following remain frozen:

- `observed change != business event`
- `add`, `delete`, `modify`, `schema_drift`, supplemental publication, and
  alias drift produce review evidence only.
- No watcher result may directly modify canonical business facts.
- Unknown dates, player identity, team identity, or business meaning must not
  be inferred.

## 12. Fail-Closed Conditions

Canonical publish must be blocked when any of the following occurs:

- index fetch failure
- child fetch failure
- invalid child JSON schema
- missing table
- unknown table schema
- canonical team coverage not equal to 20
- unknown team alias
- duplicate or unresolved team mapping
- partial child discovery
- `modify`
- `delete`
- `schema_drift`
- HTTP `403`
- HTTP `429`

## 13. Rate-Limit and Runtime Contract

The updated cycle requires three index requests plus approximately 60 or more
child requests.

Required controls:

- sequential requests only
- base delay at least 1.5 seconds
- jitter
- at most 3 retries
- exponential backoff
- timeout 20 seconds
- `403` and `429` fail closed
- no parallel bursts

## 14. Acceptance Changes

`acceptance_changed: YES`

Added acceptance IDs:

- `A22`: deterministic index-link discovery
- `A23`: dynamic child IDs
- `A24`: exactly 20 canonical teams per season
- `A25`: supplemental publications supported
- `A26`: alias normalization verified
- `A27`: both known contract-column variants accepted
- `A28`: per-child schema validation
- `A29`: partial child failure fail-closed
- `A30`: deterministic multi-response snapshot identity
- `A31`: child content change changes season identity
- `A32`: unknown alias fail-closed
- `A33`: no canonical write
- `A34`: same frozen real inputs normalize deterministically
- `A35`: three-season real-source Local Precheck
- `A36`: request and rate-limit contract respected

## 15. Release Risk

`release_risk_changed: YES`

Changes in risk:

- requests per cycle increase from approximately 3 to 60 or more
- discovery roots can drift
- child publication counts can change
- supplemental publications exist
- team aliases can drift
- partial child failure risk increases

These risks must appear in Conditional Local Precheck evidence.

## 16. Merge Gate

Private Instance topology remains:

```text
/Users/skychengneo/Agent/CBA_kb_instance
```

It is not currently established. Before merge, Conditional Local Precheck must
verify:

- private instance contract
- real fixtures
- source policy
- production compatibility mappings
- three-season real-source smoke
- split equivalence
- zero business delta
- private-material leakage

All checks must pass before WEB-04 may return `MERGE_READY`.

## 17. Implementation Policy

- Continue using PR `#13` and its existing feature branch.
- Do not create a second feature PR unless governance requires it.
- After this freeze, Codex may make only minimal repairs against the r5 machine
  contract.
- No opportunistic refactoring.
- Production access remains forbidden.
- Do not merge until the r5 Local Precheck passes.

## 18. Freeze Decision

```text
revision: r5-20260912-detail-discovery
status: FROZEN_SPEC
scope_changed: NO
acceptance_changed: YES
release_risk_changed: YES
context_expansion_required: NO
ready_to_code: YES
```
