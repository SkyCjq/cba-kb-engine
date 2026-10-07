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
pip install -r requirements.lock
```

### 1.2 环境体检
检查 Python 运行时与依赖状态：
```bash
cba-kb doctor
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
cba-kb source-intake --input <SOURCE_FILE> --output normalized.json

# 抽取声明 (Statement) 与主张 (Claim)
cba-kb statement-extract --input normalized.json --output statements.json
cba-kb claim-extract --input statements.json --output claims.json

# 生成只读研究视图 (Research View)
cba-kb research-view --statements statements.json --claims claims.json --output research_view.json
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
