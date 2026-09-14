# V1_5_1_REVIEW_2026-09-10

> **文档性质**：第三方实施核查意见（review / audit opinion）
> **核查时间**：2026-09-10
> **核查对象**：CBA-KB v1.5.1 Registration Facts Extension 实施结果
> **核查依据**：`CBA-KB v1.5.1.md`（1vQYwGHaqoWhjNQQBTofYityzKAd6X1uJ）Part A 需求 / Part C 测试方案
> **核查人**：Claude（外部审阅，非执行方）
> **结论**：**PASS WITH FINDINGS —— 对已声明的四个 source 完整执行且验收数字全部对上；
> 但四个 source 在实施前三小时已被两份上级文件取代，A2 验收基准因此失效**

> **本文件不是事实表。** 沿用 v1.1 §AA 惯例，AI 审计输入登记为
> `EXTRACTED / not_applicable`、`records_imported = 0`，不得反写 `20_data` 或
> `source_registry` 的业务状态。

---

# A. 核查边界

## A1. 已实读

```
CBA_外籍球员注册_SNAPSHOTS.xlsx   1avAxhNUTgm14MIdkgkHhia6_adSDGwLD   全文
CBA_球员注册_EVENTS.xlsx          1WtE63GIQxYPUBsfcRlhKCH8lJgyTZeGF   全文
20_data_结构化事实 目录清单        1nL4xomHNQInbskeYdqa45hHfleHhExW-
CBA-KB v1.5.1.md                 1vQYwGHaqoWhjNQQBTofYityzKAd6X1uJ   全文
两份上级来源文件                   1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR
                                 1eGrMDep9YMfPNy66M6-6Jwm9iYaF_gQj   全文
```

## A2. 未核查

```
CBA_2017-2027_国内球员注册_MASTER.xlsx 内部行数与 record_key 唯一性（见 F5）
source_registry.csv / manifest.csv 的 v1.5.1 登记状态
90_archive 下的 release report 与 before snapshot
GitHub 私有仓库代码（无凭据通道，见 V1_5_REVIEW §A1）
原始外援 XLSX 中 jersey_number 单元格的存储类型（见 D1）
```

---

# B. 验收对照 / Acceptance Verification

依 v1.5.1 §12「真实来源验收」逐项核对：

| 验收项 | 规范要求 | 实测 | 结论 |
|---|---|---|---|
| §12 A1 teams | 20 | 20（SNAPSHOTS 去重 club_id） | PASS |
| §12 A1 roster snapshots | 73 | 73 | PASS |
| §12 A1 cancellation events | 59 | 59（`sequence` 77–135 连续） | PASS |
| §12 A1 observed registrations | 132 | 73 + 59 = 132 | PASS |
| §12 A2 parsed records | 14 | 14 | PASS（但基准失效，见 E） |
| §R11 fact products | 3 | 3 | PASS |
| §9 MASTER file ID 稳定 | 不变 | `1Nb4-4rr…` 未变 | PASS |

EVENTS 总行数 = 14 domestic + 59 foreign = **73**。

> **更正**：本审阅方在 2026-09-10 早前的分析中从文本渲染统计出 58 条取消事件，
> 系统计遗漏。以实施结果 **59** 为准，与 v1.5.1 §12 A1 一致。

---

# C. 实现质量：优于规范要求之处

## C1. R5 raw/normalized 双列（实现扎实）

抓到了两个最难的样本：

```
name_en_raw = GEORGE KELL Ⅲ   (U+2162 罗马数字)
name_en_normalized = GEORGE KELL III

name_en_raw = LEONARD RANDALLⅡ
name_en_normalized = LEONARD RANDALLII
```

raw 保留原字符、normalized 供 provisional matching，未发生 raw 被规范化结果覆盖。
这是 R5 的核心立意，常见实现在此处会直接丢失 raw。

## C2. R7 年份推断标记区分正确

```
59 条外援取消事件   date_year_inferred = TRUE
14 条八一域内事件   date_year_inferred = FALSE
```

八一来源原文本身携带年份、无需推断，因此标记为 FALSE 是正确的。
说明该标记不是无条件刷成 TRUE，而是按是否真正发生推断赋值。

## C3. R3 窗口截止日语义未被误写

```
registration_window_deadline = 2021-02-27      （独立列）
disclosure_deadline                            （未写入）
raw_event_text 保留原文「notice_deadline: 2021-02-27（该赛季中期国内球员注册窗口截止日）」
```

R3 明令禁止的映射未发生。

## C4. R2 两个日期分离

```
田宇翔  event_date 2021-02-26  |  club_announcement_date 2021-03-01
石颜博  event_date 2021-02-27  |  club_announcement_date 2021-03-01
```

正是源文档反复强调不可混用的两个节点，均已分列并在 notes 中说明取值理由。

## C5. R10 处理有分寸

`verification_level` 规范为 `human_verified`，同时 `raw_event_text` 原样保留
`verification_level: manually_verified`，并保留 `source_url_primary` 与
`source_url_secondary`。既统一词表又未抹除原始声明。

## C6. R8 club 解析

`bayi` 正确作为 `from_club_id` 出现；冠名商名（九台农商银行、广州朗肽海本、
浙江方兴渡、北京北汽、新疆伊力特等）全部解析到稳定 `club_id`，未见 unresolved。

## C7. D4 provenance

每行均具备 `source_file_id` / `source_url_primary` / `source_page_or_row` /
`extraction_method` / `verification_level` / `raw_*_text`，满足 D4 最低要求
（但 raw 字段的可信度见 D1）。

---

# D. 确认缺陷 / Confirmed Defects

## D1. R6 球衣号码文本保留失效，且 raw 字段未能兜底（高）

SNAPSHOTS 中：

```
JAMES NUNNALLY   jersey_number = 0
CAMERON OLIVER   jersey_number = 0
```

而外籍来源文件（1eGrMDep…）2024-2025 注册表对同二人记为 **`00`**。

更严重的是 `raw_row_text` 同样为 `0`：

```
 | JAMES NUNNALLY | 詹姆斯·纳纳利 | 美国 | SG/SF | 0
```

**这说明 `raw_row_text` 并非源文件字面文本，而是由已被强制转换的值重新拼接而成。**

后果：
- R6「球衣号码必须按文本保留」失效；
- D4「provenance 可回溯到 source raw text」失去实质意义——
  raw 字段已被同一次转换污染，无法充当审计兜底。

需确认原 XLSX 单元格存储类型（文本 `"00"` 或数值 `0`）。但无论何种，
**`raw_row_text` 应由源单元格字面值拼接，而非由解析结果反推**。

现有回归仅覆盖 `01` 样本；真实数据中出现的是 `00`，测试未触达。

## D2. `raw_event_text` 分段边界落在标签上（低）

首条外援事件的 `raw_event_text` 含「备注：」前缀：

```
备注：8月21日天津先行者取消外籍球员DONELL COOPER（唐奈尔·库珀）的注册
```

其余 58 条无此前缀。不影响事实字段，但说明 roster/event 分段点取在备注标签处
而非标签之后，属解析残留。

## D3. `sequence` 一列两种语义（低）

```
外援事件   sequence = 源文件行号（77–135）
域内事件   sequence = 队内序号（1,2,3,4）
```

`event_key` 依赖该列做去重尚可，但该字段无法用于任何排序或计数语义。
建议拆为 `source_row_no` 与 `seq_in_club` 两列。

---

# E. 范围问题：上级来源未被处理（最高）

## E1. 事实

EVENTS / SNAPSHOTS 全部行的 `source_file_id` 仅有两个值：

```
16H0cxiItoSlGALeHce-E06pD_a3RFAmd   2024-2025 外援注册 XLSX
1C8BpmY_MMjFGnSOdllRsnVWlwPdfKViV   八一球员 2021 赛季中期转会核验 MD
```

而 `00_inbox_待处理` 在实施前已新增两份**上级合并核验文件**：

```
07:07  CBA_外籍球员优先续约权_交易_注册信息_二次核验完整修正版_2019-2027.md
       1eGrMDep9YMfPNy66M6-6Jwm9iYaF_gQj   66,483 B
07:26  CBA_赛季中国内球员人员流动_2019-2026_合并核验总表_v2.md
       1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR   24,464 B
```

实施发生于 12:54–13:37，两份文件早三小时以上到位，**未被读取**。

## E2. 后果一：A2 验收基准失效

国内合并总表明确声明**已将八一专项文件去重合并**，且 2020-2021 窗口
实际完成注册为 **22 人**（14 名原八一 + 8 名非八一自由球员）。

当前 EVENTS 缺失该窗口的 8 名非八一球员：

```
田桂森（山西→福建）  方渭博（广东→福建）  王旭（上海→南京同曦）
陈子安（北京控股→山西）  司坤（广州→山东，release_then_claim）
王政博（新疆→广州）  付磊（浙江稠州→广州）  李青翔（自由球员→广州）
```

跨赛季合计：合并总表可落库约 **46** 条国内赛季中流动
（2019-20 = 0，2020-21 = 22，2021-22 = 4，2022-23 = 1，2023-24 = 9，
2024-25 ≥ 6，2025-26 = 3 + 1 待补），当前入库 14 条，**缺口约 32 条**。

v1.5.1 §12 A2 写死「parsed records = 14」，该数字来自已被取代的来源，
不应继续作为验收基准。

## E3. 后果二：外籍数据仅覆盖单一赛季

SNAPSHOTS 全部为 `2024-2025 | snapshot_as_of 2025-03-31`。

外籍合并核验文件另含：

```
2020-2021 注册快照      45 行（官方 detail/411）
2022-2023 注册快照      约 52 行（官方 detail/642ccee86e）
2023-2024 注册快照      约 38 行（权威媒体复核，证据降级）
2025-2026 注册快照      约 74 行（官方 detail/69eac41eb0）
2025-2026 取消注册事件  约 41 条
```

合计约 250 行快照 + 41 条事件未入库。

## E4. 仍在 v1.5.1 范围之外的三类实体

以下属 v1.5.1 §3 Out of Scope 之外的新发现，无归宿：

```
优先续约权 priority rights          约 166 行（8 个目标赛季）
优先续约权交易                       快照 61 行；已二次确认真实交易 7 笔
复赛暂停/启用外援（2019-2020）        21 条，并引入第三种 player_type「亚外」
国内交易窗口 transaction_windows     约 15 行（2024-25、2025-26 各 3 个窗口）
```

**「优先续约权交易信息」页必须建模为动态状态快照，不是交易流水。**
`交易后获得优先续约权俱乐部 = /` 仅表示该时点未显示转移；
完成注册的球员会从后续快照中删除（萨林杰、奥莫特、富兰克林均如此）。
若按现有 snapshot 语义直接导入，将得出「这三笔交易从未发生」的错误结论。

---

# F. 受控词表与迁移债

## F1. `event_type` 仅两值，语义被压平

14 条八一记录全部落为 `registration_change`。而来源文档明确警告：

> 14 人均应按「自由球员认领/签约」理解，**不应在规则字段中统一写作普通转会**。

该语义目前仅存在于 `registration_method` 的自由文本值
`自由球员认领 / 签约`（含斜杠，非枚举）。

国内合并总表已提供现成受控词表：

```
free_agent_claim / player_swap / release_then_claim
status_change_only / rumor_not_completed
```

后两者是**记录"未完成"的状态**，现有 EVENTS 模型隐含假设事件均已发生。
另需 correction/retraction 机制——卡米然·司地克江一例即为已记录事实被撤回
（CBA 自由球员名单系统乌龙）。

## F2. 枚举风格不一致

```
event_type              英文枚举   registration_change / registration_cancelled
registration_status     中文自由文本  完成注册 / 完成注册并官宣 / 完成注册/公示 / 取消注册
status_at_snapshot      中文自由文本  注册在册
```

`完成注册/公示` 这类含斜杠的复合值已经出现，属自由文本膨胀的早期迹象。

## F3. 证据等级三套并存尚未映射

```
v1.1 / v1.5.1   official_api / human_verified / auto_validated / needs_review / rejected
国内合并总表      A / A/B / B / B/C / pending
外籍合并核验      A1_official_direct / A2_official_mirror / B1 / B2 / C_unverified
```

v1.5.1 §R10 决定将双轴标准化推迟至 v2.1，方向可接受；
但未给出过渡映射表，后续约 300 行入库时将携带无法映射的值。

## F4. 译名与队名变体已实际出现

```
威利·考利-斯坦     ↔ 威利·考利-斯特恩      （WILLIE CAULEY-STEIN）
凯塔·贝兹-迪奥普   ↔ 凯伊·贝茨-迪奥普      （KEITA BATES-DIOP）
广州朗肽海本       ↔ 广州朗钛海本          （肽 / 钛，其一为错字）
```

当前实现以各自源文件为准，处理正确；但应登记为 alias variant，
否则跨源比对时会产生假差异。

## F5. MASTER 体积异常，需实读确认

```
2026-09-09 14:47   314,762 B
2026-09-10 13:32   306,602 B
```

新增 14 行后体积减少 8,160 B。xlsx 为 zip 容器、压缩率浮动，
不能仅凭体积判断异常，但 §13 D1 要求「原 3,451 baseline 行的非目标字段
不被意外改变」，须以实读验证：

```
总行数 = 3,465（表头外）
record_key 唯一
Jia Hao 05:00 回归通过
```

本次未核查。

---

# G. 改进建议（按优先级）

**P1 — 修 `raw_row_text` 生成方式。** 改为源单元格字面值拼接，不得由解析结果反推。
同步核查原 XLSX 中 `00` 的存储类型，并将 `00` 加入回归断言
（现有测试只覆盖 `01`，真实数据出现的是 `00`）。

**P2 — 以合并总表重跑 A2。** 将约 46 条国内赛季中流动全部入库，
并与现有 14 条做去重比对（合并总表声明已去重，此步正好验证）。
同步新增 `transaction_windows` 实体——2024-25 与 2025-26 均为每季三个 7 天窗口，
流动必须归属到 `window_no`，单个 `registration_window_deadline` 字段承载不了。

**P3 — 趁 73 行时扩 `event_type` 至五值并回填**，同时把
`registration_status` / `status_at_snapshot` 改为英文枚举。
等 300 余行入库后再改，迁移成本上升一个量级。

**P4 — 补外籍其余四季快照与 2025-26 的 41 条取消事件。**
与已实现 adapter 同构，主要工作量在 `club_aliases` 从 1 季扩到 8 季
（约 40 个冠名商名）。其中 **北京紫禁勇士 = 北京控股**（2020-2021）最易漏；
四川一队八年五名：金强 → 五粮金樽 → 金荣实业 → 丰谷酒业 → 锦城。

**P5 — 实读确认 MASTER 行数与 record_key 唯一性**（见 F5）。

**P6 — 为优先续约权 / 权利交易 / 复赛管理三类实体确定归宿。**
建议连同证据等级映射表放入新的窄版本，不再向 v1.5.1 追加，
避免其重演 v1.1「什么都往里装」的路径。

---

# H. 结论

```
[PASS]     §12 A1 全部数字（20 队 / 73 roster / 59 events / 132 人次）
[PASS]     §12 A2 数字（14 records）——但基准来源已失效
[PASS]     §R11 三个 fact product 按 grain 分离
[PASS]     §9  MASTER file ID 原地保持
[PASS]     R5 raw/normalized、R7 年份留痕、R3 窗口截止日、R2 双日期、R10 分寸
[DEFECT]   R6 jersey 文本保留失效；raw_row_text 非源文字面值，D4 兜底失效
[DEFECT]   raw_event_text 分段含「备注：」标签残留
[DEFECT]   sequence 一列两种语义
[GAP]      两份上级来源（07:07 / 07:26）未被处理
[GAP]      国内赛季中流动缺约 32 条（14 / 46）
[GAP]      外籍快照仅 1 季，缺约 250 行 + 41 条事件
[GAP]      优先续约权 / 权利交易 / 复赛管理 / 交易窗口 四类实体无归宿
[DEBT]     event_type 两值压平语义；枚举中英混用；三套证据等级未映射
[OPEN]     MASTER 行数与 record_key 唯一性未实读
```

**建议状态表述**：

> v1.5.1 对其声明范围内的四个 source 已完整实施并通过全部验收数字；
> 惟 A2 基准来源已被上级合并文件取代，范围需重新界定后补跑。
> 当前不应表述为「v1.5.1 已覆盖赛季中流动与外籍注册数据」。

按 v1.1 既定原则：`planned` 不得写成 `implemented`；
同理，`已处理声明范围内的来源` 不得写成 `已覆盖该主题的数据`。
