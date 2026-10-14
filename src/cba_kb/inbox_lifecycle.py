"""Local-first inbox lifecycle (PR1-LOCAL-TXT-LIFECYCLE, LACD v1.0.2).

One-shot scan plus polling auto-discovery for the private instance inbox
(``<instance>/inbox/documents``), a persistent Source lifecycle registry
(``source_id -> file_id -> doc_id``), a materialized JSON index, and
rights-aware query. This is the orchestration layer the Q1 freeze requires;
it reuses :mod:`cba_kb.source_intake` and :mod:`cba_kb.document_lane` and
never rewrites them.

Design notes (frozen by Q1, summarized here for implementers):

- Only top-level regular files of the inbox are considered; no recursion,
  symlinks are never followed, dotfiles/sidecars are skipped with a reason.
- Auto-processing is limited to non-empty UTF-8 ``.txt`` files of at most
  1 MiB. Anything else becomes ``unsupported`` or ``review_required`` --
  never silently converted or ingested.
- A same-named ``<file>.source.json`` sidecar declares title / origin /
  rights. Missing or invalid metadata, or a public claim without verifiable
  evidence, forces ``REVIEW_REQUIRED``. Public visibility is restricted to
  attested synthetic fixtures; it is never granted by filename guessing.
- Identities are instance-scoped and content/path derived::

      file_id   = "file_" + sha256(relative_posix_path)[:24]
      source_id = "src_"  + sha256(b"local_file\\0" + relative_posix_path)[:24]
      doc_id    = "doc_"  + sha256(normalized_text)[:24]   # reused from document_lane

- A file is processed only after it is observed with identical size/mtime
  in at least two consecutive scans spanning the stability window
  (default 1.0 s, overridable for tests via
  ``CBA_KB_INBOX_STABILITY_SECONDS``). The bytes are re-read and re-statted
  before processing.
- All state lives under ``<instance>/data/inbox_lifecycle/`` and is written
  atomically. The engine source tree is never written to. A PID lock file
  refuses concurrent writers.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .common import atomic
from .document_lane import DocumentLane, content_hash as doc_content_hash
from .source_intake import SOURCE_INTAKE_VERSION, normalize_source_payload

SCHEMA_VERSION = 1
MAX_AUTO_TXT_BYTES = 1024 * 1024
STABILITY_ENV_VAR = "CBA_KB_INBOX_STABILITY_SECONDS"
DEFAULT_STABILITY_SECONDS = 1.0
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
MIN_POLL_INTERVAL_SECONDS = 1.0
SIDECAR_SUFFIX = ".source.json"
SYSTEM_SKIP_NAMES = frozenset({".DS_Store", "Thumbs.db", ".gitkeep"})
ORIGINS = frozenset({"synthetic_fixture", "local_owned", "third_party"})
RIGHTS_CLASSIFICATIONS = frozenset({"public", "private", "copyrighted", "unknown"})


class InboxLifecycleError(RuntimeError):
    pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stability_window() -> float:
    try:
        return max(0.0, float(os.environ.get(
            STABILITY_ENV_VAR, str(DEFAULT_STABILITY_SECONDS))))
    except ValueError:
        return DEFAULT_STABILITY_SECONDS


def file_id_for(relative_posix: str) -> str:
    return "file_" + _sha256_hex(relative_posix.encode("utf-8"))[:24]


def source_id_for(relative_posix: str) -> str:
    return "src_" + _sha256_hex(
        b"local_file\0" + relative_posix.encode("utf-8"))[:24]


def _atomic_json(path: Path, value) -> None:
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n").encode("utf-8"))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_query_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def load_sidecar(path: Path):
    """Return (metadata, error_code). error_code is None when valid."""
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        return None, "METADATA_UNREADABLE"
    if not isinstance(data, dict):
        return None, "METADATA_OBJECT_REQUIRED"
    if data.get("schema_version") != 1:
        return None, "METADATA_SCHEMA_VERSION_INVALID"
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, "METADATA_TITLE_REQUIRED"
    if data.get("origin") not in ORIGINS:
        return None, "METADATA_ORIGIN_INVALID"
    source_url = data.get("source_url")
    if source_url is not None:
        if not isinstance(source_url, str) or not source_url.strip():
            return None, "METADATA_SOURCE_URL_INVALID"
        lowered = source_url.strip().lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            return None, "METADATA_SOURCE_URL_INVALID"
    rights = data.get("rights")
    if not isinstance(rights, dict):
        return None, "METADATA_RIGHTS_REQUIRED"
    if rights.get("classification") not in RIGHTS_CLASSIFICATIONS:
        return None, "METADATA_RIGHTS_CLASSIFICATION_INVALID"
    if not isinstance(rights.get("public_export_allowed"), bool):
        return None, "METADATA_RIGHTS_EXPORT_FLAG_INVALID"
    if not isinstance(rights.get("evidence"), list):
        return None, "METADATA_RIGHTS_EVIDENCE_INVALID"
    return {
        "schema_version": 1,
        "title": title.strip(),
        "origin": data["origin"],
        "source_url": source_url.strip() if isinstance(source_url, str) else None,
        "rights": {
            "classification": rights["classification"],
            "public_export_allowed": rights["public_export_allowed"],
            "evidence": rights["evidence"],
        },
    }, None


def decide_visibility(meta):
    """Return (visibility, reason); visibility in {public, local, review}."""
    if meta is None:
        return "review", "METADATA_MISSING"
    rights = meta["rights"]
    classification = rights["classification"]
    if classification == "public":
        # Public auto-eligibility is restricted to attested synthetic
        # fixtures. A third-party or self-declared public claim without
        # verifiable authorization evidence never auto-opens.
        if (meta["origin"] == "synthetic_fixture"
                and rights["public_export_allowed"] is True
                and len(rights["evidence"]) > 0):
            return "public", "SYNTHETIC_PUBLIC_ATTESTED"
        return "review", "PUBLIC_CLAIM_UNVERIFIED"
    if classification == "private":
        return "local", "PRIVATE_LOCAL_ONLY"
    return "review", "RIGHTS_UNDETERMINED"


class InboxLifecycle:
    """Orchestrates scan / poll / status / query for one private instance."""

    def __init__(self, engine_root, instance):
        self.engine_root = Path(engine_root).resolve()
        self.instance = instance
        self.inbox_root = Path(instance.document_input_root).resolve()
        self.state_root = (Path(instance.data_root).resolve()
                           / "inbox_lifecycle")
        self.sources_root = self.state_root / "sources"
        self.ledger_path = self.state_root / "ledger" / "ledger.json"
        self.index_path = self.state_root / "index.json"
        self.checkpoint_path = self.state_root / "scan_checkpoint.json"
        self.review_root = self.state_root / "review"
        self.lock_path = self.state_root / ".lock"
        for directory in (self.inbox_root, self.sources_root,
                          self.ledger_path.parent, self.review_root):
            directory.mkdir(parents=True, exist_ok=True)

    # -- locking ---------------------------------------------------------
    def _acquire_lock(self):
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                data = _read_json(self.lock_path)
                pid = int(data.get("pid", 0))
            except (OSError, ValueError, TypeError):
                pid = 0
            if pid:
                try:
                    os.kill(pid, 0)
                    raise InboxLifecycleError(
                        "INBOX_LIFECYCLE_LOCKED: another writer holds the "
                        "instance lock")
                except OSError:
                    pass  # stale lock; fall through and replace
            fd = os.open(self.lock_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": _utc_now()}, handle)

    def _release_lock(self):
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    # -- persistent state -------------------------------------------------
    def _load_source(self, source_id):
        path = self.sources_root / f"{source_id}.json"
        if not path.is_file():
            return None
        return _read_json(path)

    def _save_source(self, record):
        _atomic_json(self.sources_root / f"{record['source_id']}.json", record)

    def _all_sources(self):
        records = []
        for path in sorted(self.sources_root.glob("*.json")):
            try:
                records.append(_read_json(path))
            except (OSError, ValueError) as exc:
                raise InboxLifecycleError(
                    "SOURCE_RECORD_INVALID:" + path.name) from exc
        return records

    def _load_checkpoint(self):
        if not self.checkpoint_path.is_file():
            return {}
        try:
            value = _read_json(self.checkpoint_path)
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_checkpoint(self, value):
        _atomic_json(self.checkpoint_path, value)

    def _ledger_event(self, kind, detail):
        try:
            ledger = _read_json(self.ledger_path)
            events = ledger.get("events", [])
        except (OSError, ValueError):
            events = []
        events.append({"ts": _utc_now(), "kind": kind, "detail": detail})
        _atomic_json(self.ledger_path,
                     {"schema_version": SCHEMA_VERSION, "events": events})

    def _index_payload(self, documents):
        ordered = sorted(documents, key=lambda item: item["doc_id"])
        return {
            "schema_version": SCHEMA_VERSION,
            "documents": ordered,
            "index_sha256": _sha256_hex(json.dumps(
                ordered, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")),
        }

    def _load_index_documents(self):
        """Return the materialized index entries; raise on corruption."""
        if not self.index_path.is_file():
            return []
        try:
            payload = _read_json(self.index_path)
        except (OSError, ValueError) as exc:
            raise InboxLifecycleError("INDEX_FAILED: index.json unreadable"
                                      ) from exc
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise InboxLifecycleError("INDEX_FAILED: index.json shape invalid")
        expected = self._index_payload(documents)["index_sha256"]
        if payload.get("index_sha256") != expected:
            raise InboxLifecycleError("INDEX_FAILED: index.json hash mismatch")
        return documents

    def _write_index(self, documents):
        _atomic_json(self.index_path, self._index_payload(documents))

    def _upsert_index_entry(self, entry):
        documents = self._load_index_documents()
        documents = [item for item in documents
                     if item["doc_id"] != entry["doc_id"]]
        documents.append(entry)
        self._write_index(documents)

    # -- scan -------------------------------------------------------------
    def _iter_inbox_files(self):
        """Yield (name, skip_reason); skip_reason None means processable."""
        try:
            entries = list(os.scandir(self.inbox_root))
        except OSError as exc:
            raise InboxLifecycleError("INBOX_UNREADABLE") from exc
        for entry in sorted(entries, key=lambda item: item.name):
            name = entry.name
            if entry.is_symlink():
                yield name, "SYMLINK_NOT_FOLLOWED"
                continue
            if not entry.is_file():
                yield name, "NOT_A_FILE"
                continue
            if name in SYSTEM_SKIP_NAMES or name.startswith("."):
                yield name, "SYSTEM_FILE_SKIPPED"
                continue
            if name.endswith(SIDECAR_SUFFIX):
                yield name, "METADATA_SIDECAR"
                continue
            yield name, None

    def scan_once(self):
        """Run one discovery+processing pass; return a JSON-able summary."""
        self._acquire_lock()
        try:
            return self._scan_once_locked()
        finally:
            self._release_lock()

    def _scan_once_locked(self):
        started = _utc_now()
        window = _stability_window()
        checkpoint = self._load_checkpoint()
        summary = {
            "schema_version": SCHEMA_VERSION,
            "started_at": started,
            "scanned_files": 0,
            "discoveries": 0,
            "processed_new": 0,
            "processed_total": 0,
            "unsupported": 0,
            "review_required": 0,
            "skipped_system_or_symlink": 0,
            "errors": [],
        }
        for name, skip_reason in self._iter_inbox_files():
            if skip_reason is not None:
                if skip_reason in ("SYMLINK_NOT_FOLLOWED",
                                   "SYSTEM_FILE_SKIPPED"):
                    summary["skipped_system_or_symlink"] += 1
                continue
            summary["scanned_files"] += 1
            try:
                self._scan_file(name, checkpoint, window, summary)
            except InboxLifecycleError as exc:
                summary["errors"].append(
                    {"file": name, "reason": str(exc)})
        self._save_checkpoint(checkpoint)
        summary["finished_at"] = _utc_now()
        self._ledger_event("scan_completed", {
            "started_at": started,
            "scanned_files": summary["scanned_files"],
            "processed_total": summary["processed_total"],
            "review_required": summary["review_required"],
            "unsupported": summary["unsupported"],
            "errors": len(summary["errors"]),
        })
        return summary

    def _scan_file(self, name, checkpoint, window, summary):
        rel_posix = name
        source_id = source_id_for(rel_posix)
        file_id = file_id_for(rel_posix)
        stat = os.stat(self.inbox_root / name)
        size, mtime_ns = stat.st_size, stat.st_mtime_ns

        record = self._load_source(source_id)
        if record is None:
            summary["discoveries"] += 1
            record = self._new_source_record(source_id, file_id, rel_posix)

        # Idempotent fast path: unchanged and already fully processed.
        if (record.get("processing_status") == "PROCESSING_COMPLETE"
                and record.get("observed_size") == size
                and record.get("observed_mtime_ns") == mtime_ns):
            record["last_seen_at"] = _utc_now()
            self._save_source(record)
            return

        suffix = Path(name).suffix.lower()
        if suffix != ".txt":
            record.update(
                processing_status="PROCESSING_COMPLETE",
                disposition="unsupported",
                review_status="UNSUPPORTED",
                index_status="NOT_INDEXED",
                raw_sha256=_sha256_hex(
                    (self.inbox_root / name).read_bytes()),
                observed_size=size,
                observed_mtime_ns=mtime_ns,
                last_seen_at=_utc_now(),
            )
            self._save_source(record)
            summary["unsupported"] += 1
            return

        # Stability gate: identical size/mtime across >=2 consecutive scans
        # spanning the stability window before any byte is processed.
        observed = checkpoint.get(source_id)
        now = time.time()
        same_as_before = (observed and observed.get("size") == size
                          and observed.get("mtime_ns") == mtime_ns)
        if same_as_before:
            observations = int(observed.get("observations", 0)) + 1
            first_observed = observed.get("first_observed_at", now)
        else:
            observations = 1
            first_observed = now
        checkpoint[source_id] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "first_observed_at": first_observed,
            "observations": observations,
            "last_scan_at": now,
        }
        if observations < 2 or (now - first_observed) < window:
            if record.get("processing_status") != "PROCESSING_COMPLETE":
                record["processing_status"] = "PENDING_STABILITY"
                record["observed_size"] = size
                record["observed_mtime_ns"] = mtime_ns
                record["last_seen_at"] = _utc_now()
                self._save_source(record)
            return

        # Stable: re-read and re-stat before processing.
        raw = (self.inbox_root / name).read_bytes()
        restat = os.stat(self.inbox_root / name)
        if restat.st_size != size or restat.st_mtime_ns != mtime_ns:
            checkpoint[source_id] = {
                "size": restat.st_size, "mtime_ns": restat.st_mtime_ns,
                "first_observed_at": now, "observations": 1,
                "last_scan_at": now,
            }
            return
        self._process_stable_txt(record, rel_posix, raw, summary)

    def _new_source_record(self, source_id, file_id, rel_posix):
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": source_id,
            "file_id": file_id,
            "provider": "local_file",
            "relative_path": rel_posix,
            "title": None,
            "source_url": None,
            "raw_sha256": None,
            "content_hash": None,
            "metadata_sha256": None,
            "doc_id": None,
            "rights": None,
            "provenance": None,
            "first_seen_at": _utc_now(),
            "last_seen_at": _utc_now(),
            "observed_size": None,
            "observed_mtime_ns": None,
            "processing_status": "DISCOVERED",
            "disposition": None,
            "review_status": "NONE",
            "index_status": "NOT_INDEXED",
            "archive_status": "NOT_REQUESTED",
            "publication_status": "NOT_PUBLISHED",
            "review_reason": None,
            "revision_history": [],
        }

    def _mark_review(self, record, reason, raw, raw_sha256, summary):
        record.update(
            processing_status="PROCESSING_COMPLETE",
            disposition="review_required",
            review_status="REVIEW_REQUIRED",
            index_status="NOT_INDEXED",
            review_reason=reason,
            last_seen_at=_utc_now(),
        )
        review_dir = self.review_root / record["source_id"]
        review_dir.mkdir(parents=True, exist_ok=True)
        if raw:
            atomic(review_dir / "original.bin", raw)
        _atomic_json(review_dir / "review.json", {
            "schema_version": SCHEMA_VERSION,
            "source_id": record["source_id"],
            "relative_path": record["relative_path"],
            "reason": reason,
            "raw_sha256": raw_sha256,
            "reviewed_at": _utc_now(),
        })
        self._save_source(record)
        summary["review_required"] += 1
        self._ledger_event("source_review_required", {
            "source_id": record["source_id"],
            "relative_path": record["relative_path"],
            "reason": reason,
        })

    def _process_stable_txt(self, record, rel_posix, raw, summary):
        raw_sha256 = _sha256_hex(raw)
        record.update(
            raw_sha256=raw_sha256,
            observed_size=len(raw),
            observed_mtime_ns=os.stat(self.inbox_root / rel_posix).st_mtime_ns,
            last_seen_at=_utc_now(),
        )
        if not raw:
            self._mark_review(record, "EMPTY_FILE", raw, raw_sha256, summary)
            return
        if len(raw) > MAX_AUTO_TXT_BYTES:
            self._mark_review(record, "FILE_TOO_LARGE", raw, raw_sha256,
                              summary)
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            self._mark_review(record, "TXT_ENCODING_UNSUPPORTED", raw,
                              raw_sha256, summary)
            return
        if not text.strip():
            self._mark_review(record, "EMPTY_TEXT", raw, raw_sha256, summary)
            return

        sidecar_path = self.inbox_root / (rel_posix + SIDECAR_SUFFIX)
        meta, meta_error = (load_sidecar(sidecar_path)
                            if sidecar_path.is_file()
                            else (None, "METADATA_MISSING"))
        if meta is not None:
            try:
                metadata_sha256 = _sha256_hex(
                    sidecar_path.read_bytes())
            except OSError:
                metadata_sha256 = None
        else:
            metadata_sha256 = None
        record["metadata_sha256"] = metadata_sha256

        visibility, visibility_reason = decide_visibility(meta)
        if meta_error is not None and meta is None:
            visibility, visibility_reason = "review", meta_error
        if visibility == "review":
            self._mark_review(record, visibility_reason, raw, raw_sha256,
                              summary)
            return

        lane_rights = {
            "classification": ("public" if visibility == "public"
                               else "private"),
            "public_export_allowed": visibility == "public",
            "evidence": meta["rights"]["evidence"],
        }
        provenance = {
            "intake_version": SOURCE_INTAKE_VERSION,
            "provider": "local_file",
            "locator": rel_posix,
            "origin": meta["origin"],
            "source_url": meta["source_url"],
            "title": meta["title"],
            "visibility_decision": visibility_reason,
            "inbox_lifecycle": "pr1",
        }
        try:
            envelope, _norm_text = normalize_source_payload(
                raw,
                source_provider="local_file",
                source_item_id_or_locator=rel_posix,
                media_type="text/plain",
                rights=lane_rights,
                provenance=provenance,
                raw_ref=rel_posix,
            )
        except Exception as exc:
            self._mark_review(record, "INTAKE_REJECTED:" + type(exc).__name__,
                              raw, raw_sha256, summary)
            return

        lane = DocumentLane(self.engine_root, self.instance)
        try:
            lane_result = lane.ingest_file(
                rel_posix,
                capture_channel="local_file",
                title=meta["title"],
                rights_classification=lane_rights["classification"],
                public_export_allowed=lane_rights["public_export_allowed"],
                rights_evidence=lane_rights["evidence"],
            )
        except Exception as exc:
            self._mark_review(record, "LANE_REJECTED:" + type(exc).__name__,
                              raw, raw_sha256, summary)
            return

        doc_id = lane_result.get("doc_id")
        content_hash = lane_result.get("content_hash")
        previous_doc_id = record.get("doc_id")
        lane_disposition = lane_result.get("disposition")
        if lane_result.get("status") == "REVIEW_REQUIRED":
            disposition = "review_required"
            review_status = "REVIEW_REQUIRED"
            index_status = "NOT_INDEXED"
        else:
            review_status = "NONE"
            index_status = "SEARCHABLE"
            if previous_doc_id and previous_doc_id != doc_id:
                disposition = "revision"
            else:
                disposition = lane_disposition or "new"

        # Source of truth for rights after a possible exact-merge
        # tightening: re-read the persisted archive record.
        archive_record = self._archive_record(doc_id)
        archive_rights = (archive_record.get("rights")
                          if archive_record else lane_rights)
        effective_visibility = (
            "public"
            if (visibility == "public"
                and isinstance(archive_rights, dict)
                and archive_rights.get("classification") == "public"
                and archive_rights.get("public_export_allowed") is True
                and bool(archive_rights.get("evidence")))
            else "local")

        is_new = record.get("doc_id") is None and disposition == "new"
        if previous_doc_id != doc_id or not record.get("revision_history"):
            record.setdefault("revision_history", []).append({
                "observed_at": _utc_now(),
                "raw_sha256": raw_sha256,
                "content_hash": content_hash,
                "doc_id": doc_id,
                "disposition": disposition,
            })
        record.update(
            title=meta["title"],
            source_url=meta["source_url"],
            content_hash=content_hash,
            doc_id=doc_id,
            rights=archive_rights,
            provenance=provenance,
            processing_status="PROCESSING_COMPLETE",
            disposition=disposition,
            review_status=review_status,
            index_status=index_status,
            review_reason=(None if review_status == "NONE"
                           else "LANE_" + str(
                               lane_result.get("reason", "NEAR_DUPLICATE"))),
            last_seen_at=_utc_now(),
        )
        self._save_source(record)

        if index_status == "SEARCHABLE":
            entry = {
                "doc_id": doc_id,
                "source_id": record["source_id"],
                "source_ids": sorted({record["source_id"]} | set(
                    item.get("source_id") for item in
                    self._load_index_documents()
                    if item["doc_id"] == doc_id and item.get("source_id"))),
                "file_id": record["file_id"],
                "title": meta["title"],
                "content_hash": content_hash,
                "visibility": effective_visibility,
                "indexed_at": _utc_now(),
            }
            # Preserve every linked source for a shared doc_id.
            entry["source_ids"] = sorted(set(entry["source_ids"]))
            self._upsert_index_entry(entry)

        summary["processed_total"] += 1
        if is_new:
            summary["processed_new"] += 1
        self._ledger_event("source_processed", {
            "source_id": record["source_id"],
            "relative_path": rel_posix,
            "doc_id": doc_id,
            "disposition": disposition,
            "visibility": effective_visibility,
        })
        _ = envelope  # envelope validated the intake contract; archive holds text

    def _archive_record(self, doc_id):
        if not doc_id:
            return None
        path = (Path(self.instance.data_root).resolve() / "document_lane"
                / "archive" / doc_id / "record.json")
        if not path.is_file():
            return None
        try:
            return _read_json(path)
        except (OSError, ValueError):
            return None

    # -- poll -------------------------------------------------------------
    def poll(self, interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
             max_scans=None):
        """Poll the inbox until interrupted or max_scans is reached.

        The instance lock is held for the whole polling run so a second
        concurrent writer is refused instead of interleaving scans.
        """
        try:
            interval = float(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise InboxLifecycleError(
                "INBOX_POLL_INTERVAL_INVALID") from exc
        if interval < MIN_POLL_INTERVAL_SECONDS:
            raise InboxLifecycleError(
                "INBOX_POLL_INTERVAL_MINIMUM_SECONDS:1")
        self._acquire_lock()
        try:
            scans = 0
            last_summary = None
            while True:
                last_summary = self._scan_once_locked()
                scans += 1
                if max_scans is not None and scans >= max_scans:
                    break
                time.sleep(interval)
        finally:
            self._release_lock()
        return {
            "schema_version": SCHEMA_VERSION,
            "scans_completed": scans,
            "interval_seconds": interval,
            "last_summary": last_summary,
        }

    # -- status -----------------------------------------------------------
    def status_all(self):
        records = self._all_sources()
        by_processing, by_disposition, by_review, by_index = {}, {}, {}, {}
        summaries = []
        for rec in records:
            for bucket, key in (
                    (by_processing, rec.get("processing_status")),
                    (by_disposition, rec.get("disposition") or "none"),
                    (by_review, rec.get("review_status")),
                    (by_index, rec.get("index_status"))):
                bucket[key] = bucket.get(key, 0) + 1
            summaries.append({
                "source_id": rec["source_id"],
                "file_id": rec["file_id"],
                "relative_path": rec["relative_path"],
                "title": rec.get("title"),
                "processing_status": rec.get("processing_status"),
                "disposition": rec.get("disposition"),
                "review_status": rec.get("review_status"),
                "review_reason": rec.get("review_reason"),
                "index_status": rec.get("index_status"),
                "doc_id": rec.get("doc_id"),
                "last_seen_at": rec.get("last_seen_at"),
            })
        return {
            "schema_version": SCHEMA_VERSION,
            "source_count": len(records),
            "by_processing_status": by_processing,
            "by_disposition": by_disposition,
            "by_review_status": by_review,
            "by_index_status": by_index,
            "sources": sorted(summaries,
                              key=lambda item: item["relative_path"] or ""),
        }

    def status_source(self, source_id):
        record = self._load_source(source_id)
        if record is None:
            raise InboxLifecycleError("SOURCE_NOT_FOUND:" + str(source_id))
        detail = dict(record)
        detail["original_location"] = str(
            self.inbox_root / record["relative_path"])
        if record.get("doc_id"):
            detail["archive_location"] = str(
                Path(self.instance.data_root).resolve() / "document_lane"
                / "archive" / record["doc_id"])
        return detail

    # -- query ------------------------------------------------------------
    def query(self, *, id=None, title=None, keyword=None,
              visibility="local"):
        """Rights-aware search over the materialized index."""
        modes = [name for name, value in
                 (("id", id), ("title", title), ("keyword", keyword))
                 if value is not None]
        if len(modes) != 1:
            raise InboxLifecycleError(
                "ARCHIVE_QUERY_EXACTLY_ONE_MODE_REQUIRED")
        if visibility not in ("local", "public"):
            raise InboxLifecycleError("ARCHIVE_QUERY_VISIBILITY_INVALID")
        self._acquire_lock()
        try:
            documents = self._load_index_documents()
            if id is not None:
                wanted = id.strip()
                if not wanted:
                    raise InboxLifecycleError("ARCHIVE_QUERY_TEXT_REQUIRED")
                candidates = [item for item in documents
                              if wanted in (item.get("file_id"),
                                            item.get("source_id"),
                                            item.get("doc_id"))]
            else:
                text = (title if title is not None else keyword) or ""
                if not text.strip():
                    raise InboxLifecycleError("ARCHIVE_QUERY_TEXT_REQUIRED")
                needle = _normalize_query_text(text)
                candidates = []
                for item in documents:
                    haystacks = [_normalize_query_text(
                        item.get("title") or "")]
                    if keyword is not None:
                        haystacks.append(_normalize_query_text(
                            self._indexed_text(item["doc_id"])))
                    if any(needle in hay for hay in haystacks):
                        candidates.append(item)
            results = []
            healed = False
            for item in candidates:
                verified = self._verify_candidate(item)
                if verified is None:
                    continue
                if verified["visibility"] != item.get("visibility"):
                    item["visibility"] = verified["visibility"]
                    healed = True
                if visibility == "public" and verified["visibility"] != "public":
                    continue
                results.append(verified)
            if healed:
                self._write_index(documents)
            return {
                "schema_version": SCHEMA_VERSION,
                "mode": modes[0],
                "visibility": visibility,
                "count": len(results),
                "results": sorted(results,
                                  key=lambda item: item["doc_id"]),
            }
        finally:
            self._release_lock()

    def _indexed_text(self, doc_id):
        path = (Path(self.instance.data_root).resolve() / "document_lane"
                / "archive" / doc_id / "text.txt")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InboxLifecycleError(
                "INDEX_FAILED: archive text unreadable:" + doc_id) from exc

    def _verify_candidate(self, item):
        """Re-check rights and integrity; return the hit or None to skip."""
        doc_id = item["doc_id"]
        source = self._load_source(item.get("source_id"))
        if source is None:
            raise InboxLifecycleError(
                "INDEX_FAILED: source record missing:" + doc_id)
        if source.get("review_status") != "NONE":
            return None
        archive_record = self._archive_record(doc_id)
        if archive_record is None:
            raise InboxLifecycleError(
                "INDEX_FAILED: archive record missing:" + doc_id)
        rights = archive_record.get("rights") or {}
        public_ok = (
            rights.get("classification") == "public"
            and rights.get("public_export_allowed") is True
            and bool(rights.get("evidence")))
        visibility = "public" if public_ok else "local"
        text = self._indexed_text(doc_id)
        if doc_content_hash(text) != item.get("content_hash"):
            raise InboxLifecycleError(
                "INDEX_FAILED: content hash mismatch:" + doc_id)
        if doc_content_hash(text) != archive_record.get("content_hash"):
            raise InboxLifecycleError(
                "INDEX_FAILED: archive content drift:" + doc_id)
        return {
            "doc_id": doc_id,
            "file_id": item.get("file_id"),
            "source_id": item.get("source_id"),
            "source_ids": item.get("source_ids", [item.get("source_id")]),
            "title": item.get("title"),
            "relative_path": source.get("relative_path"),
            "content_hash": item.get("content_hash"),
            "visibility": visibility,
        }
