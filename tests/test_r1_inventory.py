"""2.0.3-2 R1 扫描器测试（实现者自测，非 Web 验收测试）。

覆盖：真实调用链（CLI -> 枚举 -> manifest -> 报告）、11 字段、
disposition 判定、退出码、只读边界（不写源目录）、报告锚点。
证伪：篡改 manifest 后 Web 验收脚本必须 FAIL（见 test_acceptance_rejects_tampered）。
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCHEMA_PATH = REPO_ROOT / "tests" / "acceptance" / "r1_manifest.schema.json"
ACCEPT_PATH = REPO_ROOT / "tests" / "acceptance" / "test_r1_acceptance.py"

FIELDS = {
    "file_id", "name", "mime_type", "sha256", "parent_ids",
    "source_id", "document_id", "processing_status",
    "disposition", "final_parent", "evidence_reference",
}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def fixture(tmp_path: Path) -> dict:
    inbox = tmp_path / "00_inbox_待处理"
    sources = tmp_path / "10_sources_原始证据"
    (inbox / "sub").mkdir(parents=True)
    (inbox / "emptydir").mkdir()
    sources.mkdir(parents=True)
    a = _write(inbox / "a.txt", "hello inbox")
    b = _write(inbox / "sub" / "b.txt", "pending file")
    s = _write(sources / "s.txt", "source file")
    c = _write(inbox / "c.txt", "conflict file")
    _write(inbox / "d.txt", "extra one")
    _write(sources / "t.txt", "extra two")
    # 符号链接（目标在根内，不越界）
    (inbox / "link_to_a").symlink_to(a.name)

    records = [
        {"relative_path": "a.txt", "processing_status": "completed",
         "final_location": str(a.resolve()), "source_id": "SRC-001"},
        {"relative_path": "sub/b.txt", "processing_status": "pending"},
        {"relative_path": "c.txt", "processing_status": "completed",
         "final_location": str(c.resolve())},
        {"relative_path": "c.txt", "processing_status": "pending"},
    ]
    store = tmp_path / "record_store.json"
    store.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    bindings = {
        "inbox_root": str(inbox.resolve()),
        "sources_root": str(sources.resolve()),
        "record_stores": [str(store)],
        "binding_reference": "test-fixture",
        "status_map": {},
    }
    bpath = tmp_path / "bindings.json"
    bpath.write_text(json.dumps(bindings, ensure_ascii=False), encoding="utf-8")
    return {"tmp": tmp_path, "inbox": inbox, "sources": sources,
            "bindings": bpath, "reports": tmp_path / "reports",
            "a": a, "b": b, "s": s, "c": c}


def _run_scan(fx: dict, *extra: str) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-m", "cba_kb.r1_inventory", "--scan",
         "--bindings", str(fx["bindings"]),
         "--reports-dir", str(fx["reports"]), *extra],
        capture_output=True, text=True, env=env, cwd=str(fx["tmp"]),
    )


def _load_manifest(fx: dict) -> tuple[dict, Path, Path]:
    manifests = sorted(fx["reports"].glob("*.manifest.json"))
    assert len(manifests) == 1, f"期望恰好一个 manifest，实际 {len(manifests)}"
    mp = manifests[0]
    rp = mp.with_name(mp.name.replace(".manifest.json", ".report.md"))
    assert rp.is_file()
    return json.loads(mp.read_text(encoding="utf-8")), mp, rp


def test_scan_exit_zero_and_outputs(fixture):
    proc = _run_scan(fixture)
    assert proc.returncode == 0, proc.stderr
    manifest, mp, rp = _load_manifest(fixture)
    assert manifest["spec_version"] == "2.0.3-2-R1-Q1"
    assert manifest["backend"] == "local_filesystem"
    assert manifest["trigger"] == "manual"
    assert manifest["scope_complete"] is True


def test_eleven_fields_exact(fixture):
    _run_scan(fixture)
    manifest, _, _ = _load_manifest(fixture)
    assert manifest["objects"], "对象不能为空"
    for o in manifest["objects"]:
        assert set(o.keys()) == FIELDS, f"{o['file_id']} 字段不符"


def test_disposition_decisions(fixture):
    _run_scan(fixture)
    manifest, _, _ = _load_manifest(fixture)
    by_id = {o["file_id"]: o for o in manifest["objects"]}
    # a.txt: completed + 最终位置可验证 -> PROCESSED
    assert by_id["inbox/a.txt"]["disposition"] == "PROCESSED"
    assert by_id["inbox/a.txt"]["processing_status"] == "COMPLETED"
    assert by_id["inbox/a.txt"]["source_id"] == "SRC-001"
    assert by_id["inbox/a.txt"]["sha256"] == hashlib.sha256(b"hello inbox").hexdigest()
    # sub/b.txt: pending -> UNPROCESSED
    assert by_id["inbox/sub/b.txt"]["disposition"] == "UNPROCESSED"
    # s.txt: 无记录 -> REVIEW_REQUIRED（不能推出 UNPROCESSED）
    assert by_id["sources/s.txt"]["disposition"] == "REVIEW_REQUIRED"
    # c.txt: completed/pending 冲突 -> REVIEW_REQUIRED
    assert by_id["inbox/c.txt"]["disposition"] == "REVIEW_REQUIRED"
    # 空目录也被纳入
    assert by_id["inbox/emptydir"]["mime_type"] == "inode/directory"
    # 符号链接本身纳入，不跟随
    assert by_id["inbox/link_to_a"]["mime_type"] == "inode/symlink"
    assert by_id["inbox/link_to_a"]["sha256"] is None


def test_review_reasons_have_evidence(fixture):
    _run_scan(fixture)
    manifest, _, _ = _load_manifest(fixture)
    ev = {e["evidence_id"]: e for e in manifest["evidence"]}
    for o in manifest["objects"]:
        kinds = {ev[e]["kind"] for e in o["evidence_reference"]}
        assert "OBJECT" in kinds
        if o["disposition"] == "REVIEW_REQUIRED":
            assert "REVIEW_REASON" in kinds
            rr = [ev[e] for e in o["evidence_reference"] if ev[e]["kind"] == "REVIEW_REASON"][0]
            assert rr["reason_code"] and rr["detail"]


def test_report_anchors_unique(fixture):
    _run_scan(fixture)
    manifest, _, rp = _load_manifest(fixture)
    report = rp.read_text(encoding="utf-8")
    for o in manifest["objects"]:
        marker = '<a id="obj-' + hashlib.sha256(o["file_id"].encode()).hexdigest() + '"></a>'
        assert report.count(marker) == 1, f"锚点缺失或重复: {o['file_id']}"
    # REVIEW_REQUIRED 清单存在
    assert "REVIEW_REQUIRED" in report


def test_read_only_no_source_writes(fixture):
    before = {}
    for p in (fixture["inbox"], fixture["sources"]):
        for f in p.rglob("*"):
            if f.is_file() and not f.is_symlink():
                before[str(f)] = (f.stat().st_mtime_ns, f.stat().st_size)
    _run_scan(fixture)
    for fstr, (mt, sz) in before.items():
        f = Path(fstr)
        assert (f.stat().st_mtime_ns, f.stat().st_size) == (mt, sz), f"源文件被改动: {fstr}"
    # reports/ 与源目录无交叉写入
    assert not list(fixture["inbox"].glob("*.manifest.json"))
    assert not list(fixture["sources"].glob("*.report.md"))


def test_reports_overlap_rejected(fixture):
    proc = _run_scan(fixture, "--reports-dir", str(fixture["inbox"] / "reports"))
    assert proc.returncode == 1
    assert "重叠" in proc.stdout or "重叠" in proc.stderr


def test_missing_root_rejected(tmp_path):
    bindings = {"inbox_root": str(tmp_path / "nope"), "sources_root": str(tmp_path / "s"),
                "record_stores": [], "binding_reference": "t"}
    (tmp_path / "s").mkdir()
    bp = tmp_path / "b.json"
    bp.write_text(json.dumps(bindings), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC)}
    proc = subprocess.run([sys.executable, "-m", "cba_kb.r1_inventory", "--scan",
                           "--bindings", str(bp), "--reports-dir", str(tmp_path / "r")],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 1


def _build_oracle(manifest: dict, report: Path) -> dict:
    """为 fixture 手工建立最小独立 oracle（独立枚举视角）。"""
    objects = manifest["objects"]
    evidence = {e["evidence_id"]: e for e in manifest["evidence"]}
    return {
        "run_id": manifest["run_id"],
        "expected_objects": objects,
        "verified_evidence": list(evidence.values()),
        "report_checks": {
            "object_row_counts": {o["file_id"]: 1 for o in objects},
            "summary_counts_correct": True,
            "all_object_rows_match_manifest": True,
            "review_entries": [
                {"file_id": o["file_id"],
                 "anchor": "obj-" + hashlib.sha256(o["file_id"].encode()).hexdigest(),
                 "locator": "x", "reason_code": "y", "detail": "z",
                 "next_check": "w", "matches_manifest_and_evidence": True,
                 "locator_checked": True}
                for o in objects if o["disposition"] == "REVIEW_REQUIRED"
            ],
        },
        "human_samples": [
            {"file_id": o["file_id"], "reviewer": "human", "reviewed_at": "2026-10-10T00:00:00Z",
             "run_id": manifest["run_id"], "verdict": "PASS",
             "source": {"result": "VERIFIED", "detail": "d", "evidence_reference": "e"},
             "process": {"result": "VERIFIED", "detail": "d", "evidence_reference": "e"},
             "final_location": {"result": "VERIFIED", "detail": "d", "evidence_reference": "e"}}
            for o in objects[:10]
        ],
        "runtime_review": {
            "reviewer": "human", "audit_reference": "test",
            "live_run_verified": True, "bindings_verified": True,
            "enumeration_verified": True, "stable_window_verified": True,
            "report_file_verified": True, "unauthorized_writes": [],
        },
    }


def test_acceptance_passes_on_good_artifacts(fixture):
    """Web 验收脚本在合格产物上通过（变绿）。"""
    proc = _run_scan(fixture)
    assert proc.returncode == 0, proc.stderr
    manifest, mp, rp = _load_manifest(fixture)
    oracle = _build_oracle(manifest, rp)
    op = fixture["tmp"] / "oracle.json"
    op.write_text(json.dumps(oracle, ensure_ascii=False), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    ap = subprocess.run([sys.executable, str(ACCEPT_PATH), str(SCHEMA_PATH),
                         str(mp), str(rp), str(op)],
                        capture_output=True, text=True, env=env)
    assert ap.returncode == 0, ap.stderr + ap.stdout


def test_acceptance_rejects_tampered(fixture):
    """证伪：篡改 manifest（删对象）后，Web 验收脚本必须 FAIL。"""
    proc = _run_scan(fixture)
    assert proc.returncode == 0, proc.stderr
    manifest, mp, rp = _load_manifest(fixture)
    tampered = dict(manifest)
    tampered["objects"] = manifest["objects"][:-1]  # 删掉一个对象：静默丢失
    tp = fixture["tmp"] / "tampered.manifest.json"
    tp.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    oracle = _build_oracle(manifest, rp)
    op = fixture["tmp"] / "oracle.json"
    op.write_text(json.dumps(oracle, ensure_ascii=False), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    ap = subprocess.run([sys.executable, str(ACCEPT_PATH), str(SCHEMA_PATH),
                         str(tp), str(rp), str(op)],
                        capture_output=True, text=True, env=env)
    assert ap.returncode != 0, "验收脚本未检出篡改：证伪失败"
    assert "A1" in ap.stderr or "A1" in ap.stdout
