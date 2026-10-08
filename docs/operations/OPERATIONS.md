# CBA-KB Operations Manual

[中文版](OPERATIONS.zh-CN.md)

This manual provides operational workflows for both **Public Engine Users** (reusing the open-source pipeline) and **Private Instance Operators** (managing production corpora and Google Drive synchronizations).

---

## 1. Quick Start for Public Engine Users

Public engine users do not need access to private Google Drive storage or private credentials. All standard commands run offline against local files or synthetic fixtures.

### 1.1 Environment Setup
```bash
git clone https://github.com/SkyCjq/cba-kb-engine.git
cd cba-kb-engine

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

### 1.2 System Health Check
Verify your local runtime environment and Python dependencies:
```bash
make doctor
```

### 1.3 Running Offline Verification
Execute the offline contract and regression suite:
```bash
make test
```

### 1.4 Research Flow: Ingestion and Extraction
Run source normalization, statement extraction, and research view synthesis using local fixtures:
```bash
# Normalize input document
cba-kb source-intake \
  --input <SOURCE_FILE> \
  --source-provider local_file \
  --source-locator <PUBLIC_OR_LOCAL_LOCATOR> \
  --output normalized.json

# Extract statements and claims
cba-kb statement-extract \
  --normalized-document normalized.json \
  --output statements.json
cba-kb claim-extract \
  --statements statements.json \
  --claim-text "Unverified research claim" \
  --output claim.json

# Build read-only research view
cba-kb research-view \
  --name <SUBJECT_NAME> \
  --statements statements.json \
  --claims claim.json \
  --output research_view.json
```

### 1.5 Clean-room Synthetic Smoke

This procedure uses only the repository's CC0-1.0 fixtures. It must be run with
no `CBA_KB_INSTANCE_ROOT`, `CBA_KB_SOURCE_POLICY_JSON`, `CBA_STATS_AES_KEY`, or
`GOOGLE_APPLICATION_CREDENTIALS` value in the environment.

```bash
unset CBA_KB_INSTANCE_ROOT CBA_KB_SOURCE_POLICY_JSON CBA_STATS_AES_KEY GOOGLE_APPLICATION_CREDENTIALS

# Initialize a local synthetic instance and output directory.
rm -rf .cleanroom
mkdir -p .cleanroom/synthetic-instance/config .cleanroom/output

# Identity example and validation.
cba-kb --instance-root .cleanroom/synthetic-instance identity-write \
  --input tests/fixtures/cleanroom/identity_registry.json
cba-kb --instance-root .cleanroom/synthetic-instance identity-validate \
  --input tests/fixtures/cleanroom/identity_registry.json

# Source import and normalization with explicit public rights.
cba-kb source-intake \
  --input tests/fixtures/cleanroom/source.txt \
  --source-provider synthetic_fixture \
  --source-locator cc0://cleanroom/source.txt \
  --rights-classification public \
  --public-export-allowed \
  --output .cleanroom/output/normalized.json

# Statement extraction with known and unresolved actors.
cba-kb statement-extract \
  --normalized-document .cleanroom/output/normalized.json \
  --identity-registry tests/fixtures/cleanroom/identity_registry.json \
  --output .cleanroom/output/statements.json

# Offline Stats sample, validation, and missing-versus-zero preservation.
cba-kb stats-ingest \
  --season 2099 \
  --offline-payload tests/fixtures/cleanroom/stats_payload.json \
  --identity-registry tests/fixtures/cleanroom/identity_registry.json \
  --output .cleanroom/output/stats.json
cba-kb stats-validate \
  --input .cleanroom/output/stats.json \
  --output .cleanroom/output/stats_validation.json

# Generate the read-only output.
cba-kb research-view \
  --name 合成球员甲 \
  --player-uid SYNTH_PLAYER_0001 \
  --statements .cleanroom/output/statements.json \
  --stats .cleanroom/output/stats.json \
  --output .cleanroom/output/research_view.json
```

The CI-equivalent semantic assertions, including `review_required` and private
configuration fail-closed behavior, are run with:

```bash
python scripts/run_cleanroom_smoke.py --output .cleanroom/ci-smoke
python -m pytest -q tests/test_cleanroom_operations.py
```

---

## 2. Private Instance Operator Guide

> [!NOTE]
> Operating a Private Instance is **NOT** required for using the Public Engine or verifying clean-room builds.

A **Private Instance** is maintained in a completely separate directory from the Engine repository to store credentials, production allowlists, and Google Drive mappings.

### 2.1 Directory Separation
Never clone or locate the Private Instance inside the Engine Git repository:
```bash
export ENGINE_ROOT="/path/to/cba-kb-engine"
export INSTANCE_ROOT="/path/to/cba_kb_instance"
```

The Private Instance directory must follow this structure:
```text
<INSTANCE_ROOT>/
├── config/
│   ├── production.json
│   ├── import_inventory.json
│   └── source_policy.json
├── .credentials/
│   ├── credentials.json
│   └── token.json
├── fixtures/real/
└── data/
```

### 2.2 Controlled Drive Operations
All write operations to Google Drive must be executed through the governed release planner and verifier:
```bash
# Generate execution plan
cba-kb plan --instance-root "$INSTANCE_ROOT"

# Publish governed candidate
cba-kb publish --instance-root "$INSTANCE_ROOT"

# Readback verification
cba-kb verify --instance-root "$INSTANCE_ROOT"
```

---

## 3. Security and Data Hygiene Checklist
- [ ] No Google Drive IDs in public git commits
- [ ] No hardcoded local machine paths (`/Users/...`)
- [ ] No API tokens or OAuth secrets in repo
- [ ] All production writes protected by fail-closed readback and single-writer locks
