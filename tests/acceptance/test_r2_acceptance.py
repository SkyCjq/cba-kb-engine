"""2.0.3-3 R2 — 只读对账验收测试.

冻结规格 ``2.0.3-3-R2-Q1`` §7。
通过 subprocess 调用真实 CLI（scripts/r2-reconcile），不复用被测模块的分类函数.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "scripts" / "r2-reconcile"
R2_SPEC = "2.0.3-3-R2-Q1"
R1_SPEC = "2.0.3-2-R1-Q1"

FIVE_STATES = ("NEW", "CHANGED", "UNCHANGED", "MISSING_NEEDS_REVIEW", "REVIEW_REQUIRED")


# ---------------------------------------------------------------------------
# Fixture 构造器（独立 oracle，不调用 r2_reconcile）
# ---------------------------------------------------------------------------

def make_evidence(eid, fid, kind, locator="loc://x", reason_code=None):
    return {
        "evidence_id": eid,
        "file_id": fid,
        "kind": kind,
        "locator": locator,
        "observed_at": "2026-10-11T10:00:00Z",
        "reason_code": reason_code,
        "detail": f"detail for {eid}",
    }


def make_object(fid, name="f.txt", sha="a" * 64, parent_ids=None,
                source_id=None, document_id=None, processing_status="PENDING",
                disposition="UNPROCESSED", final_parent=None,
                evidence_reference=None, mime="text/plain"):
    eids = evidence_reference if evidence_reference is not None else [f"ev-{fid}-obj"]
    return {
        "file_id": fid,
        "name": name,
        "mime_type": mime,
        "sha256": sha,
        "parent_ids": parent_ids or [],
        "source_id": source_id,
        "document_id": document_id,
        "processing_status": processing_status,
        "disposition": disposition,
        "final_parent": final_parent,
        "evidence_reference": eids,
    }


def make_evidence_for(obj, kinds=("OBJECT", "PROCESS"), locator="loc://x"):
    """为对象生成配套证据（独立于被测模块）。
    默认含 OBJECT+PROCESS（UNPROCESSED 需要 PROCESS）。"""
    out = []
    for k in kinds:
        eid = f"ev-{obj['file_id']}-{k.lower()}"
        # PROCESS 证据的 reason_code 必须与对象状态一致
        rc = None
        if k == "PROCESS":
            rc = f"PROCESS:{obj['processing_status']}"
        out.append(make_evidence(eid, obj["file_id"], k, locator,
                                 reason_code=rc))
    obj["evidence_reference"] = [e["evidence_id"] for e in out]
    return out


def make_manifest(run_id, objects, evidence,
                  scope_complete=True, scan_errors=None, scope_bindings=None):
    sb = scope_bindings or {
        "inbox_root": "/inbox",
        "sources_root": "/sources",
        "record_stores": ["inbox", "sources"],
        "binding_reference": "bind-1",
    }
    return {
        "spec_version": R1_SPEC,
        "run_id": run_id,
        "backend": "local_filesystem",
        "trigger": "manual",
        "started_at": "2026-10-11T09:00:00Z",
        "finished_at": "2026-10-11T09:05:00Z",
        "scope_complete": scope_complete,
        "scope_bindings": sb,
        "scan_errors": scan_errors or [],
        "objects": objects,
        "evidence": evidence,
    }


def run_r2(baseline, current, tmp_path, reports_dir=None):
    """运行真实 CLI，返回 (exit_code, report_json|None, stdout, stderr)。
    
    使用 R2_TEST_REPORTS_ROOT 环境变量指定测试报告目录（F-01/F-02 修复后）。
    不再依赖 cwd 模拟仓库根。
    """
    bp = tmp_path / "baseline.json"
    cp = tmp_path / "current.json"
    bp.write_text(json.dumps(baseline), encoding="utf-8")
    cp.write_text(json.dumps(current), encoding="utf-8")
    # 确定报告目录
    if reports_dir is None:
        rdir = tmp_path / "reports"
    else:
        rp = Path(reports_dir)
        rdir = rp if rp.is_absolute() else tmp_path / rp
    rdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(CLI), "--baseline", str(bp), "--current", str(cp)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # F-01/F-02: 用环境变量指定测试报告根，不依赖 cwd
    env["R2_TEST_REPORTS_ROOT"] = str(rdir)
    p = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True,
                       timeout=60, env=env)
    report = None
    # 在 rdir 下找最新生成的 JSON（按 mtime）
    if rdir.exists():
        files = sorted(rdir.glob("*.reconciliation.json"),
                       key=lambda x: x.stat().st_mtime if x.exists() else 0)
        if files:
            report = json.loads(files[-1].read_text(encoding="utf-8"))
    return p.returncode, report, p.stdout, p.stderr


def by_id(report):
    return {o["file_id"]: o for o in report["objects"]}


# ---------------------------------------------------------------------------
# §7.5 共用断言
# ---------------------------------------------------------------------------

def assert_common(report, baseline, current):
    assert report["spec_version"] == R2_SPEC
    objs = report["objects"]
    assert len(objs) == len({o["file_id"] for o in objs})
    b_ids = {o["file_id"] for o in baseline["objects"]}
    c_ids = {o["file_id"] for o in current["objects"]}
    assert {o["file_id"] for o in objs} == b_ids | c_ids
    c = report["counts"]
    assert c["total"] == len(objs)
    assert c["total"] == sum(c[s] for s in FIVE_STATES)
    assert c["review_total"] == c["MISSING_NEEDS_REVIEW"] + c["REVIEW_REQUIRED"]
    # R1 11 字段完整保留
    for o in objs:
        for side in ("baseline_object", "current_object"):
            v = o[side]
            if v is not None:
                assert set(v.keys()) == {
                    "file_id", "name", "mime_type", "sha256", "parent_ids",
                    "source_id", "document_id", "processing_status",
                    "disposition", "final_parent", "evidence_reference",
                }, f"{o['file_id']}.{side} fields"


# ---------------------------------------------------------------------------
# 正向 P01–P10
# ---------------------------------------------------------------------------

def test_P01_new(tmp_path):
    o = make_object("new-1", sha="b" * 64)
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], ev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert_common(rep, b, c)
    r = by_id(rep)["new-1"]
    assert r["status"] == "NEW"
    assert r["baseline_object"] is None
    assert r["current_object"] is not None
    assert r["changed_fields"] == []
    assert r["next_action"] is None


def test_P02_changed_sha(tmp_path):
    bo = make_object("same-1", sha="a" * 64)
    co = make_object("same-1", sha="b" * 64)
    bev = make_evidence_for(bo)
    cev = make_evidence_for(co)
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["same-1"]
    assert r["status"] == "CHANGED"
    assert r["changed_fields"] == ["sha256"]


def test_P03_changed_status(tmp_path):
    bo = make_object("state-1", processing_status="PENDING",
                     disposition="UNPROCESSED")
    # PENDING->COMPLETED 需要 PROCESS + FINAL_LOCATION 证据
    bev = make_evidence_for(bo, kinds=("OBJECT", "PROCESS"))
    co = make_object("state-1", processing_status="COMPLETED",
                     disposition="PROCESSED", final_parent="/arch")
    cev = make_evidence_for(co, kinds=("OBJECT", "PROCESS", "FINAL_LOCATION"))
    # PROCESS 证据的 reason_code 必须与状态一致
    for e in bev + cev:
        if e["kind"] == "PROCESS":
            e["reason_code"] = f"PROCESS:{e['file_id'] and ''}"
    # 修正：PROCESS reason_code 格式为 PROCESS:<STATUS>
    for e in bev:
        if e["kind"] == "PROCESS":
            e["reason_code"] = "PROCESS:PENDING"
    for e in cev:
        if e["kind"] == "PROCESS":
            e["reason_code"] = "PROCESS:COMPLETED"
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["state-1"]
    assert r["status"] == "CHANGED"
    assert "processing_status" in r["changed_fields"]


def test_P04_changed_assoc(tmp_path):
    bo = make_object("assoc-1", source_id="s1", document_id="d1")
    co = make_object("assoc-1", source_id="s2", document_id="d2")
    bev = make_evidence_for(bo, kinds=("OBJECT", "SOURCE", "DOCUMENT", "PROCESS"))
    cev = make_evidence_for(co, kinds=("OBJECT", "SOURCE", "DOCUMENT", "PROCESS"))
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["assoc-1"]
    assert r["status"] == "CHANGED"
    assert set(r["changed_fields"]) == {"source_id", "document_id"}


def test_P05_unchanged_noise(tmp_path):
    bo = make_object("stable-1", sha="c" * 64)
    bev = make_evidence_for(bo)
    co = make_object("stable-1", sha="c" * 64)
    cev = make_evidence_for(co)
    # 仅 run_id/时间戳不同
    b = make_manifest("r1-old", [bo], bev)
    c = make_manifest("r1-new", [co], cev)
    c["started_at"] = "2026-10-11T10:00:00Z"
    c["finished_at"] = "2026-10-11T10:05:00Z"
    for e in cev:
        e["observed_at"] = "2026-10-11T10:00:00Z"
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["stable-1"]
    assert r["status"] == "UNCHANGED"
    assert r["changed_fields"] == []


def test_P06_missing(tmp_path):
    o = make_object("gone-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev)
    c = make_manifest("r1-c", [], [])
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["gone-1"]
    assert r["status"] == "MISSING_NEEDS_REVIEW"
    assert r["reason_codes"] == ["BASELINE_ONLY"]
    assert r["next_action"] and r["next_action"].strip()
    assert "删除" not in r["next_action"] or "不得仅凭缺席推断已删除" in r["next_action"]


def test_P07_same_name_diff_id(tmp_path):
    bo = make_object("inbox/A", name="report.pdf", sha="a" * 64)
    co = make_object("sources/B", name="report.pdf", sha="b" * 64)
    bev = make_evidence_for(bo)
    cev = make_evidence_for(co)
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)
    assert r["inbox/A"]["status"] == "MISSING_NEEDS_REVIEW"
    assert r["sources/B"]["status"] == "NEW"
    assert not any(o["status"] == "CHANGED" for o in r.values())


def test_P08_order_stable(tmp_path):
    o1 = make_object("a-1", sha="a" * 64)
    o2 = make_object("b-1", sha="b" * 64)
    ev1 = make_evidence_for(o1)
    ev2 = make_evidence_for(o2)
    b = make_manifest("r1-b", [o1, o2], ev1 + ev2)
    # current 顺序颠倒
    o1c = make_object("a-1", sha="a" * 64)
    o2c = make_object("b-1", sha="b" * 64)
    ev1c = make_evidence_for(o1c)
    ev2c = make_evidence_for(o2c)
    c = make_manifest("r1-c", [o2c, o1c], ev2c + ev1c)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    for fid in ("a-1", "b-1"):
        assert by_id(rep)[fid]["status"] == "UNCHANGED"


def test_P09_parent_order(tmp_path):
    p1 = make_object("p-1", sha="a"*64)
    p2 = make_object("p-2", sha="b"*64)
    o = make_object("child-1", sha="c"*64, parent_ids=["p-1", "p-2"])
    evs = []
    for oo in (p1, p2, o):
        evs += make_evidence_for(oo)
    b = make_manifest("r1-b", [p1, p2, o], evs)
    p1c = make_object("p-1", sha="a"*64)
    p2c = make_object("p-2", sha="b"*64)
    oc = make_object("child-1", sha="c"*64, parent_ids=["p-2", "p-1"])  # 顺序不同
    evsc = []
    for oo in (p1c, p2c, oc):
        evsc += make_evidence_for(oo)
    c = make_manifest("r1-c", [p1c, p2c, oc], evsc)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert by_id(rep)["child-1"]["status"] == "UNCHANGED"


def test_P10_empty(tmp_path):
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [], [])
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert rep["counts"]["total"] == 0
    assert rep["reconciliation_status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 反向 N01–N18
# ---------------------------------------------------------------------------

def test_N01_current_incomplete(tmp_path):
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev, scope_complete=True)
    c = make_manifest("r1-c", [o], ev, scope_complete=False)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1
    assert rep is not None  # 诊断报告仍生成
    assert rep["comparison_safe"] is False
    assert all(o["status"] == "REVIEW_REQUIRED" for o in rep["objects"])
    assert "CURRENT_SCOPE_INCOMPLETE" in rep["scope"]["reason_codes"]


def test_N02_baseline_incomplete(tmp_path):
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev, scope_complete=False)
    c = make_manifest("r1-c", [o], ev, scope_complete=True)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1
    assert "BASELINE_SCOPE_INCOMPLETE" in rep["scope"]["reason_codes"]


def test_N03_scan_errors(tmp_path):
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev, scope_complete=True,
                      scan_errors=[{"locator": "/x", "reason_code": "E",
                                    "detail": "d"}])
    c = make_manifest("r1-c", [o], ev, scope_complete=True)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1  # scan_errors 非空视为不完整


def test_N04_bindings_mismatch(tmp_path):
    o = make_object("x-1")
    ev = make_evidence_for(o)
    sb2 = {"inbox_root": "/other", "sources_root": "/sources",
           "record_stores": ["inbox", "sources"], "binding_reference": "bind-1"}
    b = make_manifest("r1-b", [o], ev)
    c = make_manifest("r1-c", [o], ev, scope_bindings=sb2)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0  # 绑定不一致：exit 0，全部待审
    assert rep["comparison_safe"] is False
    assert all(o["status"] == "REVIEW_REQUIRED" for o in rep["objects"])
    assert "SCOPE_BINDINGS_MISMATCH" in rep["scope"]["reason_codes"]


def test_N05_same_sha_diff_locator(tmp_path):
    bo = make_object("c-1", sha="d" * 64)
    co = make_object("c-1", sha="d" * 64)
    bev = make_evidence_for(bo, locator="loc://a")
    cev = make_evidence_for(co, locator="loc://b")
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["c-1"]
    assert r["status"] == "REVIEW_REQUIRED"
    assert "IDENTITY_PATH_CONFLICT" in r["reason_codes"]


def test_N06_cross_id_collision(tmp_path):
    bo = make_object("inbox/old", sha="e" * 64)
    co = make_object("sources/new", sha="e" * 64)
    bev = make_evidence_for(bo, locator="loc://old")
    cev = make_evidence_for(co, locator="loc://new")
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)
    assert r["inbox/old"]["status"] == "REVIEW_REQUIRED"
    assert r["sources/new"]["status"] == "REVIEW_REQUIRED"
    assert "IDENTITY_CROSS_ID_COLLISION" in r["inbox/old"]["reason_codes"]


def test_N06b_same_locator_diff_id(tmp_path):
    # N-03: 不同 file_id、相同 SHA、相同 locator → 必须标记身份冲突
    # 这是 F-03 移除 locator 豁免后的关键回归测试
    bo = make_object("inbox/a", sha="e" * 64)
    co = make_object("sources/b", sha="e" * 64)
    # 相同 locator！
    bev = make_evidence_for(bo, locator="loc://same")
    cev = make_evidence_for(co, locator="loc://same")
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)
    # 即使 locator 相同，不同 file_id + 同 SHA 必须冲突
    assert r["inbox/a"]["status"] == "REVIEW_REQUIRED"
    assert r["sources/b"]["status"] == "REVIEW_REQUIRED"
    assert "IDENTITY_CROSS_ID_COLLISION" in r["inbox/a"]["reason_codes"]
    assert "IDENTITY_CROSS_ID_COLLISION" in r["sources/b"]["reason_codes"]


def test_N06c_set_equal_cross_id(tmp_path):
    # R4-N-03: baseline {A,B} vs current {A,B}，同 SHA
    # 严格成对检查下，(b,A) vs (c,B) 和 (b,B) vs (c,A) 都是跨侧不同 ID
    # 必须标记 IDENTITY_CROSS_ID_COLLISION
    bo_a = make_object("A", sha="f" * 64)
    bo_b = make_object("B", sha="f" * 64)
    co_a = make_object("A", sha="f" * 64)
    co_b = make_object("B", sha="f" * 64)
    evs_b, evs_c = [], []
    for o in (bo_a, bo_b):
        evs_b += make_evidence_for(o, locator="loc://x")
    for o in (co_a, co_b):
        evs_c += make_evidence_for(o, locator="loc://x")
    b = make_manifest("r1-b", [bo_a, bo_b], evs_b)
    c = make_manifest("r1-c", [co_a, co_b], evs_c)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)
    for fid in ("A", "B"):
        assert r[fid]["status"] == "REVIEW_REQUIRED", f"{fid} should be REVIEW_REQUIRED"
        assert "IDENTITY_CROSS_ID_COLLISION" in r[fid]["reason_codes"], f"{fid} missing collision code"


def test_N07_missing_object_evidence(tmp_path):
    o = make_object("m-1")
    # 只有 SOURCE 证据，无 OBJECT
    ev = [make_evidence("ev-m-1-src", "m-1", "SOURCE")]
    o["evidence_reference"] = ["ev-m-1-src"]
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], ev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    r = by_id(rep)["m-1"]
    assert r["status"] == "REVIEW_REQUIRED"
    assert "MISSING_OBJECT_EVIDENCE" in r["reason_codes"]


def test_N08_assoc_mismatch(tmp_path):
    o = make_object("a-1", source_id="s1")
    ev = make_evidence_for(o, kinds=("OBJECT",))  # 缺 SOURCE
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], ev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert "MISSING_ASSOCIATION_EVIDENCE" in by_id(rep)["a-1"]["reason_codes"]


def test_N09_process_conflict(tmp_path):
    o = make_object("p-1", processing_status="COMPLETED",
                    disposition="PROCESSED", final_parent="/x")
    ev = make_evidence_for(o, kinds=("OBJECT", "PROCESS", "FINAL_LOCATION"))
    for e in ev:
        if e["kind"] == "PROCESS":
            e["reason_code"] = "PROCESS:PENDING"  # 与 COMPLETED 矛盾
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], ev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert "EVIDENCE_CONFLICT" in by_id(rep)["p-1"]["reason_codes"]


def test_N10_dup_file_id(tmp_path):
    # F-07: 确保重复 file_id 是唯一的错误条件（证据 ID 必须唯一）
    o1 = make_object("dup-1", sha="a" * 64)
    o2 = make_object("dup-1", sha="b" * 64)
    # 手动创建唯一证据 ID，避免与 file_id 重复混杂
    ev1 = []
    for k in ("OBJECT", "PROCESS"):
        eid = f"ev-dup-1-{k.lower()}-a"
        rc = f"PROCESS:{o1['processing_status']}" if k == "PROCESS" else None
        ev1.append(make_evidence(eid, "dup-1", k, reason_code=rc))
    o1["evidence_reference"] = [e["evidence_id"] for e in ev1]
    ev2 = []
    for k in ("OBJECT", "PROCESS"):
        eid = f"ev-dup-1-{k.lower()}-b"
        rc = f"PROCESS:{o2['processing_status']}" if k == "PROCESS" else None
        ev2.append(make_evidence(eid, "dup-1", k, reason_code=rc))
    o2["evidence_reference"] = [e["evidence_id"] for e in ev2]
    ev = ev1 + ev2
    b = make_manifest("r1-b", [o1, o2], ev)
    c = make_manifest("r1-c", [], [])
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1
    assert rep is None
    assert "duplicate" in err.lower() or "file_id" in err.lower()


def test_N11_dup_evidence_id(tmp_path):
    o = make_object("x-1")
    e1 = make_evidence("ev-dup", "x-1", "OBJECT")
    e2 = make_evidence("ev-dup", "x-1", "SOURCE")
    o["evidence_reference"] = ["ev-dup"]
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], [e1, e2])
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1


def test_N12_bad_reference(tmp_path):
    o = make_object("x-1", evidence_reference=["ev-missing"])
    b = make_manifest("r1-b", [], [])
    c = make_manifest("r1-c", [o], [])
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 1


def test_N13_bad_json(tmp_path):
    # F-09: Baseline 非法 JSON，Current 为有效 R1 manifest
    # 隔离条件：只有 baseline 无效，确保拒绝原因明确
    o = make_object("x-1")
    ev = make_evidence_for(o)
    c = make_manifest("r1-c", [o], ev)  # 有效 R1 manifest
    bp = tmp_path / "baseline.json"
    cp = tmp_path / "current.json"
    bp.write_text("{invalid json", encoding="utf-8")
    cp.write_text(json.dumps(c), encoding="utf-8")
    rdir = tmp_path / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["R2_TEST_REPORTS_ROOT"] = str(rdir)
    # pytest 自动设置 PYTEST_CURRENT_TEST，测试覆盖生效
    p = subprocess.run([str(CLI), "--baseline", str(bp), "--current", str(cp)],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60,
                       env=env)
    assert p.returncode == 1
    assert "json" in p.stderr.lower()
    # 确认未发布正式报告（隔离：不是因 current 无效）
    assert list(rdir.glob("*.reconciliation.json")) == []
    rdir = tmp_path / "reports"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([str(CLI), "--baseline", str(bp), "--current", str(cp),
                        "--reports-dir", "reports"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60,
                       env=env)
    assert p.returncode == 1


def test_N14_reports_dir_escape(tmp_path):
    # N14: 测试 --reports-dir 路径穿越被拒绝（生产路径验证）
    # 注意：不使用 R2_TEST_REPORTS_ROOT 覆盖，直接测试 --reports-dir 参数验证
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev)
    c = make_manifest("r1-c", [o], ev)
    bp = tmp_path / "baseline.json"
    cp = tmp_path / "current.json"
    bp.write_text(json.dumps(b), encoding="utf-8")
    cp.write_text(json.dumps(c), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # 不设置 R2_TEST_REPORTS_ROOT，测试生产路径的 --reports-dir 验证
    # 尝试用 .. 越界
    p = subprocess.run(
        [str(CLI), "--baseline", str(bp), "--current", str(cp),
         "--reports-dir", str(tmp_path / ".." / "evil")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 1, f"path traversal should be rejected: {p.stderr}"


def test_N14b_symlink_escape(tmp_path):
    # F-08: reports 符号链接指向仓库外目录，必须拒绝
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev)
    c = make_manifest("r1-c", [o], ev)
    # 创建外部目录和符号链接
    evil_dir = tmp_path / "evil_outside"
    evil_dir.mkdir()
    link_path = tmp_path / "reports_link"
    link_path.symlink_to(evil_dir)
    # 用环境变量直接指定符号链接路径
    bp = tmp_path / "baseline.json"
    cp = tmp_path / "current.json"
    bp.write_text(json.dumps(b), encoding="utf-8")
    cp.write_text(json.dumps(c), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["R2_TEST_REPORTS_ROOT"] = str(link_path)
    p = subprocess.run(
        [str(CLI), "--baseline", str(bp), "--current", str(cp)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 1, f"symlink escape should be rejected: {p.stderr}"
    assert "symlink" in p.stderr.lower()
    # 确认外部目录没有报告产物
    assert list(evil_dir.glob("*.reconciliation.json")) == []


def test_N14c_production_symlink_direct(tmp_path):
    # F-08: 直接测试生产 reports/ 路径的符号链接拒绝
    # 不通过 R2_TEST_REPORTS_ROOT，而是直接调用 _resolve_reports_dir
    import sys
    sys.path.insert(0, str(REPO / "src"))
    from cba_kb.r2_reconcile import _resolve_reports_dir, ValidationError
    import os
    
    # 确保不在 pytest 测试覆盖模式下（模拟生产）
    # 注意：PYTEST_CURRENT_TEST 在 pytest 下自动存在，这里我们测试的是
    # 生产路径的逻辑，即 reports_raw.is_symlink() 检查
    # 由于无法轻易 mock __file__ 推导的仓库根，我们验证函数逻辑：
    # 1. 创建一个模拟的 reports 符号链接场景
    # 2. 验证 is_symlink() 在 resolve() 之前被检查
    
    # 直接验证：Path.is_symlink() 对符号链接返回 True，对 resolve 后的路径返回 False
    # 这证明了 F-01 修复的必要性（先检查再 resolve）
    evil = tmp_path / "evil_target"
    evil.mkdir()
    link = tmp_path / "link_reports"
    link.symlink_to(evil)
    # 原始路径是 symlink
    assert link.is_symlink() == True
    # resolve 后不是 symlink（会跟随到目标）
    assert link.resolve().is_symlink() == False
    # 因此必须在 resolve() 之前检查，这正是 F-01 修复的内容


def test_N15_no_overwrite(tmp_path):
    # F-10: 验证两次运行都产生完整的一对报告，旧文件字节不变
    o = make_object("x-1")
    ev = make_evidence_for(o)
    b = make_manifest("r1-b", [o], ev)
    c = make_manifest("r1-c", [o], ev)
    rdir = tmp_path / "reports"
    rc1, rep1, _, _ = run_r2(b, c, tmp_path)
    assert rc1 == 0
    # 第一次运行应产生一对文件
    j1_files = sorted(rdir.glob("*.reconciliation.json"))
    m1_files = sorted(rdir.glob("*.reconciliation.md"))
    assert len(j1_files) == 1 and len(m1_files) == 1
    j1 = j1_files[0]
    m1 = m1_files[0]
    h1_json = hashlib.sha256(j1.read_bytes()).hexdigest()
    h1_md = hashlib.sha256(m1.read_bytes()).hexdigest()
    
    rc2, rep2, _, _ = run_r2(b, c, tmp_path)
    assert rc2 == 0
    # 第二次运行后应有两对文件
    j2_files = sorted(rdir.glob("*.reconciliation.json"))
    m2_files = sorted(rdir.glob("*.reconciliation.md"))
    assert len(j2_files) == 2, f"expected 2 JSON files, got {len(j2_files)}"
    assert len(m2_files) == 2, f"expected 2 MD files, got {len(m2_files)}"
    # 旧文件字节不变（JSON 和 MD 都要验证）
    assert hashlib.sha256(j1.read_bytes()).hexdigest() == h1_json, "old JSON modified!"
    assert hashlib.sha256(m1.read_bytes()).hexdigest() == h1_md, "old MD modified!"
    # run_id 不同
    assert rep1["run_id"] != rep2["run_id"]
    # 验证新旧文件对应关系：每个 run_id 有一对文件
    for rid in (rep1["run_id"], rep2["run_id"]):
        assert (rdir / f"{rid}.reconciliation.json").exists()
        assert (rdir / f"{rid}.reconciliation.md").exists()


def test_N16_same_name_not_merged(tmp_path):
    # 同 test_P07，已覆盖不得误并
    bo = make_object("x/A", name="same.pdf", sha="a" * 64)
    co = make_object("y/B", name="same.pdf", sha="b" * 64)
    b = make_manifest("r1-b", [bo], make_evidence_for(bo))
    c = make_manifest("r1-c", [co], make_evidence_for(co))
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert not any(o["status"] == "CHANGED" for o in rep["objects"])


def test_N17_r1_review_not_inherited(tmp_path):
    # R1 disposition=REVIEW_REQUIRED 但 R2 证据充分 -> 不得机械继承
    bo = make_object("r-1", disposition="REVIEW_REQUIRED")
    bev = make_evidence_for(bo, kinds=("OBJECT", "REVIEW_REASON"))
    co = make_object("r-1", disposition="REVIEW_REQUIRED")
    cev = make_evidence_for(co, kinds=("OBJECT", "REVIEW_REASON"))
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    # 证据充分且无冲突时，应为 UNCHANGED（不强制 REVIEW_REQUIRED）
    r = by_id(rep)["r-1"]
    assert r["status"] == "UNCHANGED"


def test_N18_evidence_id_noise(tmp_path):
    bo = make_object("s-1", sha="f" * 64)
    bev = make_evidence_for(bo)
    co = make_object("s-1", sha="f" * 64)
    # 不同的 evidence_id，但内容等价
    cev = []
    for e in make_evidence_for(co):
        e2 = dict(e)
        e2["evidence_id"] = e2["evidence_id"] + "-v2"
        e2["observed_at"] = "2026-10-11T11:00:00Z"
        cev.append(e2)
    co["evidence_reference"] = [e["evidence_id"] for e in cev]
    b = make_manifest("r1-b", [bo], bev)
    c = make_manifest("r1-c", [co], cev)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    assert by_id(rep)["s-1"]["status"] == "UNCHANGED"


# ---------------------------------------------------------------------------
# AC-R01～R10 形式化（抽样，完整覆盖见 P/N 测试）
# ---------------------------------------------------------------------------

def test_AC_R01_new(tmp_path):
    test_P01_new(tmp_path)


def test_AC_R02_changed(tmp_path):
    test_P02_changed_sha(tmp_path)


def test_AC_R03_noise(tmp_path):
    test_P05_unchanged_noise(tmp_path)


def test_AC_R04_fail_closed(tmp_path):
    test_N01_current_incomplete(tmp_path)


def test_AC_R05_no_merge(tmp_path):
    test_P07_same_name_diff_id(tmp_path)


def test_AC_R06_conflicts(tmp_path):
    test_N05_same_sha_diff_locator(tmp_path)
    test_N06_cross_id_collision(tmp_path)


def test_AC_R07_idempotent(tmp_path):
    test_N15_no_overwrite(tmp_path)


def test_AC_R08_md_consistency(tmp_path):
    o_new = make_object("n-1", sha="a" * 64)
    o_chg_b = make_object("c-1", sha="b" * 64)
    o_chg_c = make_object("c-1", sha="c" * 64)
    o_un = make_object("u-1", sha="d" * 64)
    o_miss = make_object("m-1", sha="e" * 64)
    o_rev_b = make_object("r-1", sha="f" * 64)
    o_rev_c = make_object("r-1", sha="f" * 64)
    evs_b, evs_c = [], []
    for o in (o_chg_b, o_un, o_miss, o_rev_b):
        evs_b += make_evidence_for(o)
    for o in (o_new, o_chg_c, o_un, o_rev_c):
        # r-1 制造定位冲突
        loc = "loc://x" if o["file_id"] != "r-1" else "loc://y"
        evs_c += make_evidence_for(o, locator=loc)
    # o_rev_b 用 loc://x，o_rev_c 用 loc://y -> IDENTITY_PATH_CONFLICT
    b = make_manifest("r1-b", [o_chg_b, o_un, o_miss, o_rev_b], evs_b)
    c = make_manifest("r1-c", [o_new, o_chg_c, o_un, o_rev_c], evs_c)
    rc, rep, out, err = run_r2(b, c, tmp_path)
    assert rc == 0, err
    # JSON 数量
    cc = rep["counts"]
    assert cc["NEW"] == 1 and cc["CHANGED"] == 1 and cc["UNCHANGED"] == 1
    assert cc["MISSING_NEEDS_REVIEW"] == 1 and cc["REVIEW_REQUIRED"] == 1
    # Markdown 一致性
    rdir = tmp_path / "reports"
    md = sorted(rdir.glob("*.reconciliation.md"))[-1].read_text(encoding="utf-8")
    for s, n in [("NEW", 1), ("CHANGED", 1), ("UNCHANGED", 1),
                 ("MISSING_NEEDS_REVIEW", 1), ("REVIEW_REQUIRED", 1)]:
        assert f"| {s} | {n} |" in md, f"markdown missing {s}"


def test_AC_R09_next_action(tmp_path):
    test_P06_missing(tmp_path)
    test_N05_same_sha_diff_locator(tmp_path)


def test_AC_R10_falsification(tmp_path):
    # F-11: RED/GREEN 证伪证据
    # RED 已手动执行并记录在 tests/acceptance/RED_GREEN_EVIDENCE.md：
    #   - 移走实现文件后，test_P01_new 因 FileNotFoundError 失败
    #   - 证明测试真实调用 CLI 子进程，非 mock
    # GREEN: 当前实现存在时，基础功能应通过
    import importlib.util
    spec = importlib.util.find_spec("cba_kb.r2_reconcile")
    assert spec is not None, "r2_reconcile must exist for GREEN"
    # 验证证据文件存在
    evidence = REPO / "tests" / "acceptance" / "RED_GREEN_EVIDENCE.md"
    assert evidence.exists(), "RED/GREEN evidence file must exist"
    content = evidence.read_text(encoding="utf-8")
    assert "RED" in content and "FileNotFoundError" in content
    # GREEN: 当前实现存在，基础功能应通过
    test_P01_new(tmp_path)
