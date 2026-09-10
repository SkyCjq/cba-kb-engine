#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 cba-kb 的稳定产物同步到 Google Drive。

P0 安全规则：
1. 顶层 Drive 文件夹一律按 config/drive_map.yaml 中的 folder ID 定位，不再按名称查找/创建。
2. 已存在文件优先按 manifest.csv 中的 drive_file_id 原地更新，保持 file ID 稳定。
3. manifest 没有 file ID 时，才在已知 folder ID 内按文件名查找；找不到才创建。
4. 默认只同步 data / ai / scripts / config / tools。inbox / sources / notes / archive 不做自动全量上传。
5. --dry-run 不需要 Google OAuth，也不会访问/修改 Drive；用于先确认映射和计划。

用法：
    python3 upload_drive.py --dry-run
    python3 upload_drive.py
    python3 upload_drive.py --only data ai
    python3 upload_drive.py --map ../60_config_配置与词表/drive_map.yaml
"""
import argparse
import csv
import hashlib
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SCOPES = ["https://www.googleapis.com/auth/drive"]

GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GFOLDER = "application/vnd.google-apps.folder"

RULES = {
    ".md": ("text/markdown", GDOC),
    ".csv": ("text/csv", GSHEET),
    ".jsonl": ("application/json", None),
    ".json": ("application/json", None),
    ".py": ("text/x-python", None),
    ".yaml": ("text/yaml", None),
    ".yml": ("text/yaml", None),
    ".txt": ("text/plain", None),
}
SKIP = {".DS_Store", "token.json", "credentials.json", "__pycache__", ".git"}
MANIFEST_COLUMNS = [
    "uid", "asset_type", "source_type", "source_url", "local_path",
    "content_hash", "drive_file_id", "ima_item_id", "status", "synced_at",
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_yaml(path: Path):
    try:
        import yaml
    except ImportError:
        sys.exit("缺依赖：pip install pyyaml")
    if not path.exists():
        sys.exit(f"找不到 Drive 映射：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not data.get("root", {}).get("drive_id") or not data.get("folders"):
        sys.exit(f"drive_map.yaml 缺少 root.drive_id 或 folders：{path}")
    return data


def load_manifest(path: Path):
    rows = {}
    if not path.exists():
        return rows
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("local_path"):
                rows[r["local_path"]] = {k: r.get(k, "") for k in MANIFEST_COLUMNS}
    return rows


def save_manifest(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        for key in sorted(rows):
            w.writerow({k: rows[key].get(k, "") for k in MANIFEST_COLUMNS})


def service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("缺依赖：pip install google-api-python-client google-auth-oauthlib")

    tok = HERE / "token.json"
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES) if tok.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cf = HERE / "credentials.json"
            if not cf.exists():
                sys.exit(f"找不到 {cf}，请先配置 OAuth 客户端")
            creds = InstalledAppFlow.from_client_secrets_file(str(cf), SCOPES).run_local_server(port=0)
        tok.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def q_escape(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def find_file_by_name(svc, name, parent_id):
    q = f"name = '{q_escape(name)}' and '{parent_id}' in parents and trashed = false"
    r = svc.files().list(
        q=q,
        fields="files(id,name,mimeType,modifiedTime)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = r.get("files") or []
    if len(files) > 1:
        raise RuntimeError(f"Drive 文件重名，拒绝自动选择：{parent_id}/{name} -> {len(files)} 个")
    return files[0] if files else None


def verify_folder_ids(svc, cfg):
    for role, item in cfg["folders"].items():
        got = svc.files().get(
            fileId=item["drive_id"],
            fields="id,name,mimeType,trashed",
            supportsAllDrives=True,
        ).execute()
        if got.get("mimeType") != GFOLDER or got.get("trashed"):
            raise RuntimeError(f"{role} 的 drive_id 不是有效文件夹：{item['drive_id']}")
        print(f"  ✓ {role:<8} {got['name']} ({got['id']})")


def infer_asset_type(role, path):
    if role == "data":
        return "structured_data"
    if role == "ai":
        return "ai_bundle"
    if role == "scripts":
        return "code"
    if role == "config":
        return "config"
    if role == "tools":
        return "tool"
    return role


def sync_file(svc, path: Path, parent_id, role, dry, manifest, track=True):
    local_rel = path.relative_to(ROOT).as_posix()
    src_mime, target = RULES.get(
        path.suffix.lower(),
        (mimetypes.guess_type(path.name)[0] or "application/octet-stream", None),
    )
    name = path.stem if target else path.name
    kb = path.stat().st_size // 1024
    digest = sha256_file(path)
    row = manifest.get(local_rel, {})
    file_id = row.get("drive_file_id", "")
    unchanged = bool(file_id and row.get("content_hash") == digest and str(row.get("status", "")).startswith("synced"))
    tag = {GDOC: "Docs", GSHEET: "Sheets"}.get(target, "原格式")

    if dry:
        action = "=" if unchanged else "~" if file_id else "+"
        id_hint = file_id or "unmapped"
        print(f"  {action} {role:<8} {name:<34} {kb:>5} KB -> {tag:<6} [{id_hint}]")
        return file_id

    if unchanged:
        print(f"  = {name:<34} 未变化，跳过 ({file_id})")
        return file_id

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        sys.exit("缺依赖：pip install google-api-python-client")

    if not file_id:
        existing = find_file_by_name(svc, name, parent_id)
        if existing:
            file_id = existing["id"]

    media = MediaFileUpload(str(path), mimetype=src_mime, resumable=path.stat().st_size > 5_000_000)
    if file_id:
        svc.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True,
            fields="id,name,mimeType",
        ).execute()
        print(f"  ~ {name:<34} {kb:>5} KB -> {tag} (ID 保持 {file_id})")
    else:
        body = {"name": name, "parents": [parent_id]}
        if target:
            body["mimeType"] = target
        got = svc.files().create(
            body=body,
            media_body=media,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        ).execute()
        file_id = got["id"]
        print(f"  + {name:<34} {kb:>5} KB -> {tag} ({file_id})")

    if track:
        manifest[local_rel] = {
            "uid": row.get("uid") or local_rel,
            "asset_type": row.get("asset_type") or infer_asset_type(role, path),
            "source_type": row.get("source_type") or ("generated" if role == "ai" else "local"),
            "source_url": row.get("source_url", ""),
            "local_path": local_rel,
            "content_hash": digest,
            "drive_file_id": file_id,
            "ima_item_id": row.get("ima_item_id", ""),
            "status": "synced",
            "synced_at": now_iso(),
        }
    return file_id


def files_for_role(local_dir: Path, manifest_path: Path, exclude=None):
    if not local_dir.exists():
        return []
    exclude = set(exclude or [])
    out = []
    for p in sorted(local_dir.iterdir()):
        if p.name in SKIP or p.name in exclude or p.name.startswith("."):
            continue
        if p.resolve() == manifest_path.resolve():
            continue
        if p.is_dir():
            print(f"  ! 跳过子目录（P0 不按名称自动建文件夹）：{p.relative_to(ROOT)}")
            continue
        out.append(p)
    return out


def main():
    default_map = ROOT / "60_config_配置与词表" / "drive_map.yaml"
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", dest="map_path", default=str(default_map))
    ap.add_argument("--only", nargs="*", help="只同步 role，例如 data ai scripts config tools")
    ap.add_argument("--dry-run", action="store_true", help="离线计划检查，不访问 Drive")
    ap.add_argument("--verify-folders", action="store_true", help="联网核验 drive_map 中的 folder ID")
    a = ap.parse_args()

    map_path = Path(a.map_path).expanduser().resolve()
    cfg = load_yaml(map_path)
    manifest_path = ROOT / cfg.get("manifest", "60_config_配置与词表/manifest.csv")
    manifest = load_manifest(manifest_path)
    roles = a.only or cfg.get("default_sync_roles") or ["data", "ai", "scripts", "config"]

    unknown = [r for r in roles if r not in cfg["folders"]]
    if unknown:
        sys.exit(f"drive_map.yaml 中没有这些 role：{', '.join(unknown)}")

    print(f"cba-kb P0 同步计划（root ID: {cfg['root']['drive_id']}）" + (" [dry-run/offline]" if a.dry_run else ""))
    svc = None if a.dry_run else service()
    if svc and a.verify_folders:
        print("\n核验 Drive folder ID：")
        verify_folder_ids(svc, cfg)

    for role in roles:
        item = cfg["folders"][role]
        local_dir = ROOT / item["local"]
        parent_id = item["drive_id"]
        print(f"\n[{role}] {local_dir.name}/ -> {parent_id}")
        for p in files_for_role(local_dir, manifest_path, item.get("exclude")):
            sync_file(svc, p, parent_id, role, a.dry_run, manifest)

    if a.dry_run:
        print("\nDRY-RUN 完成：顶层目录完全由 folder ID 定位，不会创建 data/bundles/scripts/config 等旧目录。")
        return

    save_manifest(manifest_path, manifest)
    config_parent = cfg["folders"]["config"]["drive_id"]
    print("\n[control] 同步 manifest.csv（不记录自身 hash，避免自引用）")
    sync_file(svc, manifest_path, config_parent, "config", False, manifest, track=False)
    print("\n完成。manifest 已更新；所有已映射文件优先按 drive_file_id 原地更新。")


if __name__ == "__main__":
    main()
