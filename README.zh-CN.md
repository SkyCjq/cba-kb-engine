# CBA-KB 知识库引擎

[English](README.md)

**CBA-KB** 是面向中国男子篮球职业联赛（CBA）的证据优先型数据与研究引擎。它将异构源材料转化为可溯源的结构化事实、具备实体身份的文档证据、陈述（Statements）、主张（Claims）、核验队列（Verification Queue）与只读研究视图（Research View），支持人类研究员与 AI 系统消费，且绝不把证据与事实混为一谈。

当前生产发布版本：**v2.0.2 — COMPLETE**。

---

## 1. CBA-KB 是什么

CBA-KB 为中国篮球研究提供高可复现性的数据与分析管线。它负责抽取并建模联赛注册事实、球员身份、文档级证据、原始陈述与研究主张，同时严格保持溯源证据链、不确定性语义与权利边界。

公开开源仓库仅包含可复用的**引擎（Public Engine）**：数据契约、适配器、确定性处理逻辑、合成测试用例与 CLI 工具。

## 2. 三条 North Star 原则

1. **证据优先的真理层级**：
   > 核心法则：**陈述 ≠ 事实。证据 ≠ 事实。未知 ≠ 0。**
   引述与观点绝不在未经独立核验的情况下静默坍缩为联赛事实。
2. **确定性与 Fail-Closed 闭环**：
   凡遇到歧义、缺失记录或未核实主张，系统直接路由至显式核验队列，绝不猜测或静默丢弃。
3. **严格的权利与溯源追踪**：
   每一个事实、陈述与文档均保留指向源头的不可篡改加密指纹链路。

## 3. Public Engine 与 Private Instance 边界

| 维度 | 公开引擎 Public Engine (本仓库) | 私有实例 Private Instance (私有部署) |
| :--- | :--- | :--- |
| **代码与工具** | **Apache-2.0** 开源许可 | 运维脚本与专有部署配置 |
| **文档与手册** | **CC BY 4.0** 知识共享许可 | 私有研究笔记与内部运维日志 |
| **数据与样例** | **CC0-1.0** 合成/公开安全样例 | 真实语料库、版权新闻、私有 Drive ID |
| **存储与密钥** | 零凭证、零私有云 ID 提交 | 私有 Google Drive 映射、OAuth 令牌 |

## 4. 版本能力演进线

- **v1.5.x**：基线球员注册模型、Excel 导出确定性与基础 Drive 发布。
- **v1.8.x**：多通道文档证据、稳定球员身份注册表与消费端闭环管控。
- **v1.9.0**：语料摄入契约 v1、陈述模型、主张提取 MVP、核验队列与只读研究视图。
- **v2.0.x**：Drive 放置安全守卫、Fail-closed 消费端画像与发布编排加固。
- **v2.0.2**：开源就绪、Apache-2.0 协议落地、CC BY 4.0 文档体系与历史记录脱敏。
- **v2.0.4（开发中，PR-1）**：本地优先 TXT Inbox 生命周期——轮询自动发现、持久 Source/Document 注册、权利感知查询、CLI 状态/报告。属开发期能力，**不是**生产发布。

## 5. 路线图

- **v2.1**：扩展 Person 实体层（教练、裁判、俱乐部管理人员）及历史流转图谱。
- **v2.2**：社区数据集分发（ODC-By/ODbL）与自动化基准测试套件。

## 6. 5 分钟快速上手

```bash
# 1. 克隆仓库
git clone https://github.com/SkyOneBranch/cba-kb-engine.git
cd cba-kb-engine

# 2. 创建并激活虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .

# 3. 运行环境自检
make doctor

# 4. 运行离线测试套件
make test
```

可编辑安装会创建文档所使用的 `cba-kb` 命令。若要在无任何私有配置的
条件下，用 CC0 合成数据复现公开流程，请继续执行
[clean-room 冒烟步骤](docs/operations/OPERATIONS.zh-CN.md#15-clean-room-合成冒烟)。

### 查看 CLI 命令：
```bash
cba-kb --help
```

### 本地优先 Inbox 生命周期（v2.0.4 开发中，PR-1）

当前生产发布版本保持 **v2.0.2 — COMPLETE**。以下命令演练开发中的 PR-1
能力：本地 `.txt` 文件自动发现、Source/Document 注册与去重、权利感知
查询、生命周期状态。不需要 Google 凭证；一切运行在你自己指定的外置
实例目录中。

```bash
# 1. 创建外置实例（位于引擎目录之外）
mkdir -p /tmp/cbakb-demo/{config,inbox/documents}

# 2. 放入合成 TXT 及其权利声明 sidecar
cat > /tmp/cbakb-demo/inbox/documents/synthetic-news.txt <<'EOF'
Synthetic CBA Bulletin
The Harbor Falcons won a fictional preseason game.
The team scored 88 points in this synthetic report.
EOF
cat > /tmp/cbakb-demo/inbox/documents/synthetic-news.txt.source.json <<'EOF'
{
  "schema_version": 1,
  "title": "Synthetic CBA Bulletin",
  "origin": "synthetic_fixture",
  "source_url": null,
  "rights": {
    "classification": "public",
    "public_export_allowed": true,
    "evidence": [
      {"type": "author_attestation", "license": "CC0-1.0",
       "description": "Original synthetic test content"}
    ]
  }
}
EOF

# 3. 扫描两次：文件须在连续两次扫描中完全一致才会处理（稳定性门）
cba-kb --instance-root /tmp/cbakb-demo inbox-scan
cba-kb --instance-root /tmp/cbakb-demo inbox-scan

# 4. 或用持续轮询代替手工扫描（Ctrl-C 停止）
cba-kb --instance-root /tmp/cbakb-demo inbox-poll --interval-seconds 30

# 5. 生命周期状态与权利感知查询
cba-kb --instance-root /tmp/cbakb-demo archive-status --all
cba-kb --instance-root /tmp/cbakb-demo archive-query --keyword "Harbor Falcons"
cba-kb --instance-root /tmp/cbakb-demo archive-query --title "Synthetic CBA Bulletin"
cba-kb --instance-root /tmp/cbakb-demo archive-query --visibility public --keyword "Harbor"
```

安全约束（默认 fail-closed）：

- 每个 `.txt` 都需要同名 `.txt.source.json` sidecar，声明标题、来源
  与权利。缺失/非法元数据、非 UTF-8 字节、无可验证证据的公开声明，
  一律进入 `REVIEW_REQUIRED`——绝不静默入库，绝不进入公开搜索。
- `local` 可见性（默认）面向该外置实例的操作者；`public` 可见性只返回
  有真实权利证据的合成样例。
- Inbox 原件永不重命名、移动或删除（`archive_status` 保持
  `NOT_REQUESTED`）。
- 所有状态保存在 `<instance>/data/` 下；引擎源码目录永不被写入。

PR-1 不包含（仍属 v2.0.4 后续工作）：Drive 侧发现与适配器、受控归档
移动/保留、完整跨后端恢复、Production 发布。

## 7. 语义原则

- **关注点分离**：陈述（Statement）表示源头说了什么；主张（Claim）表示提炼出的推论；事实（Fact）表示经官方核验的联赛记录。
- **歧义时 Fail-Closed**：当实体无法唯一匹配到球员 UID 时，保持未决状态或路由至 `verification-queue`。
- **禁止自行跃迁 / 零幻觉**：AI 模型与解析器绝不允许在没有官方 API 或人工核实的情况下将抽取陈述提升为联赛事实。

## 8. 数据与权利边界

- **代码许可**：引擎代码采用 [Apache-2.0 许可证](LICENSE)。
- **文档资料**：文档采用 [CC BY 4.0 许可证](DATA_LICENSE.md)。
- **测试样例**：合成测试用例归入公有领域 [CC0-1.0](DATA_LICENSE.md)。
- **第三方材料**：联赛通知、媒体报道与摄影作品版权归原权利人所有，详见 [`THIRD_PARTY_DATA.md`](THIRD_PARTY_DATA.md)。

## 9. 版本号说明与规范

- 当前版本：**2.0.2**
- 本项目遵循 [语义化版本 2.0.0 (SemVer)](https://semver.org/lang/zh-CN/) 规范。
- `pyproject.toml`、`src/cba_kb/__init__.py` 以及 Git 标签中的版本号严格同步。

## 10. 文档地图、贡献与许可

- **操作手册**：[运维与操作手册](docs/operations/OPERATIONS.zh-CN.md) ([English](docs/operations/OPERATIONS.md))
- **参与贡献**：提交 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [SECURITY.md](SECURITY.md)。
- **开源许可**：[Apache License 2.0](LICENSE) | [NOTICE](NOTICE) | [数据许可策略](DATA_LICENSE.md)。
