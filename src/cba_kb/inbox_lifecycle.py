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
  (1.0 s, frozen by contract and deliberately not configurable).
  The bytes are re-read and re-statted before processing.
- All state lives under ``<instance>/data/inbox_lifecycle/`` and is written
  atomically. The engine source tree is never written to. A PID lock file
  refuses concurrent writers.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import fcntl

from urllib.parse import urlsplit

from .common import atomic
from .document_lane import DocumentLane, content_hash as doc_content_hash
from .document_sources import ReviewRequired, parse_local
from .source_intake import (SOURCE_INTAKE_VERSION, SourceIntakeError,
                            normalize_source_payload)

SCHEMA_VERSION = 1
MAX_AUTO_TXT_BYTES = 1024 * 1024
# Frozen minimum: a file is processed only after identical size/mtime in
# >=2 consecutive scans spanning at least this window. Deliberately not
# configurable: the Q1 contract fixes the minimum at one second and no
# production knob may weaken it.
STABILITY_WINDOW_SECONDS = 1.0
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


# Maximum size for trusted small JSON files (record.json, etc.).
_JSON_MAX_BYTES = 1 << 20  # 1 MiB


def _read_json_nofollow(path: Path):
    """Read JSON with O_NOFOLLOW and a size bound.

    R10 F4: refuses symlinks at the final component and rejects
    unbounded reads. Used for archive record.json.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("not a regular file")
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(_JSON_MAX_BYTES + 1)
    except OSError:
        raise
    if len(raw) > _JSON_MAX_BYTES:
        raise OSError("JSON too large")
    return json.loads(raw.decode("utf-8"))


def _normalize_query_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def load_sidecar(path: Path):
    """Return (metadata, error_code, raw_bytes).

    error_code is None when valid; raw_bytes is the exact byte snapshot
    that was parsed (R7-B1: parse and hash use the same snapshot, so the
    stored metadata_sha256 always describes the parsed rights).

    C1: any structural anomaly (wrong types, unparsable URL, unreadable
    file) is a metadata rejection, never an exception escaping to the
    scan loop.
    """
    try:
        return _load_sidecar_inner(path)
    except Exception:
        return None, "METADATA_INVALID", None


def _load_sidecar_inner(path: Path):
    try:
        # O_NOFOLLOW: never follow a symlinked sidecar (A2).
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None, "METADATA_UNREADABLE", None
    try:
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None, "METADATA_UNREADABLE", None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "METADATA_UNREADABLE", None
    if not isinstance(data, dict):
        return None, "METADATA_OBJECT_REQUIRED", None
    if data.get("schema_version") != 1:
        return None, "METADATA_SCHEMA_VERSION_INVALID", None
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, "METADATA_TITLE_REQUIRED", None
    origin = data.get("origin")
    if not isinstance(origin, str) or origin not in ORIGINS:
        return None, "METADATA_ORIGIN_INVALID", None
    source_url = data.get("source_url")
    if source_url is not None:
        if not isinstance(source_url, str) or not source_url.strip():
            return None, "METADATA_SOURCE_URL_INVALID", None
        try:
            parts = urlsplit(source_url.strip())
        except ValueError:
            return None, "METADATA_SOURCE_URL_INVALID", None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None, "METADATA_SOURCE_URL_INVALID", None
    rights = data.get("rights")
    if not isinstance(rights, dict):
        return None, "METADATA_RIGHTS_REQUIRED", None
    classification = rights.get("classification")
    if not isinstance(classification, str) \
            or classification not in RIGHTS_CLASSIFICATIONS:
        return None, "METADATA_RIGHTS_CLASSIFICATION_INVALID", None
    if not isinstance(rights.get("public_export_allowed"), bool):
        return None, "METADATA_RIGHTS_EXPORT_FLAG_INVALID", None
    evidence = rights.get("evidence")
    if not isinstance(evidence, list):
        return None, "METADATA_RIGHTS_EVIDENCE_INVALID", None
    if any(not isinstance(item, dict) for item in evidence):
        return None, "METADATA_RIGHTS_EVIDENCE_STRUCTURE_INVALID", None
    return {
        "schema_version": 1,
        "title": title.strip(),
        "origin": origin,
        "source_url": source_url.strip() if isinstance(source_url, str) else None,
        "rights": {
            "classification": rights["classification"],
            "public_export_allowed": rights["public_export_allowed"],
            "evidence": rights["evidence"],
        },
    }, None, raw


def decide_visibility(meta):
    """Return (visibility, reason); visibility in {local, review}.

    SCOPED PR1 (2026-10-14 Human decision): automatic public authorization
    is DEFERRED to a future independent PR. All newly ingested documents
    are local-only by default. The rights metadata is still parsed and
    stored, but never triggers automatic public visibility.
    Review logic is preserved: invalid/missing metadata and unverified
    public claims still go to human review.
    """
    if meta is None:
        return "review", "METADATA_MISSING"
    rights = meta["rights"]
    classification = rights["classification"]
    if classification == "public":
        # SCOPED: public auto-authorization deferred. A public claim
        # (even synthetic+evidence) goes to review instead of auto-public.
        # (Original: public iff synthetic_fixture + public_export_allowed
        #  + evidence; deferred to future PR.)
        return "review", "PUBLIC_DEFERRED_TO_REVIEW"
    if classification == "private":
        return "local", "PRIVATE_LOCAL_ONLY"
    return "review", "RIGHTS_UNDETERMINED"


class InboxLifecycle:
    """Orchestrates scan / poll / status / query for one private instance."""

    def __init__(self, engine_root, instance):
        self.engine_root = Path(engine_root).resolve()
        self.instance = instance
        instance_root = Path(instance.root).resolve()
        # Invariant 2: Path-constrained I/O.
        # R6-A3: verify the UNRESOLVED data root first. The instance's
        # data_root is already resolved by load_instance(), which would hide
        # a symlink at <instance>/data. We reconstruct the raw path from
        # instance.root and check it directly.
        raw_data_root = Path(instance.root) / "data"
        if os.path.islink(raw_data_root):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: instance data/ is a symlink "
                "(state relocation not allowed)")
        # The lifecycle's state path must BE data/inbox_lifecycle, not just
        # resolve inside the instance. A symlink at data/inbox_lifecycle
        # (even to another directory inside the instance, e.g. ../inbox/
        # documents) is rejected. This prevents state relocation attacks.
        data_root = Path(instance.data_root).resolve()
        self.inbox_root = Path(instance.document_input_root).resolve()
        # Check the UNRESOLVED path first: it must not be a symlink.
        raw_state = data_root / "inbox_lifecycle"
        if os.path.islink(raw_state):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: data/inbox_lifecycle is a "
                "symlink (state relocation not allowed)")
        # Then resolve and validate the final path.
        self.state_root = raw_state.resolve()
        # A2: also validate the DocumentLane write targets.
        lane_roots = [
            (data_root / "document_lane" / "archive").resolve(),
            (data_root / "document_lane" / "review").resolve(),
            (data_root / "document_lane" / "reports").resolve(),
        ]
        # A6/A2: the resolved state, inbox, and lane paths must stay inside
        # the instance and outside the engine tree.
        for label, path in ([("state", self.state_root),
                             ("inbox", self.inbox_root)] +
                            [(f"lane_{i}", p)
                             for i, p in enumerate(lane_roots)]):
            try:
                path.relative_to(instance_root)
            except ValueError:
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: " + label +
                    " resolves outside the instance")
            try:
                path.relative_to(self.engine_root)
            except ValueError:
                pass
            else:
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: " + label +
                    " resolves inside the engine tree")
        self.sources_root = self.state_root / "sources"
        self.ledger_path = self.state_root / "ledger" / "ledger.json"
        self.index_path = self.state_root / "index.json"
        self.checkpoint_path = self.state_root / "scan_checkpoint.json"
        self.review_root = self.state_root / "review"
        self.lock_path = self.state_root / ".lock"
        self._lock_fd = None
        # A2: all state subdirectories must be real directories, not symlinks.
        # An attacker could symlink sources/, ledger/, or review/ to an
        # external location before the lifecycle runs.
        for directory in (self.inbox_root, self.sources_root,
                          self.ledger_path.parent, self.review_root):
            if os.path.islink(directory):
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: state subdir is a symlink: "
                    + str(directory))
            directory.mkdir(parents=True, exist_ok=True)
            # Re-check after mkdir (TOCTOU: symlink created between check
            # and mkdir). If it's now a symlink, refuse.
            if os.path.islink(directory):
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: state subdir became symlink: "
                    + str(directory))

    # -- locking ---------------------------------------------------------
    # Single-writer mutual exclusion via an OS file lock (fcntl.flock).
    # The lock is atomic: LOCK_EX|LOCK_NB either succeeds (we own it) or
    # fails (someone else owns it). A dead process's lock is released by
    # the OS itself, so there is no "stale lock" to take over and no
    # takeover race. The PID written into the file is diagnostic only.
    def _acquire_lock(self):
        if self._lock_fd is not None:
            raise InboxLifecycleError("INBOX_LIFECYCLE_LOCK_REENTRANT")
        # C-lock: O_NOFOLLOW prevents following a symlinked .lock to an
        # external file. We also verify the lock path's parent is a real
        # directory (not a symlink) before opening.
        lock_parent = self.lock_path.parent
        if os.path.islink(lock_parent):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: lock parent is a symlink")
        if os.path.islink(self.lock_path):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: .lock is a symlink")
        try:
            fd = os.open(self.lock_path,
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
        except OSError as exc:
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_LOCK_OPEN_FAILED: " + str(exc)) from exc
        # Verify it's a regular file (not a FIFO, socket, etc.).
        # Also reject hardlinks (st_nlink > 1): ftruncate on a hardlinked
        # .lock would truncate an external file (R10 F2). The lock file
        # must be owned solely by this instance.
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                os.close(fd)
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: .lock is not a regular file")
            if st.st_nlink > 1:
                os.close(fd)
                raise InboxLifecycleError(
                    "INBOX_LIFECYCLE_PATH_ESCAPE: .lock has hardlinks "
                    "(refusing to truncate)")
        except OSError as exc:
            if isinstance(exc, InboxLifecycleError):
                raise
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_LOCKED: another writer holds the "
                "instance lock") from exc
        try:
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(
                {"pid": os.getpid(), "started_at": _utc_now()}).encode())
        except OSError:
            pass  # diagnostic content only; the flock is the mechanism
        self._lock_fd = fd

    def _release_lock(self):
        fd, self._lock_fd = self._lock_fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

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

    def _load_index_documents(self, *, strict=True):
        """Return the materialized index entries; raise on corruption.

        Every scan materializes the index (possibly empty), and source
        records are only ever created by a scan. So a missing index.json
        with existing source records means the index was deleted or lost:
        that is INDEX_FAILED, never a silent empty result. A missing index
        with no source records is a fresh instance: [].
        With strict=False (mutation paths), a missing index is treated as
        empty; the read path (query) always stays strict.
        """
        if not self.index_path.is_file():
            if strict and self._all_sources():
                raise InboxLifecycleError("INDEX_FAILED: index.json missing")
            return []
        try:
            payload = _read_json(self.index_path)
        except (OSError, ValueError) as exc:
            raise InboxLifecycleError("INDEX_FAILED: index.json unreadable"
                                      ) from exc
        # C2: validate the top-level shape before touching it.
        if not isinstance(payload, dict):
            raise InboxLifecycleError("INDEX_FAILED: index.json shape invalid")
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise InboxLifecycleError("INDEX_FAILED: index.json shape invalid")
        for entry in documents:
            if not isinstance(entry, dict) or not entry.get("doc_id"):
                raise InboxLifecycleError(
                    "INDEX_FAILED: index entry shape invalid")
            links = entry.get("links", [])
            if not isinstance(links, list) or not links or not all(
                    isinstance(link, dict) and link.get("source_id")
                    for link in links):
                raise InboxLifecycleError(
                    "INDEX_FAILED: index entry links invalid")
        expected = self._index_payload(documents)["index_sha256"]
        if payload.get("index_sha256") != expected:
            raise InboxLifecycleError("INDEX_FAILED: index.json hash mismatch")
        return documents

    def _write_index(self, documents):
        _atomic_json(self.index_path, self._index_payload(documents))

    def _index_link(self, doc_id, *, source_id, file_id, relative_path,
                    title, content_hash, visibility):
        """Link a source to a document entry, creating it if needed.

        A5: every linked source's title is retained and searchable; the
        entry title is the latest writer's (display), but queries match
        against all of them.
        """
        documents = self._load_index_documents(strict=False)
        entry = next((item for item in documents
                      if item["doc_id"] == doc_id), None)
        link = {"source_id": source_id, "file_id": file_id,
                "relative_path": relative_path, "title": title}
        if entry is None:
            entry = {"doc_id": doc_id, "title": title,
                     "titles": [title],
                     "content_hash": content_hash, "visibility": visibility,
                     "indexed_at": _utc_now(), "links": [link]}
            documents.append(entry)
        else:
            entry["title"] = title
            entry["content_hash"] = content_hash
            entry["visibility"] = visibility
            entry["indexed_at"] = _utc_now()
            entry["links"] = [item for item in entry.get("links", [])
                              if item.get("source_id") != source_id]
            entry["links"].append(link)
            entry["titles"] = sorted({
                item.get("title") for item in entry["links"]
                if item.get("title")})
        self._write_index(documents)

    def _index_unlink_source(self, source_id, doc_id=None):
        """Drop a source's links; remove entries left with no links."""
        documents = self._load_index_documents(strict=False)
        changed = False
        kept = []
        for item in documents:
            if doc_id is not None and item["doc_id"] != doc_id:
                kept.append(item)
                continue
            links = [link for link in item.get("links", [])
                     if link.get("source_id") != source_id]
            if len(links) != len(item.get("links", [])):
                changed = True
            if links:
                item["links"] = links
                kept.append(item)
            else:
                changed = True
        if changed:
            self._write_index(kept)

    def _doc_visibility(self, doc_id, linked_records):
        """Recompute a document's visibility.

        SCOPED PR1 (2026-10-14 Human decision): automatic public
        authorization is DEFERRED. Always returns "local".
        The rights metadata is preserved for future use.
        """
        return "local"

    def _rebuild_index(self):
        """Rebuild the materialized index from the source records.

        The source records are the source of truth; the index is a derived
        view. Rebuilding is deterministic and logged, never silent.

        B1: sources in review keep their links so the query-time quarantine
        (shared doc not public while any linked source is in review) survives
        a rebuild. A5: all linked titles are retained.
        R6-A2: FAILED sources are also kept. A failed source's quarantine
        must survive a rebuild; dropping it would lose the safety constraint
        and potentially re-public a document that should stay isolated.
        """
        by_doc = {}
        for rec in self._all_sources():
            # Include SEARCHABLE sources, reviewed sources (for B1
            # quarantine), AND failed sources (for R6-A2 quarantine).
            # A failed source's link is kept; the query path sees
            # PROCESSING_FAILED and blocks public.
            if not rec.get("doc_id"):
                continue
            if rec.get("index_status") not in ("SEARCHABLE", "NOT_INDEXED"):
                continue
            # Include NOT_INDEXED if it's a review OR a failure.
            # (Failures were previously dropped, losing the quarantine.)
            if (rec.get("index_status") == "NOT_INDEXED"
                    and rec.get("review_status") != "REVIEW_REQUIRED"
                    and rec.get("processing_status") != "PROCESSING_FAILED"):
                continue
            doc_id = rec["doc_id"]
            entry = by_doc.setdefault(doc_id, {"records": []})
            entry["records"].append(rec)
        documents = []
        for doc_id, grouped in by_doc.items():
            records = grouped["records"]
            first = records[0]
            links = [{"source_id": rec["source_id"],
                      "file_id": rec["file_id"],
                      "relative_path": rec["relative_path"],
                      "title": rec.get("title")}
                     for rec in records]
            documents.append({
                "doc_id": doc_id,
                "title": first.get("title"),
                "titles": sorted({rec.get("title") for rec in records
                                  if rec.get("title")}),
                "content_hash": first.get("content_hash"),
                "visibility": self._doc_visibility(doc_id, records),
                "indexed_at": _utc_now(),
                "links": links,
            })
        self._write_index(documents)
        self._ledger_event("index_rebuilt", {
            "documents": len(documents),
            "reason": "index.json missing with existing sources",
        })

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

    def _read_verified_bytes(self, path, expected_size, expected_mtime_ns):
        """Invariant 1: Read verified bytes from a pinned fd.

        Opens with O_NOFOLLOW, verifies regular file via fstat, does a
        bounded read (MAX+1), and re-verifies fd identity after. Returns
        the bytes. Raises InboxLifecycleError on any verification failure.

        The caller must have already established stability via path stat;
        this method pins to the inode and eliminates the ABA window.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise InboxLifecycleError(
                "VERIFY_OPEN_FAILED: " + str(exc)) from exc
        try:
            st1 = os.fstat(fd)
            if not stat.S_ISREG(st1.st_mode):
                raise InboxLifecycleError("VERIFY_NOT_REGULAR_FILE")
            # Size from fstat (not path stat) — the fd is pinned.
            if st1.st_size != expected_size:
                raise InboxLifecycleError("VERIFY_SIZE_CHANGED")
            if st1.st_mtime_ns != expected_mtime_ns:
                raise InboxLifecycleError("VERIFY_MTIME_CHANGED")
            if st1.st_size > MAX_AUTO_TXT_BYTES:
                raise InboxLifecycleError("VERIFY_OVERSIZE")
            # Bounded read: MAX+1 to detect oversize without unbounded read.
            chunks = []
            remaining = MAX_AUTO_TXT_BYTES + 1
            while remaining > 0:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_AUTO_TXT_BYTES:
                raise InboxLifecycleError("VERIFY_OVERSIZE")
            # Re-verify fd identity after read (detects truncation/replace).
            st2 = os.fstat(fd)
            if (st2.st_ino != st1.st_ino or st2.st_dev != st1.st_dev
                    or st2.st_size != st1.st_size
                    or st2.st_mtime_ns != st1.st_mtime_ns):
                raise InboxLifecycleError("VERIFY_CHANGED_DURING_READ")
            # Belt-and-suspenders: length must match.
            if len(raw) != st1.st_size:
                raise InboxLifecycleError("VERIFY_LENGTH_MISMATCH")
            return raw
        finally:
            os.close(fd)

    def _scan_once_locked(self):
        started = _utc_now()
        window = STABILITY_WINDOW_SECONDS
        checkpoint = self._load_checkpoint()
        # If the index was deleted or lost while source records exist,
        # rebuild it deterministically from the source records (logged,
        # never silent) instead of continuing with a partial view.
        if not self.index_path.is_file() and self._all_sources():
            self._rebuild_index()
        # C1: validate the existing index at scan start. A corrupt index
        # must not be silently ignored while the scan reports success.
        if self.index_path.is_file():
            try:
                self._load_index_documents(strict=True)
            except InboxLifecycleError as exc:
                if self._all_sources():
                    self._rebuild_index()
                else:
                    raise InboxLifecycleError(
                        f"SCAN_FAILED: {exc}") from exc
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
            "skipped": [],
            "errors": [],
        }
        for name, skip_reason in self._iter_inbox_files():
            if skip_reason is not None:
                if skip_reason in ("SYMLINK_NOT_FOLLOWED",
                                   "SYSTEM_FILE_SKIPPED"):
                    summary["skipped_system_or_symlink"] += 1
                summary["skipped"].append(
                    {"file": name, "reason": skip_reason})
                continue
            summary["scanned_files"] += 1
            try:
                self._scan_file(name, checkpoint, window, summary)
            except InboxLifecycleError as exc:
                summary["errors"].append(
                    {"file": name, "reason": str(exc)})
            except Exception as exc:
                # C1: one bad file must not abort the whole scan. Record
                # it as a processing failure and continue.
                summary["errors"].append(
                    {"file": name,
                     "reason": "UNEXPECTED:" + type(exc).__name__})
                self._ledger_event("source_scan_error", {
                    "relative_path": name,
                    "reason": type(exc).__name__,
                })
        # A3: a file absent from this scan loses its stability observations;
        # "consecutive scans" must not span a disappearance.
        seen_ids = {source_id_for(name) for name, reason
                    in self._iter_inbox_files() if reason is None}
        for stale_id in [key for key in checkpoint if key not in seen_ids]:
            del checkpoint[stale_id]
        self._save_checkpoint(checkpoint)
        # Every scan materializes the index (possibly empty). This keeps the
        # invariant "a scan ran => index.json exists", so a later missing
        # index.json with existing source records is detectable index loss.
        if not self.index_path.is_file():
            self._write_index([])
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

    def _fast_path_hash_ok(self, name, size, record):
        """Verify stored content hash for the fast path (A4).

        Returns True only for TXT files with a stored raw_sha256 whose
        current bytes hash to the same value. Anything else (unsupported
        suffix, empty/too-large, missing hash) falls back to trusting the
        stat identity, which is safe because those states are terminal and
        content-independent.
        """
        if Path(name).suffix.lower() != ".txt":
            return True
        expected = record.get("raw_sha256")
        if not expected or size == 0 or size > MAX_AUTO_TXT_BYTES:
            return True
        # R10 F4: bounded no-follow read (not unbounded read_bytes).
        try:
            fd = os.open(self.inbox_root / name,
                         os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return False
        try:
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(MAX_AUTO_TXT_BYTES + 1)
        except OSError:
            return False
        if len(raw) > MAX_AUTO_TXT_BYTES:
            return False
        return _sha256_hex(raw) == expected

    def _sidecar_content_hash(self, rel_posix):
        """Return the sidecar's content hash, or None if absent/unreadable.

        A1/B1: sidecar identity must be content-based, not stat-based.
        A symlinked sidecar returns None (rejected elsewhere).
        """
        path = self.inbox_root / (rel_posix + SIDECAR_SUFFIX)
        if os.path.islink(path):
            return None
        try:
            # O_NOFOLLOW for defense in depth (A2).
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            with os.fdopen(fd, "rb") as handle:
                return _sha256_hex(handle.read())
        except OSError:
            return None

    def _fast_path_sidecar_ok(self, rel_posix, record):
        """Verify sidecar content hash for the fast path (B1).

        If the record has a stored metadata_sha256, the current sidecar
        content must hash to the same value. If the sidecar is absent
        (or was absent), stat comparison suffices.
        """
        expected = record.get("metadata_sha256")
        if not expected:
            # No stored hash (e.g. sidecar was missing); stat check suffices.
            return True
        actual = self._sidecar_content_hash(rel_posix)
        # If the sidecar is now unreadable but was present, that's a change.
        return actual == expected

    def _sidecar_stat(self, rel_posix):
        """Return (exists, size, mtime_ns) for the sidecar, without reading.

        Uses lstat: a symlinked sidecar is never followed and reports as
        non-existent here; the processing path rejects it explicitly.
        """
        path = self.inbox_root / (rel_posix + SIDECAR_SUFFIX)
        try:
            lst = os.lstat(path)
        except OSError:
            return False, None, None
        if stat.S_ISLNK(lst.st_mode):
            return False, None, None
        if not stat.S_ISREG(lst.st_mode):
            return False, None, None
        return True, lst.st_size, lst.st_mtime_ns

    def _scan_file(self, name, checkpoint, window, summary):
        rel_posix = name
        source_id = source_id_for(rel_posix)
        file_id = file_id_for(rel_posix)
        # R7-C1: resolve the source identity BEFORE recoverable scan
        # operations, so a pre-verification failure can quarantine the
        # previously trusted state instead of leaving it intact.
        record = self._load_source(source_id)
        try:
            stat = os.stat(self.inbox_root / name)
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
            sidecar_exists, sidecar_size, sidecar_mtime_ns = \
                self._sidecar_stat(rel_posix)
        except OSError as exc:
            # The current input cannot be inspected. Do not trust the old
            # completed state: quarantine it (fail-closed). A later scan
            # will retry once the input is inspectable again.
            if (record is not None
                    and record.get("processing_status")
                    == "PROCESSING_COMPLETE"):
                self._mark_failed(record, "INPUT_INSPECT_FAILED", summary)
            else:
                summary["errors"].append(
                    {"file": name,
                     "reason": "INPUT_INSPECT_FAILED:"
                               + type(exc).__name__})
            return

        if record is None:
            summary["discoveries"] += 1
            record = self._new_source_record(source_id, file_id, rel_posix)

        # Idempotent fast path: the TXT and its sidecar are both unchanged
        # and the source was fully processed. Sidecar state is part of the
        # identity: a metadata-only change must re-trigger rights
        # re-evaluation, never ride the fast path.
        # A4/B1: stat alone is spoofable (same size + restored mtime), so
        # for TXT and sidecar we verify content hashes. Reading + hashing
        # is cheaper than reprocessing and closes the silent-staleness gap.
        if (record.get("processing_status") == "PROCESSING_COMPLETE"
                and record.get("observed_size") == size
                and record.get("observed_mtime_ns") == mtime_ns
                and record.get("sidecar_exists") == sidecar_exists
                and record.get("sidecar_size") == sidecar_size
                and record.get("sidecar_mtime_ns") == sidecar_mtime_ns
                and self._fast_path_hash_ok(name, size, record)
                and self._fast_path_sidecar_ok(rel_posix, record)):
            record["last_seen_at"] = _utc_now()
            self._save_source(record)
            return

        suffix = Path(name).suffix.lower()
        if suffix != ".txt":
            # Unsupported formats are never read: size/mtime from stat only.
            record.update(
                processing_status="PROCESSING_COMPLETE",
                disposition="unsupported",
                review_status="UNSUPPORTED",
                index_status="NOT_INDEXED",
                raw_sha256=None,
                observed_size=size,
                observed_mtime_ns=mtime_ns,
                sidecar_exists=sidecar_exists,
                sidecar_size=sidecar_size,
                sidecar_mtime_ns=sidecar_mtime_ns,
                last_seen_at=_utc_now(),
            )
            self._save_source(record)
            self._index_unlink_source(source_id)
            summary["unsupported"] += 1
            return

        # A1: If the stat matches a COMPLETE record but we are not on
        # the fast path, the content hash must have changed (same-stat
        # content swap). Reset stability observations; the old checkpoint
        # trust does not survive a hash-only change.
        if (record.get("processing_status") == "PROCESSING_COMPLETE"
                and record.get("observed_size") == size
                and record.get("observed_mtime_ns") == mtime_ns
                and record.get("sidecar_exists") == sidecar_exists
                and record.get("sidecar_size") == sidecar_size
                and record.get("sidecar_mtime_ns") == sidecar_mtime_ns):
            # Fast path was not taken despite matching stat -> hash changed.
            checkpoint.pop(source_id, None)

        # Stability gate: identical TXT and sidecar size/mtime across >=2
        # consecutive scans spanning the stability window before any byte
        # is processed.
        observed = checkpoint.get(source_id)
        now = time.time()
        same_as_before = (
            observed and observed.get("size") == size
            and observed.get("mtime_ns") == mtime_ns
            and observed.get("sidecar_exists") == sidecar_exists
            and observed.get("sidecar_size") == sidecar_size
            and observed.get("sidecar_mtime_ns") == sidecar_mtime_ns)
        if same_as_before:
            observations = int(observed.get("observations", 0)) + 1
            first_observed = observed.get("first_observed_at", now)
        else:
            observations = 1
            first_observed = now
        checkpoint[source_id] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "sidecar_exists": sidecar_exists,
            "sidecar_size": sidecar_size,
            "sidecar_mtime_ns": sidecar_mtime_ns,
            "first_observed_at": first_observed,
            "observations": observations,
            "last_scan_at": now,
        }
        if observations < 2 or (now - first_observed) < window:
            # R7-A1: a changed input must immediately invalidate the old
            # completed state, even if it was PROCESSING_COMPLETE. The old
            # trust does not survive while the new input awaits stability.
            record["processing_status"] = "PENDING_STABILITY"
            record["observed_size"] = size
            record["observed_mtime_ns"] = mtime_ns
            record["sidecar_exists"] = sidecar_exists
            record["sidecar_size"] = sidecar_size
            record["sidecar_mtime_ns"] = sidecar_mtime_ns
            record["last_seen_at"] = _utc_now()
            self._save_source(record)
            return

        # Stable: re-read and re-stat before processing.
        restat = os.stat(self.inbox_root / name)
        if restat.st_size != size or restat.st_mtime_ns != mtime_ns:
            checkpoint[source_id] = {
                "size": restat.st_size, "mtime_ns": restat.st_mtime_ns,
                "sidecar_exists": sidecar_exists,
                "sidecar_size": sidecar_size,
                "sidecar_mtime_ns": sidecar_mtime_ns,
                "first_observed_at": now, "observations": 1,
                "last_scan_at": now,
            }
            return
        # Size guards from stat, before any byte is read.
        if size == 0:
            self._mark_review(record, "EMPTY_FILE", None, None, summary,
                              sidecar=(sidecar_exists, sidecar_size,
                                       sidecar_mtime_ns))
            return
        if size > MAX_AUTO_TXT_BYTES:
            self._mark_review(record, "FILE_TOO_LARGE", None, None, summary,
                              sidecar=(sidecar_exists, sidecar_size,
                                       sidecar_mtime_ns))
            return
        # Invariant 1: Verified Bytes Pipeline.
        # The ONLY bytes that reach _process_stable_txt are those read from
        # a pinned file descriptor via _read_verified_bytes(). This method:
        #  - opens with O_NOFOLLOW (refuses symlinks)
        #  - verifies it's a regular file via fstat
        #  - checks size <= MAX before reading (from fstat, not path stat)
        #  - does a BOUNDED read of MAX+1 bytes (never unbounded)
        #  - re-verifies fd identity (dev/ino/size/mtime) after read
        # There is no TOCTOU window: the fd is pinned to the inode, and
        # the path is never re-read. ABA swaps are impossible.
        try:
            raw = self._read_verified_bytes(self.inbox_root / name, size,
                                            mtime_ns)
        except InboxLifecycleError as exc:
            # Oversize, symlink, or changed under us -> re-stabilize.
            msg = str(exc)
            if "OVERSIZE" in msg:
                self._mark_review(record, "FILE_TOO_LARGE", None, None,
                                  summary,
                                  sidecar=(sidecar_exists, sidecar_size,
                                           sidecar_mtime_ns))
                return
            # R6-C2: For other verification failures, if the source was
            # previously public (has a doc_id and was PROCESSING_COMPLETE),
            # we must NOT leave the old public state trusted. Mark failed
            # to quarantine; the checkpoint is reset for retry, but the
            # public authorization is revoked until re-verification succeeds.
            if record.get("doc_id") and record.get(
                    "processing_status") == "PROCESSING_COMPLETE":
                self._mark_failed(
                    record, "VERIFY_FAILED:" + type(exc).__name__,
                    summary,
                    sidecar=(sidecar_exists, sidecar_size, sidecar_mtime_ns))
                # Reset checkpoint for retry, but the source is now FAILED
                # and _verify_candidate will block public.
            checkpoint[source_id] = {
                "size": size, "mtime_ns": mtime_ns,
                "sidecar_exists": sidecar_exists,
                "sidecar_size": sidecar_size,
                "sidecar_mtime_ns": sidecar_mtime_ns,
                "first_observed_at": now, "observations": 1,
                "last_scan_at": now,
            }
            return
        self._process_stable_txt(record, rel_posix, raw, summary,
                                 sidecar=(sidecar_exists, sidecar_size,
                                          sidecar_mtime_ns))

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
            "sidecar_exists": None,
            "sidecar_size": None,
            "sidecar_mtime_ns": None,
            "attempt_count": 0,
            "processing_status": "DISCOVERED",
            "disposition": None,
            "review_status": "NONE",
            "index_status": "NOT_INDEXED",
            "archive_status": "NOT_REQUESTED",
            "publication_status": "NOT_PUBLISHED",
            "review_reason": None,
            "revision_history": [],
        }

    def _mark_review(self, record, reason, raw, raw_sha256, summary,
                     sidecar=(None, None, None), store_raw=True):
        """Route a source to human review.

        B1: the source's index link is KEPT (not unlinked). The query path
        refuses to serve a document via a source that is in review, and
        quarantines a shared document from public search while any linked
        source has an unresolved review. Removing the link would hide the
        dispute; keeping it makes the quarantine visible. Oversized inputs
        are never copied into the review area.

        A4: rejected revisions are appended to revision_history. The history
        records the content hash, reason, and status, so a content change
        that leads to rejection is not silently lost.
        """
        exists, size, mtime_ns = sidecar
        # A4: append rejected revision to history before updating.
        # For empty/oversize files where raw_sha256 is None, we still record
        # the observation (with a marker) so the rejection is auditable.
        history = record.setdefault("revision_history", [])
        last_hash = history[-1].get("raw_sha256") if history else None
        # Use a sentinel for None to avoid duplicate entries on re-scan.
        curr_key = raw_sha256 if raw_sha256 else f"__{reason}__"
        last_key = last_hash if last_hash else f"__{history[-1].get('review_reason')}__" if history else None
        if not history or last_key != curr_key:
            history.append({
                "observed_at": _utc_now(),
                "raw_sha256": raw_sha256,  # None for empty/oversize (not read)
                "content_hash": None,  # rejected before lane ingest
                "doc_id": record.get("doc_id"),
                "disposition": "review_required",
                "review_reason": reason,
            })
        record.update(
            processing_status="PROCESSING_COMPLETE",
            disposition="review_required",
            review_status="REVIEW_REQUIRED",
            index_status="NOT_INDEXED",
            review_reason=reason,
            raw_sha256=raw_sha256,  # A4: update hash even when rejected
            sidecar_exists=exists,
            sidecar_size=size,
            sidecar_mtime_ns=mtime_ns,
            last_seen_at=_utc_now(),
        )
        review_dir = self.review_root / record["source_id"]
        # Invariant 2: per-source review dirs must not be symlinks.
        # The source_id is predictable, so an attacker could pre-create
        # review/<source_id> as a symlink. We refuse if it's a symlink.
        if os.path.islink(review_dir):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: review subdir is a symlink: "
                + str(review_dir))
        review_dir.mkdir(parents=True, exist_ok=True)
        if os.path.islink(review_dir):
            raise InboxLifecycleError(
                "INBOX_LIFECYCLE_PATH_ESCAPE: review subdir became symlink")
        if raw and store_raw:
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

    def _mark_failed(self, record, reason, summary,
                     sidecar=(None, None, None)):
        """Record a genuine processing failure (distinct from review)."""
        exists, size, mtime_ns = sidecar
        record.update(
            processing_status="PROCESSING_FAILED",
            disposition=None,
            review_status="NONE",
            index_status="NOT_INDEXED",
            review_reason=reason,
            sidecar_exists=exists,
            sidecar_size=size,
            sidecar_mtime_ns=mtime_ns,
            attempt_count=int(record.get("attempt_count", 0)) + 1,
            last_seen_at=_utc_now(),
        )
        self._save_source(record)
        # Invariant 4 (B2 fix): do NOT unlink the source from the index on
        # failure. If the source was trying to tighten rights (e.g.
        # public->private) but the lane write failed, unlinking would remove
        # the restriction and leave the document public via other sources.
        # Keep the link; _verify_candidate will see PROCESSING_FAILED and
        # block public search until the failure is resolved.
        # self._index_unlink_source(record["source_id"])  # REMOVED (B2)
        summary["errors"].append(
            {"file": record["relative_path"], "reason": reason})
        self._ledger_event("source_processing_failed", {
            "source_id": record["source_id"],
            "relative_path": record["relative_path"],
            "reason": reason,
            "attempt_count": record["attempt_count"],
        })

    def _process_stable_txt(self, record, rel_posix, raw, summary,
                            sidecar=(None, None, None)):
        # Invariant 1 (belt-and-suspenders): the bytes must satisfy the
        # size limit. _read_verified_bytes guarantees this, but we assert
        # here to make the invariant explicit at the processing boundary.
        if len(raw) > MAX_AUTO_TXT_BYTES:
            raise InboxLifecycleError(
                "INVARIANT_VIOLATED: oversize bytes reached processing")
        raw_sha256 = _sha256_hex(raw)
        previous_raw_sha256 = record.get("raw_sha256")
        prev_review = record.get("review_status")
        # A1: an unresolved human review persists. Re-ingesting identical
        # inputs (same bytes, same sidecar CONTENT) is not an approval and
        # must not clear the review decision. Only a real input change
        # re-opens it. Identity is content-based, not stat-based: touching
        # mtime without changing bytes does not clear the review.
        if prev_review == "REVIEW_REQUIRED":
            # A1: for near-duplicate reviews (human judgment required), the
            # TXT content identity is what matters. Sidecar changes (even
            # whitespace) must not clear the review. Only a TXT content
            # change re-opens the near-duplicate determination.
            review_reason = record.get("review_reason") or ""
            is_near_duplicate = ("NEAR_DUPLICATE" in review_reason or
                                 "duplicate" in review_reason.lower())
            if is_near_duplicate:
                # TXT unchanged -> persist review regardless of sidecar.
                if previous_raw_sha256 == raw_sha256:
                    record["last_seen_at"] = _utc_now()
                    self._save_source(record)
                    summary["review_required"] += 1
                    self._ledger_event("source_review_persisted", {
                        "source_id": record["source_id"],
                        "relative_path": rel_posix,
                        "review_reason": record.get("review_reason"),
                    })
                    return
                # TXT changed -> fall through to re-evaluate.
            else:
                # For non-near-duplicate reviews (e.g. bad metadata), require
                # both TXT and sidecar content to be identical.
                prev_meta_hash = record.get("metadata_sha256")
                curr_meta_hash = self._sidecar_content_hash(rel_posix)
                sidecar_identical = (
                    (prev_meta_hash is None and curr_meta_hash is None) or
                    (prev_meta_hash is not None and
                     prev_meta_hash == curr_meta_hash))
                if (previous_raw_sha256 == raw_sha256 and sidecar_identical):
                    record["last_seen_at"] = _utc_now()
                    self._save_source(record)
                    summary["review_required"] += 1
                    self._ledger_event("source_review_persisted", {
                        "source_id": record["source_id"],
                        "relative_path": rel_posix,
                        "review_reason": record.get("review_reason"),
                    })
                    return
        record.update(
            raw_sha256=raw_sha256,
            observed_size=len(raw),
            observed_mtime_ns=os.stat(self.inbox_root / rel_posix).st_mtime_ns,
            sidecar_exists=sidecar[0],
            sidecar_size=sidecar[1],
            sidecar_mtime_ns=sidecar[2],
            last_seen_at=_utc_now(),
        )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            self._mark_review(record, "TXT_ENCODING_UNSUPPORTED", raw,
                              raw_sha256, summary, sidecar=sidecar)
            return
        if not text.strip():
            self._mark_review(record, "EMPTY_TEXT", raw, raw_sha256, summary,
                              sidecar=sidecar)
            return

        sidecar_path = self.inbox_root / (rel_posix + SIDECAR_SUFFIX)
        if os.path.islink(sidecar_path):
            # A2: never follow a symlinked sidecar; its metadata is untrusted.
            self._mark_review(record, "METADATA_SYMLINK_REJECTED", raw,
                              raw_sha256, summary, sidecar=sidecar)
            return
        meta, meta_error, meta_raw = (load_sidecar(sidecar_path)
                                      if sidecar_path.is_file()
                                      else (None, "METADATA_MISSING", None))
        # R7-B1: hash the SAME byte snapshot that was parsed; never
        # re-read the file (a second read could observe different bytes).
        metadata_sha256 = (_sha256_hex(meta_raw)
                           if meta is not None and meta_raw is not None
                           else None)
        record["metadata_sha256"] = metadata_sha256

        visibility, visibility_reason = decide_visibility(meta)
        if meta_error is not None and meta is None:
            visibility, visibility_reason = "review", meta_error
        if visibility == "review":
            self._mark_review(record, visibility_reason, raw, raw_sha256,
                              summary, sidecar=sidecar)
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
        # Failure taxonomy: domain rejections (invalid intake, lane review
        # signals) -> REVIEW_REQUIRED for a human; unexpected failures
        # (I/O, programming errors) -> PROCESSING_FAILED with attempt count,
        # retried on the next scan. The two are never conflated.
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
        except SourceIntakeError as exc:
            self._mark_review(record, "INTAKE_REJECTED:" + type(exc).__name__,
                              raw, raw_sha256, summary, sidecar=sidecar)
            return
        except Exception as exc:
            self._mark_failed(record, "INTAKE_FAILED:" + type(exc).__name__,
                              summary, sidecar=sidecar)
            return

        lane = DocumentLane(self.engine_root, self.instance)
        # A3 root fix: the lane must consume our verified byte snapshot,
        # not re-read the file from disk. We parse the verified raw bytes
        # ourselves and call _ingest_parsed directly. This closes the TOCTOU
        # between our verification and the lane's read, and ensures the
        # 1 MiB limit is enforced on the bytes we verified.
        try:
            parsed = parse_local(
                raw,
                suffix=Path(rel_posix).suffix,
                source_locator=rel_posix,
                channel="local_file",
            )
        except ReviewRequired as exc:
            self._mark_review(record, "LANE_REVIEW:" + type(exc).__name__,
                              raw, raw_sha256, summary, sidecar=sidecar)
            return
        except Exception as exc:
            self._mark_failed(record, "PARSE_FAILED:" + type(exc).__name__,
                              summary, sidecar=sidecar)
            return
        try:
            # R6-C1: Invariant 1 requires verified bytes. There is NO fallback
            # to ingest_file(). If _ingest_parsed is unavailable, fail closed.
            # The lane must consume our verified byte snapshot, never re-read
            # from disk.
            if not hasattr(lane, "_ingest_parsed"):
                raise InboxLifecycleError(
                    "INVARIANT_VIOLATED: lane missing _ingest_parsed; "
                    "refusing path-based ingest")
            lane_result = lane._ingest_parsed(
                parsed,
                capture_channel="local_file",
                title=meta["title"],
                rights_classification=lane_rights["classification"],
                public_export_allowed=lane_rights["public_export_allowed"],
                rights_evidence=lane_rights["evidence"],
            )
        except ReviewRequired as exc:
            self._mark_review(record, "LANE_REVIEW:" + type(exc).__name__,
                              raw, raw_sha256, summary, sidecar=sidecar)
            return
        except Exception as exc:
            self._mark_failed(record, "LANE_FAILED:" + type(exc).__name__,
                              summary, sidecar=sidecar)
            return

        doc_id = lane_result.get("doc_id")
        content_hash = lane_result.get("content_hash")
        lane_disposition = lane_result.get("disposition")
        # Note: the lane consumed our verified byte snapshot directly via
        # _ingest_parsed (see above), so no TOCTOU re-read is possible.
        # The content_hash here is derived from those verified bytes.
        #
        # Invariant 3: Human review is a persistent decision.
        # The lane's disposition (including exact_merge/ACCEPTED) CANNOT
        # override an unresolved human review. If the source had a
        # near-duplicate REVIEW_REQUIRED and the doc_id is unchanged,
        # the review persists even if the lane accepts the ingest.
        # A normalized-equivalent TXT change is not a human approval.
        prev_was_near_dup_review = (
            prev_review == "REVIEW_REQUIRED"
            and record.get("doc_id") == doc_id
            and "NEAR_DUPLICATE" in (record.get("review_reason") or "")
        )
        if lane_result.get("status") == "REVIEW_REQUIRED":
            disposition = "review_required"
            review_status = "REVIEW_REQUIRED"
            index_status = "NOT_INDEXED"
        elif prev_was_near_dup_review:
            # Lane accepted, but human review is unresolved. Persist it.
            disposition = "review_required"
            review_status = "REVIEW_REQUIRED"
            index_status = "NOT_INDEXED"
            # Keep the original review_reason.
        else:
            review_status = "NONE"
            index_status = "SEARCHABLE"
            # A revision is any raw-content change, even when normalization
            # yields the same doc_id (e.g. whitespace/line-ending edits).
            if previous_raw_sha256 and previous_raw_sha256 != raw_sha256:
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
        history = record.setdefault("revision_history", [])
        if (not history or history[-1].get("raw_sha256") != raw_sha256
                or history[-1].get("doc_id") != doc_id):
            history.append({
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
            # The source record stores its OWN rights from its sidecar.
            # The archive_rights (document-level) is used for visibility
            # decisions, but the source's rights must reflect its sidecar.
            rights=lane_rights,
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
            # The source's link moves to the current document; its old
            # document loses this link (and vanishes from the index when no
            # source links to it anymore). History stays in the archive.
            self._index_unlink_source(record["source_id"])
            self._index_link(
                doc_id, source_id=record["source_id"],
                file_id=record["file_id"], relative_path=rel_posix,
                title=meta["title"], content_hash=content_hash,
                visibility=effective_visibility)
        else:
            self._index_unlink_source(record["source_id"])

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

    def _archive_root_ok(self):
        """Check the archive root for detectable relocation.

        DELTA §6.4: reject pre-existing illegal relocations detectable
        at query time. The init-time resolved-path check is point-in-time;
        this catches a swapped archive/ directory (symlink) before use.
        Concurrent substitution races remain out-of-model (DELTA §5).
        """
        archive_root = (Path(self.instance.data_root).resolve()
                        / "document_lane" / "archive")
        if os.path.islink(archive_root):
            return False
        try:
            resolved = archive_root.resolve()
            resolved.relative_to(Path(self.instance.root).resolve())
        except (OSError, ValueError):
            return False
        return True

    def _archive_record(self, doc_id):
        if not doc_id:
            return None
        if not self._archive_root_ok():
            return None
        # R7-A2 (lifecycle-side defense): do not trust reads through a
        # symlinked per-document archive directory. Lane-side writes are
        # outside this module's control (document_lane.py is out of scope
        # for this PR); this check ensures we never READ through such a
        # link and treat the result as our own state.
        doc_dir = (Path(self.instance.data_root).resolve() / "document_lane"
                   / "archive" / doc_id)
        if os.path.islink(doc_dir):
            return None
        path = doc_dir / "record.json"
        if not path.is_file():
            return None
        try:
            return _read_json_nofollow(path)
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
    def _match_link(self, item, wanted):
        """Return the linked source matching an exact id, or None.

        A2: for doc_id lookup, prefer a review-clean, complete link over
        links[0]. A document shared by multiple sources must be findable
        via its clean sources even if the first link is in review.
        """
        if wanted == item.get("doc_id"):
            links = item.get("links", [])
            if not links:
                return None
            # Prefer a clean, complete link.
            for link in links:
                rec = self._load_source(link.get("source_id"))
                if (rec is not None
                        and rec.get("review_status") == "NONE"
                        and rec.get("processing_status")
                        == "PROCESSING_COMPLETE"
                        and rec.get("doc_id") == item.get("doc_id")):
                    return link
            # Fallback to first link (will be filtered by _verify_candidate).
            return links[0]
        for link in item.get("links", []):
            if wanted in (link.get("source_id"), link.get("file_id")):
                return link
        return None

    def query(self, *, id=None, title=None, keyword=None,
              visibility="local"):
        """Rights-aware search over the materialized index.

        SCOPED PR1: only local visibility is supported. Public is deferred.
        """
        modes = [name for name, value in
                 (("id", id), ("title", title), ("keyword", keyword))
                 if value is not None]
        if len(modes) != 1:
            raise InboxLifecycleError(
                "ARCHIVE_QUERY_EXACTLY_ONE_MODE_REQUIRED")
        # SCOPED: public deferred.
        if visibility == "public":
            raise InboxLifecycleError(
                "ARCHIVE_QUERY_PUBLIC_DEFERRED")
        if visibility not in ("local",):
            raise InboxLifecycleError("ARCHIVE_QUERY_VISIBILITY_INVALID")
        self._acquire_lock()
        try:
            documents = self._load_index_documents()
            if id is not None:
                wanted = id.strip()
                if not wanted:
                    raise InboxLifecycleError("ARCHIVE_QUERY_TEXT_REQUIRED")
                # Exact id matches the doc id or ANY linked source/file id.
                candidates = [(item, self._match_link(item, wanted))
                              for item in documents]
                candidates = [(item, link) for item, link in candidates
                              if link is not None]
            else:
                text = (title if title is not None else keyword) or ""
                if not text.strip():
                    raise InboxLifecycleError("ARCHIVE_QUERY_TEXT_REQUIRED")
                needle = _normalize_query_text(text)
                candidates = []
                for item in documents:
                    # A5: match against every linked source's title, not
                    # just the latest writer's display title.
                    all_titles = item.get("titles") or [item.get("title")]
                    haystacks = [_normalize_query_text(t or "")
                                 for t in all_titles]
                    if keyword is not None:
                        # R9: verify source eligibility BEFORE reading
                        # archive text. Do not read files for documents
                        # with no eligible source.
                        has_eligible = False
                        for link in item.get("links", []):
                            rec = self._load_source(link.get("source_id"))
                            if (rec is not None
                                    and rec.get("review_status") == "NONE"
                                    and rec.get("processing_status")
                                    == "PROCESSING_COMPLETE"
                                    and rec.get("doc_id")
                                    == item.get("doc_id")):
                                has_eligible = True
                                break
                        if has_eligible:
                            haystacks.append(_normalize_query_text(
                                self._indexed_text(item["doc_id"])))
                    if any(needle in hay for hay in haystacks):
                        # B1: try every link; a shared document may be
                        # reachable via a clean source even if the first
                        # link is in review.
                        for link in item.get("links", []):
                            candidates.append((item, link))
                candidates = [(item, link) for item, link in candidates
                              if link is not None]
            results = []
            healed = False
            seen_doc_ids = set()
            for item, link in candidates:
                if item["doc_id"] in seen_doc_ids:
                    continue
                verified = self._verify_candidate(item, link)
                if verified is None:
                    continue
                # B1: a shared document is reported once; if any link
                # verifies, the document is included (visibility is the
                # quarantined value from _verify_candidate).
                seen_doc_ids.add(item["doc_id"])
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
        # C2 (R9 fix): open with O_NOFOLLOW. The directory check alone
        # is insufficient: a symlink at text.txt itself (inside a normal
        # directory) would bypass it. O_NOFOLLOW refuses symlinks at the
        # final component too.
        if not self._archive_root_ok():
            raise InboxLifecycleError(
                "INDEX_FAILED: archive root relocated:" + doc_id)
        doc_dir = (Path(self.instance.data_root).resolve() / "document_lane"
                   / "archive" / doc_id)
        if os.path.islink(doc_dir):
            raise InboxLifecycleError(
                "INDEX_FAILED: archive directory is a symlink:" + doc_id)
        path = doc_dir / "text.txt"
        try:
            # O_NOFOLLOW: refuse symlink at text.txt itself.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise InboxLifecycleError(
                "INDEX_FAILED: archive text unreadable:" + doc_id) from exc
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise InboxLifecycleError(
                "INDEX_FAILED: archive text unreadable:" + doc_id) from exc

    def _verify_candidate(self, item, link):
        """Re-check integrity; hit or None to skip.

        SCOPED PR1 (2026-10-14): public authorization is deferred. All
        documents are local-only. This method verifies the source is
        review-clean and the archive is intact; it never returns public.
        A stale link (source moved on) is healed away.
        """
        doc_id = item["doc_id"]
        source = self._load_source(link.get("source_id"))
        if source is None:
            raise InboxLifecycleError(
                "INDEX_FAILED: source record missing:" + doc_id)
        if source.get("review_status") != "NONE":
            return None
        # C1: only PROCESSING_COMPLETE sources are searchable. A FAILED
        # or PENDING_STABILITY source must not remain searchable via its
        # old index link.
        if source.get("processing_status") != "PROCESSING_COMPLETE":
            return None
        if source.get("doc_id") != doc_id:
            # Stale link: the source has moved on (revision/review); drop it.
            self._index_unlink_source(link.get("source_id"), doc_id)
            return None
        archive_record = self._archive_record(doc_id)
        if archive_record is None:
            raise InboxLifecycleError(
                "INDEX_FAILED: archive record missing:" + doc_id)
        # B2: a missing linked source record is fail-closed, not skipped.
        for other in item.get("links", []):
            other_rec = self._load_source(other.get("source_id"))
            if other_rec is None:
                raise InboxLifecycleError(
                    "INDEX_FAILED: linked source record missing: " +
                    str(other.get("source_id")))
        # SCOPED: always local; public is deferred to a future PR.
        visibility = "local"
        text = self._indexed_text(doc_id)
        if doc_content_hash(text) != item.get("content_hash"):
            raise InboxLifecycleError(
                "INDEX_FAILED: content hash mismatch:" + doc_id)
        if doc_content_hash(text) != archive_record.get("content_hash"):
            raise InboxLifecycleError(
                "INDEX_FAILED: archive content drift:" + doc_id)
        return {
            "doc_id": doc_id,
            "file_id": link.get("file_id"),
            "source_id": link.get("source_id"),
            "source_ids": sorted(
                link.get("source_id") for link in item.get("links", [])
                if link.get("source_id")),
            "title": item.get("title"),
            "relative_path": link.get("relative_path"),
            "content_hash": item.get("content_hash"),
            "visibility": visibility,
        }
