# REQ-180-MENTION-01 Task Brief

Implement the frozen v1.8 `document_player_mention` link layer only. Base branch is `main`; re-frozen Stage-1 base SHA is `a001addabec3ca3d63ed136701b7c0c72480a216`. Production access is forbidden.

## Outcome

Create a deterministic private relation with grain `doc_id × player_uid` and fields:
`mention_status=same|not_same|undecided`, `mention_role=subject|mentioned`, `mention_method=exact_name|alias|manual|external_id`, `mention_confidence`, `evidence_ref`.

One document must support multiple players. Exact-name/alias discovery is candidate-only and MUST NOT promote to `same` by name alone. Unresolved, unauthorized, and blocked relations must remain visible in coverage; no silent truncation.

## Reuse, do not replace

Keep current Document Lane `doc_id`, raw archive, rights classification, and v1.7 per-target authorization unchanged. `PRIVATE_ACCEPTANCE_ONLY` never changes `public_export_allowed`. Do not modify MASTER/Facts/Event/Profile v1.7/release infrastructure.

Real canary: Drive source `1gOTwWRk2w2SP7fWuXVDEv-vWlsAvGx2m` normalizes to SHA-256 `0f98d91df284a9d42afda9da9ca5057b4dd208ca0c12aada0aa67027706265ec` / `doc_0f98d91df284a9d42afda9da`; the article has one subject plus multiple named people. REQ-170 approval sheet `1iN6SxHp2FLCk3gsRYRQMh4VVj_ZzFKiisPznvUzQjgM` proves current rights model can authorize copyrighted/private evidence separately for ChatGPT, Gemini Notebook, and WorkBuddy.

## Upstream identity compatibility

`REQ-180-IDENTITY-01` is now merged into `main`. Treat `src/cba_kb/player_identity.py` as read-only upstream authority. Mention may consume opaque `player_uid` semantics but must not alter Identity behavior. Because both requirements share `src/cba_kb/cli.py`, focused regression now also includes `tests/test_player_identity.py` and `tests/test_cli_identity_v18.py`.

## Allowed code scope

`src/cba_kb/document_mentions.py`, minimal `src/cba_kb/cli.py`, new mention/CLI tests, plus the three requirement contract files. Any other path change requires STOP + re-freeze. `src/cba_kb/player_identity.py` is explicitly must-not-change.

## Hard gates

MASTER/Facts/Event ZERO DIFF; Document Lane/doc_id/rights ZERO DIFF; false implicit identity assertion = 0; one-doc-many-player supported; `undecided` visible; deterministic bytes/hash; private mappings never enter Git; missing Private Instance fails closed. Run focused tests only; full regression belongs to GitHub Actions.
