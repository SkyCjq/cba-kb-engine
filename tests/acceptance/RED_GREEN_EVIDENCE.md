# R2 验收测试 RED/GREEN 证伪证据

## RED（2026-10-11 15:12Z）

**条件**：移走 `src/cba_kb/r2_reconcile.py` 和 `scripts/r2-reconcile`

**执行**：
```
pytest tests/acceptance/test_r2_acceptance.py::test_P01_new -xvs
```

**结果**：FAIL
```
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/kb/scripts/r2-reconcile'
```

**结论**：无实现时测试失败，符合证伪要求。失败原因是 CLI 不存在（环境级），
而非业务逻辑错误。这证明测试确实依赖真实 CLI，而非 mock。

## GREEN（2026-10-11，Q2 FAIL 修复后）

**条件**：恢复实现文件，完成 F-01～F-11 修复

**执行**：
```
pytest tests/acceptance/test_r2_acceptance.py -q
```

**结果**：39 passed
- P01-P10：10 项通过
- N01-N18 + N14b：19 项通过
- AC-R01-R10：10 项通过

**R1 回归**：10 passed

## Q2 FAIL 修复摘要（F-01～F-11）

### HIGH（阻断性）
- **F-01**：`_resolve_reports_dir()` 在 `resolve()` 前检查符号链接，防止绕过
- **F-02**：CLI 包装器从脚本位置推导仓库根并 `cd`；Python 从 `__file__` 推导，不依赖 cwd
- **F-03**：跨 ID 冲突：不同 file_id + 同 SHA 必标记，不再以 locator 豁免
- **F-04**：R1 实质待审原因（RECORD_CONFLICT 等）→ `SOURCE_REVIEW_REQUIRED`

### MEDIUM
- **F-05**：验证 R1 状态约束（PROCESSED 需 COMPLETED + final_parent）
- **F-06**：原子独占发布（O_CREAT|O_EXCL），防 TOCTOU
- **F-07**：N10 隔离重复 file_id 条件（证据 ID 唯一）
- **F-08**：新增 N14b 符号链接逃逸测试
- **F-09**：N13 一侧有效，隔离 JSON 解析失败条件
- **F-10**：N15 验证 JSON+MD 双报告完整不覆盖
- **F-11**：AC-R10 引用本证据文件，记录手动 RED/GREEN
