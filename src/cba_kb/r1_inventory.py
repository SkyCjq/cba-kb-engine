"""2.0.3-2 R1 — Read-only Inventory 与对账.

实现 Q1 冻结规格 ``2.0.3-2-R1-Q1``：
纯只读盘点 ``00_inbox_待处理`` + ``10_sources_原始证据`` 及关联记录，
输出 JSON manifest + Markdown 报告。

规格要点（实现必须遵守）：
- 唯一允许的写入是 ``reports/`` 下本任务产物；
- 对象记录恰好 11 个字段，未知值用 ``null``/``UNKNOWN``/空数组，不得省略；
- 不存在记录不能推出 UNPROCESSED；位于 sources 根不能推出 PROCESSED；
- 任何冲突/歧义 -> REVIEW_REQUIRED，不做裁决；
- 枚举前后成员变化 -> 运行失败（exit 1），保留诊断。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

SPEC_VERSION = "2.0.3-2-R1-Q1"
BACKEND = "local_filesystem"
TRIGGER = "manual"

FIELDS = (
    "file_id", "name", "mime_type", "sha256", "parent_ids",
    "source_id", "document_id", "processing_status",
    "disposition", "final_parent", "evidence_reference",
)

PROCESSING_STATUSES = ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "UNKNOWN")
DISPOSITIONS = ("UNPROCESSED", "PROCESSED", "REVIEW_REQUIRED")

EVIDENCE_KINDS = (
    "OBJECT", "SOURCE", "DOCUMENT", "PROCESS",
    "FINAL_LOCATION", "REVIEW_REASON",
)

DEFAULT_STATUS_MAP = {
    "pending": "PENDING",
    "processing": "PROCESSING",
    "in_progress": "PROCESSING",
    "completed": "COMPLETED",
    "complete": "COMPLETED",
    "done": "COMPLETED",
    "failed": "FAILED",
    "error": "FAILED",
    "unknown": "UNKNOWN",
}

# 记录 -> 对象的关联候选键（按优先级）。
# 强关联键（路径/ID 类，可验证身份）；弱关联键（名称类，需降级处理）。
STRONG_ASSOC_KEYS = ("file_id", "relative_path", "path")
WEAK_ASSOC_KEYS = ("name", "business_key", "artifact_key")
ASSOC_KEYS = STRONG_ASSOC_KEYS + WEAK_ASSOC_KEYS
# 记录中表示"最终位置"的候选字段。
FINAL_LOCATION_KEYS = ("final_parent", "final_location", "location", "archived_path")
# 记录中表示状态的候选字段。
STATUS_KEYS = ("processing_status", "status", "state")


class ScanError(Exception):
    """可预期的扫描失败（exit 1）。"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str | None:
    """O_RDONLY 只读计算文件 SHA-256；失败返回 None（不抛异常）。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def guess_mime(path: Path, is_dir: bool, is_symlink: bool) -> str:
    if is_dir:
        return "inode/directory"
    if is_symlink:
        return "inode/symlink"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def load_bindings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"bindings 配置读取失败: {exc}")
    for key in ("inbox_root", "sources_root", "record_stores", "binding_reference"):
        if key not in data:
            raise ScanError(f"bindings 缺少必需键: {key}")
    bindings = {
        "inbox_root": Path(data["inbox_root"]),
        "sources_root": Path(data["sources_root"]),
        "record_stores": [Path(p) for p in data["record_stores"]],
        "binding_reference": str(data["binding_reference"]),
        "status_map": {k.lower(): v for k, v in dict(data.get("status_map", {})).items()},
        "record_schema": dict(data.get("record_schema", {})),
    }
    for label in ("inbox_root", "sources_root"):
        root = bindings[label]
        if not root.is_absolute():
            raise ScanError(f"{label} 必须为绝对路径: {root}")
        if not root.is_dir():
            raise ScanError(f"{label} 不存在或不可遍历: {root}")
    return bindings


def _snapshot_stores(paths: list[Path]) -> dict[str, tuple | None]:
    """record-store 快照：每个文件 -> (mtime_ns, size)。缺失/不可读记为 None。"""
    snap: dict[str, tuple | None] = {}
    for p in paths:
        try:
            st = p.stat()
            snap[str(p)] = (st.st_mtime_ns, st.st_size)
        except OSError:
            snap[str(p)] = None
    return snap


def _snapshot_dirs(roots: list[Path]) -> dict[str, list[str]]:
    """枚举前/后快照：每个目录 -> 排序后的成员名（不跟随符号链接）。"""
    snapshot: dict[str, list[str]] = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            try:
                with os.scandir(dirpath) as it:
                    names = sorted(entry.name for entry in it)
            except OSError:
                names = None  # 不可读目录：记录为 None，枚举阶段同样会失败
            snapshot[dirpath] = names
    return snapshot


def enumerate_fs(root_id: str, root: Path) -> tuple[list[dict], list[dict]]:
    """枚举一个根目录。返回 (objects, scan_errors)。"""
    objects: list[dict] = []
    scan_errors: list[dict] = []
    root_abs = root.resolve()

    def file_id_for(rel: str) -> str:
        return root_id if rel == "." else f"{root_id}/{rel}"

    # 根目录本身也是一个对象。
    objects.append({
        "_kind": "dir", "_abs": str(root_abs), "_rel": ".",
        "_file_id": file_id_for("."), "_parent": None,
    })

    stack = [(root_abs, ".")]
    while stack:
        dir_abs, dir_rel = stack.pop()
        try:
            with os.scandir(dir_abs) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            scan_errors.append({
                "locator": str(dir_abs),
                "reason_code": "DIR_UNREADABLE",
                "detail": f"目录不可遍历，范围不完整: {exc}",
            })
            continue
        parent_id = file_id_for(dir_rel)
        for entry in entries:
            try:
                is_symlink = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                scan_errors.append({
                    "locator": str(Path(dir_abs) / entry.name),
                    "reason_code": "ENTRY_STAT_FAILED",
                    "detail": str(exc),
                })
                continue
            rel = entry.name if dir_rel == "." else f"{dir_rel}/{entry.name}"
            abs_path = str(Path(dir_abs) / entry.name)
            objects.append({
                "_kind": "symlink" if is_symlink else ("dir" if is_dir else "file"),
                "_abs": abs_path, "_rel": rel,
                "_file_id": file_id_for(rel), "_parent": parent_id,
                "_symlink_target": os.readlink(abs_path) if is_symlink else None,
            })
            if is_dir and not is_symlink:
                stack.append((Path(abs_path), rel))
    return objects, scan_errors

# --- chunk 2: record stores, association, disposition ---

def load_record_stores(paths: list[Path]) -> list[dict]:
    """读取关联记录快照（JSON/YAML）。返回 (record, store_index, store_path) 列表。"""
    records: list[dict] = []
    for index, path in enumerate(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ScanError(f"record store 不可读: {path}: {exc}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(text)
            except Exception as exc:
                raise ScanError(f"record store 解析失败: {path}: {exc}")
        if isinstance(data, dict):
            # 常见封装：{"sources": [...]} / {"records": [...]} / {"documents": [...]}
            items: list = []
            for key in ("records", "sources", "documents", "items"):
                if isinstance(data.get(key), list):
                    items.extend(data[key])
            if not items:
                items = [data]
        elif isinstance(data, list):
            items = data
        else:
            raise ScanError(f"record store 顶层结构不支持: {path}")
        for item in items:
            if isinstance(item, dict):
                records.append({"_record": item, "_store": index, "_path": str(path)})
    return records


def _norm(s: str) -> str:
    return s.strip().lower()


def match_records(obj: dict, records: list[dict]) -> list[dict]:
    """把记录关联到对象。返回命中的记录列表（可能多条 -> 冲突判定）。

    每个命中记录附加 ``_match_strength``：``"strong"``（file_id/relative_path/path
    命中）或 ``"weak"``（name/business_key/artifact_key 命中）。弱关联不能单独
    作为身份依据（规格 §2：不得用文件名推断处理状态）。
    """
    rel = obj["_rel"]
    name = Path(obj["_abs"]).name
    fid = obj["_file_id"]
    hits: list[dict] = []
    for rec in records:
        r = rec["_record"]
        strength: str | None = None
        for key in ASSOC_KEYS:
            val = r.get(key)
            if val is None:
                continue
            v = str(val)
            if key in ("file_id",):
                if v == fid:
                    strength = "strong"
                    break
            elif key in ("relative_path", "path"):
                if _norm(v) == _norm(rel) or _norm(v.rstrip("/")) == _norm(rel):
                    strength = "strong"
                    break
                # 绝对路径结尾匹配（相对路径形式）
                if _norm(obj["_abs"]).endswith(_norm(v)):
                    strength = "strong"
                    break
            elif key in ("name", "business_key", "artifact_key"):
                if v == name or v == fid:
                    strength = "weak"
                    break
        if strength is not None:
            rec["_match_strength"] = strength
            hits.append(rec)
    # 去重（同一记录多个键命中）
    seen: set[int] = set()
    uniq: list[dict] = []
    for h in hits:
        if id(h) not in seen:
            seen.add(id(h))
            uniq.append(h)
    return uniq


def _record_assoc_signature(rec: dict, status_map: dict[str, str]) -> tuple:
    """关联判定用的记录签名：状态 + 单值字段 + 最终位置。

    多条候选记录必须在这些维度上完全一致，否则不得任取其一（规格 §2）。
    """
    r = rec["_record"]
    return (
        record_status(rec, status_map),
        str(r.get("source_id") or r.get("source_key") or ""),
        str(r.get("document_id") or r.get("document_key") or ""),
        str(record_final_location(rec) or ""),
    )


def record_status(rec: dict, status_map: dict[str, str]) -> str:
    r = rec["_record"]
    for key in STATUS_KEYS:
        val = r.get(key)
        if val is not None:
            mapped = status_map.get(_norm(str(val)), DEFAULT_STATUS_MAP.get(_norm(str(val))))
            if mapped:
                return mapped
    return "UNKNOWN"


def record_final_location(rec: dict) -> str | None:
    r = rec["_record"]
    for key in FINAL_LOCATION_KEYS:
        val = r.get(key)
        if val:
            return str(val)
    return None


def _evidence_id(file_id: str, kind: str, locator: str) -> str:
    return "ev-" + hashlib.sha256(f"{file_id}|{kind}|{locator}".encode()).hexdigest()[:16]


def decide(obj: dict, hits: list[dict], status_map: dict[str, str],
           roots_abs: list[str], observed_at: str) -> tuple[dict, list[dict]]:
    """判定单个对象的 11 字段记录 + 证据列表。返回 (record, evidence_list)。"""
    fid = obj["_file_id"]
    name = Path(obj["_abs"]).name or fid
    kind = obj["_kind"]
    is_file = kind == "file"
    is_symlink = kind == "symlink"
    is_dir = kind == "dir"

    evidence: list[dict] = []

    def add_ev(k: str, locator: str, detail: str, reason_code: str | None = None) -> str:
        eid = _evidence_id(fid, k, locator)
        evidence.append({
            "evidence_id": eid, "file_id": fid, "kind": k,
            "locator": locator, "observed_at": observed_at,
            "reason_code": reason_code, "detail": detail,
        })
        return eid

    # --- sha256（仅普通文件；O_RDONLY 只读） ---
    digest: str | None = None
    sha_fail_reason: str | None = None
    if is_file:
        digest = sha256_file(Path(obj["_abs"]))
        if digest is None:
            sha_fail_reason = "SHA256_READ_FAILED"

    # --- 符号链接目标越界检查 ---
    symlink_oob = False
    if is_symlink and obj.get("_symlink_target"):
        target = obj["_symlink_target"]
        target_abs = str((Path(obj["_abs"]).parent / target).resolve()) \
            if not os.path.isabs(target) else target
        if not any(target_abs == r or target_abs.startswith(r + os.sep) for r in roots_abs):
            symlink_oob = True

    # --- 关联记录与状态 ---
    # 规格 §2：多条候选必须在状态 + 单值字段 + 最终位置上完全一致，
    # 否则"不得任取一个"，写 null 并标记 REVIEW_REQUIRED。
    sigs = {_record_assoc_signature(h, status_map) for h in hits}
    conflict = len(sigs) > 1
    # 弱关联（仅 name/business_key 命中）不能单独作为已验证身份。
    weak_only = bool(hits) and all(
        h.get("_match_strength") == "weak" for h in hits
    )

    source_id: str | None = None
    document_id: str | None = None
    primary = hits[0] if hits else None
    if primary is not None and not conflict and not weak_only:
        r = primary["_record"]
        source_id = str(r.get("source_id") or r.get("source_key") or "") or None
        document_id = str(r.get("document_id") or r.get("document_key") or "") or None

    # --- disposition 判定（§2.1；保守：凡需解释的一律 REVIEW_REQUIRED） ---
    reason: str | None = None
    detail_note = ""
    processing_status = "UNKNOWN"
    disposition = "REVIEW_REQUIRED"
    final_parent: str | None = None

    if symlink_oob:
        reason = "SYMLINK_TARGET_OUT_OF_SCOPE"
        detail_note = "符号链接目标超出授权扫描根，不跟随；目标不读取。"
    elif sha_fail_reason:
        reason = sha_fail_reason
        detail_note = "文件内容不可读，哈希失败；对象保留，不得跳过。"
    elif not hits:
        reason = "NO_RECORD"
        detail_note = "无关联记录；不存在记录不能推出 UNPROCESSED。"
    elif weak_only:
        reason = "WEAK_ASSOCIATION"
        detail_note = ("仅通过名称类弱关联键命中记录，无法唯一验证身份；"
                       "按规格 §2 不得据此推断处理状态。")
    elif conflict:
        reason = "RECORD_CONFLICT"
        detail_note = "多条关联记录在状态/单值字段/最终位置上不一致，不做裁决。"
    else:
        st = record_status(primary, status_map)
        processing_status = st
        if st == "PENDING":
            disposition = "UNPROCESSED"
            final_parent = None
        elif st == "COMPLETED":
            loc = record_final_location(primary)
            actual = str(Path(obj["_abs"]).resolve()) if not is_symlink else None
            if loc and actual:
                # 授权边界：最终位置若在扫描根之外，不得读取/验证，
                # 直接 REVIEW_REQUIRED（规格 §1.1 未授权路径规则）。
                loc_is_abs = os.path.isabs(loc)
                in_scope = (
                    loc_is_abs
                    and any(loc == r or loc.startswith(r + os.sep) for r in roots_abs)
                )
                if loc_is_abs and not in_scope:
                    reason = "FINAL_LOCATION_OUT_OF_SCOPE"
                    detail_note = (f"完成记录的最终位置超出授权扫描根，不读取：{loc!r}。")
                    disposition = "REVIEW_REQUIRED"
                else:
                    norm_loc = str(Path(loc).resolve()) if os.path.exists(loc) else None
                    if norm_loc and norm_loc == actual:
                        disposition = "PROCESSED"
                        final_parent = norm_loc
                    else:
                        reason = "FINAL_LOCATION_UNVERIFIED"
                        detail_note = f"完成记录的最终位置不可验证（记录值 {loc!r}）。"
                        disposition = "REVIEW_REQUIRED"
            else:
                reason = "FINAL_LOCATION_MISSING"
                detail_note = "完成记录缺少可验证的最终位置。"
                disposition = "REVIEW_REQUIRED"
        else:
            # PROCESSING / FAILED / UNKNOWN -> REVIEW_REQUIRED
            reason = f"STATUS_{st}"
            detail_note = f"处理状态为 {st}，无充分处置证据。"
            disposition = "REVIEW_REQUIRED"

    if disposition == "REVIEW_REQUIRED" and processing_status == "UNKNOWN" and hits:
        processing_status = record_status(primary, status_map)

    # --- parent_ids ---
    parent_ids = [] if obj["_parent"] is None else [obj["_parent"]]

    # --- 证据 ---
    locator = obj["_abs"]
    add_ev("OBJECT", locator,
           f"对象定位证据：{kind}，{locator}")
    if source_id:
        add_ev("SOURCE", f"{primary['_path']}#source_id={source_id}",
               "Source 关联证据", None)
    if document_id:
        add_ev("DOCUMENT", f"{primary['_path']}#document_id={document_id}",
               "Document 关联证据", None)
    if primary is not None:
        add_ev("PROCESS", primary["_path"],
               f"处理状态证据：{processing_status}", None)
    if disposition == "PROCESSED" and final_parent:
        add_ev("FINAL_LOCATION", final_parent, "最终位置验证证据", None)
    if disposition == "REVIEW_REQUIRED":
        add_ev("REVIEW_REASON", locator,
               f"{reason}: {detail_note} 人工核查动作：打开定位器核实对象现状，"
               "确认关联记录与状态，补充缺失证据后重新判定。",
               reason)

    record = {
        "file_id": fid,
        "name": name,
        "mime_type": guess_mime(Path(obj["_abs"]), is_dir, is_symlink),
        "sha256": digest,
        "parent_ids": parent_ids,
        "source_id": source_id,
        "document_id": document_id,
        "processing_status": processing_status,
        "disposition": disposition,
        "final_parent": final_parent,
        "evidence_reference": [e["evidence_id"] for e in evidence],
    }
    assert set(record.keys()) == set(FIELDS), "对象记录必须恰好 11 个字段"
    return record, evidence

# --- chunk 2b: 逻辑记录作为独立对象 + 关联闭包 ---
# 规格 §1.1：Source、Document、历史处置记录每条逻辑记录一个对象；
# 关联记录的关联记录继续追踪，直到没有新的关联记录；循环引用按唯一 ID 去重。

# 记录主键候选字段（按优先级）。
RECORD_PK_KEYS = ("id", "key", "pk", "record_id", "source_id", "document_id",
                  "business_key", "artifact_key")
# 记录类型候选字段。
RECORD_TYPE_KEYS = ("record_type", "type", "kind", "category")
# 可能包含对其他记录引用的字段名模式（小写后缀匹配）。
REF_KEY_SUFFIXES = ("_id", "_ids", "_ref", "_refs", "_key", "_keys")


def record_uid(rec: dict) -> str:
    """记录的稳定唯一 ID：record/{store_idx}/{type}/{pk}。"""
    r = rec["_record"]
    store = rec["_store"]
    rtype = ""
    for k in RECORD_TYPE_KEYS:
        if r.get(k):
            rtype = str(r[k])
            break
    rtype = rtype or "record"
    pk = ""
    for k in RECORD_PK_KEYS:
        if r.get(k):
            pk = str(r[k])
            break
    if not pk:
        pk = hashlib.sha256(
            json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{rtype}-{pk}")
    return f"record/{store}/{safe}"[:200]


def record_display_name(rec: dict) -> str:
    r = rec["_record"]
    for k in ("name", "title", "label"):
        if r.get(k):
            return str(r[k])
    for k in RECORD_PK_KEYS:
        if r.get(k):
            return str(r[k])
    return record_uid(rec)


def build_record_index(records: list[dict]) -> dict[str, dict]:
    """按主键值索引记录，用于解析记录间引用。"""
    index: dict[str, dict] = {}
    for rec in records:
        r = rec["_record"]
        for k in RECORD_PK_KEYS:
            v = r.get(k)
            if v:
                index.setdefault(str(v), rec)
    return index


def find_record_refs(rec: dict, index: dict[str, dict]) -> list[dict]:
    """找出该记录字段中引用其他记录的条目（启发式：引用型字段名 + 值命中索引）。"""
    refs: list[dict] = []
    r = rec["_record"]
    for k, v in r.items():
        if not isinstance(v, str) or not v:
            continue
        kl = k.lower()
        looks_like_ref = kl.endswith(REF_KEY_SUFFIXES) or kl in ("id", "key", "ref")
        if looks_like_ref and v in index and index[v] is not rec:
            refs.append(index[v])
    # 去重
    seen: set[int] = set()
    uniq: list[dict] = []
    for x in refs:
        if id(x) not in seen:
            seen.add(id(x))
            uniq.append(x)
    return uniq


def transitive_closure(seed_records: list[dict],
                       index: dict[str, dict]) -> list[dict]:
    """从种子记录出发，沿引用双向传递闭包；按 UID 去重，天然防循环。

    双向：既跟随本记录引用的他记录（出边），也纳入引用本记录的他记录（入边），
    因为"关联记录的关联记录"包含"引用了已关联记录"的记录。
    """
    # 预计算每条记录的出边（引用的他记录 UID 集合）。
    out_edges: dict[str, set[str]] = {}
    uid_of: dict[int, str] = {}
    all_recs = list({id(r): r for r in index.values()}.values())
    for rec in all_recs:
        uid = record_uid(rec)
        uid_of[id(rec)] = uid
        out_edges[uid] = {record_uid(x) for x in find_record_refs(rec, index)}
    # 反向索引：被谁引用。
    in_edges: dict[str, set[str]] = {}
    for uid, targets in out_edges.items():
        for t in targets:
            in_edges.setdefault(t, set()).add(uid)
    uid_to_rec = {record_uid(r): r for r in all_recs}

    seen: set[str] = set()
    queue = list(seed_records)
    result: list[dict] = []
    while queue:
        rec = queue.pop(0)
        uid = record_uid(rec)
        if uid in seen:
            continue
        seen.add(uid)
        result.append(rec)
        neighbors = out_edges.get(uid, set()) | in_edges.get(uid, set())
        for nuid in neighbors:
            if nuid not in seen and nuid in uid_to_rec:
                queue.append(uid_to_rec[nuid])
    return result


def decide_record(rec: dict, container_id: str, status_map: dict[str, str],
                  observed_at: str) -> tuple[dict, list[dict]]:
    """为一条逻辑记录生成 11 字段对象 + 证据（规格 §1.1、§2.1）。

    逻辑记录同样适用处置判定表；没有充分处置证据时统一 REVIEW_REQUIRED，
    不得继承其他对象状态。
    """
    r = rec["_record"]
    uid = record_uid(rec)
    name = record_display_name(rec)
    evidence: list[dict] = []

    def add_ev(k: str, locator: str, detail: str,
               reason_code: str | None = None) -> str:
        eid = _evidence_id(uid, k, locator)
        evidence.append({
            "evidence_id": eid, "file_id": uid, "kind": k,
            "locator": locator, "observed_at": observed_at,
            "reason_code": reason_code, "detail": detail,
        })
        return eid

    locator = f"{rec['_path']}#{uid}"
    add_ev("OBJECT", locator, f"逻辑记录定位证据：{uid}，{locator}")

    st = record_status(rec, status_map)
    processing_status = st
    disposition = "REVIEW_REQUIRED"
    final_parent: str | None = None
    reason: str | None = None
    detail_note = ""

    # 记录自身的 source/document 身份：类型明确时自关联。
    rtype = ""
    for k in RECORD_TYPE_KEYS:
        if r.get(k):
            rtype = str(r[k]).lower()
            break
    source_id = None
    document_id = None
    pk_val = ""
    for k in RECORD_PK_KEYS:
        if r.get(k):
            pk_val = str(r[k])
            break
    if "source" in rtype and pk_val:
        source_id = pk_val
        add_ev("SOURCE", locator, "Source 记录自关联证据", None)
    if "document" in rtype and pk_val:
        document_id = pk_val
        add_ev("DOCUMENT", locator, "Document 记录自关联证据", None)

    if st == "PENDING":
        disposition = "UNPROCESSED"
        add_ev("PROCESS", rec["_path"], f"处理状态证据：{st}", None)
    elif st == "COMPLETED":
        # 逻辑记录的完成需有可验证的最终位置（容器 URI 或授权路径），否则保守处理。
        loc = record_final_location(rec)
        if loc:
            final_parent = loc
            disposition = "PROCESSED"
            add_ev("PROCESS", rec["_path"], f"处理状态证据：{st}", None)
            add_ev("FINAL_LOCATION", loc, "最终位置证据（记录自述）", None)
        else:
            reason = "RECORD_FINAL_LOCATION_MISSING"
            detail_note = "逻辑记录声称完成但缺少最终位置。"
            add_ev("PROCESS", rec["_path"], f"处理状态证据：{st}", None)
    else:
        reason = f"RECORD_STATUS_{st}"
        detail_note = f"逻辑记录处理状态为 {st}，无充分处置证据。"
        if st != "UNKNOWN":
            add_ev("PROCESS", rec["_path"], f"处理状态证据：{st}", None)

    if disposition == "REVIEW_REQUIRED":
        if reason is None:
            reason = "RECORD_UNVERIFIED"
            detail_note = "逻辑记录无充分处置证据。"
        add_ev("REVIEW_REASON", locator,
               f"{reason}: {detail_note} 人工核查动作：打开记录定位器核实记录内容与状态。",
               reason)

    record = {
        "file_id": uid,
        "name": name,
        "mime_type": "application/json",
        "sha256": None,
        "parent_ids": [container_id],
        "source_id": source_id,
        "document_id": document_id,
        "processing_status": processing_status,
        "disposition": disposition,
        "final_parent": final_parent,
        "evidence_reference": [e["evidence_id"] for e in evidence],
    }
    assert set(record.keys()) == set(FIELDS), "记录对象必须恰好 11 个字段"
    return record, evidence


def emit_store_containers(bindings: dict, observed_at: str) -> tuple[list[dict], list[dict]]:
    """为每个 record-store 生成容器对象（逻辑记录的父对象，保证引用可解析）。"""
    records_out: list[dict] = []
    evidence_out: list[dict] = []
    for idx, p in enumerate(bindings["record_stores"]):
        cid = f"record-store/{idx}"
        locator = str(p)
        eid = _evidence_id(cid, "OBJECT", locator)
        evidence_out.append({
            "evidence_id": eid, "file_id": cid, "kind": "OBJECT",
            "locator": locator, "observed_at": observed_at,
            "reason_code": None, "detail": f"记录容器：{locator}",
        })
        records_out.append({
            "file_id": cid,
            "name": Path(locator).name or cid,
            "mime_type": "application/json",
            "sha256": None,
            "parent_ids": [],
            "source_id": None,
            "document_id": None,
            "processing_status": "UNKNOWN",
            "disposition": "REVIEW_REQUIRED",
            "final_parent": None,
            "evidence_reference": [eid],
        })
        # 容器本身 REVIEW_REQUIRED 需要 REVIEW_REASON 证据（规格 §3）。
        rid = _evidence_id(cid, "REVIEW_REASON", locator)
        evidence_out.append({
            "evidence_id": rid, "file_id": cid, "kind": "REVIEW_REASON",
            "locator": locator, "observed_at": observed_at,
            "reason_code": "RECORD_CONTAINER",
            "detail": ("记录容器对象：承载逻辑记录的快照文件。人工核查动作："
                       "确认快照来源与授权边界。"),
        })
        records_out[-1]["evidence_reference"].append(rid)
    return records_out, evidence_out

# --- chunk 3: manifest, report, CLI ---

def _anchor(file_id: str) -> str:
    return "obj-" + hashlib.sha256(file_id.encode("utf-8")).hexdigest()


def _md_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def build_manifest(bindings: dict, records: list[dict], evidence: list[dict],
                   scan_errors: list[dict], run_id: str,
                   started_at: str, finished_at: str) -> dict:
    return {
        "spec_version": SPEC_VERSION,
        "run_id": run_id,
        "backend": BACKEND,
        "trigger": TRIGGER,
        "started_at": started_at,
        "finished_at": finished_at,
        "scope_complete": len(scan_errors) == 0,
        "scope_bindings": {
            "inbox_root": str(bindings["inbox_root"]),
            "sources_root": str(bindings["sources_root"]),
            "record_stores": [str(p) for p in bindings["record_stores"]],
            "binding_reference": bindings["binding_reference"],
        },
        "scan_errors": scan_errors,
        "objects": records,
        "evidence": evidence,
    }


def write_report(manifest: dict, report_path: Path) -> None:
    objs = manifest["objects"]
    ev_by_id = {e["evidence_id"]: e for e in manifest["evidence"]}
    total = len(objs)
    by_disp: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for o in objs:
        by_disp[o["disposition"]] = by_disp.get(o["disposition"], 0) + 1
        by_status[o["processing_status"]] = by_status.get(o["processing_status"], 0) + 1
    review_objs = [o for o in objs if o["disposition"] == "REVIEW_REQUIRED"]

    lines: list[str] = []
    a = lines.append
    a(f"# R1 只读盘点报告 — {manifest['run_id']}")
    a("")
    a("## 运行摘要")
    a("")
    a(f"- 规格版本：`{manifest['spec_version']}`")
    a(f"- run_id：`{manifest['run_id']}`")
    a(f"- 起止时间：{manifest['started_at']} → {manifest['finished_at']}")
    b = manifest["scope_bindings"]
    a(f"- 绑定范围：inbox_root=`{b['inbox_root']}`，sources_root=`{b['sources_root']}`")
    a(f"- record_stores：{', '.join('`' + s + '`' for s in b['record_stores']) or '（无）'}")
    a(f"- binding_reference：{b['binding_reference']}")
    a(f"- 范围完整：{manifest['scope_complete']}；扫描错误数：{len(manifest['scan_errors'])}")
    a(f"- 运行结果：{'SUCCESS' if manifest['scope_complete'] else 'FAILED'}")
    a("")
    a("## 数量对账")
    a("")
    a(f"- 对象总数：{total}")
    a("- 按 disposition：" + "、".join(f"{k}={v}" for k, v in sorted(by_disp.items())))
    a("- 按 processing_status：" + "、".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    a(f"- 分项合计校验：disposition 合计={sum(by_disp.values())}，"
      f"processing_status 合计={sum(by_status.values())}，总数={total}")
    a("")
    a("## 范围异常")
    a("")
    if manifest["scan_errors"]:
        for e in manifest["scan_errors"]:
            a(f"- [{e['reason_code']}] `{_md_escape(e['locator'])}` — {_md_escape(e['detail'])}")
    else:
        a("无。")
    a("")
    a("## 逐对象索引")
    a("")
    a("| file_id | 定位器 | processing_status | disposition | 最终位置 | 证据 |")
    a("|---|---|---|---|---|---|")
    for o in objs:
        anchor = _anchor(o["file_id"])
        a(f'<a id="{anchor}"></a>')
        a(f"| `{_md_escape(o['file_id'])}` | `{_md_escape(_locator_of(o, manifest))}` | "
          f"{o['processing_status']} | {o['disposition']} | "
          f"`{_md_escape(o['final_parent'] or '-')}` | "
          f"{', '.join('`' + e + '`' for e in o['evidence_reference'])} |")
    a("")
    a("## REVIEW_REQUIRED 清单")
    a("")
    if review_objs:
        for o in review_objs:
            anchor = _anchor(o["file_id"])
            reasons = [ev_by_id[e] for e in o["evidence_reference"]
                       if ev_by_id[e]["kind"] == "REVIEW_REASON"]
            r = reasons[0] if reasons else {}
            a(f"- file_id：`{_md_escape(o['file_id'])}`")
            a(f"  - 原始定位器：`{_md_escape(_locator_of(o, manifest))}`")
            a(f"  - 稳定锚点：`{anchor}`")
            a(f"  - 原因码：`{r.get('reason_code', '-')}`")
            a(f"  - 说明：{_md_escape(r.get('detail', '-'))}")
            evs = ", ".join(f"`{e}`" for e in o["evidence_reference"])
            a(f"  - 证据 ID：{evs}")
            a(f"  - 人工核查动作：打开定位器核实对象现状，确认关联记录与状态，"
              "补充缺失证据后重新判定。")
    else:
        a("无。")
    a("")
    a("## 抽查导航（候选对象，非验收结论）")
    a("")
    a("按 disposition / processing_status 列出候选 file_id，供 Human 抽查采样：")
    a("")
    for disp in sorted(by_disp):
        for st in sorted(by_status):
            cands = [o["file_id"] for o in objs
                     if o["disposition"] == disp and o["processing_status"] == st]
            if cands:
                shown = ", ".join(f"`{_md_escape(c)}`" for c in cands[:20])
                more = f"（等，共 {len(cands)} 个）" if len(cands) > 20 else ""
                a(f"- {disp} / {st}：{shown}{more}")
    a("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _locator_of(obj: dict, manifest: dict) -> str:
    for eid in obj["evidence_reference"]:
        for e in manifest["evidence"]:
            if e["evidence_id"] == eid and e["kind"] == "OBJECT":
                return e["locator"]
    return obj["file_id"]


def _paths_overlap(a: Path, b: Path) -> bool:
    try:
        a_r, b_r = a.resolve(), b.resolve()
    except OSError:
        return False
    return a_r == b_r or b_r in a_r.parents or a_r in b_r.parents


def run_scan(bindings_path: Path, reports_dir: Path) -> tuple[int, str]:
    """执行一次扫描。返回 (exit_code, message)。"""
    started_at = utcnow()
    try:
        bindings = load_bindings(bindings_path)
    except ScanError as exc:
        return 1, f"配置失败: {exc}"

    roots = [bindings["inbox_root"], bindings["sources_root"]]
    roots_abs = [str(r.resolve()) for r in roots]

    reports_dir = reports_dir.resolve()
    for r in roots:
        if _paths_overlap(r, reports_dir):
            return 1, "配置失败: reports/ 与扫描根重叠，拒绝运行"
    if _paths_overlap(roots[0], roots[1]):
        return 1, "配置失败: 两个扫描根重叠，拒绝运行"
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 1, f"输出失败: reports/ 不可写: {exc}"

    # 枚举前后稳定性快照（§1.2）：目录成员 + record-store 集合。
    before = _snapshot_dirs(roots)
    before_stores = _snapshot_stores(bindings["record_stores"])
    all_objects: list[dict] = []
    scan_errors: list[dict] = []
    for root_id, root in (("inbox", roots[0]), ("sources", roots[1])):
        objs, errs = enumerate_fs(root_id, root)
        all_objects.extend(objs)
        scan_errors.extend(errs)
    after = _snapshot_dirs(roots)
    if before != after:
        changed = [k for k in before if before.get(k) != after.get(k)]
        scan_errors.append({
            "locator": "; ".join(changed[:10]),
            "reason_code": "SCOPE_CHANGED_DURING_SCAN",
            "detail": f"枚举前后 {len(changed)} 个目录成员发生变化，范围不完整，本次运行失败。",
        })

    # 关联记录。
    try:
        records = load_record_stores(bindings["record_stores"])
    except ScanError as exc:
        return 1, f"配置失败: {exc}"
    # record-store 在加载前后必须稳定（§1.2 记录集合变化 -> 运行失败）。
    after_stores = _snapshot_stores(bindings["record_stores"])
    if before_stores != after_stores:
        changed_stores = [k for k in before_stores
                          if before_stores.get(k) != after_stores.get(k)]
        scan_errors.append({
            "locator": "; ".join(changed_stores[:10]),
            "reason_code": "RECORD_STORE_CHANGED_DURING_SCAN",
            "detail": "record-store 集合在枚举前后发生变化，范围不完整，本次运行失败。",
        })

    observed_at = utcnow()
    out_records: list[dict] = []
    out_evidence: list[dict] = []
    # 先判定文件系统对象，并收集命中的关联记录作为闭包种子。
    seed_records: list[dict] = []
    for obj in all_objects:
        hits = match_records(obj, records)
        rec, evs = decide(obj, hits, bindings["status_map"], roots_abs, observed_at)
        out_records.append(rec)
        out_evidence.extend(evs)
        seed_records.extend(hits)
    # 规格 §1.1：逻辑记录每条一个对象；关联的关联继续追踪，循环按 UID 去重。
    # 去重种子（同一记录被多个对象命中）。
    seen_seed: set[int] = set()
    uniq_seeds: list[dict] = []
    for s in seed_records:
        if id(s) not in seen_seed:
            seen_seed.add(id(s))
            uniq_seeds.append(s)
    rec_index = build_record_index(records)
    closure = transitive_closure(uniq_seeds, rec_index)
    container_recs, container_evs = emit_store_containers(bindings, observed_at)
    out_records.extend(container_recs)
    out_evidence.extend(container_evs)
    for rec in closure:
        rrec, revs = decide_record(rec, f"record-store/{rec['_store']}",
                                   bindings["status_map"], observed_at)
        out_records.append(rrec)
        out_evidence.extend(revs)

    finished_at = utcnow()
    # run_id 必须每次新建且不覆盖已有产物。
    run_id = f"r1-{finished_at.replace(':', '').replace('-', '')}-{secrets.token_hex(4)}"
    manifest_path = reports_dir / f"{run_id}.manifest.json"
    report_path = reports_dir / f"{run_id}.report.md"
    if manifest_path.exists() or report_path.exists():
        return 1, "输出失败: run_id 碰撞（极不可能），拒绝覆盖"

    manifest = build_manifest(bindings, out_records, out_evidence,
                              scan_errors, run_id, started_at, finished_at)
    try:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        write_report(manifest, report_path)
    except OSError as exc:
        return 1, f"输出失败: {exc}"

    if scan_errors:
        return 1, (f"范围不完整，运行失败（exit 1）。"
                   f"诊断已写入 {manifest_path} / {report_path}")
    return 0, (f"扫描成功（exit 0）。"
               f"对象数={len(out_records)}，REVIEW_REQUIRED="
               f"{sum(1 for r in out_records if r['disposition'] == 'REVIEW_REQUIRED')}。"
               f"产物：{manifest_path} / {report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r1-inventory",
                                     description="2.0.3-2 R1 只读盘点（规格 2.0.3-2-R1-Q1）")
    parser.add_argument("--scan", action="store_true", help="执行一次手工扫描")
    parser.add_argument("--bindings", type=Path, default=Path("bindings_2.0.3-2.json"),
                        help="只读绑定配置文件（JSON）")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"),
                        help="报告输出目录")
    args = parser.parse_args(argv)
    if not args.scan:
        parser.print_usage(sys.stderr)
        return 2
    code, message = run_scan(args.bindings, args.reports_dir)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
