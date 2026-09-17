# REQ-180-IDENTITY-01 Task Brief

目标：在 `main@d2771afbecc33ad9ce91bf34a4adce4c333e5821` 上实现 v1.8 Player Identity relation layer；不改 MASTER，不做文档 Mention/Profile v2，不碰 production。

**必须实现**：新增 `player_identity.py`，提供 `player / player_alias / player_record_link` schema、validator、deterministic serialization/hash、candidate discovery 与 Private Instance I/O；`player_uid` 必须 opaque，`record_key × player_uid` 支持 `same/not_same/undecided`；exact-name/alias 不得自动确认身份。CLI 只操作显式 Private Instance。

**允许改动**：Requirement 三文件、`src/cba_kb/player_identity.py`、`src/cba_kb/cli.py`、`tests/test_player_identity.py`、`tests/test_cli_identity_v18.py`。其他路径均不可改；尤其 `master.py`、`player_profile.py`、Document Lane、consumer、release/Drive、workflow/config 不得改。

**测试**：先跑新增 focused tests，再跑 `test_player_profile.py`、`test_master.py`、`test_engine_instance_boundary.py`、`test_security_guards.py`、`test_current_history_separation.py`。不要在 Codex 内跑 full regression；full regression 属于 GitHub Actions。

**硬门**：MASTER.csv `0aafb6a17748f8d13b83736ac22b5ba3d5947a924e1f987d9ba249bcd6879e7f`、3465 rows/record_keys 必须 ZERO DIFF；synthetic same-name/ambiguous alias 必须 fail closed；real identity canary 必须在 Private Instance 做独立验收，false merge=0，undecided 可见。

**停止条件**：需要新增 allowed path、需要修改 canonical facts/Master/Profile v1.7、需要外部 API、需要 production access、或 baseline 不再是 `d2771afbecc33ad9ce91bf34a4adce4c333e5821` 时立即 STOP，回 Stage 1 re-freeze。

Stage 2 尚未授权。本文件仅为 frozen static contract 摘要。
