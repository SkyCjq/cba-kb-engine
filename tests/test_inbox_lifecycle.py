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
    merged = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
              "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    # Stability window default is 1s; tests use instant stability.
    merged.setdefault("CBA_KB_INBOX_STABILITY_SECONDS", "0")
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.cli", "--root", str(ROOT), *args],
        cwd=ROOT, env=merged, capture_output=True, text=True, timeout=120,
    )


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
                     "inbox-poll", "--interval-seconds", "1", "--max-scans", "3")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["scans_completed"] == 3
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
