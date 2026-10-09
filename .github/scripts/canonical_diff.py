"""规范 diff 生成（可信，base 唯一来源）— rev9.

policy-gate.py（可信层，直接 import）与 build-review-bundle.py（隔离层，
经 `git show <base>:.github/scripts/canonical_diff.py` 取用）必须使用同一实现。
修改此文件即改变 diff 校验语义；两边实现分叉会导致合法 bundle 被误判。

rev9：
- 换行可见性：行内的 \\r 转为可见的 "\\r" 字样，CRLF/LF 差异逐行可见；
  不再依赖归一化 + 文件级指纹（rev8 的混合换行盲区）。
- 文件模式：FileChange 携带 base_mode/head_mode（"100644" 等）；
  mode 变化显式标注并参与 digest。
- compute_diff_digest 单源（含模式与新旧路径）。

设计：difflib.unified_diff 固定参数（n=3）。文本 diff 格式与 `git diff`
不完全相同，但同样可读；关键是"展示文本 == 可信重算"，而不是像 git。
"""
import difflib
import hashlib
from typing import Dict, NamedTuple, Optional

DIFF_CONTEXT = 3
DIFF_CAP = 500 * 1024
TRUNCATED_MARKER = "\n[TRUNCATED: diff 超过 500KB，已截断；审查人须自行获取完整 diff]\n"
DIFF_START = "<!-- MODE2-DIFF-START -->"
DIFF_END = "<!-- MODE2-DIFF-END -->"


class FileChange(NamedTuple):
    base_bytes: bytes
    head_bytes: bytes
    old_path: Optional[str]  # 重命名时的旧路径，否则 None
    base_mode: str  # "100644"/"100755"/"120000"，不存在则 ""
    head_mode: str


def compute_diff_digest(file_contents: Dict[str, FileChange]) -> str:
    """diff 字节摘要（单源）。

    绑定：旧路径 + 新路径 + base/head 模式 + base/head 字节。
    重命名、chmod、换行变化都会改变摘要。
    """
    parts = []
    for new_path in sorted(file_contents):
        fc = file_contents[new_path]
        rec = ((fc.old_path or "").encode() + b"\x00" + new_path.encode() + b"\x00"
               + fc.base_mode.encode() + b"\x00" + fc.head_mode.encode() + b"\x00"
               + hashlib.sha256(fc.base_bytes).digest()
               + hashlib.sha256(fc.head_bytes).digest())
        parts.append(f"{new_path}:{hashlib.sha256(rec).hexdigest()}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _vis_r(line: str) -> str:
    """让行内的 \\r 可见（CRLF vs LF 逐行可辨）。

    rev10：用 U+240D（␍）而非 "\\r" 字符串——文件若本就含字面量反斜杠+r，
    旧做法会与真实回车显示混淆（rev9 复审 finding 5）。
    """
    return line.replace("\r", "␍")


def canonical_file_diff(new_path: str, fc: FileChange) -> str:
    """单个文件的规范 diff（rev9：换行可见 + 模式标注）。"""
    try:
        bt = fc.base_bytes.decode("utf-8")
        ht = fc.head_bytes.decode("utf-8")
        binary = False
    except Exception:
        binary = True
        bt = ht = ""

    hdr = ""
    if fc.old_path and fc.old_path != new_path:
        hdr = f"rename from {fc.old_path}\nrename to {new_path}\n"
        fromfile, tofile = f"a/{fc.old_path}", f"b/{new_path}"
    else:
        fromfile, tofile = f"a/{new_path}", f"b/{new_path}"

    notes = []
    # 文件模式变化（chmod）必须显式标注并参与展示
    if fc.base_mode != fc.head_mode:
        b_m = fc.base_mode or "(不存在)"
        h_m = fc.head_mode or "(不存在)"
        notes.append(f"[文件模式：{b_m} → {h_m}]")
    if binary:
        return hdr + "".join(n + "\n" for n in notes) + \
            f"Binary file {new_path} differs\n"

    # 文件末尾换行变化标注
    b_end = bt.endswith("\n") or bt.endswith("\r")
    h_end = ht.endswith("\n") or ht.endswith("\r")
    if b_end != h_end:
        notes.append(f"[文件末尾换行：{'有' if b_end else '无'} → {'有' if h_end else '无'}]")

    # 保留原始行结束符，但 \\r 可见化；difflib 输出强制每行终止
    bl = [_vis_r(l) for l in bt.splitlines(keepends=True)]
    hl = [_vis_r(l) for l in ht.splitlines(keepends=True)]

    raw = difflib.unified_diff(bl, hl, fromfile=fromfile, tofile=tofile,
                               lineterm="\n", n=DIFF_CONTEXT)
    body = "".join(l if l.endswith("\n") else l + "\n" for l in raw)
    note_txt = "".join(n + "\n" for n in notes)
    return hdr + note_txt + body


def canonical_diffs_text(file_contents: Dict[str, FileChange]) -> str:
    """{new_path: FileChange} → 规范 diff 全文（new_path 排序）。"""
    return "".join(canonical_file_diff(p, file_contents[p])
                   for p in sorted(file_contents))


def capped_diff(text: str) -> str:
    """与 bundle 生成侧一致的截断。"""
    if len(text) <= DIFF_CAP:
        return text
    return text[:DIFF_CAP] + TRUNCATED_MARKER


def diff_section(file_contents: Dict[str, FileChange]) -> str:
    """bundle 中的完整 diff 节（含起止标记）。"""
    return f"{DIFF_START}\n{capped_diff(canonical_diffs_text(file_contents))}{DIFF_END}\n"


def parse_diff_section(bundle: str):
    """从 bundle 提取 diff 节 inner；缺失返回 None。"""
    import re
    m = re.search(
        re.escape(DIFF_START) + r"\n(.*)" + re.escape(DIFF_END),
        bundle, re.S)
    return m.group(1) if m else None
