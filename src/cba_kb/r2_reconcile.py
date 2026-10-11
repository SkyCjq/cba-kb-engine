"""2.0.3-3 R2 — 两份 R1 Manifest 只读差异对账.

实现 Q1 冻结规格 ``2.0.3-3-R2-Q1``：
输入两份 R1 manifest（baseline/current），输出五态对账报告
``reconciliation.json`` + ``reconciliation.md``，仅写授权 ``reports/`` 目录.

规格要点（实现必须遵守）：
- 只读：不修改 Drive/Inbox/Source Registry/输入 manifest；
- 对账主键 = file_id（精确字符串匹配）；
- 五态：NEW / CHANGED / UNCHANGED / MISSING_NEEDS_REVIEW / REVIEW_REQUIRED；
- 缺席 != 删除；同 SHA 异路径 -> REVIEW_REQUIRED（人工裁决）；
- 范围不完整 -> fail-closed（exit 1）；
- 唯一允许的写入是 reports/ 下本任务产物.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

SPEC_VERSION = "2.0.3-3-R2-Q1"
R1_SPEC_VERSION = "2.0.3-2-R1-Q1"

FIVE_STATES = (
    "NEW",
    "CHANGED",
    "UNCHANGED",
    "MISSING_NEEDS_REVIEW",
    "REVIEW_REQUIRED",
)

# §4.2: 9 个实质业务字段（按规格顺序）
MATERIAL_FIELDS = (
    "name",
    "mime_type",
    "sha256",
    "parent_ids",
    "source_id",
    "document_id",
    "processing_status",
    "disposition",
    "final_parent",
)

# §4.9: 12 个冻结原因码
REASON_CODES = (
    "SCOPE_INCOMPLETE",
    "SCOPE_MISMATCH",
    "IDENTITY_PATH_CONFLICT",
    "IDENTITY_CROSS_ID_COLLISION",
    "MISSING_OBJECT_EVIDENCE",
    "MISSING_ASSOCIATION_EVIDENCE",
    "MISSING_PROCESS_EVIDENCE",
    "MISSING_FINAL_LOCATION_EVIDENCE",
    "MISSING_REVIEW_REASON",
    "EVIDENCE_CONFLICT",
    "BASELINE_ONLY",
    "SOURCE_REVIEW_REQUIRED",
)

R1_TOP_FIELDS = (
    "spec_version", "run_id", "backend", "trigger",
    "started_at", "finished_at", "scope_complete",
    "scope_bindings", "scan_errors", "objects", "evidence",
)

R1_OBJECT_FIELDS = (
    "file_id", "name", "mime_type", "sha256", "parent_ids",
    "source_id", "document_id", "processing_status",
    "disposition", "final_parent", "evidence_reference",
)

R1_EVIDENCE_FIELDS = (
    "evidence_id", "file_id", "kind", "locator",
    "observed_at", "reason_code", "detail",
)


class ValidationError(Exception):
    """输入验证失败（exit 1，不发布正式报告）。"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strict_json_loads(text: str):
    """严格 JSON 解析：拒绝重复 key。"""
    def _reject_dup(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                raise ValidationError(f"duplicate JSON key: {k!r}")
            seen.add(k)
        return dict(pairs)
    try:
        return json.loads(text, object_pairs_hook=_reject_dup)
    except json.JSONDecodeError as e:
        raise ValidationError(f"invalid JSON: {e}")


def _is_tz_aware(s: str) -> bool:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.tzinfo is not None
    except Exception:
        return False


def validate_r1_manifest(data, label: str) -> dict:
    """§2.1: R1 manifest 最低验证。返回规范化后的 manifest 摘要。"""
    if not isinstance(data, dict):
        raise ValidationError(f"{label}: top level must be object")
    for f in R1_TOP_FIELDS:
        if f not in data:
            raise ValidationError(f"{label}: missing top field {f!r}")
    if data["spec_version"] != R1_SPEC_VERSION:
        raise ValidationError(f"{label}: spec_version must be {R1_SPEC_VERSION!r}")
    if not isinstance(data["run_id"], str) or not data["run_id"]:
        raise ValidationError(f"{label}: run_id must be non-empty string")
    if not isinstance(data["scope_complete"], bool):
        raise ValidationError(f"{label}: scope_complete must be boolean")
    for f in ("scan_errors", "objects", "evidence"):
        if not isinstance(data[f], list):
            raise ValidationError(f"{label}: {f} must be array")
    sb = data["scope_bindings"]
    if not isinstance(sb, dict):
        raise ValidationError(f"{label}: scope_bindings must be object")
    for f in ("inbox_root", "sources_root", "record_stores", "binding_reference"):
        if f not in sb:
            raise ValidationError(f"{label}: scope_bindings missing {f!r}")
    if not isinstance(sb["record_stores"], list):
        raise ValidationError(f"{label}: record_stores must be array")
    if len(set(sb["record_stores"])) != len(sb["record_stores"]):
        raise ValidationError(f"{label}: record_stores must not contain duplicates")

    # objects
    seen_fid = set()
    for i, o in enumerate(data["objects"]):
        if not isinstance(o, dict):
            raise ValidationError(f"{label}: objects[{i}] must be object")
        if set(o.keys()) != set(R1_OBJECT_FIELDS):
            raise ValidationError(
                f"{label}: objects[{i}] must have exactly 11 R1 fields, "
                f"got {sorted(o.keys())}")
        fid = o["file_id"]
        if not isinstance(fid, str) or not fid:
            raise ValidationError(f"{label}: objects[{i}].file_id must be non-empty string")
        if fid in seen_fid:
            raise ValidationError(f"{label}: duplicate file_id {fid!r}")
        seen_fid.add(fid)
        if not isinstance(o["parent_ids"], list) or not isinstance(o["evidence_reference"], list):
            raise ValidationError(f"{label}: objects[{i}] parent_ids/evidence_reference must be arrays")
        sha = o["sha256"]
        if sha is not None and not (
                isinstance(sha, str) and len(sha) == 64
                and all(c in "0123456789abcdefABCDEF" for c in sha)):
            raise ValidationError(f"{label}: objects[{i}].sha256 must be null or 64-hex")
        for f in ("source_id", "document_id", "final_parent"):
            v = o[f]
            if v is not None and not isinstance(v, str):
                raise ValidationError(f"{label}: objects[{i}].{f} must be string or null")
        if o["processing_status"] not in (
                "PENDING", "PROCESSING", "COMPLETED", "FAILED", "UNKNOWN"):
            raise ValidationError(f"{label}: objects[{i}].processing_status invalid")
        if o["disposition"] not in ("UNPROCESSED", "PROCESSED", "REVIEW_REQUIRED"):
            raise ValidationError(f"{label}: objects[{i}].disposition invalid")
        # F-05: R1 状态约束验证
        if o["disposition"] == "PROCESSED":
            if o["processing_status"] != "COMPLETED":
                raise ValidationError(
                    f"{label}: objects[{i}].disposition=PROCESSED requires "
                    f"processing_status=COMPLETED, got {o['processing_status']}")
            if not o["final_parent"]:
                raise ValidationError(
                    f"{label}: objects[{i}].disposition=PROCESSED requires "
                    f"non-null final_parent")
    # parent_ids 必须可解析、无自引用、无循环（§2.1.16）
    for o in data["objects"]:
        fid = o["file_id"]
        for p in o["parent_ids"]:
            if p == fid:
                raise ValidationError(f"{label}: {fid!r} self-reference in parent_ids")
            if p not in seen_fid:
                raise ValidationError(f"{label}: {fid!r} parent {p!r} not found")
    # 简单循环检测（DFS）
    _check_no_cycles(data["objects"], label)

    # evidence
    seen_eid = set()
    ev_by_id = {}
    for i, e in enumerate(data["evidence"]):
        if not isinstance(e, dict):
            raise ValidationError(f"{label}: evidence[{i}] must be object")
        if set(e.keys()) != set(R1_EVIDENCE_FIELDS):
            raise ValidationError(f"{label}: evidence[{i}] must have 7 R1 fields")
        eid = e["evidence_id"]
        if not isinstance(eid, str) or not eid:
            raise ValidationError(f"{label}: evidence[{i}].evidence_id invalid")
        if eid in seen_eid:
            raise ValidationError(f"{label}: duplicate evidence_id {eid!r}")
        seen_eid.add(eid)
        if e["kind"] not in (
                "OBJECT", "SOURCE", "DOCUMENT", "PROCESS",
                "FINAL_LOCATION", "REVIEW_REASON"):
            raise ValidationError(f"{label}: evidence[{i}].kind invalid")
        if not isinstance(e["locator"], str) or not e["locator"]:
            raise ValidationError(f"{label}: evidence[{i}].locator invalid")
        if not _is_tz_aware(e["observed_at"]):
            raise ValidationError(f"{label}: evidence[{i}].observed_at must be tz-aware")
        ev_by_id[eid] = e
    # 对象引用的证据必须存在且 file_id 一致（§2.1.12）
    for o in data["objects"]:
        for eid in o["evidence_reference"]:
            if eid not in ev_by_id:
                raise ValidationError(
                    f"{label}: {o['file_id']!r} references missing evidence {eid!r}")
            if ev_by_id[eid]["file_id"] != o["file_id"]:
                raise ValidationError(
                    f"{label}: evidence {eid!r} file_id mismatch for {o['file_id']!r}")
    # 时间戳带时区（§2.1.18）
    for f in ("started_at", "finished_at"):
        if not _is_tz_aware(data[f]):
            raise ValidationError(f"{label}: {f} must be tz-aware")
    return data


def _check_no_cycles(objects, label: str):
    children = {}
    for o in objects:
        children.setdefault(o["file_id"], [])
        for p in o["parent_ids"]:
            children.setdefault(p, []).append(o["file_id"])
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {fid: WHITE for fid in children}
    def dfs(u, stack):
        color[u] = GRAY
        for v in children.get(u, []):
            if color[v] == GRAY:
                raise ValidationError(f"{label}: parent cycle detected at {v!r}")
            if color[v] == WHITE:
                dfs(v, stack + [v])
        color[u] = BLACK
    for fid in children:
        if color[fid] == WHITE:
            dfs(fid, [fid])


def _object_locators(obj, ev_by_id) -> list[str]:
    """返回该对象所有 OBJECT 证据的 locator。"""
    out = []
    for eid in obj["evidence_reference"]:
        e = ev_by_id.get(eid)
        if e and e["kind"] == "OBJECT":
            out.append(e["locator"])
    return out


def validate_object_evidence(obj, ev_by_id) -> set[str]:
    """§2.4: 必要证据检查。返回原因码集合（空=通过）。"""
    reasons = set()
    eids = obj["evidence_reference"]
    kinds = {ev_by_id[e]["kind"] for e in eids if e in ev_by_id}
    if "OBJECT" not in kinds:
        reasons.add("MISSING_OBJECT_EVIDENCE")
    if obj["source_id"] is not None and "SOURCE" not in kinds:
        reasons.add("MISSING_ASSOCIATION_EVIDENCE")
    if obj["document_id"] is not None and "DOCUMENT" not in kinds:
        reasons.add("MISSING_ASSOCIATION_EVIDENCE")
    if obj["disposition"] == "PROCESSED":
        if "PROCESS" not in kinds:
            reasons.add("MISSING_PROCESS_EVIDENCE")
        if "FINAL_LOCATION" not in kinds:
            reasons.add("MISSING_FINAL_LOCATION_EVIDENCE")
    elif obj["disposition"] == "UNPROCESSED":
        if "PROCESS" not in kinds:
            reasons.add("MISSING_PROCESS_EVIDENCE")
    elif obj["disposition"] == "REVIEW_REQUIRED":
        if "REVIEW_REASON" not in kinds:
            reasons.add("MISSING_REVIEW_REASON")
    return reasons


def detect_evidence_conflicts(obj, ev_by_id) -> set[str]:
    """§4.11: 显式证据矛盾检测（不做开放式推测）。"""
    reasons = set()
    locs = _object_locators(obj, ev_by_id)
    if len(set(locs)) > 1:
        reasons.add("EVIDENCE_CONFLICT")
    # PROCESS 声明的状态与对象 processing_status 矛盾（固定模式识别）
    for eid in obj["evidence_reference"]:
        e = ev_by_id.get(eid)
        if not e:
            continue
        if e["kind"] == "PROCESS" and e.get("reason_code"):
            rc = e["reason_code"]
            # 固定模式：PROCESS:<STATUS>
            if rc.startswith("PROCESS:"):
                stated = rc.split(":", 1)[1]
                if stated in ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "UNKNOWN"):
                    if stated != obj["processing_status"]:
                        reasons.add("EVIDENCE_CONFLICT")
    return reasons


def find_identity_conflicts(b_objs, c_objs, b_ev, c_ev) -> dict[str, set[str]]:
    """§4.3: 身份冲突检测。返回 {file_id: {原因码}}。"""
    out: dict[str, set[str]] = {}
    def add(fid, code):
        out.setdefault(fid, set()).add(code)

    # 同 ID：同 SHA（非 null）但定位器不同 -> IDENTITY_PATH_CONFLICT
    for fid in set(b_objs) & set(c_objs):
        bo, co = b_objs[fid], c_objs[fid]
        bs, cs = bo["sha256"], co["sha256"]
        if bs is not None and cs is not None and bs.lower() == cs.lower():
            bl = set(_object_locators(bo, b_ev))
            cl = set(_object_locators(co, c_ev))
            if bl and cl and bl != cl:
                add(fid, "IDENTITY_PATH_CONFLICT")
            elif set(bo["parent_ids"]) != set(co["parent_ids"]):
                add(fid, "IDENTITY_PATH_CONFLICT")
    # 跨 ID：同 SHA（非 null）、异定位器/父级、不能完整对应
    # 收集所有 (sha -> [(side, fid)])
    from collections import defaultdict
    sha_map: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for fid, o in b_objs.items():
        if o["sha256"]:
            sha_map[o["sha256"].lower()].append(("b", fid))
    for fid, o in c_objs.items():
        if o["sha256"]:
            sha_map[o["sha256"].lower()].append(("c", fid))
    for sha, lst in sha_map.items():
        sides = {s for s, _ in lst}
        if "b" not in sides or "c" not in sides:
            continue
        # F-03: 不同 file_id + 同 SHA = 身份冲突，必须人工复核
        # file_id 是主键；两个不同主键对应相同内容哈希，本质上可疑
        fids_b = {f for s, f in lst if s == "b"}
        fids_c = {f for s, f in lst if s == "c"}
        # 如果两侧的 file_id 集合不完全相同（存在跨 ID），则标记冲突
        if fids_b != fids_c:
            for s, fid in lst:
                add(fid, "IDENTITY_CROSS_ID_COLLISION")
        # 注意：同 file_id 的情况已在 IDENTITY_PATH_CONFLICT 中处理
    return out


def material_value(field, value):
    if field == "parent_ids":
        return frozenset(value or [])
    if field == "sha256" and value is not None:
        return value.lower()
    return value


def material_diff(old, new) -> list[str]:
    """§4.2: 按 MATERIAL_FIELDS 顺序比较，返回差异字段列表。"""
    changed = []
    for f in MATERIAL_FIELDS:
        if material_value(f, old[f]) != material_value(f, new[f]):
            changed.append(f)
    return changed


def _evidence_bundle(obj, ev_by_id):
    if obj is None:
        return None
    refs = obj["evidence_reference"]
    return {
        "references": sorted(refs),
        "records": [ev_by_id[e] for e in sorted(refs) if e in ev_by_id],
    }


REASON_ACTIONS = {
    "SCOPE_INCOMPLETE": "Human：核查扫描错误、补齐范围并重跑 R1。",
    "SCOPE_MISMATCH": "Human：确认两次盘点是否针对同一授权范围。",
    "IDENTITY_PATH_CONFLICT": "Human：核查是否复制、移动或定位记录冲突。",
    "IDENTITY_CROSS_ID_COLLISION": "Human：核实是否独立副本、改名或移动，不得自动合并。",
    "MISSING_OBJECT_EVIDENCE": "Human：补充可信对象定位证据。",
    "MISSING_ASSOCIATION_EVIDENCE": "Human：核对关联记录和证据。",
    "MISSING_PROCESS_EVIDENCE": "Human：核实处理状态来源。",
    "MISSING_FINAL_LOCATION_EVIDENCE": "Human：核实最终位置声明。",
    "MISSING_REVIEW_REASON": "Human：检查原始待审原因。",
    "EVIDENCE_CONFLICT": "Human：查阅两侧原始证据，判断冲突来源。",
    "BASELINE_ONLY": ("Human：核查当前 R1 扫描范围、对象定位器和关联记录，"
                      "确认对象为何未出现在 current；不得仅凭缺席推断已删除，"
                      "必要时重新执行 R1 盘点。"),
    "SOURCE_REVIEW_REQUIRED": "Human：继续核查原 R1 待审原因。",
}


def build_human_action(status: str, reasons: set[str]) -> str | None:
    if status not in ("MISSING_NEEDS_REVIEW", "REVIEW_REQUIRED"):
        return None
    parts = [REASON_ACTIONS[r] for r in sorted(reasons) if r in REASON_ACTIONS]
    return " ".join(parts) if parts else "Human：核查该对象。" 


def _bindings_equal(b_sb, c_sb) -> bool:
    if b_sb["inbox_root"] != c_sb["inbox_root"]:
        return False
    if b_sb["sources_root"] != c_sb["sources_root"]:
        return False
    if set(b_sb["record_stores"]) != set(c_sb["record_stores"]):
        return False
    if b_sb["binding_reference"] != c_sb["binding_reference"]:
        return False
    return True


def reconcile(baseline: dict, current: dict) -> dict:
    """§4.10: 主对账流程。返回 reconciliation dict（不含 run_id 生成）。"""
    # P0: 输入验证（已在外层完成，这里做对象索引）
    b_objs = {o["file_id"]: o for o in baseline["objects"]}
    c_objs = {o["file_id"]: o for o in current["objects"]}
    b_ev = {e["evidence_id"]: e for e in baseline["evidence"]}
    c_ev = {e["evidence_id"]: e for e in current["evidence"]}

    # P1: 范围
    b_complete = baseline["scope_complete"] is True and baseline["scan_errors"] == []
    c_complete = current["scope_complete"] is True and current["scan_errors"] == []
    bindings_eq = _bindings_equal(baseline["scope_bindings"], current["scope_bindings"])
    backend_eq = baseline["backend"] == current["backend"]
    comparison_safe = b_complete and c_complete and bindings_eq and backend_eq

    scope_reasons = []
    if not b_complete:
        scope_reasons.append("BASELINE_SCOPE_INCOMPLETE")
    if not c_complete:
        scope_reasons.append("CURRENT_SCOPE_INCOMPLETE")
    if not bindings_eq:
        scope_reasons.append("SCOPE_BINDINGS_MISMATCH")
    if not backend_eq:
        scope_reasons.append("BACKEND_MISMATCH")

    # P2: 身份冲突
    identity_conflicts = find_identity_conflicts(b_objs, c_objs, b_ev, c_ev)

    result = []
    for fid in sorted(set(b_objs) | set(c_objs)):
        old = b_objs.get(fid)
        new = c_objs.get(fid)
        old_ev = _evidence_bundle(old, b_ev)
        new_ev = _evidence_bundle(new, c_ev)
        reasons: set[str] = set()

        # P1: 范围不完整 -> 全部 REVIEW_REQUIRED
        if not comparison_safe:
            if not b_complete or not c_complete:
                reasons.add("SCOPE_INCOMPLETE")
            if not bindings_eq or not backend_eq:
                reasons.add("SCOPE_MISMATCH")
        # P2: 身份冲突
        reasons |= identity_conflicts.get(fid, set())
        # P3: 证据检查
        if old is not None:
            reasons |= validate_object_evidence(old, b_ev)
            reasons |= detect_evidence_conflicts(old, b_ev)
        if new is not None:
            reasons |= validate_object_evidence(new, c_ev)
            reasons |= detect_evidence_conflicts(new, c_ev)
        # R1 REVIEW_REQUIRED 的实质阻碍 -> SOURCE_REVIEW_REQUIRED (F-04)
        # 仅当 R1 的 REVIEW_REASON 包含实质性阻碍原因时标记
        # 实质性原因：RECORD_CONFLICT、WEAK_ASSOCIATION、SHA256_READ_FAILED、
        #           SYMLINK_TARGET_OUT_OF_SCOPE
        # 按规格 §4.9：不能仅凭 R1 disposition 强制 R2 REVIEW_REQUIRED
        SUBSTANTIVE_R1_REASONS = {
            "RECORD_CONFLICT",
            "WEAK_ASSOCIATION", 
            "SHA256_READ_FAILED",
            "SYMLINK_TARGET_OUT_OF_SCOPE",
        }
        for obj, ev_map in ((old, b_ev), (new, c_ev)):
            if obj is None:
                continue
            if obj.get("disposition") == "REVIEW_REQUIRED":
                for eid in obj.get("evidence_reference", []):
                    e = ev_map.get(eid)
                    if e and e.get("kind") == "REVIEW_REASON":
                        rc = e.get("reason_code")
                        if rc in SUBSTANTIVE_R1_REASONS:
                            reasons.add("SOURCE_REVIEW_REQUIRED")
                            break

        # P4/P5: 分类
        if reasons:
            status = "REVIEW_REQUIRED"
            diff = material_diff(old, new) if (old and new) else []
        elif old is None:
            # P4: NEW 需 OBJECT 证据有效（已在 P3 检查）
            status = "NEW"
            diff = []
        elif new is None:
            status = "MISSING_NEEDS_REVIEW"
            reasons.add("BASELINE_ONLY")
            diff = []
        else:
            diff = material_diff(old, new)
            status = "CHANGED" if diff else "UNCHANGED"

        # MISSING_NEEDS_REVIEW 必须有 BASELINE_ONLY
        if status == "MISSING_NEEDS_REVIEW" and "BASELINE_ONLY" not in reasons:
            reasons.add("BASELINE_ONLY")

        result.append({
            "file_id": fid,
            "status": status,
            "baseline_object": old,
            "current_object": new,
            "baseline_evidence": old_ev,
            "current_evidence": new_ev,
            "changed_fields": diff,
            "reason_codes": sorted(reasons),
            "next_action": build_human_action(status, reasons),
        })

    # P6: 数量守恒
    counts = {s: 0 for s in FIVE_STATES}
    for r in result:
        counts[r["status"]] += 1
    total = len(result)
    review_total = counts["MISSING_NEEDS_REVIEW"] + counts["REVIEW_REQUIRED"]
    assert total == sum(counts[s] for s in FIVE_STATES)
    assert review_total == counts["MISSING_NEEDS_REVIEW"] + counts["REVIEW_REQUIRED"]

    reconciliation_status = (
        "REVIEW_REQUIRED" if (not comparison_safe or review_total > 0)
        else "COMPLETED"
    )

    return {
        "spec_version": SPEC_VERSION,
        "baseline": {
            "run_id": baseline["run_id"],
            "spec_version": baseline["spec_version"],
            "backend": baseline["backend"],
            "trigger": baseline["trigger"],
            "started_at": baseline["started_at"],
            "finished_at": baseline["finished_at"],
            "scope_complete": baseline["scope_complete"],
            "scope_bindings": baseline["scope_bindings"],
            "scan_errors": baseline["scan_errors"],
            "object_count": len(baseline["objects"]),
        },
        "current": {
            "run_id": current["run_id"],
            "spec_version": current["spec_version"],
            "backend": current["backend"],
            "trigger": current["trigger"],
            "started_at": current["started_at"],
            "finished_at": current["finished_at"],
            "scope_complete": current["scope_complete"],
            "scope_bindings": current["scope_bindings"],
            "scan_errors": current["scan_errors"],
            "object_count": len(current["objects"]),
        },
        "scope": {
            "baseline_complete": b_complete,
            "current_complete": c_complete,
            "bindings_equal": bindings_eq,
            "backend_equal": backend_eq,
            "reason_codes": sorted(scope_reasons),
        },
        "reconciliation_status": reconciliation_status,
        "comparison_safe": comparison_safe,
        "counts": {
            "total": total,
            "NEW": counts["NEW"],
            "CHANGED": counts["CHANGED"],
            "UNCHANGED": counts["UNCHANGED"],
            "MISSING_NEEDS_REVIEW": counts["MISSING_NEEDS_REVIEW"],
            "REVIEW_REQUIRED": counts["REVIEW_REQUIRED"],
            "review_total": review_total,
        },
        "objects": result,
    }


def _md_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _locator_of(obj, ev_bundle) -> str:
    if not obj or not ev_bundle:
        return "—"
    for rec in ev_bundle["records"]:
        if rec["kind"] == "OBJECT":
            return rec["locator"]
    return "—"


def render_markdown(rep: dict, run_id: str, generated_at: str) -> str:
    """§5: Markdown 报告（JSON 的可读投影，不重新分类）。"""
    c = rep["counts"]
    L = []
    L.append(f"# R2 只读差异对账报告 — {run_id}")
    L.append("")
    L.append("## 1. 运行摘要")
    L.append("")
    L.append(f"- spec_version：`{rep['spec_version']}`")
    L.append(f"- run_id：`{run_id}`")
    L.append(f"- generated_at：`{generated_at}`")
    L.append(f"- baseline.run_id：`{rep['baseline']['run_id']}`")
    L.append(f"- current.run_id：`{rep['current']['run_id']}`")
    L.append(f"- reconciliation_status：`{rep['reconciliation_status']}`")
    L.append(f"- comparison_safe：`{rep['comparison_safe']}`")
    L.append("")
    L.append("## 2. 输入与范围完整性")
    L.append("")
    L.append("| 属性 | Baseline | Current |")
    L.append("|---|---|---|")
    for attr, key in [
        ("R1 run_id", "run_id"), ("backend", "backend"),
        ("scope_complete", "scope_complete"),
        ("scan_errors 数量", None), ("objects 数量", None),
        ("inbox_root", None), ("sources_root", None),
        ("record_stores", None), ("binding_reference", None),
    ]:
        if key:
            bv, cv = rep["baseline"][key], rep["current"][key]
        elif attr == "scan_errors 数量":
            bv, cv = len(rep["baseline"]["scan_errors"]), len(rep["current"]["scan_errors"])
        elif attr == "objects 数量":
            bv, cv = rep["baseline"]["object_count"], rep["current"]["object_count"]
        elif attr in ("inbox_root", "sources_root", "binding_reference"):
            k = attr
            bv, cv = rep["baseline"]["scope_bindings"][k], rep["current"]["scope_bindings"][k]
        else:
            bv = ", ".join(rep["baseline"]["scope_bindings"]["record_stores"])
            cv = ", ".join(rep["current"]["scope_bindings"]["record_stores"])
        L.append(f"| {attr} | {_md_escape(str(bv))} | {_md_escape(str(cv))} |")
    rc = rep["scope"]["reason_codes"]
    L.append(f"- scope.reason_codes：`{', '.join(rc) if rc else '无'}`")
    if not rep["comparison_safe"]:
        L.append("")
        L.append("> 本次对账范围不具备安全可比性；所有对象归入 REVIEW_REQUIRED。"
                 "不得据此推断对象无变化、新增或删除。")
    L.append("")
    L.append("## 3. 五态数量对账")
    L.append("")
    L.append("| Status | Count |")
    L.append("|---|---|")
    for s in FIVE_STATES:
        L.append(f"| {s} | {c[s]} |")
    L.append(f"| TOTAL | {c['total']} |")
    L.append("")
    L.append(f"review_total = {c['review_total']}")
    L.append(f"五态合计 = {c['total']}")
    total_ok = c["total"] == sum(c[s] for s in FIVE_STATES)
    rt_ok = c["review_total"] == c["MISSING_NEEDS_REVIEW"] + c["REVIEW_REQUIRED"]
    L.append(f"数量对账结果 = {'PASS' if (total_ok and rt_ok) else 'FAIL'}")
    if not (total_ok and rt_ok):
        raise ValidationError("count invariants failed before markdown render")
    L.append("")
    L.append("## 4. 逐对象差异索引")
    L.append("")
    L.append("| file_id | status | baseline_name | current_name | baseline_locator | "
             "current_locator | changed_fields | reason_codes | next_action |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for o in rep["objects"]:
        anchor = "obj-" + hashlib.sha256(o["file_id"].encode()).hexdigest()
        L.append(f'<a id="{anchor}"></a>')
        bn = o["baseline_object"]["name"] if o["baseline_object"] else "—"
        cn = o["current_object"]["name"] if o["current_object"] else "—"
        bl = _locator_of(o["baseline_object"], o["baseline_evidence"])
        cl = _locator_of(o["current_object"], o["current_evidence"])
        L.append("| " + " | ".join([
            _md_escape(o["file_id"]), o["status"],
            _md_escape(str(bn)), _md_escape(str(cn)),
            _md_escape(str(bl)), _md_escape(str(cl)),
            _md_escape(",".join(o["changed_fields"])),
            _md_escape(",".join(o["reason_codes"])),
            _md_escape(o["next_action"] or "—"),
        ]) + " |")
    L.append("")
    L.append("## 5. Human 待审候选")
    L.append("")
    review_objs = [o for o in rep["objects"]
                   if o["status"] in ("MISSING_NEEDS_REVIEW", "REVIEW_REQUIRED")]
    if not review_objs:
        L.append("无 Human 待审候选。")
    else:
        for o in review_objs:
            anchor = "obj-" + hashlib.sha256(o["file_id"].encode()).hexdigest()
            L.append(f"### {_md_escape(o['file_id'])}")
            L.append(f"- 锚点：[#{anchor}](#{anchor})")
            L.append(f"- 对账状态：`{o['status']}`")
            L.append(f"- 原因码：`{', '.join(o['reason_codes'])}`")
            bl = _locator_of(o["baseline_object"], o["baseline_evidence"])
            cl = _locator_of(o["current_object"], o["current_evidence"])
            L.append(f"- Baseline 定位器：`{_md_escape(str(bl))}`")
            L.append(f"- Current 定位器：`{_md_escape(str(cl))}`")
            L.append(f"- 业务字段差异：`{', '.join(o['changed_fields']) or '—'}`")
            L.append(f"- Human next_action：{_md_escape(o['next_action'] or '—')}")
            if o["status"] == "MISSING_NEEDS_REVIEW":
                L.append("- 说明：Current 对象缺席，不能据此推断对象已删除。")
            if "IDENTITY_PATH_CONFLICT" in o["reason_codes"] or \
               "IDENTITY_CROSS_ID_COLLISION" in o["reason_codes"]:
                L.append("- 说明：存在对象身份或定位冲突，未执行自动合并或 Move。")
            L.append("")
    L.append("## 6. 只读边界与结论")
    L.append("")
    L.append("本次 R2 仅读取两份 R1 manifest，未访问或修改 Drive、Inbox、"
             "Source Registry，未执行归档、移动、删除或版权裁决。")
    L.append("")
    L.append(f"结论：`{rep['reconciliation_status']}`")
    if not rep["comparison_safe"]:
        L.append("")
        L.append("诊断：范围不完整或绑定不一致，本次为 fail-closed 诊断报告。")
    L.append("")
    return "\n".join(L)


def _get_repo_root() -> Path:
    """可信的仓库根：从模块文件位置推导，不依赖 cwd（F-02）。
    
    src/cba_kb/r2_reconcile.py -> 3 级上是仓库根。
    """
    return Path(__file__).resolve().parent.parent.parent


def _resolve_reports_dir(arg: str | None) -> Path:
    """§6.3: 报告目录授权规则（F-01/F-02 修复版，N-01 加固）。
    
    - 仓库根从模块 __file__ 推导，不依赖 cwd
    - 先检查原始路径是否为符号链接，再 resolve（F-01）
    - 确保最终目标在仓库根内，不逃逸
    - N-01: 测试覆盖仅在 pytest 下生效，防止生产滥用
    """
    # N-01: 测试钩子仅在 pytest 环境下生效
    # PYTEST_CURRENT_TEST 由 pytest 自动设置，生产环境不存在
    # 防止生产调用者通过设置 R2_TEST_REPORTS_ROOT 绕过授权目录限制
    test_override = None
    if "PYTEST_CURRENT_TEST" in os.environ:
        test_override = os.environ.get("R2_TEST_REPORTS_ROOT")
    if test_override:
        raw = Path(test_override)
        # F-01: 先检查原始路径是否为符号链接
        if raw.is_symlink():
            raise ValidationError("reports/ must not be a symlink")
        target = raw.resolve()
        return target
    
    root = _get_repo_root()
    reports_raw = root / "reports"
    # F-01: 在 resolve() 之前检查是否为符号链接
    # （resolve() 会跟随链接，导致 is_symlink() 永远返回 False）
    if reports_raw.is_symlink():
        raise ValidationError("reports/ must not be a symlink")
    reports = reports_raw.resolve()
    # 确保解析后仍在仓库根内（纵深防御）
    if reports != reports_raw:
        try:
            reports.relative_to(root)
        except ValueError:
            raise ValidationError(
                f"reports/ resolves outside repo root: {reports}")
    
    if arg is None:
        target = reports
    else:
        p = Path(arg)
        if not p.is_absolute():
            # 相对路径以可信仓库根为基准，不是 cwd
            p = root / p
        # F-01: 检查原始路径是否为符号链接
        if p.is_symlink():
            raise ValidationError("reports-dir must not be a symlink")
        target = p.resolve()
        # 必须精确等于授权目录
        if target != reports:
            raise ValidationError(
                f"reports-dir must resolve to authorized reports/ ({reports}), "
                f"got {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="r2-reconcile",
        description="R2 read-only reconciliation of two R1 manifests.")
    ap.add_argument("--baseline", required=False, help="baseline R1 manifest JSON")
    ap.add_argument("--current", required=False, help="current R1 manifest JSON")
    ap.add_argument("--reports-dir", required=False, default=None)
    args = ap.parse_args(argv)

    if not args.baseline or not args.current:
        ap.print_usage(sys.stderr)
        return 2

    # 输入只读打开
    try:
        with open(args.baseline, "r", encoding="utf-8") as f:
            baseline_raw = f.read()
        with open(args.current, "r", encoding="utf-8") as f:
            current_raw = f.read()
    except OSError as e:
        print(f"error: cannot read input: {e}", file=sys.stderr)
        return 1

    # 报告目录授权
    try:
        reports_dir = _resolve_reports_dir(args.reports_dir)
    except ValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    # 输入路径不得在 reports/ 内（§6.3.6）
    for p in (args.baseline, args.current):
        try:
            rp = Path(p).resolve()
            if rp == reports_dir or reports_dir in rp.parents:
                print("error: input manifest must not be inside reports/",
                      file=sys.stderr)
                return 1
        except OSError:
            pass

    # P0: 严格验证
    try:
        baseline = _strict_json_loads(baseline_raw)
        current = _strict_json_loads(current_raw)
        validate_r1_manifest(baseline, "baseline")
        validate_r1_manifest(current, "current")
    except ValidationError as e:
        print(f"error: input validation failed: {e}", file=sys.stderr)
        return 1

    # 对账
    rep = reconcile(baseline, current)

    # run_id 与输出
    run_id = "r2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") \
        + "-" + secrets.token_hex(4)
    generated_at = utcnow()
    rep["run_id"] = run_id
    rep["generated_at"] = generated_at

    reports_dir.mkdir(parents=True, exist_ok=True)
    
    # 先渲染 Markdown（数量检查在内）
    try:
        md_text = render_markdown(rep, run_id, generated_at)
    except ValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # F-06: 原子独占发布（防 TOCTOU）
    # N-02: 确保 JSON+MD 作为一对原子发布，处理短写，失败时清理
    # 使用 O_CREAT|O_EXCL 确保不覆盖已存在文件
    def _write_all(fd, data: bytes):
        """处理短写，确保全部写入。"""
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    
    json_bytes = (json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    md_bytes = md_text.encode("utf-8")
    if not md_text.endswith("\n"):
        md_bytes += b"\n"
    
    for attempt in range(6):
        json_path = reports_dir / f"{run_id}.reconciliation.json"
        md_path = reports_dir / f"{run_id}.reconciliation.md"
        json_created = False
        try:
            # 原子独占创建 JSON
            fd_j = os.open(json_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                _write_all(fd_j, json_bytes)
            finally:
                os.close(fd_j)
            json_created = True
            # 原子独占创建 Markdown
            try:
                fd_m = os.open(md_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                try:
                    _write_all(fd_m, md_bytes)
                finally:
                    os.close(fd_m)
            except (FileExistsError, OSError):
                # MD 创建失败：删除已创建的 JSON，避免孤立文件（N-02）
                if json_created:
                    try:
                        json_path.unlink()
                    except OSError:
                        pass
                raise
            # 成功：JSON+MD 配对完整
            break
        except FileExistsError:
            if attempt >= 5:
                print("error: cannot allocate unused run_id", file=sys.stderr)
                return 1
            run_id = "r2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") \
                + "-" + secrets.token_hex(4)
            rep["run_id"] = run_id
            try:
                md_text = render_markdown(rep, run_id, generated_at)
            except ValidationError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            json_bytes = (json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
            md_bytes = md_text.encode("utf-8")
            if not md_text.endswith("\n"):
                md_bytes += b"\n"
        except OSError as e:
            print(f"error: write failed: {e}", file=sys.stderr)
            return 1

    # exit code (§1.3, §2.3):
    # - scope_complete=false 或 scan_errors 非空 -> 1
    # - 仅绑定/backend 不一致（双方完整）-> 0
    scope = rep["scope"]
    if not scope["baseline_complete"] or not scope["current_complete"]:
        print(f"diagnostic report written: {json_path} (scope incomplete)",
              file=sys.stderr)
        return 1
    print(f"reconciliation written: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
