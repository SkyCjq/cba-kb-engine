# REQ-180-PROD-RELEASE-01 — First Frozen Requirement

Status: `FROZEN_SPEC`; `READY_TO_CODE = YES`; `freeze_count = 1`; `re_freeze = NO`.
Human approval: `批准 FIRST_FREEZE`.

This specifies one cumulative v1.8.0 Production release: `v1.7 + v1.8 -> v1.8.0-1`. It does not authorize a separate v1.7.0 release.

```text
target_release_id: v1.8.0-1
predecessor_release_id: v1.6.1-1
previous_production_code_sha: 81bd581fafbccb602f9ecaf9aaefca4533be69a4
product_candidate_sha: 7b73997dca8fb104ee51d1353183f8eda8532583
release_execution_sha: TO_FREEZE_AFTER_IMPLEMENTATION_MERGE
final_target_inventory: TO_BE_PROVEN_BY_POST_IMPLEMENTATION_PROJECT
production_authorized: false
end_gate: WEB_REVIEW_MERGE_AND_READ_ONLY_PROJECT
```

Allowed paths are exactly the five entries in `task.yaml`. The release engine, Drive/transport/native code, canonical or production facts/data, production manifest/release status before publish, workflows, dependencies, taxonomy, and release schema/state-machine expansion are forbidden. Production remains forbidden.
