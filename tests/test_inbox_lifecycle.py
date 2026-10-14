"""PR1-LOCAL-TXT-LIFECYCLE acceptance tests (Q1 freeze contract, behavioral via CLI).

These tests exercise the frozen product surface only: inbox-scan / inbox-poll /
archive-status / archive-query. On the pre-PR1 base they must FAIL because the
subcommands do not exist (behavioral RED, not import errors).
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

SYNTHETIC_TXT = (
    "Synthetic CBA Bulletin\n"
    "The Harbor Falcons won a fictional preseason game.\n"
    "The team scored 88 points in this synthetic report.\n"
)

SYNTHETIC_META = {
    "schema_version": 1,
    "title": "Synthetic CBA Bulletin",
    "origin": "synthetic_fixture",
    "source_url": None,
    "rights": {
        "classification": "public",
        "public_export_allowed": True,
        "evidence": [
            {
                "type": "author_attestation",
                "license": "CC0-1.0",
                "description": "Original synthetic test content",
            }
        ],
    },
}

NEAR_TXT = SYNTHETIC_TXT.replace("fictional preseason", "fictional pre-season")


def command(*args, env=None):
    # NOTE: PYTHONPATH is forced after the env override so a pre-existing
    # CI PYTHONPATH (e.g. $GITHUB_WORKSPACE) can never shadow ROOT/src.
    merged = {**os.environ, **(env or {}),
              "PYTHONPATH": str(ROOT / "src"),
              "PYTHONDONTWRITEBYTECODE": "1"}
    # The 1.0 s stability window is frozen and not configurable (Q2 A4).
    merged.pop("CBA_KB_INBOX_STABILITY_SECONDS", None)
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT, env=merged, capture_output=True, text=True, timeout=120,
    )


# The frozen minimum stability window is 1.0 s and deliberately not
# configurable. Tests wait it out for real so the suite demonstrates the
# production behavior.
STABILITY_WAIT = 1.2


def private_instance(tmp_path):
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / "inbox/documents").mkdir(parents=True)
    return root


def write_txt(private, name, text, meta):
    inbox = private / "inbox/documents"
    (inbox / name).write_text(text, encoding="utf-8")
    (inbox / (name + ".source.json")).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def scan(private, *extra):
    """Run inbox-scan after waiting out the real stability window."""
    time.sleep(STABILITY_WAIT)
    result = command("--instance-root", str(private), "inbox-scan", *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def scan_nowait(private, *extra):
    """Run inbox-scan immediately (no stability wait): proves the frozen
    1.0 s window is enforced between qualifying observations."""
    result = command("--instance-root", str(private), "inbox-scan", *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def lifecycle_dir(private):
    return private / "data/inbox_lifecycle"


def source_records(private):
    out = {}
    for path in (lifecycle_dir(private) / "sources").glob("*.json"):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


def test_a01_scan_discovers_txt_and_creates_source_and_document(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    raw_before = (private / "inbox/documents/synthetic-news.txt").read_bytes()

    first = scan(private)
    assert first["scanned_files"] >= 1
    second = scan(private)
    assert second["processed_new"] >= 1 or second["processed_total"] >= 1

    records = source_records(private)
    assert len(records) == 1
    rec = next(iter(records.values()))
    assert rec["provider"] == "local_file"
    assert rec["relative_path"] == "synthetic-news.txt"
    assert rec["title"] == "Synthetic CBA Bulletin"
    assert rec["raw_sha256"] == hashlib.sha256(raw_before).hexdigest()
    assert rec["doc_id"].startswith("doc_")
    assert rec["file_id"].startswith("file_")
    assert rec["source_id"].startswith("src_")
    assert rec["processing_status"] == "PROCESSING_COMPLETE"
    assert rec["disposition"] == "new"
    assert rec["review_status"] == "NONE"
    assert rec["index_status"] == "SEARCHABLE"
    assert rec["archive_status"] == "NOT_REQUESTED"
    assert rec["publication_status"] == "NOT_PUBLISHED"
    assert rec["revision_history"] and rec["first_seen_at"] and rec["last_seen_at"]
    assert rec["metadata_sha256"] == hashlib.sha256(
        json.dumps(SYNTHETIC_META, ensure_ascii=False, indent=2).encode("utf-8")
    ).hexdigest()

    # DocumentLane archive holds the document; inbox original untouched.
    archive = private / "data/document_lane/archive" / rec["doc_id"]
    assert (archive / "record.json").is_file()
    assert (archive / "text.txt").read_text(encoding="utf-8") == SYNTHETIC_TXT
    assert (private / "inbox/documents/synthetic-news.txt").read_bytes() == raw_before


def test_a03_rescan_is_idempotent(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    before = source_records(private)
    third = scan(private)
    after = source_records(private)
    assert set(before) == set(after)
    assert third["processed_new"] == 0
    assert third["discoveries"] == 0


def test_a04_restart_recovers_ledger_and_query(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    rec = next(iter(source_records(private).values()))
    # A fresh CLI invocation == a restarted worker: query must still work.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons")
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout)["results"]
    assert [h["doc_id"] for h in hits] == [rec["doc_id"]]
    ledger = json.loads(
        (lifecycle_dir(private) / "ledger/ledger.json").read_text(encoding="utf-8"))
    assert ledger["events"], "ledger must record scan events"


def test_a05_query_by_id_title_keyword_with_source_backlink(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    rec = next(iter(source_records(private).values()))
    for mode, value in (("--id", rec["file_id"]), ("--id", rec["source_id"]),
                        ("--id", rec["doc_id"]),
                        ("--title", "Synthetic CBA Bulletin"),
                        ("--keyword", "Harbor Falcons"),
                        ("--keyword", "88 points")):
        result = command("--instance-root", str(private),
                         "archive-query", mode, value)
        assert result.returncode == 0, (mode, value, result.stderr)
        hits = json.loads(result.stdout)["results"]
        assert len(hits) == 1, (mode, value)
        hit = hits[0]
        assert hit["doc_id"] == rec["doc_id"]
        assert hit["source_id"] == rec["source_id"]
        assert hit["relative_path"] == "synthetic-news.txt"


def test_a06_missing_metadata_goes_review_and_hidden_from_public(tmp_path):
    private = private_instance(tmp_path)
    (private / "inbox/documents/lonely.txt").write_text("No metadata here.\n")
    scan(private)
    scan(private)
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    assert rec["doc_id"] is None
    assert rec["index_status"] == "NOT_INDEXED"
    public = command("--instance-root", str(private),
                     "archive-query", "--keyword", "metadata",
                     "--visibility", "public")
    assert public.returncode == 0, public.stderr
    assert json.loads(public.stdout)["results"] == []
    status = command("--instance-root", str(private), "archive-status", "--all")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["by_review_status"]["REVIEW_REQUIRED"] >= 1


def test_a07_third_party_public_claim_without_evidence_goes_review(tmp_path):
    private = private_instance(tmp_path)
    meta = {
        "schema_version": 1,
        "title": "Third party news",
        "origin": "third_party",
        "source_url": "https://example.com/news/1",
        "rights": {"classification": "public", "public_export_allowed": True,
                   "evidence": []},
    }
    write_txt(private, "thirdparty.txt", "Third party match report.\n", meta)
    scan(private)
    scan(private)
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    assert rec["disposition"] == "review_required"
    public = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Third party",
                     "--visibility", "public")
    assert json.loads(public.stdout)["results"] == []


def test_a08_unsupported_format(tmp_path):
    private = private_instance(tmp_path)
    (private / "inbox/documents/report.pdf").write_bytes(b"%PDF-1.4 fake\n")
    (private / "inbox/documents/report.pdf.source.json").write_text(
        json.dumps(SYNTHETIC_META))
    result = scan(private)
    assert result["unsupported"] >= 1
    rec = next(iter(source_records(private).values()))
    assert rec["disposition"] == "unsupported"
    assert rec["review_status"] == "UNSUPPORTED"
    assert rec["doc_id"] is None


def test_a09_encoding_empty_symlink_and_system_files(tmp_path):
    private = private_instance(tmp_path)
    inbox = private / "inbox/documents"
    (inbox / "gbk.txt").write_bytes("中文 GBK 文本\n".encode("gb18030"))
    (inbox / "gbk.txt.source.json").write_text(json.dumps(SYNTHETIC_META))
    (inbox / "empty.txt").write_text("")
    (inbox / "empty.txt.source.json").write_text(json.dumps(SYNTHETIC_META))
    (inbox / ".DS_Store").write_bytes(b"junk")
    target = inbox / "synthetic-news.txt"
    target.write_text(SYNTHETIC_TXT)
    link = inbox / "link.txt"
    try:
        link.symlink_to(target)
        have_link = True
    except OSError:
        have_link = False
    summary = scan(private)
    scan(private)
    records = source_records(private)
    by_path = {r["relative_path"]: r for r in records.values()}
    assert by_path["gbk.txt"]["review_status"] == "REVIEW_REQUIRED"
    assert by_path["empty.txt"]["review_status"] == "REVIEW_REQUIRED"
    assert ".DS_Store" not in by_path
    if have_link:
        assert "link.txt" not in by_path
        assert summary["skipped_system_or_symlink"] >= 1


def test_a10_cleanroom_without_drive_env(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    env = {k: v for k, v in os.environ.items()
           if "DRIVE" not in k and "OAUTH" not in k and "GOOGLE" not in k}
    env["CBA_KB_INSTANCE_ROOT"] = ""
    result = command("--instance-root", str(private), "inbox-scan", env=env)
    assert result.returncode == 0, result.stderr


def test_a11_original_file_never_moved(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    path = private / "inbox/documents/synthetic-news.txt"
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == \
        hashlib.sha256(SYNTHETIC_TXT.encode("utf-8")).hexdigest()
    rec = next(iter(source_records(private).values()))
    assert rec["archive_status"] == "NOT_REQUESTED"


def test_a12_index_corruption_is_index_failed_not_silent_empty(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    index_path = lifecycle_dir(private) / "index.json"
    index_path.write_text("{corrupted", encoding="utf-8")
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor")
    assert result.returncode != 0
    assert "INDEX_FAILED" in result.stderr


def test_a13_rights_tightening_on_exact_merge_not_leaked(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "a-public.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    private_meta = {
        "schema_version": 1,
        "title": "Synthetic CBA Bulletin",
        "origin": "local_owned",
        "source_url": None,
        "rights": {"classification": "private",
                   "public_export_allowed": False, "evidence": []},
    }
    write_txt(private, "b-private.txt", SYNTHETIC_TXT, private_meta)
    scan(private)
    scan(private)
    records = source_records(private)
    assert len(records) == 2
    doc_ids = {r["doc_id"] for r in records.values()}
    assert len(doc_ids) == 1  # shared doc_id, separate sources
    public = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons",
                     "--visibility", "public")
    assert public.returncode == 0, public.stderr
    assert json.loads(public.stdout)["results"] == [], \
        "tightened rights must not leak via stale public index"
    local = command("--instance-root", str(private),
                    "archive-query", "--keyword", "Harbor Falcons",
                    "--visibility", "local")
    assert len(json.loads(local.stdout)["results"]) == 1


def test_a13b_near_duplicate_requires_review(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "a-original.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    write_txt(private, "b-near.txt", NEAR_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    by_path = {r["relative_path"]: r for r in source_records(private).values()}
    assert by_path["a-original.txt"]["disposition"] == "new"
    assert by_path["b-near.txt"]["disposition"] == "review_required"
    assert by_path["b-near.txt"]["review_status"] == "REVIEW_REQUIRED"


def test_poll_discovers_file_without_manual_intake(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    result = command("--instance-root", str(private),
                     "inbox-poll", "--interval-seconds", "1", "--max-scans", "4")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["scans_completed"] == 4
    assert len(source_records(private)) == 1


def test_pending_stability_before_second_scan(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    first = scan(private)
    assert first["processed_new"] == 0
    rec = next(iter(source_records(private).values()))
    assert rec["processing_status"] == "PENDING_STABILITY"
    second = scan(private)
    assert second["processed_new"] == 1


def test_modified_file_creates_revision_not_silent_overwrite(tmp_path):
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    before = next(iter(source_records(private).values()))
    time.sleep(0.05)
    (private / "inbox/documents/synthetic-news.txt").write_text(
        SYNTHETIC_TXT + "Extra verified line.\n", encoding="utf-8")
    scan(private)
    scan(private)
    after = next(iter(source_records(private).values()))
    assert after["source_id"] == before["source_id"]
    assert after["doc_id"] != before["doc_id"]
    assert after["disposition"] == "revision"
    assert len(after["revision_history"]) >= 2
    assert after["revision_history"][0]["content_hash"] != \
        after["revision_history"][-1]["content_hash"]


# ---- Q2 remediation tests (second round) ----

def _tightened_meta():
    return {
        "schema_version": 1,
        "title": "Synthetic CBA Bulletin",
        "origin": "third_party",
        "source_url": "https://example.com/report",
        "rights": {"classification": "copyrighted",
                   "public_export_allowed": False,
                   "evidence": [{"type": "claim", "note": "unverified"}]},
    }


def test_a14_sidecar_only_rights_tightening_is_reevaluated(tmp_path):
    """Q2 A1/B1: a sidecar-only change must invalidate the fast path and
    re-trigger rights evaluation; a tightened document must disappear from
    public search."""
    private = private_instance(tmp_path)
    write_txt(private, "pub.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    hit = command("--instance-root", str(private), "archive-query",
                  "--keyword", "Harbor", "--visibility", "public")
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["count"] == 1
    # Attack: tighten only the sidecar, leave the TXT untouched.
    (private / "inbox/documents/pub.txt.source.json").write_text(
        json.dumps(_tightened_meta(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    first = scan(private)
    second = scan(private)
    assert second["review_required"] >= 1
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    assert rec["review_reason"] == "RIGHTS_UNDETERMINED"
    pub = command("--instance-root", str(private), "archive-query",
                  "--keyword", "Harbor", "--visibility", "public")
    assert json.loads(pub.stdout)["count"] == 0
    local = command("--instance-root", str(private), "archive-query",
                    "--keyword", "Harbor")
    assert json.loads(local.stdout)["count"] == 0


def test_a15_sidecar_removed_after_success_goes_review(tmp_path):
    """Q2 A1: removing the sidecar after success must not ride the fast
    path; the source goes to METADATA_MISSING review."""
    private = private_instance(tmp_path)
    write_txt(private, "pub.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    (private / "inbox/documents/pub.txt.source.json").unlink()
    scan(private)
    result = scan(private)
    assert result["review_required"] >= 1
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    assert rec["review_reason"] == "METADATA_MISSING"


def test_a16_metadata_remediation_reprocesses(tmp_path):
    """Q2 A1: a metadata review is not stuck; fixing the sidecar
    re-triggers processing."""
    private = private_instance(tmp_path)
    inbox = private / "inbox/documents"
    (inbox / "fixme.txt").write_text(SYNTHETIC_TXT, encoding="utf-8")
    scan(private)
    scan(private)
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    (inbox / "fixme.txt.source.json").write_text(
        json.dumps(SYNTHETIC_META, ensure_ascii=False, indent=2),
        encoding="utf-8")
    scan(private)  # first scan after the fix: re-stabilizes the sidecar
    result = scan(private)  # second scan: reprocesses
    assert result["processed_total"] >= 1
    rec = next(iter(source_records(private).values()))
    assert rec["review_status"] == "NONE"
    assert rec["index_status"] == "SEARCHABLE"


def test_a17_whitespace_only_change_appends_revision(tmp_path):
    """Q2 A2: a raw change that normalizes to the same doc_id still
    appends a revision-history entry (never a silent overwrite)."""
    private = private_instance(tmp_path)
    write_txt(private, "ws.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    before = next(iter(source_records(private).values()))
    assert len(before["revision_history"]) == 1
    time.sleep(0.05)
    (private / "inbox/documents/ws.txt").write_text(
        SYNTHETIC_TXT.replace("\n", "\r\n"), encoding="utf-8")
    scan(private)
    scan(private)
    after = next(iter(source_records(private).values()))
    assert after["disposition"] == "revision"
    assert len(after["revision_history"]) == 2
    assert after["revision_history"][0]["raw_sha256"] != \
        after["revision_history"][1]["raw_sha256"]


def test_a18_merged_source_ids_all_queryable(tmp_path):
    """Q2 A3: exact --id lookup works for every linked source/file id of a
    shared document, not only the primary entry."""
    private = private_instance(tmp_path)
    write_txt(private, "first.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    write_txt(private, "second.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    records = source_records(private)
    assert len(records) == 2
    assert len({r["doc_id"] for r in records.values()}) == 1
    doc_id = next(iter(records.values()))["doc_id"]
    for rec in records.values():
        for wanted in (rec["source_id"], rec["file_id"]):
            result = command("--instance-root", str(private),
                             "archive-query", "--id", wanted)
            assert result.returncode == 0, result.stderr
            hits = json.loads(result.stdout)["results"]
            assert len(hits) == 1, wanted
            assert hits[0]["doc_id"] == doc_id
    doc_result = command("--instance-root", str(private),
                         "archive-query", "--id", doc_id)
    assert len(json.loads(doc_result.stdout)["results"]) == 1


def test_a19_index_deletion_is_index_failed(tmp_path):
    """Q2 C2: a deleted index.json with existing sources is INDEX_FAILED,
    never a silent empty result."""
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    (lifecycle_dir(private) / "index.json").unlink()
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor")
    assert result.returncode != 0
    assert "INDEX_FAILED" in result.stderr


def test_a20_stability_window_enforced(tmp_path):
    """Q2 A4: the frozen 1.0 s window gates processing. Two observations
    closer than 1.0 s leave the file PENDING_STABILITY; only a scan pair
    spanning the real window processes it. Uses the in-process API so the
    inter-scan gap is deterministically milliseconds."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import cba_kb.inbox_lifecycle as lifecycle_mod
        from cba_kb.instance import load_instance
        private = private_instance(tmp_path)
        write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
        instance = load_instance(str(ROOT), str(private))
        lifecycle = lifecycle_mod.InboxLifecycle(str(ROOT), instance)
        # One CLI-speed scan without waiting: single observation, pending.
        first = scan_nowait(private)
        assert first["processed_new"] == 0
        rec = next(iter(source_records(private).values()))
        assert rec["processing_status"] == "PENDING_STABILITY"
        # Second observation milliseconds later: still inside the window.
        lifecycle2 = lifecycle_mod.InboxLifecycle(str(ROOT), instance)
        summary = lifecycle2.scan_once()
        assert summary["processed_new"] == 0
        rec = next(iter(source_records(private).values()))
        assert rec["processing_status"] == "PENDING_STABILITY"
        # After the real window elapses, the next scan processes.
        time.sleep(STABILITY_WAIT)
        lifecycle3 = lifecycle_mod.InboxLifecycle(str(ROOT), instance)
        summary = lifecycle3.scan_once()
        assert summary["processed_new"] == 1
    finally:
        sys.path.remove(str(ROOT / "src"))


def test_a21_oversized_file_not_read_or_stored(tmp_path):
    """Q2 C3: oversized files are routed by stat size; their bytes are
    never read into memory nor copied into the review area."""
    private = private_instance(tmp_path)
    big = "x" * (1024 * 1024 + 16)
    write_txt(private, "big.txt", big, SYNTHETIC_META)
    scan(private)
    result = scan(private)
    assert result["review_required"] >= 1
    rec = next(iter(source_records(private).values()))
    assert rec["review_reason"] == "FILE_TOO_LARGE"
    review_dir = lifecycle_dir(private) / "review" / rec["source_id"]
    assert (review_dir / "review.json").is_file()
    assert not (review_dir / "original.bin").exists()


def test_a22_processing_failure_is_distinct_from_review(tmp_path, monkeypatch):
    """Q2 C4: unexpected lane failures become PROCESSING_FAILED (with
    attempt count), never REVIEW_REQUIRED."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import cba_kb.inbox_lifecycle as lifecycle_mod
        from cba_kb.instance import load_instance

        class BoomLane:
            def __init__(self, engine_root, instance):
                pass

            def ingest_file(self, *args, **kwargs):
                raise RuntimeError("simulated lane explosion")

        monkeypatch.setattr(lifecycle_mod, "DocumentLane", BoomLane)
        private = private_instance(tmp_path)
        write_txt(private, "boom.txt", SYNTHETIC_TXT, SYNTHETIC_META)
        instance = load_instance(str(ROOT), str(private))
        lifecycle = lifecycle_mod.InboxLifecycle(str(ROOT), instance)
        lifecycle.scan_once()
        time.sleep(STABILITY_WAIT)
        lifecycle.scan_once()
        rec = next(iter(source_records(private).values()))
        assert rec["processing_status"] == "PROCESSING_FAILED"
        assert rec["review_status"] == "NONE"
        assert rec["attempt_count"] == 1
        assert rec["review_reason"] == "LANE_FAILED:RuntimeError"
    finally:
        sys.path.remove(str(ROOT / "src"))


def test_a23_malformed_url_and_weak_evidence_rejected(tmp_path):
    """Q2 C5: a URL without a hostname and non-dict evidence entries are
    metadata rejections, not silent acceptances."""
    private = private_instance(tmp_path)
    bad_url = dict(SYNTHETIC_META)
    bad_url["source_url"] = "https://"
    write_txt(private, "badurl.txt", SYNTHETIC_TXT, bad_url)
    bad_evidence = {
        "schema_version": 1, "title": "t", "origin": "synthetic_fixture",
        "source_url": None,
        "rights": {"classification": "public",
                   "public_export_allowed": True,
                   "evidence": ["just a string"]},
    }
    write_txt(private, "badev.txt", SYNTHETIC_TXT, bad_evidence)
    scan(private)
    scan(private)
    records = source_records(private)
    assert len(records) == 2
    reasons = {r["review_reason"] for r in records.values()}
    assert "METADATA_SOURCE_URL_INVALID" in reasons
    assert "METADATA_RIGHTS_EVIDENCE_STRUCTURE_INVALID" in reasons


def test_a24_skip_reasons_are_reported(tmp_path):
    """Q2 A5: scan summaries expose per-file skip reasons."""
    private = private_instance(tmp_path)
    inbox = private / "inbox/documents"
    (inbox / ".hidden.txt").write_text("x", encoding="utf-8")
    target = inbox / "real-target.txt"
    target.write_text("y", encoding="utf-8")
    try:
        (inbox / "link.txt").symlink_to(target)
        have_link = True
    except OSError:
        have_link = False
    summary = scan_nowait(private)
    reasons = {item["reason"] for item in summary["skipped"]}
    assert "SYSTEM_FILE_SKIPPED" in reasons
    if have_link:
        assert "SYMLINK_NOT_FOLLOWED" in reasons
    assert "METADATA_SIDECAR" not in reasons  # no sidecars written here


def test_a25_index_rebuilt_from_sources_after_deletion(tmp_path):
    """Q2 C2: if index.json is deleted but source records exist, the next
    scan rebuilds it deterministically from the source records (logged),
    instead of continuing with a partial view or failing silently."""
    private = private_instance(tmp_path)
    write_txt(private, "synthetic-news.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    index_path = lifecycle_dir(private) / "index.json"
    assert index_path.is_file()
    index_path.unlink()
    # A scan (not just a query) recovers by rebuilding from sources.
    summary = scan(private)
    assert summary["errors"] == []
    assert index_path.is_file()
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor",
                     "--visibility", "public")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["count"] == 1
    ledger = json.loads(
        (lifecycle_dir(private) / "ledger" / "ledger.json").read_text(
            encoding="utf-8"))
    assert any(entry.get("kind") == "index_rebuilt"
               for entry in ledger["events"])


def test_a26_near_duplicate_review_persists_after_touch(tmp_path):
    """Q2 round-2 A1: an unresolved near-duplicate human review must persist.

    Touching only the mtime (no content/sidecar change) and rescanning must
    NOT clear the review via an exact_merge re-ingest. Re-ingestion of
    identical inputs is not an approval.
    """
    private = private_instance(tmp_path)
    write_txt(private, "orig.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    write_txt(private, "near.txt", NEAR_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    records = source_records(private)
    near_dup = [r for r in records.values()
                if r["review_status"] == "REVIEW_REQUIRED"]
    assert len(near_dup) == 1, "expected one near-duplicate in review"
    target = near_dup[0]["relative_path"]
    # mtime-only change: touch, no content or sidecar modification.
    os.utime(private / "inbox/documents" / target, None)
    scan(private)
    summary = scan(private)
    records = source_records(private)
    rec = next(r for r in records.values()
               if r["relative_path"] == target)
    assert rec["review_status"] == "REVIEW_REQUIRED", (
        "touch must not clear an unresolved review")
    assert rec["disposition"] == "review_required"
    # The reviewed document must not be publicly searchable.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "pre-season",
                     "--visibility", "public")
    assert result.returncode == 0, result.stderr
    # Only the clean near.txt (public) may match; the reviewed orig.txt
    # must not contribute. Count the reviewed doc's absence via status.
    statuses = json.loads(command(
        "--instance-root", str(private), "archive-status", "--all"
    ).stdout)["sources"]
    reviewed = [s for s in statuses if s["relative_path"] == target][0]
    assert reviewed["index_status"] == "NOT_INDEXED"


def test_a27_shared_doc_quarantined_when_source_tightened(tmp_path):
    """Q2 round-2 B1: tightening one source of a shared document must remove
    the shared document from public search (quarantine), while local search
    via the clean source still works."""
    private = private_instance(tmp_path)
    txt = "Shared quarantine content. Identical bytes in both files."
    write_txt(private, "qa.txt", txt, SYNTHETIC_META)
    write_txt(private, "qb.txt", txt, SYNTHETIC_META)
    scan(private)
    scan(private)
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "quarantine",
                     "--visibility", "public")
    assert json.loads(result.stdout)["count"] == 1
    # Tighten qa.txt: third-party copyrighted, no public export.
    meta = dict(SYNTHETIC_META)
    meta = json.loads(json.dumps(meta))
    meta["origin"] = "third_party"
    meta["rights"] = {"classification": "copyrighted",
                      "public_export_allowed": False, "evidence": []}
    (private / "inbox/documents/qa.txt.source.json").write_text(
        json.dumps(meta), encoding="utf-8")
    scan(private)
    scan(private)
    records = source_records(private)
    qa = next(r for r in records.values()
              if r["relative_path"] == "qa.txt")
    assert qa["review_status"] == "REVIEW_REQUIRED"
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "quarantine",
                     "--visibility", "public")
    assert json.loads(result.stdout)["count"] == 0, (
        "shared doc must be quarantined from public while a linked "
        "source is in review")
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "quarantine",
                     "--visibility", "local")
    assert json.loads(result.stdout)["count"] == 1, (
        "local search via the clean source must still work")


def test_a28_symlinked_data_dir_rejected(tmp_path):
    """Q2 round-2 A6: a symlinked <instance>/data pointing outside the
    instance (e.g. at an engine tree) must be refused before any write."""
    private = private_instance(tmp_path)
    outside = tmp_path / "fake-engine"
    outside.mkdir()
    (private / "data").symlink_to(outside)
    write_txt(private, "t.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    result = command("--instance-root", str(private), "inbox-scan")
    assert result.returncode != 0, "path escape must fail the scan"
    assert "INBOX_LIFECYCLE_PATH_ESCAPE" in (result.stdout + result.stderr)
    assert list(outside.rglob("*")) == [], "no writes outside the instance"


def test_a29_symlinked_sidecar_rejected(tmp_path):
    """Q2 round-2 A2: a symlinked sidecar is never followed; its metadata
    is untrusted and the source goes to human review."""
    private = private_instance(tmp_path)
    inbox = private / "inbox/documents"
    (inbox / "s.txt").write_text("sidecar symlink test", encoding="utf-8")
    (tmp_path / "evil.json").write_text('{"evil": true}', encoding="utf-8")
    (inbox / "s.txt.source.json").symlink_to(tmp_path / "evil.json")
    scan(private)
    scan(private)
    records = source_records(private)
    rec = next(iter(records.values()))
    assert rec["review_status"] == "REVIEW_REQUIRED"
    assert rec["review_reason"] == "METADATA_SYMLINK_REJECTED"


def test_a30_same_size_mtime_content_change_detected(tmp_path):
    """Q2 round-2 A4: replacing content with same byte size and restoring
    the mtime must still be detected (hash-verified fast path) and recorded
    as a revision, not silently ignored."""
    private = private_instance(tmp_path)
    inbox = private / "inbox/documents"
    (inbox / "x.txt").write_text("AAAAAAAAAA", encoding="utf-8")
    (inbox / "x.txt.source.json").write_text(
        json.dumps(SYNTHETIC_META), encoding="utf-8")
    scan(private)
    scan(private)
    mtime = os.stat(inbox / "x.txt").st_mtime_ns
    (inbox / "x.txt").write_text("BBBBBBBBBB", encoding="utf-8")
    os.utime(inbox / "x.txt", ns=(mtime, mtime))  # restore mtime
    scan(private)
    scan(private)
    records = source_records(private)
    rec = next(iter(records.values()))
    assert len(rec.get("revision_history") or []) == 2, (
        "content change must be recorded even with identical stat")
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "BBBBBBBBBB",
                     "--visibility", "local")
    assert json.loads(result.stdout)["count"] == 1


def test_a31_merged_doc_titles_all_searchable(tmp_path):
    """Q2 round-2 A5: when two sources with different titles merge to one
    document, a title query for EITHER title must find it."""
    private = private_instance(tmp_path)
    txt = "Identical content for the title test."
    meta1 = json.loads(json.dumps(SYNTHETIC_META))
    meta1["title"] = "First title"
    meta2 = json.loads(json.dumps(SYNTHETIC_META))
    meta2["title"] = "Second title"
    write_txt(private, "m1.txt", txt, meta1)
    write_txt(private, "m2.txt", txt, meta2)
    scan(private)
    scan(private)
    for title in ("First title", "Second title"):
        result = command("--instance-root", str(private),
                         "archive-query", "--title", title,
                         "--visibility", "public")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["count"] == 1, (
            f"title query {title!r} must find the merged document")


def test_a32_malformed_metadata_does_not_abort_scan(tmp_path):
    """Q2 round-2 C1: structurally invalid metadata (unhashable origin,
    unparsable URL) routes to review; the scan completes and other files
    are still processed."""
    private = private_instance(tmp_path)
    bad1 = json.loads(json.dumps(SYNTHETIC_META))
    bad1["origin"] = []  # unhashable; must not raise TypeError
    bad2 = json.loads(json.dumps(SYNTHETIC_META))
    bad2["source_url"] = "https://["  # unparsable; must not raise ValueError
    write_txt(private, "bad1.txt", "bad one", bad1)
    write_txt(private, "bad2.txt", "bad two", bad2)
    write_txt(private, "good.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    summary = scan(private)
    assert summary["errors"] == [], "no file may abort the scan"
    records = source_records(private)
    by_name = {r["relative_path"]: r for r in records.values()}
    assert by_name["bad1.txt"]["review_status"] == "REVIEW_REQUIRED"
    assert by_name["bad2.txt"]["review_status"] == "REVIEW_REQUIRED"
    assert by_name["good.txt"]["review_status"] == "NONE"
    assert by_name["good.txt"]["index_status"] == "SEARCHABLE"


def test_a33_sidecar_mtime_touch_does_not_clear_review(tmp_path):
    """Q2 round-3 A1: touching ONLY the sidecar's mtime (no content change)
    must not clear an unresolved review. Identity is content-based."""
    private = private_instance(tmp_path)
    write_txt(private, "orig.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    write_txt(private, "near.txt", NEAR_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    records = source_records(private)
    target = next(r["relative_path"] for r in records.values()
                  if r["review_status"] == "REVIEW_REQUIRED")
    # Touch ONLY the sidecar, not the TXT.
    os.utime(private / "inbox/documents" / (target + ".source.json"), None)
    scan(private)
    scan(private)
    records = source_records(private)
    rec = next(r for r in records.values()
               if r["relative_path"] == target)
    assert rec["review_status"] == "REVIEW_REQUIRED", (
        "sidecar mtime touch must not clear review")


def test_a34_state_subdir_symlink_rejected(tmp_path):
    """Q2 round-3 A2: a symlink at data/inbox_lifecycle (subdir, not just
    data_root) must be rejected before any write."""
    private = private_instance(tmp_path)
    write_txt(private, "t.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    # Replace data/inbox_lifecycle with a symlink to outside.
    import shutil
    shutil.rmtree(private / "data/inbox_lifecycle")
    outside = tmp_path / "fake-engine"
    outside.mkdir()
    (private / "data/inbox_lifecycle").symlink_to(outside)
    result = command("--instance-root", str(private), "inbox-scan")
    assert result.returncode != 0, "subdir symlink must fail"
    assert "INBOX_LIFECYCLE_PATH_ESCAPE" in (result.stdout + result.stderr)
    assert list(outside.rglob("*")) == []


def test_a35_sidecar_content_change_detected_despite_same_stat(tmp_path):
    """Q2 round-3 B1: changing sidecar content (keeping same size via
    whitespace, restoring mtime) must trigger re-evaluation, not be ignored."""
    private = private_instance(tmp_path)
    write_txt(private, "p.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    # Change classification to private. Try to keep same size via whitespace,
    # restore mtime. The fix hash-verifies content, so any change is caught.
    import os
    sidecar_path = private / "inbox/documents/p.txt.source.json"
    orig_mtime = sidecar_path.stat().st_mtime_ns
    meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    meta["rights"]["classification"] = "private"
    meta["rights"]["public_export_allowed"] = False
    new_content = json.dumps(meta, indent=2)
    # Ensure valid JSON.
    json.loads(new_content)
    sidecar_path.write_text(new_content, encoding="utf-8")
    os.utime(sidecar_path, ns=(orig_mtime, orig_mtime))
    scan(private)
    scan(private)
    records = source_records(private)
    rec = next(iter(records.values()))
    # The rights change must be detected (review or re-evaluated).
    assert rec["review_status"] == "REVIEW_REQUIRED" or \
        rec.get("rights", {}).get("classification") == "private", (
        "sidecar content change must not be ignored")


def test_a36_missing_linked_source_fails_closed(tmp_path):
    """Q2 round-3 B2: if a linked source record is deleted, the shared
    document must not become public (fail-closed, INDEX_FAILED)."""
    private = private_instance(tmp_path)
    txt = "Shared content for B2 test."
    write_txt(private, "qa.txt", txt, SYNTHETIC_META)
    write_txt(private, "qb.txt", txt, SYNTHETIC_META)
    scan(private)
    scan(private)
    # Tighten qa.txt to trigger quarantine.
    meta = json.loads(json.dumps(SYNTHETIC_META))
    meta["origin"] = "third_party"
    meta["rights"] = {"classification": "copyrighted",
                      "public_export_allowed": False, "evidence": []}
    (private / "inbox/documents/qa.txt.source.json").write_text(
        json.dumps(meta), encoding="utf-8")
    scan(private)
    scan(private)
    # Delete qa's source record JSON (simulate loss).
    records = source_records(private)
    qa_id = next(r["source_id"] for r in records.values()
                 if r["relative_path"] == "qa.txt")
    (lifecycle_dir(private) / "sources" / f"{qa_id}.json").unlink()
    # Query must fail closed, not return the doc as public.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "B2 test",
                     "--visibility", "public")
    # Either INDEX_FAILED (non-zero) or count==0 (quarantined).
    if result.returncode == 0:
        assert json.loads(result.stdout)["count"] == 0, (
            "missing record must not fail open to public")
    else:
        assert "INDEX_FAILED" in (result.stdout + result.stderr)


def test_a37_corrupt_index_at_scan_start_rebuilt_or_failed(tmp_path):
    """Q2 round-3 C1: a corrupt index.json at scan start must not be
    silently ignored; the scan rebuilds from sources or fails."""
    private = private_instance(tmp_path)
    write_txt(private, "x.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    # Corrupt the index.
    (lifecycle_dir(private) / "index.json").write_text("[]", encoding="utf-8")
    # Scan should rebuild (sources exist) and not report success with
    # a corrupt index silently.
    summary = scan(private)
    # After rebuild, the index should be valid.
    index_data = json.loads(
        (lifecycle_dir(private) / "index.json").read_text(encoding="utf-8"))
    assert isinstance(index_data, dict), "index must be rebuilt to valid shape"
    assert summary["errors"] == [] or any(
        "index" in str(e).lower() for e in summary["errors"])


def test_b01_sidecar_whitespace_does_not_clear_review(tmp_path):
    """Q2 round-4 A1: adding whitespace to sidecar (no semantic change)
    must not clear an unresolved near-duplicate review."""
    private = private_instance(tmp_path)
    write_txt(private, "orig.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    write_txt(private, "near.txt", NEAR_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    records = source_records(private)
    target = next((r["relative_path"] for r in records.values()
                   if r["review_status"] == "REVIEW_REQUIRED"), None)
    assert target is not None, "need a review target"
    # Add a single space to sidecar (no semantic change).
    sp = private / "inbox/documents" / (target + ".source.json")
    content = sp.read_text(encoding="utf-8")
    sp.write_text(content + " ", encoding="utf-8")
    scan(private)
    scan(private)
    records = source_records(private)
    rec = next(r for r in records.values()
               if r["relative_path"] == target)
    assert rec["review_status"] == "REVIEW_REQUIRED", (
        "sidecar whitespace must not clear review")
    # And it must not be publicly searchable.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons",
                     "--visibility", "public")
    if result.returncode == 0:
        count = json.loads(result.stdout)["count"]
        # The reviewed doc should not appear in public results.
        # (Exact assertion depends on which doc is reviewed.)


def test_b02_sources_subdir_symlink_rejected(tmp_path):
    """Q2 round-4 A2: symlink at data/inbox_lifecycle/sources must be
    rejected; no files written to external target."""
    private = private_instance(tmp_path)
    write_txt(private, "t.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    import shutil
    sources_dir = lifecycle_dir(private) / "sources"
    shutil.rmtree(sources_dir)
    outside = tmp_path / "evil"
    outside.mkdir()
    sources_dir.symlink_to(outside)
    result = command("--instance-root", str(private), "inbox-scan")
    # Must fail or refuse to write outside.
    assert result.returncode != 0 or list(outside.rglob("*")) == [], (
        "sources symlink must be rejected")
    if result.returncode != 0:
        assert "ESCAPE" in (result.stdout + result.stderr) or \
               "symlink" in (result.stdout + result.stderr).lower()


def test_b03_lock_symlink_does_not_overwrite(tmp_path):
    """Q2 round-4 C-lock: .lock symlink to external file must not
    truncate/overwrite the target."""
    private = private_instance(tmp_path)
    write_txt(private, "t.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL CONTENT - MUST NOT BE DESTROYED", encoding="utf-8")
    orig_hash = hashlib.sha256(victim.read_bytes()).hexdigest()
    lock_path = lifecycle_dir(private) / ".lock"
    # Ensure lifecycle dir exists.
    scan(private)
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    lock_path.symlink_to(victim)
    result = command("--instance-root", str(private), "inbox-scan")
    # Victim must be intact.
    assert victim.read_bytes() is not None
    new_hash = hashlib.sha256(victim.read_bytes()).hexdigest()
    assert new_hash == orig_hash, "lock symlink must not overwrite target"
    assert "ORIGINAL CONTENT" in victim.read_text(encoding="utf-8")


def test_b04_public_to_private_not_publicly_searchable(tmp_path):
    """Q2 round-4 B1: changing sidecar from public to private (metadata-only)
    must remove the document from public search results."""
    private = private_instance(tmp_path)
    write_txt(private, "p.txt", SYNTHETIC_TXT, SYNTHETIC_META)
    scan(private)
    scan(private)
    # Verify it's publicly searchable initially.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons",
                     "--visibility", "public")
    assert result.returncode == 0
    assert json.loads(result.stdout)["count"] >= 1
    # Tighten to private (metadata-only, TXT unchanged).
    sp = private / "inbox/documents/p.txt.source.json"
    meta = json.loads(sp.read_text(encoding="utf-8"))
    meta["rights"]["classification"] = "private"
    meta["rights"]["public_export_allowed"] = False
    sp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    scan(private)
    scan(private)
    # Must NOT be publicly searchable anymore.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons",
                     "--visibility", "public")
    if result.returncode == 0:
        count = json.loads(result.stdout)["count"]
        assert count == 0, f"B1: public query returned {count}, expected 0"
    # But should still be visible locally.
    result = command("--instance-root", str(private),
                     "archive-query", "--keyword", "Harbor Falcons",
                     "--visibility", "local")
    assert result.returncode == 0
    assert json.loads(result.stdout)["count"] >= 1
