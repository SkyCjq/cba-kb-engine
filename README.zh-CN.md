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

## 5. 路线图

- **v2.1**：扩展 Person 实体层（教练、裁判、俱乐部管理人员）及历史流转图谱。
- **v2.2**：社区数据集分发（ODC-By/ODbL）与自动化基准测试套件。

## 6. 5 分钟快速上手

```bash
# 1. 克隆仓库
git clone https://github.com/SkyCjq/cba-kb-engine.git
cd cba-kb-engine

# 2. 创建并激活虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock

# 3. 运行环境自检
cba-kb doctor

# 4. 运行离线测试套件
make test
```

### 查看 CLI 命令：
```bash
cba-kb --help
```

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
