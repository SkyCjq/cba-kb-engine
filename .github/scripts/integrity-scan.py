#!/usr/bin/env python3
"""Mode 2.1 测试完整性指标采集（隔离执行层运行）— rev4.
输出 JSON：{test_files, test_funcs, skips, xfails, asserts}
可信层用 base 版重跑本脚本得基线，并用 base 版扫描 PR head 的 tests/ 得真实指标，
两者比对：test_funcs/test_files/asserts 减少 / skips,xfails 增加 -> FAIL。
rev4：新增 --json（只向 stdout 输出 JSON，不写文件；供可信层调用）。
审查发现修复：语法错误 fail-closed（原静默跳过，会漏检测试削弱）。
DRAFT: 断言计数为启发式（ast.Assert 计数），实测阶段校准。
"""
import argparse
import ast
import json
import pathlib
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="integrity.json")
ap.add_argument("--root", default="tests")
ap.add_argument("--json", action="store_true",
                help="只向 stdout 输出 JSON（可信层调用模式），不写文件")
a = ap.parse_args()

agg = {"test_files": 0, "test_funcs": 0, "skips": 0, "xfails": 0, "asserts": 0}
errors = []
for p in sorted(pathlib.Path(a.root).rglob("test_*.py")):
    agg["test_files"] += 1
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        errors.append(f"{p}: {e}")  # fail-closed：语法错误不再静默跳过
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            agg["test_funcs"] += 1
            try:
                decos = [ast.unparse(d) for d in node.decorator_list]
            except Exception:
                decos = []
            if any("skip" in d for d in decos):
                agg["skips"] += 1
            if any("xfail" in d for d in decos):
                agg["xfails"] += 1
        if isinstance(node, ast.Assert):
            agg["asserts"] += 1

if errors:
    print("FAIL: 以下测试文件语法解析失败（fail-closed）：")
    print("\n".join(errors))
    sys.exit(1)

payload = json.dumps(agg)
if a.json:
    # 可信层调用模式：stdout 只输出 JSON
    print(payload)
else:
    json.dump(agg, open(a.out, "w", encoding="utf-8"), indent=2)
    print(json.dumps(agg, indent=2))
