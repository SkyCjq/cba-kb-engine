"""Read-only parsers for documentary evidence.

The module never fetches remote images, calls an ima API, or scrapes WeChat.
Google Docs content is read only through the Drive transport supplied by the
caller so credentials remain in the Private Instance.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .transport import retry_read


PARSER_VERSION = "document-sources-r2-20260913"
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*]\((https?://[^)\s]+)")


class ReviewRequired(RuntimeError):
    """A source is preserved for review but cannot be promoted as accepted."""

    def __init__(self, reason, *, raw=b"", metadata=None):
        super().__init__(reason)
        self.reason = reason
        self.raw = raw
        self.metadata = metadata or {}


def _decode_utf8(raw):
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReviewRequired("TXT_ENCODING_UNSUPPORTED", raw=raw) from exc


def _front_matter(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            try:
                value = yaml.safe_load("".join(lines[1:index])) or {}
            except yaml.YAMLError as exc:
                raise ReviewRequired("MARKDOWN_FRONT_MATTER_INVALID") from exc
            if not isinstance(value, dict):
                raise ReviewRequired("MARKDOWN_FRONT_MATTER_OBJECT_REQUIRED")
            return value, "".join(lines[index + 1:])
    raise ReviewRequired("MARKDOWN_FRONT_MATTER_UNCLOSED")


def _list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _attachment(value, base_dir):
    if isinstance(value, dict):
        url = value.get("url")
        path = value.get("path")
        name = value.get("name")
    else:
        url = str(value) if str(value).startswith(("http://", "https://")) else None
        path = None if url else str(value)
        name = None
    if url:
        return {"kind": "remote", "url": url, "name": name or Path(urlparse(url).path).name}
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(base_dir) / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise ReviewRequired("LOCAL_ATTACHMENT_MISSING")
    return {"kind": "local", "path": str(candidate), "name": name or candidate.name}


def parse_markdown(raw, *, channel="wechat_browser_clip", source_type="wechat_mp",
                   source_locator=None, base_dir=None):
    """Parse a captured Markdown document while retaining the original bytes."""
    text = _decode_utf8(raw)
    try:
        metadata, body = _front_matter(text)
        attachments = [
            _attachment(value, base_dir or ".")
            for value in _list(metadata.get("attachments")) + _list(metadata.get("images"))
        ]
    except ReviewRequired as exc:
        if not exc.raw:
            exc.raw = raw
        raise
    remote_images = sorted(set(MARKDOWN_IMAGE.findall(body)))
    for url in remote_images:
        attachments.append({"kind": "remote", "url": url,
                            "name": Path(urlparse(url).path).name})
    seen = set()
    unique = []
    for item in attachments:
        key = (item["kind"], item.get("url") or item.get("path"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return {
        "raw": raw,
        "text": body,
        "capture_channel": channel,
        "source_type": source_type,
        "source_locator": source_locator,
        "canonical_url": (
            metadata.get("canonical_url")
            or metadata.get("source_url")
            or metadata.get("url")
        ),
        "published_at": metadata.get("published_at") or metadata.get("date"),
        "title": metadata.get("title"),
        "tags": _list(metadata.get("tags")),
        "rights": metadata.get("rights"),
        "front_matter": metadata,
        "attachments": unique,
        "remote_references": sorted(set(remote_images)),
        "parser_version": PARSER_VERSION,
    }


def parse_ima(raw, *, source_locator=None, base_dir=None):
    """Ingest a caller-supplied ima export without any ima API dependency."""
    result = parse_markdown(
        raw, channel="ima_file_export", source_type="local_file",
        source_locator=source_locator, base_dir=base_dir,
    )
    result["parser_name"] = "ima_file"
    return result


def parse_txt(raw, *, source_locator=None):
    return {
        "raw": raw,
        "text": _decode_utf8(raw),
        "capture_channel": "local_file",
        "source_type": "local_file",
        "source_locator": source_locator,
        "canonical_url": None,
        "published_at": None,
        "title": None,
        "tags": [],
        "rights": None,
        "front_matter": {},
        "attachments": [],
        "remote_references": [],
        "parser_version": PARSER_VERSION,
        "parser_name": "txt",
    }


def _table_text(table):
    lines = []
    for row in table.rows:
        values = []
        for cell in row.cells:
            values.append(" ".join(p.text for p in cell.paragraphs).strip())
        lines.append("\t".join(values))
    return "\n".join(lines)


def parse_docx(raw, *, source_locator=None):
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn

        document = Document(io.BytesIO(raw))
        blocks = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                blocks.append(Paragraph(child, document).text)
            elif child.tag == qn("w:tbl"):
                blocks.append(_table_text(Table(child, document)))
        relationships = []
        for relationship in document.part.rels.values():
            kind = relationship.reltype.rsplit("/", 1)[-1]
            if kind == "image" or relationship.is_external:
                relationships.append({
                    "relationship_id": relationship.rId,
                    "kind": kind,
                    "target": relationship.target_ref,
                    "external": bool(relationship.is_external),
                })
        relationships.sort(key=lambda item: item["relationship_id"])
    except ReviewRequired:
        raise
    except Exception as exc:
        raise ReviewRequired("DOCX_PARSE_FAILED", raw=raw) from exc
    return {
        "raw": raw,
        "text": "\n".join(blocks),
        "capture_channel": "local_file",
        "source_type": "local_file",
        "source_locator": source_locator,
        "canonical_url": None,
        "published_at": None,
        "title": None,
        "tags": [],
        "rights": None,
        "front_matter": {},
        "attachments": [],
        "remote_references": [],
        "parser_version": PARSER_VERSION,
        "parser_name": "docx",
        "relationship_provenance": relationships,
    }


def parse_pdf(raw, *, source_locator=None):
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ReviewRequired("PDF_PARSE_FAILED", raw=raw) from exc
    if not any(page.strip() for page in pages):
        raise ReviewRequired(
            "OCR_REQUIRED", raw=raw,
            metadata={"page_count": len(pages), "text_layer": False},
        )
    return {
        "raw": raw,
        "text": "\n\n".join(pages),
        "capture_channel": "local_file",
        "source_type": "local_file",
        "source_locator": source_locator,
        "canonical_url": None,
        "published_at": None,
        "title": None,
        "tags": [],
        "rights": None,
        "front_matter": {},
        "attachments": [],
        "remote_references": [],
        "parser_version": PARSER_VERSION,
        "parser_name": "pdf",
        "page_count": len(pages),
    }


def parse_local(raw, *, suffix, source_locator=None, channel="local_file"):
    suffix = suffix.lower()
    if suffix in {".md", ".markdown"}:
        if channel == "ima_file_export":
            return parse_ima(raw, source_locator=source_locator,
                             base_dir=Path(source_locator).parent)
        return parse_markdown(
            raw, channel=channel, source_type="local_file",
            source_locator=source_locator, base_dir=Path(source_locator).parent,
        )
    if suffix == ".txt":
        return parse_txt(raw, source_locator=source_locator)
    if suffix == ".docx":
        return parse_docx(raw, source_locator=source_locator)
    if suffix == ".pdf":
        return parse_pdf(raw, source_locator=source_locator)
    raise ReviewRequired("LOCAL_FILE_TYPE_UNSUPPORTED", raw=raw)


def _ordered_tabs(tabs):
    def key(tab):
        properties = tab.get("tabProperties") or {}
        index = properties.get("index")
        return (index if isinstance(index, int) else 2**31, properties.get("tabId") or "")

    return sorted(tabs, key=key)


def _element_text(element):
    if "paragraph" in element:
        parts = []
        for item in element["paragraph"].get("elements", []):
            run = item.get("textRun")
            if run and isinstance(run.get("content"), str):
                parts.append(run["content"])
        return "".join(parts) or "\n"
    if "table" in element:
        rows = []
        for row in element["table"].get("tableRows", []):
            cells = []
            for cell in row.get("tableCells", []):
                cells.append("".join(
                    _element_text(item)
                    for item in (cell.get("body") or {}).get("content", [])
                ).strip())
            rows.append("\t".join(cells))
        return "\n".join(rows) + "\n"
    if "tableOfContents" in element:
        return "".join(
            _element_text(item)
            for item in element["tableOfContents"].get("content", [])
        )
    if "sectionBreak" in element:
        return ""
    raise ReviewRequired("GOOGLE_DOC_UNSUPPORTED_ELEMENT")


def _tab_text(tab, parent_tab_id, topology):
    properties = tab.get("tabProperties")
    document_tab = tab.get("documentTab")
    if not isinstance(properties, dict) or not isinstance(document_tab, dict):
        raise ReviewRequired("GOOGLE_DOC_TAB_STRUCTURE_INVALID")
    tab_id = properties.get("tabId")
    if not isinstance(tab_id, str) or not tab_id:
        raise ReviewRequired("GOOGLE_DOC_TAB_ID_REQUIRED")
    content = (document_tab.get("body") or {}).get("content")
    if not isinstance(content, list):
        raise ReviewRequired("GOOGLE_DOC_BODY_REQUIRED")
    children = _ordered_tabs(tab.get("childTabs") or [])
    body = "".join(_element_text(element) for element in content)
    topology.append({
        "tab_id": tab_id,
        "title": properties.get("title"),
        "parent_tab_id": parent_tab_id,
        "child_tab_ids": [
            (child.get("tabProperties") or {}).get("tabId") for child in children
        ],
    })
    child_text = "".join(
        _tab_text(child, tab_id, topology) for child in children
    )
    return body + child_text


def parse_drive_document(drive, file_id, *, canonical_url=None):
    """Read one native Doc using the existing authenticated Drive/Docs transport."""
    metadata = drive.meta(file_id)
    if metadata.get("mimeType") != "application/vnd.google-apps.document":
        raise ReviewRequired("GOOGLE_DOC_MIME_REQUIRED")
    document = retry_read(
        lambda: drive.docs.document(file_id),
        label="native document",
        reset=getattr(drive, "_reset_read_connections", None),
    )
    if not isinstance(document, dict):
        raise ReviewRequired("GOOGLE_DOC_DOCUMENT_REQUIRED")
    raw = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    try:
        tabs = document.get("tabs")
        if not isinstance(tabs, list) or not tabs:
            raise ReviewRequired("GOOGLE_DOC_TABS_REQUIRED")
        topology = []
        text = "".join(_tab_text(tab, None, topology) for tab in _ordered_tabs(tabs))
    except ReviewRequired as exc:
        if not exc.raw:
            exc.raw = raw
        raise
    locator = canonical_url or metadata.get("webViewLink")
    if not locator:
        locator = f"https://docs.google.com/document/d/{file_id}/edit"
    return {
        "raw": raw,
        "text": text,
        "capture_channel": "google_drive_doc",
        "source_type": "gdrive",
        "source_locator": locator,
        "canonical_url": locator,
        "published_at": None,
        "title": metadata.get("name"),
        "tags": [],
        "rights": None,
        "front_matter": {},
        "attachments": [],
        "remote_references": [],
        "parser_version": PARSER_VERSION,
        "parser_name": "google_docs",
        "document_metadata": metadata,
        "tab_topology": topology,
        "document_revision": document.get("revisionId"),
    }
