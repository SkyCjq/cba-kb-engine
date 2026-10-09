#!/usr/bin/env python3
"""展示用 tier 计算（隔离执行层运行，仅用于 bundle 展示；权威定级在可信层）。
rev3：复用 tiering.py 的统一算法（逐文件取最大，未知 T2），不再自维护第二套逻辑。
环境变量：BASE_SHA, HEAD_SHA。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiering

base = os.environ.get("BASE_SHA", "")
head = os.environ.get("HEAD_SHA", "")
proc = subprocess.run(
    ["git", "diff", "--name-only", f"{base}...{head}"],
    capture_output=True, text=True)
if proc.returncode != 0:
    print(f"display-tier FAIL: git diff 失败: {proc.stderr.strip()[:200]}",
          file=sys.stderr)
    sys.exit(1)
files = proc.stdout.split()
tiers = tiering.load_tiers(".github/risk-tiers.yml")
tier = tiering.compute_tier(files, tiers)
open("tier.txt", "w", encoding="utf-8").write(tier)
print("display tier:", tier)
