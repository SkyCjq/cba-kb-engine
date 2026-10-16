# PR #184 Q2 Round 5 root-cause report

## Scope and evidence identity

- Repository: `SkyOneBranch/cba-kb-engine`
- PR: <https://github.com/SkyOneBranch/cba-kb-engine/pull/184>
- Round 5 reviewed HEAD: `5861c075007e14a2dcd1021b4e3f9a5155b989e5`
- Base: `1071203abad6ede606480c7fa0c66bbdc9e00920`
- Source report: complete original conversation `6ad22c08-7578-83ea-b711-f47cb948de6d`, titled `独立安全审查 Verdict`; the report identifies itself as `Independent Q2 Security Review — Round 5` and is bound to the SHA above.
- Product contract: Notion page `3f9ad272535b81989f9bced84b3b8317`, version tree `3f3ad272535b81fa8090ec74993f944f`, and LACD SOP `3f7ad272535b81aea143dabe626a42c2`, read on 2026-10-16.

This repair does not run real Drive M4, does not access OAuth material, does not change M5 or production configuration, and does not claim that v2.0.4-2 is complete.

## F-01 — source snapshot / execution identity TOCTOU

### Original security promise

Only Python source from the reviewed clean Engine commit may execute before a Drive mutation. Bytecode caches, ambient `PYTHONPATH`, external packages, preloaded `cba_kb` modules, and source bytes that do not match Git HEAD must fail closed.

### Root cause

At `5861c075`, `_SourceOnlyLoader.exec_module()` called `get_data()` to compute the registered SHA-256, then delegated to `SourceFileLoader.exec_module()`. The superclass called the overridden `get_code()`, which called `get_data()` again and compiled the second result. The registry could therefore describe bytes A while Python executed bytes B.

### Reproduction before the fix

A loader whose first `get_data()` returned `VALUE = "HASHED_BENIGN"` and whose second returned `VALUE = "EXECUTED_ATTACKER"` produced:

```text
{'executed': 'EXECUTED_ATTACKER',
 'registered_sha256': 'ae222aecc0a489dc097249007352bf39a8801ae68b3fb6c0383e125ef647e812',
 'benign_sha256': 'ae222aecc0a489dc097249007352bf39a8801ae68b3fb6c0383e125ef647e812',
 'remaining_reads': 0}
```

This is a concrete validation/execution identity failure even though reliable exploitation of the narrow live filesystem window was not separately claimed.

### Repair

`_SourceOnlyLoader.exec_module()` now performs exactly one source read and derives all of the following from that same immutable byte string:

1. Git blob SHA-1;
2. Git HEAD comparison when the loader is bound to the Engine root;
3. SHA-256 execution evidence;
4. `compile()` input;
5. executed code object.

The registry additionally binds the resolved source path, actual source blob, expected Git blob, SHA-256, module name, and loader identity. The later mutation gate rechecks disk content and current Git identity against the registered execution snapshot.

### Regression proof

`test_source_only_loader_uses_one_snapshot_for_hash_and_execution` supplies different first and hypothetical second reads. The repaired loader reads once, executes the verified snapshot, and registers hashes of that same snapshot. Existing external-source, forged preloaded-module, `.pyc`, Git blob, and all-loaded-module checks remain enabled.

### Remaining issue / Human decision

No F-01 security-boundary decision is required. Exact-head independent Q2 is still required after the PR HEAD changes.

## F-02 — macOS ACL and ancestor permission audit

### Original security promise

The independent consumption directory and its ancestors are expected to be a trustworthy root-controlled macOS path: no symlink target, root ownership, canonical wheel group, no unsafe POSIX write permissions, and no ACL allow entry that grants an unauthorized principal write, add, delete, metadata-write, security-write, or ownership-change capability. Missing or unparseable evidence fails closed.

This check protects the local authorization ledger only. It does not change Google Drive rights, OAuth scope, approved folder IDs, or the product's Drive data boundary.

### Root cause

Three finite implementation gaps existed at `5861c075`:

1. the ancestor loop stopped before `/`;
2. the `ls -lde` parser could accept an ACL marker with no ACE list and could skip semantically malformed records;
3. missing `wheel` could silently fall back to another group or numeric GID 0.

The audit also needed a closed action vocabulary so an unknown future or corrupted permission token could not be treated as harmless.

### Repair

- The ancestor walk now audits `/` as well as every intermediate ancestor.
- ACL output requires a coherent header/ACE relationship, contiguous numeric indices, a nonempty principal identifier (including native UUID form), an explicit `allow`/`deny`, and a nonempty closed set of known actions.
- Unknown/missing actions and malformed/incomplete output are rejected.
- On macOS, `wheel` must exist and resolve to GID 0; there is no group-name fallback. Non-macOS unit execution uses GID 0 only to exercise logic and is not represented as native macOS proof.
- Unsafe `allow` actions remain fail-closed.

### Actual permission consequence

An unauthorized actor can replace or delete ledger records only if the actor has directory-entry mutation rights (`add_file`, `add_subdirectory`, `delete_child`, `delete`) or can alter the file/metadata/ACL/ownership through an allow ACE or writable ancestor. Read/search-only ACLs do not provide replacement/deletion authority. A same-UID owner of a ledger file is a separate F-03 problem even if directory ownership and ACL parsing are correct.

### Regression proof and evidence limit

New tests cover root inclusion, ACL-marker-without-entries, unknown actions, missing/nonzero wheel, and the host's native macOS UUID-style ACL output. The native test accepts a real `deny delete` ACE and rejects a real `allow delete` ACE. Existing tests continue to cover named-user/group write ACLs, ancestor ACLs, command failure, malformed records, unsafe POSIX modes, non-root ownership, and symlinks.

The current machine is macOS and `/bin/ls -lde /` was read successfully, but this repair did not create or mutate system ACLs and did not provision `/etc/cba-kb/consumed_authorizations`. Therefore the parser and traversal are dynamically tested, while a real root-owned target-directory provisioning/readback remains `NOT_VERIFIED` and cannot be replaced by Linux CI.

### Remaining issue / Human decision

The bounded F-02 code defects are repaired. Independent Q2 should assess the strict parser against the intended deployed macOS version. This does not solve F-03 and does not justify real M4 while the authorization boundary is undecided.

## F-03 — `UF_IMMUTABLE` and authorization anti-rollback

### Original security promise

The documentation and script described the dual ledger plus mandatory `UF_IMMUTABLE` as anti-rollback replay protection, including the same-UID attacker scenario from earlier Q2 rounds.

### What the implementation really guarantees

- First normal consumption: `O_CREAT | O_EXCL` creates both records, fsyncs them, sets `UF_IMMUTABLE`, and reads the flag back.
- Normal repeat while either record exists: rejected before any Drive mutation.
- Missing `chflags`, missing `UF_IMMUTABLE`, flag-set failure, missing ledger directory, invalid ownership/mode/ACL, and partial ledger I/O: fail closed.
- Instance-ledger deletion alone: replay is rejected by the independent record.
- Same-UID rollback of both user-owned records: **not prevented**. The file owner can clear the user immutable flag, delete both files, and allow `O_EXCL` creation again while the signed authorization remains valid.
- System-level non-rollback: **not provided** by the current ordinary-user process.

### Concrete reproduction

`test_attack_b3_same_uid_can_clear_both_user_owned_ledgers_and_replay` consumes once, clears both flags as the owning UID, deletes both records, and consumes the same authorization again. It passes as a characterization of the unresolved boundary, not as a claimed defense.

### Bounded changes made

- No anti-rollback claim was deleted from the acceptance contract.
- Script and M4 documents now distinguish `O_EXCL` duplicate rejection from same-UID rollback resistance and system-level non-rollback.
- Unexpected ledger I/O exceptions are normalized to a security-critical `ValueError` after emitting partial-state recovery guidance.
- No root helper, daemon, sudoers entry, SUID program, or root-run Drive Engine was introduced.

### Options and cost

1. **Maintain the stronger same-UID anti-rollback promise.** Add a narrowly scoped privileged writer or another protection domain in which the ordinary Drive execution UID cannot remove records. This requires privileged installation, request validation, upgrade/rollback procedures, logging, failure recovery, and long-term platform maintenance. The whole Engine should not run as root.
2. **Change the threat model.** Explicitly accept that a fully malicious same-UID local process can roll back both local records, while retaining signature checks, expiry, exact SHA/folder/account binding, mutation-time revalidation, and `O_EXCL` protection against normal duplicates. This is a reduction of the previously claimed security boundary and therefore requires explicit Human approval.

### Decision

`HUMAN_SECURITY_BOUNDARY_DECISION_REQUIRED`

The current task does not authorize either a privileged component or acceptance of the weaker threat model. Real M4 remains blocked on this decision and subsequent exact-head independent Q2.

## Root-cause outcome

- F-01: repaired with a single verified execution snapshot.
- F-02: finite implementation gaps repaired; native provisioned target evidence remains outstanding.
- F-03: underlying same-UID design contradiction is proven and transparently documented; no unauthorized weakening or privileged redesign was performed.

Overall result: `PR184_PARTIAL_REPAIR_HUMAN_SECURITY_DECISION_REQUIRED`.
