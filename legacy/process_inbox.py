#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register, extract, validate-routing, and optionally archive CBA Drive sources.

CBA-KB v1.1 changes the source pipeline from **move-first** to **register-first**.
The physical Drive folder of a source is no longer its processing state.

Default processing is read-only against Google Drive:

    discover -> source_registry -> extract -> local companion -> validate/import downstream

A Drive parent change is performed only when ``--archive`` or ``--archive-canary``
is explicitly requested.  Extraction canaries therefore test parsers and routing without
mutating Drive.

``manifest.csv`` remains a file-sync control plane.  Business source state is written to
``60_config_配置与词表/source_registry.csv`` and is keyed by stable Drive file ID.

Images are never sent to OCR/vision automatically.  Low-text PDFs are marked
``needs_ocr``.  Extracted text is unverified evidence and must not overwrite
``official_api`` facts.

Dependencies::

    pip install google-api-python-client google-auth-oauthlib pyyaml \
        openpyxl pypdf python-docx

Examples::

    python3 process_inbox.py --scan
    python3 process_inbox.py --extract-canary
    python3 process_inbox.py --process
    python3 process_inbox.py --archive-canary
    python3 process_inbox.py --archive

Backward compatibility::

    --canary     aliases --extract-canary
    --no-move    is accepted but is now the default behaviour
    no mode      aliases --process
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import mimetypes
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "60_config_配置与词表"
DEFAULT_MAP = CONFIG_DIR / "drive_map.yaml"
DEFAULT_MANIFEST = CONFIG_DIR / "manifest.csv"
DEFAULT_SOURCE_REGISTRY = CONFIG_DIR / "source_registry.csv"
LOCAL_EXTRACTED_DIR = ROOT / "10_sources_原始证据" / "_extracted_text"
DEFAULT_CREDENTIALS = Path(
    os.environ.get("CBA_GOOGLE_CREDENTIALS", HERE / "credentials.json")
)
DEFAULT_TOKEN = Path(os.environ.get("CBA_GOOGLE_TOKEN", HERE / "token.json"))

GFOLDER = "application/vnd.google-apps.folder"
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/tiff"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv"}
OFFICE_EXTS = {".xlsx", ".docx"}
MANIFEST_COLUMNS = [
    "uid",
    "asset_type",
    "source_type",
    "source_url",
    "local_path",
    "content_hash",
    "drive_file_id",
    "ima_item_id",
    "status",
    "synced_at",
]

SOURCE_REGISTRY_COLUMNS = [
    "source_id",
    "drive_file_id",
    "source_title",
    "source_url",
    "current_parent_id",
    "original_discovery_path",
    "source_type",
    "source_role",
    "season",
    "club_id",
    "content_hash",
    "file_size_bytes",
    "business_status",
    "extraction_status",
    "extraction_method",
    "validation_status",
    "records_generated",
    "records_imported",
    "discovered_at",
    "registered_at",
    "processed_at",
    "verified_at",
    "notes",
]

BUSINESS_STATUS_ORDER = {
    "DISCOVERED": 0,
    "EXTRACTED": 1,
    "IMPORTED": 2,
    "VERIFIED": 3,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip()
    return value[:180] or "untitled"


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        raise SystemExit("缺依赖：pip install pyyaml") from None
    if not path.exists():
        raise SystemExit(f"找不到配置：{path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {key: row.get(key, "") for key in MANIFEST_COLUMNS}
            for row in csv.DictReader(handle)
        ]


def save_manifest(path: Path, rows: Iterable[dict[str, str]]) -> None:
    """Atomically checkpoint the local manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("asset_type", ""),
            row.get("local_path", ""),
            row.get("uid", ""),
        ),
    )
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow({key: row.get(key, "") for key in MANIFEST_COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def upsert_manifest(rows: list[dict[str, str]], row: dict[str, str]) -> None:
    normalized = {key: row.get(key, "") for key in MANIFEST_COLUMNS}
    for index, old in enumerate(rows):
        if old.get("uid") == normalized["uid"]:
            rows[index] = normalized
            return
    rows.append(normalized)


def manifest_row(
    item: dict[str, Any],
    *,
    path: str,
    digest: str,
    status: str,
) -> dict[str, str]:
    return {
        "uid": f"drive:{item['id']}",
        "asset_type": "source_file",
        "source_type": "drive_inbox",
        "source_url": item.get("webViewLink")
        or f"https://drive.google.com/open?id={item['id']}",
        "local_path": path,
        "content_hash": digest,
        "drive_file_id": item["id"],
        "ima_item_id": "",
        "status": status,
        "synced_at": now_iso(),
    }



def load_source_registry(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: row.get(key, "") for key in SOURCE_REGISTRY_COLUMNS}
            for row in reader
        ]


def save_source_registry(path: Path, rows: Iterable[dict[str, str]]) -> None:
    """Atomically save business source state; Drive location is not source identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("season", ""),
            row.get("source_role", ""),
            row.get("source_title", ""),
            row.get("drive_file_id", ""),
        ),
    )
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_REGISTRY_COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow({key: row.get(key, "") for key in SOURCE_REGISTRY_COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def registry_index(rows: list[dict[str, str]]) -> dict[str, int]:
    return {
        row.get("drive_file_id", ""): index
        for index, row in enumerate(rows)
        if row.get("drive_file_id")
    }


def infer_season(relative_path: str, name: str) -> str:
    combined = f"{relative_path} {name}"
    match = re.search(r"(20\d{2}-20\d{2}|201\d-20\d{2})", combined)
    return match.group(1) if match else ""


def promoted_business_status(old: str, candidate: str) -> str:
    old = old or "DISCOVERED"
    candidate = candidate or "DISCOVERED"
    if BUSINESS_STATUS_ORDER.get(old, 0) >= BUSINESS_STATUS_ORDER.get(candidate, 0):
        return old
    return candidate


def extraction_method_for(item: dict[str, Any], status: str) -> str:
    ext = Path(item["name"]).suffix.lower()
    if status == "extracted":
        if ext == ".xlsx":
            return "spreadsheet_extracted"
        if ext == ".docx":
            return "docx_text_extracted"
        if ext == ".pdf":
            return "pdf_text_layer"
        return "text_extracted"
    if status == "needs_ocr":
        return "pdf_text_layer_probe"
    if status == "needs_vision":
        return "vision_not_run"
    if status == "duplicate":
        return "sha256_duplicate"
    return status


def business_status_for_extraction(status: str) -> str:
    return "EXTRACTED" if status == "extracted" else "DISCOVERED"


def register_source(
    rows: list[dict[str, str]],
    item: dict[str, Any],
    *,
    relative_path: str,
    digest: str = "",
    extraction_status: str = "not_started",
    extraction_method: str = "",
    processed: bool = False,
    current_parent_id: str | None = None,
) -> dict[str, str]:
    """Upsert one source by Drive file ID without downgrading imported/verified state."""
    indexes = registry_index(rows)
    existing = (
        dict(rows[indexes[item["id"]]])
        if item["id"] in indexes
        else {key: "" for key in SOURCE_REGISTRY_COLUMNS}
    )
    now = now_iso()
    role = existing.get("source_role") or source_role(relative_path, item["name"])
    parent = current_parent_id
    if parent is None:
        parents = item.get("parents") or []
        parent = parents[0] if len(parents) == 1 else (existing.get("current_parent_id") or "")
    new_business = business_status_for_extraction(extraction_status)
    row = dict(existing)
    row.update(
        {
            "source_id": existing.get("source_id") or f"drive:{item['id']}",
            "drive_file_id": item["id"],
            "source_title": item["name"],
            "source_url": item.get("webViewLink")
            or existing.get("source_url")
            or f"https://drive.google.com/open?id={item['id']}",
            "current_parent_id": parent or "",
            "original_discovery_path": existing.get("original_discovery_path")
            or f"00_inbox_待处理/{relative_path}",
            "source_type": existing.get("source_type") or item.get("mimeType", ""),
            "source_role": role,
            "season": existing.get("season") or infer_season(relative_path, item["name"]),
            "content_hash": digest or existing.get("content_hash", ""),
            "file_size_bytes": str(item.get("size") or existing.get("file_size_bytes") or ""),
            "business_status": promoted_business_status(
                existing.get("business_status", ""), new_business
            ),
            "extraction_status": extraction_status or existing.get("extraction_status", ""),
            "extraction_method": extraction_method or existing.get("extraction_method", ""),
            "validation_status": existing.get("validation_status") or "not_applicable",
            "records_generated": existing.get("records_generated") or "0",
            "records_imported": existing.get("records_imported") or "0",
            "discovered_at": existing.get("discovered_at") or item.get("modifiedTime") or now,
            "registered_at": existing.get("registered_at") or now,
            "processed_at": now if processed else existing.get("processed_at", ""),
            "verified_at": existing.get("verified_at", ""),
            "notes": existing.get("notes", ""),
        }
    )
    if item["id"] in indexes:
        rows[indexes[item["id"]]] = row
    else:
        rows.append(row)
    return row


def find_duplicate_source(
    rows: list[dict[str, str]], digest: str, drive_file_id: str
) -> dict[str, str] | None:
    if not digest:
        return None
    return next(
        (
            row
            for row in rows
            if row.get("content_hash") == digest
            and row.get("drive_file_id") != drive_file_id
        ),
        None,
    )


def write_local_companion(name: str, content: str) -> Path:
    LOCAL_EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_EXTRACTED_DIR / name
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)
    return path


READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/drive"


def drive_service(credentials_path: Path, token_path: Path, *, write: bool = False):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit(
            "缺依赖：pip install google-api-python-client google-auth-oauthlib"
        ) from None

    requested_scope = WRITE_SCOPE if write else READ_SCOPE
    credentials = (
        Credentials.from_authorized_user_file(str(token_path))
        if token_path.exists()
        else None
    )

    def has_required_scope(creds) -> bool:
        granted = set(getattr(creds, "scopes", None) or [])
        if write:
            return WRITE_SCOPE in granted
        return WRITE_SCOPE in granted or READ_SCOPE in granted

    needs_consent = credentials is None or not has_required_scope(credentials)
    if credentials and credentials.expired and credentials.refresh_token and not needs_consent:
        credentials.refresh(Request())
    elif credentials and credentials.valid and not needs_consent:
        pass
    else:
        if not credentials_path.exists():
            mode = "写入/归档" if write else "只读抽取"
            raise SystemExit(
                f"找不到 OAuth 客户端文件：{credentials_path}\n"
                f"当前模式={mode}；可用 --credentials 指定，或设置 CBA_GOOGLE_CREDENTIALS。"
            )
        credentials = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path), [requested_scope]
        ).run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_children(service, folder_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    token = None
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields=(
                "nextPageToken,files("
                "id,name,mimeType,size,modifiedTime,webViewLink,parents)"
            ),
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        output.extend(response.get("files") or [])
        token = response.get("nextPageToken")
        if not token:
            return output


def walk_drive(
    service, folder_id: str, prefix: PurePosixPath = PurePosixPath()
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in list_children(service, folder_id):
        relative = prefix / item["name"]
        if item["mimeType"] == GFOLDER:
            output.extend(walk_drive(service, item["id"], relative))
        else:
            record = dict(item)
            record["relative_path"] = relative.as_posix()
            output.append(record)
    return output


def all_inbox_files(service, inbox_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in list_children(service, inbox_id):
        if item["mimeType"] == GFOLDER:
            output.extend(
                walk_drive(service, item["id"], PurePosixPath(item["name"]))
            )
        else:
            record = dict(item)
            record["relative_path"] = item["name"]
            output.append(record)
    return sorted(output, key=lambda record: record["relative_path"])


def ensure_folder(service, parent_id: str, name: str) -> str:
    escaped = name.replace("'", "\\'")
    query = (
        f"name='{escaped}' and '{parent_id}' in parents and "
        f"mimeType='{GFOLDER}' and trashed=false"
    )
    files = (
        service.files()
        .list(q=query, fields="files(id,name)", pageSize=10)
        .execute()
        .get("files")
        or []
    )
    if len(files) > 1:
        raise RuntimeError(f"Drive 子目录重名：{parent_id}/{name}")
    if files:
        return files[0]["id"]
    return (
        service.files()
        .create(
            body={"name": name, "mimeType": GFOLDER, "parents": [parent_id]},
            fields="id",
        )
        .execute()["id"]
    )


def ensure_folder_path(service, root_id: str, parts: Iterable[str]) -> str:
    parent_id = root_id
    for part in parts:
        if part not in ("", "."):
            parent_id = ensure_folder(service, parent_id, str(part))
    return parent_id


def move_item(service, file_id: str, old_parent: str, new_parent: str) -> str:
    """Move one file and verify both stable ID and destination parent."""
    if old_parent == new_parent:
        return new_parent
    result = (
        service.files()
        .update(
            fileId=file_id,
            addParents=new_parent,
            removeParents=old_parent,
            fields="id,parents",
            supportsAllDrives=True,
        )
        .execute()
    )
    if result.get("id") != file_id:
        raise RuntimeError(
            f"Drive file ID 在 move 后不一致：{file_id} -> {result.get('id')}"
        )
    parents = result.get("parents") or []
    if new_parent not in parents or old_parent in parents:
        raise RuntimeError(
            f"Drive parent 校验失败：file={file_id} parents={parents}"
        )
    return new_parent


def download_file(service, file_id: str, path: Path) -> None:
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        raise SystemExit("缺依赖：pip install google-api-python-client") from None
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with path.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def extract_text_file(path: Path) -> tuple[str, str]:
    return path.read_text(encoding="utf-8", errors="replace"), "extracted"


def extract_xlsx(path: Path) -> tuple[str, str]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return "", "error_missing_openpyxl"
    workbook = load_workbook(path, read_only=True, data_only=True)
    blocks = []
    for sheet in workbook.worksheets:
        lines = [f"## Sheet: {sheet.title}", "```tsv"]
        for row in sheet.iter_rows(values_only=True):
            values = [
                ""
                if value is None
                else str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
                for value in row
            ]
            if not any(value.strip() for value in values):
                continue
            while values and values[-1] == "":
                values.pop()
            lines.append("\t".join(values))
        lines.append("```")
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    return text, "extracted" if text.strip() else "empty"


def extract_docx(path: Path) -> tuple[str, str]:
    try:
        from docx import Document
    except ImportError:
        return "", "error_missing_python_docx"
    document = Document(path)
    parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table_index, table in enumerate(document.tables, 1):
        parts.append(f"\n## Table {table_index}")
        for row in table.rows:
            parts.append(
                "\t".join(cell.text.replace("\n", " ").strip() for cell in row.cells)
            )
    text = "\n".join(parts)
    return text, "extracted" if text.strip() else "empty"


def extract_pdf(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "error_missing_pypdf"
    reader = PdfReader(str(path))
    parts = []
    raw_chars = 0
    for page_index, page in enumerate(reader.pages, 1):
        page_text = (page.extract_text() or "").strip()
        raw_chars += len(page_text)
        if page_text:
            parts.append(f"## Page {page_index}\n\n{page_text}")
    text = "\n\n".join(parts)
    if raw_chars <= 200 * max(1, len(reader.pages)):
        return text, "needs_ocr"
    return text, "extracted"


def extract_local(path: Path, mime_type: str = "") -> tuple[str, str]:
    extension = path.suffix.lower()
    if mime_type in IMAGE_MIMES or extension in IMAGE_EXTS:
        return "", "needs_vision"
    if extension in TEXT_EXTS:
        return extract_text_file(path)
    if extension == ".xlsx":
        return extract_xlsx(path)
    if extension == ".docx":
        return extract_docx(path)
    if extension == ".pdf":
        return extract_pdf(path)
    return "", "archived_only"


def source_role(relative_path: str, name: str) -> str:
    combined = f"{relative_path} {name}"
    suffix = Path(name).suffix.lower()
    if "2019-2025" in combined and "深圳" in combined and suffix == ".pdf":
        return "cross_validation_source"
    if name == "CBA_2024-2027_国内球员注册数据库_官网核验版.xlsx":
        return "official_audit_workbook"
    if "外籍球员" in combined and suffix in IMAGE_EXTS:
        return "foreign_player_visual_evidence"
    if suffix in IMAGE_EXTS:
        return "historical_registration_visual_evidence"
    if suffix == ".xlsx" and "OCR" in name.upper():
        return "historical_registration_ocr_workbook"
    if suffix in {".pdf", ".xlsx"}:
        return "historical_registration_primary"
    return "historical_source"


def verification_level(status: str) -> str:
    if status == "extracted":
        return "text_extracted_unverified"
    if status in {"needs_ocr", "needs_vision", "archived_only", "duplicate"}:
        return "source_archive"
    return "unverified"


def markdown_companion(
    item: dict[str, Any],
    relative_path: str,
    digest: str,
    text: str,
    status: str,
) -> str:
    url = item.get("webViewLink") or f"https://drive.google.com/open?id={item['id']}"
    role = source_role(relative_path, item["name"])
    title = item["name"].replace('"', "'")
    lines = [
        "---",
        f'uid: "drive:{item["id"]}"',
        f'title: "{title}"',
        'source_type: "drive_inbox"',
        f'source_role: "{role}"',
        f'verification_level: "{verification_level(status)}"',
        f'source_url: "{url}"',
        f'drive_file_id: "{item["id"]}"',
        f'original_path: "00_inbox_待处理/{relative_path}"',
        f'content_sha256: "{digest}"',
        f'extraction_status: "{status}"',
        f'processed_at: "{now_iso()}"',
        "---",
        "",
        f"# {item['name']}",
        "",
        f"原件：{url}",
        "",
    ]
    if role == "cross_validation_source":
        lines += [
            "> 角色：跨赛季深圳队交叉验证源。不得直接覆盖 official_api 事实；"
            "用于核对历史 PDF/图片抽取结果。",
            "",
        ]
    if status == "needs_ocr":
        lines += [
            "> 自动检测：PDF 无可靠文本层，原件归档；需 OCR 后再做闭集字段校验与"
            "人工/双跑一致性复核。",
            "",
        ]
    if text.strip():
        lines += [text.strip(), ""]
    return "\n".join(lines)


def upsert_raw_text_file(service, parent_id: str, name: str, content: str) -> str:
    try:
        from googleapiclient.http import MediaIoBaseUpload
    except ImportError:
        raise SystemExit("缺依赖：pip install google-api-python-client") from None
    escaped = name.replace("'", "\\'")
    query = f"name='{escaped}' and '{parent_id}' in parents and trashed=false"
    files = (
        service.files()
        .list(q=query, fields="files(id,name)", pageSize=10)
        .execute()
        .get("files")
        or []
    )
    if len(files) > 1:
        raise RuntimeError(f"提取文本文件重名：{name}")
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )
    if files:
        service.files().update(
            fileId=files[0]["id"],
            media_body=media,
            supportsAllDrives=True,
        ).execute()
        return files[0]["id"]
    return (
        service.files()
        .create(
            body={"name": name, "parents": [parent_id]},
            media_body=media,
            fields="id",
        )
        .execute()["id"]
    )


CANARY_RULES = [
    (
        "root_xlsx",
        lambda item: "/" not in item["relative_path"]
        and item["name"].lower().endswith(".xlsx"),
    ),
    (
        "text_pdf_2024_2025",
        lambda item: "2024-2025赛季CBA联赛球员注册信息.pdf" in item["name"],
    ),
    (
        "scanned_pdf_2022_2023",
        lambda item: "2022-2023赛季CBA联赛球员注册信息.pdf" in item["name"],
    ),
    (
        "nested_shenzhen_image",
        lambda item: "/" in item["relative_path"]
        and "深圳" in item["name"]
        and Path(item["name"]).suffix.lower() in IMAGE_EXTS,
    ),
    (
        "nested_shenzhen_ocr_xlsx",
        lambda item: "/" in item["relative_path"]
        and item["name"] == "深圳-OCR.xlsx",
    ),
]


def choose_canary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exactly one item for every required canary class, or fail closed."""
    chosen: list[dict[str, Any]] = []
    missing = []
    for label, predicate in CANARY_RULES:
        item = next(
            (
                candidate
                for candidate in items
                if candidate["id"] not in {entry["id"] for entry in chosen}
                and predicate(candidate)
            ),
            None,
        )
        if item is None:
            missing.append(label)
            continue
        chosen.append(item)
        print(f"CANARY {label}: {item['relative_path']} [id={item['id']}]")
    if missing:
        raise RuntimeError(
            "Canary 门禁失败，缺少必需样本：" + ", ".join(missing)
        )
    return chosen


def rough_status(item: dict[str, Any]) -> str:
    extension = Path(item["name"]).suffix.lower()
    mime_type = (
        item.get("mimeType")
        or mimetypes.guess_type(item["name"])[0]
        or "application/octet-stream"
    )
    if mime_type in IMAGE_MIMES or extension in IMAGE_EXTS:
        return "needs_vision"
    if extension == ".pdf":
        return "candidate_pdf_audit"
    if extension in TEXT_EXTS | OFFICE_EXTS:
        return "candidate_extract"
    return "archived_only"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", default=str(DEFAULT_MAP))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Deprecated: sync control only")
    parser.add_argument("--source-registry", default=str(DEFAULT_SOURCE_REGISTRY))
    parser.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS))
    parser.add_argument("--token", default=str(DEFAULT_TOKEN))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scan", action="store_true", help="Register source metadata only; no download/move")
    parser.add_argument("--process", action="store_true", help="Extract/classify all selected files; no Drive move")
    parser.add_argument("--extract-canary", action="store_true", help="Read-only adversarial 5-file extraction canary")
    parser.add_argument("--archive", action="store_true", help="Explicitly move selected files under 10_sources")
    parser.add_argument("--archive-canary", action="store_true", help="Explicit 5-file Drive mutation canary")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--extract-local")
    # Backward-compatible flags. v1.1 defaults to no move.
    parser.add_argument("--canary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-move", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def selected_mode(args: argparse.Namespace) -> str:
    explicit = [
        name
        for name in ("scan", "process", "extract_canary", "archive", "archive_canary")
        if getattr(args, name)
    ]
    if args.canary:
        explicit.append("extract_canary")
    if len(set(explicit)) > 1:
        raise SystemExit("--scan/--process/--extract-canary/--archive/--archive-canary 只能选择一个")
    return explicit[0] if explicit else "process"


def main() -> None:
    args = parse_args()
    mode = selected_mode(args)
    if args.extract_local:
        path = Path(args.extract_local)
        text, status = extract_local(
            path, mimetypes.guess_type(path.name)[0] or ""
        )
        print(f"status={status} chars={len(text)} sha256={sha256_path(path)}")
        if text:
            print(text[:1500])
        return

    config = load_yaml(Path(args.map))
    inbox_id = config["folders"]["inbox"]["drive_id"]
    sources_id = config["folders"]["sources"]["drive_id"]
    registry_path = Path(args.source_registry)
    registry = load_source_registry(registry_path)

    # Only archive modes request Drive mutation capability.
    service = drive_service(
        Path(args.credentials), Path(args.token), write=mode in {"archive", "archive_canary"}
    )
    items = all_inbox_files(service, inbox_id)
    print(f"inbox discovered files: {len(items)}")

    if mode in {"extract_canary", "archive_canary"}:
        items = choose_canary(items)
    elif args.sample:
        items = items[: args.sample]

    summary: dict[str, int] = {}
    if mode == "scan":
        for item in items:
            row = register_source(
                registry,
                item,
                relative_path=item["relative_path"],
                extraction_status=(
                    registry[registry_index(registry)[item["id"]]].get("extraction_status")
                    if item["id"] in registry_index(registry)
                    else "not_started"
                ),
            )
            summary[row["business_status"]] = summary.get(row["business_status"], 0) + 1
            print(f"REGISTER {row['business_status']:<10} {item['relative_path']} [id={item['id']}]")
        if not args.dry_run:
            save_source_registry(registry_path, registry)
            print(f"source_registry checkpoint: {registry_path}")
        else:
            print("DRY_RUN: source_registry not written")
        print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
        return

    for item in items:
        relative_path = item["relative_path"]
        mime_type = (
            item.get("mimeType")
            or mimetypes.guess_type(item["name"])[0]
            or "application/octet-stream"
        )
        digest = ""
        status = "not_started"
        destination_parent = None
        try:
            with tempfile.TemporaryDirectory(prefix="cba-inbox-") as temp_dir:
                local_path = Path(temp_dir) / safe_name(item["name"])
                download_file(service, item["id"], local_path)
                digest = sha256_path(local_path)
                duplicate = find_duplicate_source(registry, digest, item["id"])
                text, status = (
                    ("", "duplicate")
                    if duplicate
                    else extract_local(local_path, mime_type)
                )
                method = extraction_method_for(item, status)
                row = register_source(
                    registry,
                    item,
                    relative_path=relative_path,
                    digest=digest,
                    extraction_status=status,
                    extraction_method=method,
                    processed=True,
                )
                summary[status] = summary.get(status, 0) + 1

                # Extracted companions are local generated evidence. They are synced later
                # by upload_drive.py; the extraction path itself does not mutate Drive.
                if status in {"extracted", "needs_ocr"}:
                    companion = markdown_companion(
                        item, relative_path, digest, text, status
                    )
                    companion_name = (
                        safe_name(relative_path.replace("/", "__"))
                        + f"__{item['id'][-8:]}.md"
                    )
                    companion_path = write_local_companion(companion_name, companion)
                    print(f"  companion={companion_path}")

                print(
                    f"{status:<18} business={row['business_status']:<10} "
                    f"role={row['source_role']:<38} {relative_path} [id={item['id']}]"
                )

                if mode in {"archive", "archive_canary"}:
                    old_parents = item.get("parents") or []
                    if len(old_parents) != 1:
                        raise RuntimeError(
                            f"预期恰好一个 current parent，实际 {old_parents}: {relative_path}"
                        )
                    destination_parent = ensure_folder_path(
                        service,
                        sources_id,
                        PurePosixPath(relative_path).parent.parts,
                    )
                    if args.dry_run:
                        print(
                            f"  ARCHIVE PLAN file_id={item['id']} "
                            f"parent={old_parents[0]} -> {destination_parent}"
                        )
                    else:
                        verified_parent = move_item(
                            service, item["id"], old_parents[0], destination_parent
                        )
                        register_source(
                            registry,
                            item,
                            relative_path=relative_path,
                            digest=digest,
                            extraction_status=status,
                            extraction_method=method,
                            processed=True,
                            current_parent_id=verified_parent,
                        )
                        print(
                            f"  ARCHIVE verified file_id={item['id']} parent={verified_parent}"
                        )
        except Exception as error:  # noqa: BLE001 - per-file isolation is intentional
            summary["error"] = summary.get("error", 0) + 1
            register_source(
                registry,
                item,
                relative_path=relative_path,
                digest=digest,
                extraction_status=f"error:{type(error).__name__}",
                extraction_method="error",
                processed=True,
                current_parent_id=destination_parent,
            )
            print(f"ERROR {relative_path}: {type(error).__name__}: {error}")

        # Per-file checkpoint for idempotent resumability. No Drive move unless explicit.
        if not args.dry_run:
            save_source_registry(registry_path, registry)

    if args.dry_run:
        print("DRY_RUN: source_registry not written; Drive not mutated")
    else:
        print(f"\nsource_registry checkpoint: {registry_path}")
    print("summary:", ", ".join(f"{key}={value}" for key, value in sorted(summary.items())))


if __name__ == "__main__":
    main()
