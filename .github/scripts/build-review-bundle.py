#!/usr/bin/env python3
"""Mode 2.1 审查材料包生成（隔离执行层运行）— rev3.
rev2 修复：完整 diff、TRUNCATED 标注、完整 sha256、head/base SHA 记录。
rev3 修复（Vuln 5）：git 命令失败必须阻断（原 sh() 忽略退出码，可生成空 diff
的"成功"材料）；bundle 头部写入 BUNDLE_HEAD_SHA（实际 checkout 的 HEAD，
供可信层回绑）。
Web 审查时必须回显校验串（证明读完整）。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

DIFF_CAP = 500_000  # diff 上限 500KB，超限显式截断

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="review-bundle.md")
ap.add_argument("--pytest-log", default="")
ap.add_argument("--tier", default="T?")
ap.add_argument("--integrity", default="integrity.json")
a = ap.parse_args()


def sh(*cmd):
    """Vuln 5 修复：git 命令失败直接阻断，不再生成空材料的"成功"包。"""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"BUNDLE FAIL: 命令失败 {' '.join(cmd)}: {proc.stderr.strip()[:300]}",
              file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def git_show(rev, path):
    """取某 revision 的文件字节（rev10：严格区分"不存在"与真实错误）。

    - "does not exist" → b""（新增/删除场景的预期情况）。
    - 其他失败 → 直接退出阻断（不得把损坏的 git 状态当成空文件）。
    """
    proc = subprocess.run(["git", "show", f"{rev}:{path}"],
                          capture_output=True, check=False)
    if proc.returncode == 0:
        return proc.stdout
    err = proc.stderr.decode(errors="replace")
    if "does not exist" in err or "not in '" in err or "exists on disk" in err:
        return b""
    print(f"BUNDLE FAIL: git show {rev}:{path} 失败：{err[:300]}",
          file=sys.stderr)
    sys.exit(1)


def git_mode(rev, path):
    """经 git ls-tree 取文件模式（100644/100755/120000）；不存在返回 ""。"""
    proc = subprocess.run(["git", "ls-tree", rev, "--", path],
                          capture_output=True, check=False, text=True)
    if proc.returncode != 0:
        return ""
    m = re.match(r"(\d+) \w+ [0-9a-f]+\t", proc.stdout)
    return m.group(1) if m else ""


def load_canonical_diff(base_rev):
    """从 base 取可信的 canonical_diff 实现（单源，防两边分叉）。

    rev7：diff 展示文本必须与可信层重算完全一致，因此隔离层也必须用 base 的
    同一实现生成，而不是各自写一份。
    """
    proc = subprocess.run(["git", "show", f"{base_rev}:.github/scripts/canonical_diff.py"],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit("无法从 base 获取 canonical_diff.py")
    ns = {}
    exec(compile(proc.stdout.decode("utf-8"), "canonical_diff.py", "exec"), ns)
    return ns


head = os.environ.get("HEAD_SHA", sh("git", "rev-parse", "HEAD").strip())
base = os.environ.get("BASE_SHA", "")
diffstat = sh("git", "diff", "--stat", f"{base}...{head}") if base else ""
# rev8：用 --name-status 保留重命名元信息（R100 old new）
status_out = sh("git", "diff", "--name-status", f"{base}...{head}").strip() if base else ""
file_list = []  # [(status, old_or_None, new)]
for line in status_out.splitlines():
    parts = line.split("\t")
    if not parts:
        continue
    st = parts[0]
    if st.startswith("R") and len(parts) >= 3:
        file_list.append(("renamed", parts[1], parts[2]))
    elif len(parts) >= 2:
        file_list.append((st[0].lower(), None, parts[1]))
files = "\n".join(new for _, _, new in file_list)
if base:
    cd = load_canonical_diff(base)
    _contents = {new: cd["FileChange"](
                     git_show(base, old or new), git_show(head, new), old,
                     git_mode(base, old or new), git_mode(head, new))
                 for _, old, new in file_list}
    diff_digest = cd["compute_diff_digest"](_contents)
    diff_section = cd["diff_section"](_contents)
else:
    diff_digest = "(未知)"
    diff_section = "(未知)"

pytest_log = ""
if a.pytest_log and os.path.exists(a.pytest_log):
    pytest_log = open(a.pytest_log, encoding="utf-8").read()[-8000:]
try:
    integrity = open(a.integrity, encoding="utf-8").read()
except FileNotFoundError:
    integrity = "(未生成)"

body = f"""# Review Bundle（Mode 2.1）
- BUNDLE_HEAD_SHA: {head}
- base: {base or "(未知)"}
- DIFF_DIGEST: {diff_digest}
- tier（展示用，权威定级见可信层）: {a.tier}

## 变更文件
```
{files[:4000]}
```

## diffstat
```
{diffstat[:4000]}
```

## 完整 diff（规范格式，可信层会重算核对）
{diff_section}
## pytest（尾部 8000 字符）
```
{pytest_log}
```

## 完整性指标
```
{integrity}
```
"""
checksum = hashlib.sha256(body.encode()).hexdigest()
bundle = body + f"\nEND-OF-BUNDLE sha256:{checksum}\n"
open(a.out, "w", encoding="utf-8").write(bundle)
print(f"bundle 写入 {a.out}，校验串 END-OF-BUNDLE sha256:{checksum[:16]}…")
