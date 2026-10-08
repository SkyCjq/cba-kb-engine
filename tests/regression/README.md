# Regression baseline harness

`golden_set_r1.json` is the byte-for-byte frozen 27-question input for
`REQ-202-REGRESSION-BASELINE-01`. The scorer has its SHA-256 embedded in code
and checks it before parsing the questions, reading candidate responses,
calculating scores, or writing reports.

Run the deterministic harness self-check:

```bash
python -m tests.regression.harness \
  --reference-replay \
  --output-json tests/regression/reports/golden_r1_report.json \
  --output-md tests/regression/reports/golden_r1_report.md
```

`FROZEN_REFERENCE_REPLAY` deliberately replays expected answers. It validates
the freeze guard, scoring path, hard gates, and deterministic report rendering;
it is not a measurement of product performance.

To score a candidate, provide this complete response shape:

```json
{
  "schema_version": "cba-kb.regression-responses.v1",
  "mode": "CANDIDATE",
  "system_under_test": "candidate identity",
  "items": [
    {
      "id": "R1-001",
      "actual_answer": "candidate answer",
      "false_fact_promotion": false
    },
    {
      "id": "R1-007",
      "actual_answer": "candidate answer",
      "false_fact_promotion": false,
      "attribution_correct": true
    }
  ]
}
```

Every frozen question must appear exactly once. `Statement` entries additionally
require the independently assessed `attribution_correct` boolean. The harness
normalizes answers to Unicode NFC, collapses whitespace, and then uses exact
matching. It records Statement and attribution precision without making either
a release threshold. Any answer failure, hard-gate failure, or non-zero false
fact promotion yields `FAIL`.

Run a candidate replay:

```bash
python -m tests.regression.harness \
  --responses /path/to/responses.json \
  --output-json /path/to/report.json \
  --output-md /path/to/report.md
```

The command returns exit code 0 for `PASS`, 1 for a scored `FAIL`, and raises
without producing reports if the frozen hash or input contract is invalid.
