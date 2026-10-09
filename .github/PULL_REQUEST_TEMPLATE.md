## 需求

Closes #（Issue 号）

## 风险等级（信息展示；权威定级由可信层代码计算，Muse 不可下调）

<!-- 可信层自动计算：max(路径逐文件取最大, 能力, 语义)。人工确认即可。 -->

## 证伪收据

- 旧 SHA：`<sha>`，新测试 FAIL（须为预期的行为缺陷失败，环境失败不算）：
- 新 SHA：`<sha>`，PASS：

## 治理例外（如无填"无"）

<!-- 任何例外必须：经 Human 批准 + 注明不延续到下个需求。无批准的例外 = 阻断。 -->

## 自查

- [ ] 验收测试文件未被改动（以冻结 test_sha256 为准，机器核验）
- [ ] 覆盖率未下降；测试数未减；无新增 skip/xfail（完整性基线比对）
- [ ] 本地 `python scripts/check_secrets.py` 通过
- [ ] 风险等级未被下调；能力/语义风险已在出题阶段评估

<!--
机器权威记录不在 PR body 中（rev3 起 policy-gate 不再解析 PR body）：
- freeze 签发记录：Issue 评论（可信作者发布，marker 置顶），见 docs/mode2/digest-schema.md；
- review 签发记录：PR 评论（可信作者发布，marker 置顶）。
PR body 只保留人类可读摘要，避免双份记录不一致。
-->
