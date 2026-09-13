"""Deterministic private archive for documentary evidence.

Document Lane observes and retains source material only. It cannot write
canonical facts or enter the release orchestration path.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from .common import atomic
from .document_sources import (
    PARSER_VERSION,
    ReviewRequired,
    parse_drive_document,
    parse_ima,
    parse_local,
    parse_markdown,
)


SCHEMA_VERSION = 1
CAPTURE_CHANNELS = (
    "wechat_browser_clip",
    "ima_file_export",
    "local_file",
    "google_drive_doc",
)
RIGHTS_CLASSIFICATIONS = ("public", "copyrighted", "private", "unknown")
DEFAULT_RIGHTS = {
    "wechat_browser_clip": "copyrighted",
    "ima_file_export": "unknown",
    "local_file": "private",
    "google_drive_doc": "private",
}


def canonical_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def normalize_text(text):
    """Return deterministic UTF-8-ready text without semantic rewriting."""
    if not isinstance(text, str):
        raise TypeError("DOCUMENT_TEXT_REQUIRED")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def content_hash(text):
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def doc_id(text):
    return "doc_" + content_hash(text)[:24]


def normalize_canonical_url(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ReviewRequired("CANONICAL_URL_REQUIRED")
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        raise ReviewRequired("CANONICAL_URL_ABSOLUTE_REQUIRED")
    hostname = parts.hostname
    if not hostname:
        raise ReviewRequired("CANONICAL_URL_HOST_REQUIRED")
    netloc = hostname.lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise ReviewRequired("CANONICAL_URL_PORT_INVALID") from exc
    if port is not None:
        netloc += f":{port}"
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    query.sort()
    return urlunsplit((
        parts.scheme.lower(),
        netloc,
        parts.path,
        urlencode(query, doseq=True),
        "",
    ))


def _timestamp(value, *, label, required):
    if value in (None, ""):
        if required:
            raise ReviewRequired(f"{label.upper()}_REQUIRED")
        return None
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise ReviewRequired(f"{label.upper()}_ISO8601_REQUIRED")
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            date.fromisoformat(text)
            return text
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewRequired(f"{label.upper()}_ISO8601_REQUIRED") from exc
    if parsed.tzinfo is None:
        return parsed.replace(microsecond=0).isoformat()
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def load_taxonomy(engine_root):
    path = Path(engine_root) / "config/taxonomy.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("DOCUMENT_TAXONOMY_INVALID") from exc
    if not isinstance(value, dict) or not isinstance(value.get("topic"), list):
        raise RuntimeError("DOCUMENT_TAXONOMY_TOPIC_REQUIRED")
    return value


def validate_tags(engine_root, values):
    allowed = set(load_taxonomy(engine_root)["topic"])
    tags = []
    candidates = []
    for value in values or []:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value in allowed:
            tags.append(value)
        else:
            candidates.append(value)
    return sorted(set(tags)), sorted(set(candidates))


def rights_decision(parsed, *, classification=None, public_export_allowed=None,
                    evidence=None):
    source = parsed.get("rights")
    if isinstance(source, str):
        source = {"classification": source}
    if not isinstance(source, dict):
        source = {}
    selected = (
        classification
        or source.get("classification")
        or DEFAULT_RIGHTS.get(parsed.get("capture_channel"), "unknown")
    )
    if selected not in RIGHTS_CLASSIFICATIONS:
        raise ReviewRequired("RIGHTS_CLASSIFICATION_INVALID")
    selected_evidence = evidence if evidence is not None else source.get("evidence") or []
    if isinstance(selected_evidence, str):
        selected_evidence = [selected_evidence]
    selected_evidence = [_jsonable(item) for item in selected_evidence]
    requested = (
        bool(public_export_allowed)
        if public_export_allowed is not None
        else bool(source.get("public_export_allowed", False))
    )
    if requested and (selected != "public" or not selected_evidence):
        raise ReviewRequired("PUBLIC_EXPORT_EVIDENCE_REQUIRED")
    return {
        "classification": selected,
        "public_export_allowed": requested,
        "evidence": selected_evidence,
    }


def assert_public_export_allowed(record):
    rights = record.get("rights") or {}
    allowed = (
        rights.get("classification") == "public"
        and rights.get("public_export_allowed") is True
        and bool(rights.get("evidence"))
    )
    if not allowed:
        raise RuntimeError("DOCUMENT_PUBLIC_EXPORT_BLOCKED")
    return True


def _shingles(text, width=5):
    points = list(normalize_text(text))
    if not points:
        return set()
    if len(points) <= width:
        return {"".join(points)}
    return {"".join(points[index:index + width])
            for index in range(len(points) - width + 1)}


def near_duplicate(left, right):
    left_text, right_text = normalize_text(left), normalize_text(right)
    if not left_text or not right_text:
        return {"jaccard": 0.0, "length_ratio": 0.0, "candidate": False}
    left_shingles, right_shingles = _shingles(left_text), _shingles(right_text)
    union = left_shingles | right_shingles
    score = len(left_shingles & right_shingles) / len(union) if union else 0.0
    ratio = min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    return {
        "jaccard": round(score, 12),
        "length_ratio": round(ratio, 12),
        "candidate": score >= 0.92 and ratio >= 0.90,
    }


def _capture_id(parsed, capture_channel):
    payload = {
        "raw_sha256": hashlib.sha256(parsed["raw"]).hexdigest(),
        "source_locator": parsed.get("source_locator"),
        "capture_channel": capture_channel,
    }
    return "capture_" + hashlib.sha256(canonical_bytes(payload)).hexdigest()[:20]


def _raw_suffix(parsed):
    return {
        "ima_file": ".md",
        "txt": ".txt",
        "docx": ".docx",
        "pdf": ".pdf",
        "google_docs": ".json",
    }.get(parsed.get("parser_name"), ".bin")


def _safe_name(value):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(value).name).strip("._")
    return name or "attachment"


class DocumentLane:
    def __init__(self, engine_root, instance):
        self.engine_root = Path(engine_root).resolve()
        self.instance = instance
        self.input_root = Path(instance.document_input_root).resolve()
        self.archive_root = Path(instance.document_archive_root).resolve()
        self.review_root = Path(instance.document_review_root).resolve()
        self.report_root = Path(instance.document_report_root).resolve()

    def ingest_file(self, source, *, capture_channel, **options):
        source = Path(source)
        if not source.is_absolute():
            source = self.input_root / source
        source = source.resolve()
        if not source.is_relative_to(self.input_root):
            raise RuntimeError("DOCUMENT_SOURCE_MUST_BE_PRIVATE_INPUT")
        if not source.is_file():
            raise RuntimeError("DOCUMENT_SOURCE_MISSING")
        raw = source.read_bytes()
        locator = source.relative_to(self.input_root).as_posix()
        try:
            if capture_channel == "wechat_browser_clip":
                parsed = parse_markdown(
                    raw, channel=capture_channel, source_locator=locator,
                    base_dir=source.parent,
                )
            elif capture_channel == "ima_file_export":
                parsed = parse_ima(
                    raw, source_locator=locator, base_dir=source.parent,
                )
            elif capture_channel == "local_file":
                parsed = parse_local(
                    raw, suffix=source.suffix, source_locator=locator,
                    channel=capture_channel,
                )
            else:
                raise ReviewRequired("DOCUMENT_FILE_CHANNEL_INVALID", raw=raw)
        except ReviewRequired as exc:
            return self._review(exc, source_locator=locator,
                                capture_channel=capture_channel)
        try:
            return self._ingest_parsed(parsed, **options)
        except ReviewRequired as exc:
            if not exc.raw:
                exc.raw = parsed.get("raw", b"")
            return self._review(exc, source_locator=locator,
                                capture_channel=capture_channel)

    def ingest_drive_document(self, drive, file_id, **options):
        try:
            parsed = parse_drive_document(drive, file_id)
        except ReviewRequired as exc:
            return self._review(
                exc, source_locator=f"google-drive:{file_id}",
                capture_channel="google_drive_doc",
            )
        try:
            return self._ingest_parsed(parsed, **options)
        except ReviewRequired as exc:
            if not exc.raw:
                exc.raw = parsed.get("raw", b"")
            return self._review(
                exc, source_locator=f"google-drive:{file_id}",
                capture_channel="google_drive_doc",
            )

    def ingest_batch(self, manifest, *, drive=None, drive_factory=None, **options):
        path = Path(manifest)
        if not path.is_absolute():
            path = self.input_root / path
        path = path.resolve()
        if not path.is_relative_to(self.input_root):
            raise RuntimeError("DOCUMENT_MANIFEST_MUST_BE_PRIVATE_INPUT")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("DOCUMENT_MANIFEST_INVALID") from exc
        entries = value.get("documents") if isinstance(value, dict) else value
        if not isinstance(entries, list):
            raise RuntimeError("DOCUMENT_MANIFEST_ITEMS_REQUIRED")
        results = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("DOCUMENT_MANIFEST_ITEM_OBJECT_REQUIRED")
            item_options = {
                key: value
                for key, value in entry.items()
                if key not in {"path", "drive_id", "capture_channel"}
            }
            item_options.update(options)
            if entry.get("drive_id"):
                if drive is None and drive_factory is not None:
                    drive = drive_factory()
                if drive is None:
                    raise RuntimeError("DOCUMENT_DRIVE_REQUIRED")
                result = self.ingest_drive_document(
                    drive, entry["drive_id"], **item_options,
                )
            else:
                result = self.ingest_file(
                    entry.get("path"),
                    capture_channel=entry.get("capture_channel", "local_file"),
                    **item_options,
                )
            results.append(result)
        report = {
            "schema_version": SCHEMA_VERSION,
            "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "documents": len(results),
            "accepted": sum(item["accepted"] for item in results),
            "review_required": sum(not item["accepted"] for item in results),
            "results": results,
        }
        report_path = (
            self.report_root
            / f"document-report-{report['manifest_sha256'][:24]}.json"
        )
        atomic(report_path, canonical_bytes(report))
        return report

    def _review(self, error, *, source_locator, capture_channel):
        reason = getattr(error, "reason", str(error))
        raw = getattr(error, "raw", b"") or b""
        review_id = "review_" + hashlib.sha256(
            canonical_bytes({
                "reason": reason,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "source_locator": source_locator,
            })
        ).hexdigest()[:24]
        root = self.review_root / review_id
        record = {
            "schema_version": SCHEMA_VERSION,
            "review_id": review_id,
            "reason": reason,
            "capture_channel": capture_channel,
            "source_locator": source_locator,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "metadata": _jsonable(getattr(error, "metadata", {})),
            "canonical_write_performed": False,
        }
        if raw:
            atomic(root / "raw/original.bin", raw)
        atomic(root / "review.json", canonical_bytes(record))
        return {
            "status": "REVIEW_REQUIRED",
            "accepted": False,
            "reason": reason,
            "review_id": review_id,
            "review_path": str(root),
        }

    def _existing_records(self):
        if not self.archive_root.is_dir():
            return []
        records = []
        for path in sorted(self.archive_root.glob("doc_*/record.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise RuntimeError("DOCUMENT_ARCHIVE_RECORD_INVALID") from exc
        return records

    def _capture(self, parsed, capture_channel):
        capture_id = _capture_id(parsed, capture_channel)
        raw_name = capture_id + _raw_suffix(parsed)
        attachments = []
        for item in parsed.get("attachments") or []:
            if item.get("kind") == "remote":
                attachments.append({
                    "kind": "remote",
                    "name": item.get("name"),
                    "url": item.get("url"),
                })
                continue
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(self.input_root):
                raise ReviewRequired("LOCAL_ATTACHMENT_MUST_BE_PRIVATE_INPUT")
            data = path.read_bytes()
            name = _safe_name(item.get("name") or path.name)
            archived = f"attachments/{capture_id}/{name}"
            attachments.append({
                "kind": "local",
                "name": name,
                "source_locator": path.relative_to(self.input_root).as_posix(),
                "archive_path": archived,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "_data": data,
            })
        return {
            "capture_id": capture_id,
            "capture_channel": capture_channel,
            "source_type": parsed.get("source_type"),
            "source_locator": parsed.get("source_locator"),
            "raw_path": f"raw/{raw_name}",
            "raw_sha256": hashlib.sha256(parsed["raw"]).hexdigest(),
            "raw_bytes": len(parsed["raw"]),
            "parser_name": parsed.get("parser_name", capture_channel),
            "parser_version": parsed.get("parser_version", PARSER_VERSION),
            "source_metadata": _jsonable(parsed.get("front_matter") or {}),
            "document_metadata": _jsonable(parsed.get("document_metadata") or {}),
            "tab_topology": _jsonable(parsed.get("tab_topology") or []),
            "remote_references": sorted(set(parsed.get("remote_references") or [])),
            "relationship_provenance": _jsonable(
                parsed.get("relationship_provenance") or []
            ),
            "attachments": attachments,
        }

    def _write_capture(self, root, capture, raw):
        atomic(root / capture["raw_path"], raw)
        for item in capture["attachments"]:
            if item["kind"] == "local":
                atomic(root / item["archive_path"], item["_data"])

    def _public_capture(self, capture):
        return {
            key: value
            for key, value in capture.items()
            if key != "_data"
            and not (key == "attachments" and any(
                isinstance(item, dict) and "_data" in item
                for item in value
            ))
        }

    def _capture_provenance(self, capture):
        public = self._public_capture(capture)
        public["attachments"] = [
            {key: value for key, value in item.items() if key != "_data"}
            for item in capture["attachments"]
        ]
        return public

    def _ingest_parsed(self, parsed, *, capture_channel=None, captured_at=None,
                       published_at=None, canonical_url=None, title=None,
                       tags=None, rights_classification=None,
                       public_export_allowed=None, rights_evidence=None,
                       latency_seconds=0.0, **_ignored):
        channel = capture_channel or parsed.get("capture_channel")
        if channel not in CAPTURE_CHANNELS:
            raise ReviewRequired("CAPTURE_CHANNEL_INVALID")
        normalized = normalize_text(parsed["text"])
        normalized_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        identifier = "doc_" + normalized_hash[:24]
        selected_url = normalize_canonical_url(
            canonical_url if canonical_url is not None else parsed.get("canonical_url")
        )
        selected_published = _timestamp(
            published_at if published_at is not None else parsed.get("published_at"),
            label="published_at", required=False,
        )
        selected_captured = _timestamp(
            captured_at if captured_at is not None
            else datetime.now(timezone.utc).isoformat(),
            label="captured_at", required=True,
        )
        selected_tags, review_tags = validate_tags(
            self.engine_root, tags if tags is not None else parsed.get("tags")
        )
        rights = rights_decision(
            {**parsed, "capture_channel": channel},
            classification=rights_classification,
            public_export_allowed=public_export_allowed,
            evidence=rights_evidence,
        )
        capture = self._capture({**parsed, "capture_channel": channel}, channel)
        record = {
            "schema_version": SCHEMA_VERSION,
            "doc_id": identifier,
            "canonical_url": selected_url,
            "published_at": selected_published,
            "captured_at": selected_captured,
            "content_hash": normalized_hash,
            "capture_channel": channel,
            "rights": rights,
            "title": title if title is not None else parsed.get("title"),
            "tags": selected_tags,
            "review_candidates": review_tags,
            "source_type": parsed.get("source_type"),
            "parser_version": parsed.get("parser_version", PARSER_VERSION),
            "canonical_write_allowed": False,
        }
        root = self.archive_root / identifier
        existing = root.is_dir()
        if existing:
            try:
                previous = json.loads((root / "record.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ReviewRequired("DOCUMENT_ARCHIVE_RECORD_INVALID") from exc
            if previous.get("content_hash") != normalized_hash:
                raise ReviewRequired("DOCUMENT_ID_COLLISION")
            return self._merge_exact(root, previous, capture, parsed["raw"])

        related = sorted(
            item["doc_id"] for item in self._existing_records()
            if selected_url and item.get("canonical_url") == selected_url
        )
        near = []
        if not related:
            for item in self._existing_records():
                path = self.archive_root / item["doc_id"] / "text.txt"
                try:
                    candidate_text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise RuntimeError("DOCUMENT_ARCHIVE_TEXT_INVALID") from exc
                comparison = near_duplicate(normalized, candidate_text)
                if comparison["candidate"]:
                    near.append({"doc_id": item["doc_id"], **comparison})
        near.sort(key=lambda item: item["doc_id"])
        disposition = (
            "revision" if related else
            "review_required" if near else
            "new"
        )
        record["dedup_disposition"] = disposition
        provenance = {
            "schema_version": SCHEMA_VERSION,
            "doc_id": identifier,
            "captures": [self._capture_provenance(capture)],
        }
        dedup = {
            "schema_version": SCHEMA_VERSION,
            "doc_id": identifier,
            "content_hash": normalized_hash,
            "canonical_url": selected_url,
            "disposition": disposition,
            "capture_ids": [capture["capture_id"]],
            "revision_of": related,
            "near_duplicates": near,
        }
        latency = float(latency_seconds or 0.0)
        if not math.isfinite(latency) or latency < 0:
            raise ReviewRequired("DOCUMENT_LATENCY_INVALID")
        metrics = {
            "schema_version": SCHEMA_VERSION,
            "doc_id": identifier,
            "tokens": 0,
            "tokens_per_document": 0,
            "cost_usd": 0.0,
            "cost_per_document_usd": 0.0,
            "latency_seconds": latency,
            "latency_seconds_per_document": latency,
            "review_minutes": 0.0,
            "review_minutes_per_document": 0.0,
            "llm_used": False,
        }
        self._write_capture(root, capture, parsed["raw"])
        atomic(root / "text.txt", normalized.encode("utf-8"))
        atomic(root / "record.json", canonical_bytes(record))
        atomic(root / "provenance.json", canonical_bytes(provenance))
        atomic(root / "dedup.json", canonical_bytes(dedup))
        atomic(root / "metrics.json", canonical_bytes(metrics))
        (root / "attachments").mkdir(parents=True, exist_ok=True)
        return {
            "status": "REVIEW_REQUIRED" if disposition == "review_required"
            else "ACCEPTED",
            "accepted": disposition != "review_required",
            "doc_id": identifier,
            "content_hash": normalized_hash,
            "disposition": disposition,
            "archive_path": str(root),
            "review_candidates": review_tags,
        }

    def _merge_exact(self, root, record, capture, raw):
        provenance_path = root / "provenance.json"
        dedup_path = root / "dedup.json"
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            dedup = json.loads(dedup_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReviewRequired("DOCUMENT_ARCHIVE_PROVENANCE_INVALID") from exc
        capture_ids = {item["capture_id"] for item in provenance.get("captures", [])}
        if capture["capture_id"] in capture_ids:
            return {
                "status": "ACCEPTED",
                "accepted": True,
                "doc_id": record["doc_id"],
                "content_hash": record["content_hash"],
                "disposition": "exact_merge",
                "archive_path": str(root),
                "changed": False,
            }
        public = self._capture_provenance(capture)
        self._write_capture(root, capture, raw)
        provenance["captures"].append(public)
        provenance["captures"].sort(key=lambda item: item["capture_id"])
        dedup["disposition"] = "exact_merge"
        dedup["capture_ids"] = sorted(
            set(dedup.get("capture_ids", [])) | {capture["capture_id"]}
        )
        atomic(provenance_path, canonical_bytes(provenance))
        atomic(dedup_path, canonical_bytes(dedup))
        return {
            "status": "ACCEPTED",
            "accepted": True,
            "doc_id": record["doc_id"],
            "content_hash": record["content_hash"],
            "disposition": "exact_merge",
            "archive_path": str(root),
            "changed": True,
        }
