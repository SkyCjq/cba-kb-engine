import hashlib
import json
from pathlib import Path
import socket

import pytest

from cba_kb.document_lane import (
    DocumentLane,
    assert_public_export_allowed,
    content_hash,
    doc_id,
    near_duplicate,
    normalize_canonical_url,
    normalize_text,
)
from cba_kb.instance import Instance


ROOT = Path(__file__).resolve().parents[1]
FIXED_AT = "2026-09-13T06:00:00Z"


def instance(tmp_path):
    root = tmp_path / "instance"
    config = root / "config"
    config.mkdir(parents=True)
    (root / "inbox/documents").mkdir(parents=True)
    return Instance(
        root=root,
        config_root=config,
        credentials_store=root / ".credentials",
        real_fixture_root=root / "fixtures/real",
        data_root=root / "data",
    )


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    return path


def test_normalization_hash_url_and_identity_are_deterministic():
    assert normalize_text("\uff23\uff22\uff21 \t x\r\n\r\nend \n") == "CBA x\n\nend\n"
    assert content_hash("CBA \t x\r\n") == content_hash("CBA x\n")
    assert doc_id("same") == doc_id("same")
    assert normalize_canonical_url(
        "HTTPS://Example.COM/a?utm_source=x&__biz=keep&mid=2#fragment"
    ) == "https://example.com/a?__biz=keep&mid=2"
    assert normalize_canonical_url(
        "https://example.com/a?b=2&a=1&utm_medium=x"
    ) == "https://example.com/a?a=1&b=2"


def test_exact_dedup_preserves_each_capture_and_attachment(tmp_path):
    private = instance(tmp_path)
    lane = DocumentLane(ROOT, private)
    write(private.document_input_root / "one.bin", b"one")
    write(private.document_input_root / "two.bin", b"two")
    first = write(
        private.document_input_root / "first.md",
        "---\nsource_url: https://example.com/story?utm_source=test\n"
        "attachments: [one.bin]\n---\nSame body\n",
    )
    second = write(
        private.document_input_root / "second.md",
        "---\nsource_url: https://example.com/story?utm_source=test\n"
        "attachments: [two.bin]\n---\nSame body\n",
    )
    options = dict(
        capture_channel="wechat_browser_clip",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    one = lane.ingest_file(first, **options)
    two = lane.ingest_file(second, **options)
    assert one["doc_id"] == two["doc_id"]
    assert two["disposition"] == "exact_merge"
    archive = private.document_archive_root / one["doc_id"]
    assert len(list((archive / "raw").iterdir())) == 2
    assert len(list((archive / "attachments").glob("capture_*/*"))) == 2
    provenance = json.loads((archive / "provenance.json").read_text())
    assert len(provenance["captures"]) == 2
    assert all(item["attachments"] for item in provenance["captures"])
    dedup = json.loads((archive / "dedup.json").read_text())
    assert dedup["disposition"] == "exact_merge"
    assert dedup["near_duplicates"] == []
    before = (archive / "provenance.json").read_bytes()
    again = lane.ingest_file(second, **options)
    assert again["changed"] is False
    assert (archive / "provenance.json").read_bytes() == before


def test_same_canonical_url_content_change_keeps_revision(tmp_path):
    private = instance(tmp_path)
    lane = DocumentLane(ROOT, private)
    first = write(
        private.document_input_root / "v1.md",
        "---\nsource_url: https://example.com/story\n---\nversion one\n",
    )
    second = write(
        private.document_input_root / "v2.md",
        "---\nsource_url: https://example.com/story\n---\nversion two\n",
    )
    options = dict(
        capture_channel="wechat_browser_clip",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    one = lane.ingest_file(first, **options)
    two = lane.ingest_file(second, **options)
    assert one["doc_id"] != two["doc_id"]
    assert two["disposition"] == "revision"
    dedup = json.loads(
        (private.document_archive_root / two["doc_id"] / "dedup.json").read_text()
    )
    assert dedup["revision_of"] == [one["doc_id"]]
    assert (private.document_archive_root / one["doc_id"] / "record.json").is_file()
    assert (private.document_archive_root / two["doc_id"] / "record.json").is_file()


def test_near_duplicate_uses_frozen_rule_and_requires_review(tmp_path):
    private = instance(tmp_path)
    lane = DocumentLane(ROOT, private)
    body = "".join(chr(0x4E00 + index) for index in range(200)) + "x"
    changed = body[:-1] + "y"
    assert near_duplicate(body, changed)["candidate"] is True
    first = write(private.document_input_root / "one.txt", body)
    second = write(private.document_input_root / "two.txt", changed)
    options = dict(
        capture_channel="local_file",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    assert lane.ingest_file(first, **options)["accepted"] is True
    result = lane.ingest_file(second, **options)
    assert result["accepted"] is False
    assert result["status"] == "REVIEW_REQUIRED"
    dedup = json.loads(
        (private.document_archive_root / result["doc_id"] / "dedup.json").read_text()
    )
    assert dedup["disposition"] == "review_required"
    assert dedup["near_duplicates"][0]["doc_id"].startswith("doc_")


def test_near_duplicate_boundaries_and_repeated_determinism():
    base = "".join(chr(0x4E00 + index) for index in range(27))
    boundary = chr(0x5000) + base[1:]
    at_threshold = near_duplicate(base, boundary)
    assert at_threshold["jaccard"] == 0.92
    assert at_threshold["length_ratio"] == 1.0
    assert at_threshold["candidate"] is True

    below = (
        "".join(chr(0x6000 + index) for index in range(2))
        + base[2:]
    )
    below_threshold = near_duplicate(base, below)
    assert below_threshold["jaccard"] < 0.92
    assert below_threshold["length_ratio"] >= 0.90
    assert below_threshold["candidate"] is False

    length_boundary = near_duplicate("a" * 99, "a" * 89)
    assert length_boundary["jaccard"] == 1.0
    assert length_boundary["length_ratio"] == 0.90
    assert length_boundary["candidate"] is True

    below_length_boundary = near_duplicate("a" * 99, "a" * 88)
    assert below_length_boundary["jaccard"] == 1.0
    assert below_length_boundary["length_ratio"] < 0.90
    assert below_length_boundary["candidate"] is False

    repeated = [near_duplicate(base, boundary) for _ in range(5)]
    assert repeated == [at_threshold] * 5


def test_ima_export_end_to_end_archives_local_evidence(tmp_path, monkeypatch):
    private = instance(tmp_path)
    source = write(
        private.document_input_root / "ima-export.md",
        "---\ntitle: Local ima export\n---\nima body\n",
    )
    raw = source.read_bytes()

    def network_forbidden(*args, **kwargs):
        raise AssertionError("ima ingestion must not perform network access")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    result = DocumentLane(ROOT, private).ingest_file(
        source,
        capture_channel="ima_file_export",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    assert result["status"] == "ACCEPTED"
    archive = private.document_archive_root / result["doc_id"]
    for name in (
        "record.json",
        "text.txt",
        "provenance.json",
        "dedup.json",
        "raw",
        "attachments",
    ):
        assert (archive / name).exists()
    record = json.loads((archive / "record.json").read_text())
    assert record["capture_channel"] == "ima_file_export"
    assert record["rights"] == {
        "classification": "unknown",
        "public_export_allowed": False,
        "evidence": [],
    }
    provenance = json.loads((archive / "provenance.json").read_text())
    assert len(provenance["captures"]) == 1
    assert provenance["captures"][0]["parser_name"] == "ima_file"
    assert (archive / provenance["captures"][0]["raw_path"]).read_bytes() == raw


def test_rights_taxonomy_and_public_export_guards(tmp_path):
    private = instance(tmp_path)
    lane = DocumentLane(ROOT, private)
    source = write(
        private.document_input_root / "tagged.md",
        "---\nsource_url: https://example.com/tagged\n"
        "tags: [外援, free-form]\n---\nbody\n",
    )
    result = lane.ingest_file(
        source,
        capture_channel="wechat_browser_clip",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    record = json.loads(
        (private.document_archive_root / result["doc_id"] / "record.json").read_text()
    )
    assert record["rights"] == {
        "classification": "copyrighted",
        "public_export_allowed": False,
        "evidence": [],
    }
    assert record["tags"] == ["外援"]
    assert record["review_candidates"] == ["free-form"]
    with pytest.raises(RuntimeError, match="PUBLIC_EXPORT_BLOCKED"):
        assert_public_export_allowed(record)

    public = write(
        private.document_input_root / "public.md",
        "---\nsource_url: https://example.com/public\n---\npublic body\n",
    )
    public_result = lane.ingest_file(
        public,
        capture_channel="wechat_browser_clip",
        captured_at=FIXED_AT,
        rights_classification="public",
        public_export_allowed=True,
        rights_evidence=["source states CC0"],
        latency_seconds=0.0,
    )
    public_record = json.loads(
        (private.document_archive_root / public_result["doc_id"]
         / "record.json").read_text()
    )
    assert_public_export_allowed(public_record)
    private_duplicate = write(
        private.document_input_root / "public-private-copy.txt",
        "public body\n",
    )
    merged = lane.ingest_file(
        private_duplicate,
        capture_channel="local_file",
        captured_at=FIXED_AT,
        rights_classification="private",
        public_export_allowed=False,
        latency_seconds=0.0,
    )
    assert merged["doc_id"] == public_result["doc_id"]
    downgraded = json.loads(
        (private.document_archive_root / merged["doc_id"] / "record.json").read_text()
    )
    assert downgraded["rights"]["classification"] == "private"
    assert downgraded["rights"]["public_export_allowed"] is False
    with pytest.raises(RuntimeError, match="PUBLIC_EXPORT_BLOCKED"):
        assert_public_export_allowed(downgraded)

    unsupported = write(
        private.document_input_root / "blocked.md",
        "---\nsource_url: https://example.com/blocked\n---\nblocked body\n",
    )
    blocked = lane.ingest_file(
        unsupported,
        capture_channel="wechat_browser_clip",
        captured_at=FIXED_AT,
        rights_classification="private",
        public_export_allowed=True,
        rights_evidence=["not public"],
        latency_seconds=0.0,
    )
    assert blocked["status"] == "REVIEW_REQUIRED"
    assert blocked["reason"] == "PUBLIC_EXPORT_EVIDENCE_REQUIRED"


def test_parser_review_preserves_raw_and_facts_are_untouched(tmp_path):
    private = instance(tmp_path)
    facts = tmp_path / "facts"
    facts.mkdir()
    master = write(facts / "MASTER.xlsx", b"immutable")
    before = hashlib.sha256(master.read_bytes()).hexdigest()
    invalid = write(private.document_input_root / "invalid.txt", b"\xff\xfe")
    lane = DocumentLane(ROOT, private)
    result = lane.ingest_file(
        invalid,
        capture_channel="local_file",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    assert result["status"] == "REVIEW_REQUIRED"
    assert (private.document_review_root / result["review_id"] / "raw/original.bin").is_file()
    assert hashlib.sha256(master.read_bytes()).hexdigest() == before


def test_archive_files_are_byte_stable_across_fixed_reruns(tmp_path):
    private = instance(tmp_path)
    source = write(private.document_input_root / "stable.txt", "stable\n")
    options = dict(
        capture_channel="local_file",
        captured_at=FIXED_AT,
        latency_seconds=0.0,
    )
    DocumentLane(ROOT, private).ingest_file(source, **options)
    archive = next(private.document_archive_root.glob("doc_*"))
    before = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*") if path.is_file()
    }
    DocumentLane(ROOT, private).ingest_file(source, **options)
    after = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*") if path.is_file()
    }
    assert before == after
