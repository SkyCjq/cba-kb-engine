# Contributing to CBA-KB

Thank you for your interest in contributing to the CBA-KB Engine!

## 1. Public Engine vs. Private Instance Boundary

CBA-KB strictly separates the **Public Engine** (this open-source repository) from **Private Instances** (private deployments, proprietary datasets, production Drive folders, and credentials).

### Cardinal Rules:
1. **Never Import or Depend on Private Instance Code**: The Engine must remain completely independent and self-contained. Engine code must never import private modules or hardcode private infrastructure assumptions.
2. **Never Commit Credentials or Private Artifacts**: Do not commit API keys, OAuth credentials, tokens, real Google Drive folder IDs, or private player/club correspondence.
3. **No Proprietary Corpora**: Test fixtures must be synthetic or public-safe under CC0-1.0. Do not commit raw copyrighted news articles, scanned PDFs, or unredacted spreadsheets.

## 2. Right to Submit / Developer Certificate of Origin (DCO)

By submitting a Pull Request to this repository, you certify that:
- You authored the contribution, or you have the legal right to submit the contribution under the Apache License 2.0;
- The contribution does not infringe any third-party patent, trademark, trade secret, or copyright;
- The contribution is provided under the terms of the Apache License 2.0 (for code) and CC BY 4.0 (for documentation).

## 3. Pull Request Guidelines

1. **Branch Naming**: Use descriptive branch names: `feat/...`, `fix/...`, `docs/...`.
2. **Target Branch**: All PRs must target `main`. Direct pushes to `main` are blocked.
3. **Tests & CI**: All existing tests must pass cleanly offline without requiring network access or private credentials (`make test` or `PYTHONPATH=src:legacy:. pytest`).
4. **Code Quality**: Follow PEP 8 style guidelines. Ensure explicit fail-closed error handling.
