#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线审计 PDF 是否存在可用文本层。不会做 OCR，也不会访问网络或 Google Drive。

默认判定：
    total_chars > chars_per_page * pages  => TEXT
    否则                                  => SCANNED_OR_LOW_TEXT

用法：
    python3 audit_pdf_text_layers.py /path/to/pdfs
    python3 audit_pdf_text_layers.py a.pdf b.pdf --csv report.csv
"""
import argparse
import csv
import re
from pathlib import Path

def audit_pdf(path: Path, chars_per_page: int):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    counts = []
    for p in r.pages:
        try:
            counts.append(len((p.extract_text() or "").strip()))
        except Exception:
            counts.append(0)
    total = sum(counts)
    pages = len(counts)
    classification = "TEXT" if total > chars_per_page * max(1, pages) else "SCANNED_OR_LOW_TEXT"
    role = "cross_validation_source" if ("2019-2025" in path.name and "深圳" in path.name) else "season_source"
    m = re.search(r"(20\d{2})[-–](20\d{2})", path.name)
    season = f"{m.group(1)}-{m.group(2)}" if m else ""
    return {
        "file": path.name,
        "season": season,
        "role": role,
        "pages": pages,
        "text_chars": total,
        "avg_chars_per_page": round(total / pages, 1) if pages else 0,
        "min_chars_page": min(counts) if counts else 0,
        "max_chars_page": max(counts) if counts else 0,
        "classification": classification,
    }

def collect(args):
    out = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            out.append(p)
    seen = set()
    return [p for p in out if not (str(p.resolve()) in seen or seen.add(str(p.resolve())))]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--chars-per-page", type=int, default=200)
    ap.add_argument("--csv")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        raise SystemExit("未找到 PDF")

    rows = [audit_pdf(p, args.chars_per_page) for p in files]
    for r in rows:
        print(
            f"{r['classification']:<20} pages={r['pages']:<3} chars={r['text_chars']:<7} "
            f"avg={r['avg_chars_per_page']:<7} role={r['role']:<24} {r['file']}"
        )

    if args.csv:
        dest = Path(args.csv)
        with dest.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"CSV: {dest}")

if __name__ == "__main__":
    main()
