#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把浏览器剪藏出来的 .md 归入 vault。

配合 70_tools_辅助工具/wechat_clipper.user.js：剪藏产物落在下载目录，本脚本负责
  1. 带 Referer 把 image_urls 里的图下到 10_sources_原始证据/wechat/attachments/（防盗链必须带这个头）
  2. 把正文里的 ![[IMG_01]] 占位符换成真实文件名
  3. 用正文哈希去重（公众号转载极多，同一篇常有多个 URL）
  4. 规范化 Markdown 移入 10_sources_原始证据/wechat/processed/，原件留档到 .../raw/

用法：
    python3 ingest_wechat.py ~/Downloads
    python3 ingest_wechat.py ~/Downloads --no-images     # 只收文字，不下图

注意：本脚本只处理**你已经在浏览器里打开过**的文章。
mp.weixin.qq.com 的 robots.txt 禁止自动抓取，本管线不做服务器端爬取。
"""
import argparse, hashlib, json, re, shutil, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "10_sources_原始证据" / "wechat" / "processed"
ATTACH = ROOT / "10_sources_原始证据" / "wechat" / "attachments"
RAW = ROOT / "10_sources_原始证据" / "wechat" / "raw"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://mp.weixin.qq.com/",   # 防盗链关键
}


def split_fm(text):
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    out = {}
    for ln in fm.splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        try:
            out[k.strip()] = json.loads(v)
        except Exception:                                    # noqa: BLE001
            out[k.strip()] = v.strip('"')
    return out, body.lstrip("\n")


def dump_fm(fm, body):
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines += ["---", "", body.rstrip(), ""]
    return "\n".join(lines)


def known_hashes():
    out = {}
    for p in NOTES.glob("*.md"):
        fm, _ = split_fm(p.read_text(encoding="utf-8"))
        if fm.get("body_sha256"):
            out[fm["body_sha256"]] = p.name
    return out


def fetch_images(urls, sid, timeout=25):
    ATTACH.mkdir(parents=True, exist_ok=True)
    names = []
    for i, u in enumerate(urls, 1):
        ext = ".png" if "wx_fmt=png" in u else ".gif" if "wx_fmt=gif" in u else ".jpg"
        name = f"{sid}_{i:02d}{ext}"
        dst = ATTACH / name
        if not dst.exists():
            try:
                with urlopen(Request(u, headers=HEADERS), timeout=timeout) as r:
                    dst.write_bytes(r.read())
            except Exception as e:                            # noqa: BLE001
                print(f"    ! 图 {i} 下载失败: {e}")
                names.append(None)
                continue
            time.sleep(0.4)
        names.append(name)
    return names


def ingest(path: Path, seen, want_images):
    fm, body = split_fm(path.read_text(encoding="utf-8"))
    if fm.get("source_type") != "wechat_mp":
        print(f"  - 跳过（不是公众号剪藏）{path.name}")
        return None
    sid = fm.get("uid", path.stem).replace("wx_", "")

    h = hashlib.sha256(re.sub(r"\s+", "", body).encode()).hexdigest()[:16]
    if h in seen:
        print(f"  = 重复，已存在 {seen[h]}，跳过 {path.name}")
        return None
    fm["body_sha256"] = h

    urls = fm.pop("image_urls", []) or []
    if urls and want_images:
        names = fetch_images(urls, sid)
        for i, n in enumerate(names, 1):
            ph = f"![[IMG_{i:02d}]]"
            body = body.replace(ph, f"![[{n}]]" if n else f"> 图片未下载：{urls[i-1]}")
        fm["n_images_saved"] = sum(1 for n in names if n)
    elif urls:
        for i, u in enumerate(urls, 1):
            body = body.replace(f"![[IMG_{i:02d}]]", f"![]({u})")
        fm["n_images_saved"] = 0

    NOTES.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    dst = NOTES / f"wx_{sid}.md"
    dst.write_text(dump_fm(fm, body), encoding="utf-8")
    shutil.copy2(path, RAW / path.name)
    seen[h] = dst.name
    print(f"  + {fm.get('title','(无标题)')[:34]}  {len(urls)} 图  -> {dst.name}")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="剪藏产物所在目录，通常是浏览器下载目录")
    ap.add_argument("--no-images", action="store_true")
    a = ap.parse_args()
    src = Path(a.src).expanduser()
    if not src.is_dir():
        sys.exit(f"目录不存在：{src}")
    seen = known_hashes()
    files = sorted(src.glob("wx_*.md"))
    if not files:
        sys.exit(f"{src} 里没有 wx_*.md，先用 70_tools_辅助工具/wechat_clipper.user.js 剪藏几篇")
    print(f"发现 {len(files)} 份剪藏，vault 现有 {len(seen)} 篇")
    n = sum(1 for f in files if ingest(f, seen, not a.no_images))
    print(f"\n入库 {n} 篇。下一步：python3 50_scripts_自动化脚本/classify_notes.py")


if __name__ == "__main__":
    main()
