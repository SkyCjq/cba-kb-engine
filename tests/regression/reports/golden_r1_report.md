# CBA-KB v2.0.2 Regression Baseline Report

- Requirement: `REQ-202-REGRESSION-BASELINE-01`
- Status: **PASS**
- Evaluation mode: `FROZEN_REFERENCE_REPLAY`
- Product performance claim: `false`
- Frozen golden SHA-256: `78d68240d00db797b67f8a3219cf4e3cc711f9647dcf8def4f5811601449112d`
- Responses SHA-256: `df52cae8246a72d1d90d13a4d8491b41034a23fd10a1bb3ffb5a4f87baea4e10`
- Score: 27/27 (100.00%)
- False fact promotions: 0 (required: 0)
- Statement precision (record-only): 100.00%
- Attribution precision (record-only): 100.00%

> This run verifies frozen-input integrity, scoring, and report reproducibility. 
> It replays the frozen expected answers and is not a product-performance measurement.

## Hard gates

| Gate | Status |
|---|---|
| identity_fail_safe | PASS |
| missing_vs_zero | PASS |
| stats_statement_separation | PASS |
| rights_boundary | PASS |
| false_fact_promotion | PASS |

## Category coverage

| Category | Passed | Total |
|---|---:|---:|
| Fact | 2 | 2 |
| Identity | 2 | 2 |
| Document | 2 | 2 |
| Statement | 2 | 2 |
| Claim | 2 | 2 |
| Player-season Stats | 2 | 2 |
| Unknown | 2 | 2 |
| 跨粒度 | 2 | 2 |
| missing vs zero | 2 | 2 |
| unresolved | 1 | 1 |
| rights | 1 | 1 |
| duplicate / revision | 2 | 2 |
| coverage 局限 | 1 | 1 |
| 因果陷阱 | 2 | 2 |
| Stats 与媒体数字冲突 | 2 | 2 |

## Replay

```bash
python -m tests.regression.harness --reference-replay --output-json tests/regression/reports/golden_r1_report.json --output-md tests/regression/reports/golden_r1_report.md
```
