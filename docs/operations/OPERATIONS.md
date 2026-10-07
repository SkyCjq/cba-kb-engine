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
pip install -r requirements.lock
```

### 1.2 System Health Check
Verify your local runtime environment and Python dependencies:
```bash
cba-kb doctor
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
cba-kb source-intake --input <SOURCE_FILE> --output normalized.json

# Extract statements and claims
cba-kb statement-extract --input normalized.json --output statements.json
cba-kb claim-extract --input statements.json --output claims.json

# Build read-only research view
cba-kb research-view --statements statements.json --claims claims.json --output research_view.json
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
