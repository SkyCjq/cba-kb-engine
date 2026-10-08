# CBA-KB 运维与操作手册

[English](OPERATIONS.md)

本手册面向两类使用者提供标准操作路径：
1. **公开引擎使用者（Public Engine Users）**：复用开源管线、运行本地抽取与离线验证；
2. **私有实例运维者（Private Instance Operators）**：管理真实语料、Google Drive 同步与凭据。

---

## 1. 公开引擎使用者快速路径

公开引擎使用者**不需要**访问私有 Google Drive，也**不需要**私有凭据。所有核心处理均可在本地文件或合成样例上离线运行。

### 1.1 环境初始化
```bash
git clone https://github.com/SkyCjq/cba-kb-engine.git
cd cba-kb-engine

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

### 1.2 环境体检
检查 Python 运行时与依赖状态：
```bash
make doctor
```

### 1.3 运行离线回归测试
验证代码逻辑与语义契约：
```bash
make test
```

### 1.4 研究管线：语料摄入与抽取
使用本地文件或合成样例运行事实与声明抽取：
```bash
# 规范化输入文档
cba-kb source-intake \
  --input <SOURCE_FILE> \
  --source-provider local_file \
  --source-locator <公开或本地定位符> \
  --output normalized.json

# 抽取声明 (Statement) 与主张 (Claim)
cba-kb statement-extract \
  --normalized-document normalized.json \
  --output statements.json
cba-kb claim-extract \
  --statements statements.json \
  --claim-text "尚未核验的研究主张" \
  --output claim.json

# 生成只读研究视图 (Research View)
cba-kb research-view \
  --name <对象名称> \
  --statements statements.json \
  --claims claim.json \
  --output research_view.json
```

### 1.5 Clean-room 合成冒烟

此流程只使用仓库内的 CC0-1.0 合成 fixtures。执行环境不得包含
`CBA_KB_INSTANCE_ROOT`、`CBA_KB_SOURCE_POLICY_JSON`、`CBA_STATS_AES_KEY`
或 `GOOGLE_APPLICATION_CREDENTIALS`。

```bash
unset CBA_KB_INSTANCE_ROOT CBA_KB_SOURCE_POLICY_JSON CBA_STATS_AES_KEY GOOGLE_APPLICATION_CREDENTIALS

# 初始化本地合成实例和输出目录。
rm -rf .cleanroom
mkdir -p .cleanroom/synthetic-instance/config .cleanroom/output

# 身份示例与校验。
cba-kb --instance-root .cleanroom/synthetic-instance identity-write \
  --input tests/fixtures/cleanroom/identity_registry.json
cba-kb --instance-root .cleanroom/synthetic-instance identity-validate \
  --input tests/fixtures/cleanroom/identity_registry.json

# 导入并规范化显式声明为公开安全的 Source。
cba-kb source-intake \
  --input tests/fixtures/cleanroom/source.txt \
  --source-provider synthetic_fixture \
  --source-locator cc0://cleanroom/source.txt \
  --rights-classification public \
  --public-export-allowed \
  --output .cleanroom/output/normalized.json

# 抽取包含已知身份与 unresolved actor 的 Statement。
cba-kb statement-extract \
  --normalized-document .cleanroom/output/normalized.json \
  --identity-registry tests/fixtures/cleanroom/identity_registry.json \
  --output .cleanroom/output/statements.json

# 离线 Stats 样本、校验以及 missing 与 zero 保真。
cba-kb stats-ingest \
  --season 2099 \
  --offline-payload tests/fixtures/cleanroom/stats_payload.json \
  --identity-registry tests/fixtures/cleanroom/identity_registry.json \
  --output .cleanroom/output/stats.json
cba-kb stats-validate \
  --input .cleanroom/output/stats.json \
  --output .cleanroom/output/stats_validation.json

# 生成只读输出。
cba-kb research-view \
  --name 合成球员甲 \
  --player-uid SYNTH_PLAYER_0001 \
  --statements .cleanroom/output/statements.json \
  --stats .cleanroom/output/stats.json \
  --output .cleanroom/output/research_view.json
```

CI 同等的语义断言（含 `review_required` 和无私有配置时 fail-closed）执行：

```bash
python scripts/run_cleanroom_smoke.py --output .cleanroom/ci-smoke
python -m pytest -q tests/test_cleanroom_operations.py
```

---

## 2. 私有实例运维者路径

> [!NOTE]
> 运行私有实例**不是**公开引擎使用或 clean-room 验证的前提条件。

**私有实例（Private Instance）**必须存放在与开源代码库完全独立的目录，用于存放凭证、生产白名单与真实 Google Drive 映射。

### 2.1 目录物理隔离
严禁将私有实例置于开源仓库内部：
```bash
export ENGINE_ROOT="/path/to/cba-kb-engine"
export INSTANCE_ROOT="/path/to/cba_kb_instance"
```

标准私有实例目录结构：
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

### 2.2 受控生产发布操作
对 Google Drive 的所有发布写入必须经过治理规划与回读核查：
```bash
# 生成发布规划
cba-kb plan --instance-root "$INSTANCE_ROOT"

# 执行受控发布
cba-kb publish --instance-root "$INSTANCE_ROOT"

# 回读验证
cba-kb verify --instance-root "$INSTANCE_ROOT"
```

---

## 3. 安全与数据红线检查清单
- [ ] 严禁在公开提交中包含真实 Google Drive ID
- [ ] 严禁硬编码本机绝对路径 (`/Users/...`)
- [ ] 严禁提交 OAuth 密钥与 Token 凭证
- [ ] 生产写入必须满足单写者互斥与严格回读核验
