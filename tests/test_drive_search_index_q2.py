"""Q2 follow-up tests for PR #191 (Issue #189).

Covers the four Cat-2 findings from independent Q2:
1. Chinese query precision (single-char CJK not matching multi-char queries)
2. --texts CLI validation (missing file, invalid JSON, non-object)
3. Record fallback chain (body_text/text/ocr_text) with type validation
4. Negative isolation for fallback paths
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cba_kb import drive_search_index as si

SRC_DIR = Path(__file__).parent.parent / "src"


def _grant(file_id, **over):
    g = {"approved": True, "approver": "boss", "file_id": file_id,
         "scope": "drive:read", "granted_at": "2026-10-15T00:00:00Z",
         "note": ""}
    g.update(over)
    return g


def _rec(name, status, file_id, grant=None, **extra):
    r = {"name": name, "rights_status": status}
    if grant is not None:
        r["rights_grant"] = grant
    r.update(extra)
    return r


def _search(idx, reg, q):
    return si.search_index(idx, q, "alice", reg)


class TestRecordFallbackChain:
    """Cat-2 #3, #4: record.get() fallback chain coverage."""

    def test_body_text_field_indexed(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          body_text="深圳湾体育中心")}
        idx = si.build_index(reg)
        r = _search(idx, reg, "体育中心")
        assert [x["file_id"] for x in r["results"]] == ["d1"]

    def test_text_field_indexed(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          text="深圳湾体育中心")}
        idx = si.build_index(reg)
        r = _search(idx, reg, "体育中心")
        assert [x["file_id"] for x in r["results"]] == ["d1"]

    def test_ocr_text_field_indexed(self):
        reg = {"d1": _rec("扫描件", "approved", "d1", _grant("d1"),
                          ocr_text="深圳湾体育中心活动记录")}
        idx = si.build_index(reg)
        r = _search(idx, reg, "体育中心")
        assert [x["file_id"] for x in r["results"]] == ["d1"]

    def test_explicit_texts_dict_takes_precedence(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          ocr_text="苹果香蕉")}
        idx = si.build_index(reg, {"d1": "西瓜葡萄"})
        # "西瓜" should be indexed, "苹果" should not
        r = _search(idx, reg, "西瓜")
        assert [x["file_id"] for x in r["results"]] == ["d1"]
        r = _search(idx, reg, "苹果")
        assert r["results"] == []

    def test_explicit_empty_string_does_not_fall_through(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          ocr_text="苹果香蕉")}
        idx = si.build_index(reg, {"d1": ""})
        r = _search(idx, reg, "苹果")
        assert r["results"] == []

    def test_non_string_body_text_raises(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          body_text=12345)}
        with pytest.raises(ValueError, match="SEARCH_BODY_TEXT_NOT_STRING"):
            si.build_index(reg)

    def test_non_string_ocr_text_raises(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"),
                          ocr_text=["not", "a", "string"])}
        with pytest.raises(ValueError, match="SEARCH_BODY_TEXT_NOT_STRING"):
            si.build_index(reg)

    def test_non_string_texts_dict_value_raises(self):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        with pytest.raises(ValueError, match="SEARCH_BODY_TEXT_NOT_STRING"):
            si.build_index(reg, {"d1": 999})

    def test_review_doc_with_ocr_text_isolated(self):
        reg = {"d1": _rec("待审文档", "review", "d1",
                          ocr_text="深圳秘密内容")}
        idx = si.build_index(reg)
        r = _search(idx, reg, "深圳")
        assert r["results"] == []

    def test_private_doc_with_body_text_isolated(self):
        reg = {"d1": _rec("私有文档", "private", "d1",
                          body_text="深圳秘密内容")}
        idx = si.build_index(reg)
        r = _search(idx, reg, "深圳")
        assert r["results"] == []

    def test_revoked_grant_with_ocr_text_excluded(self):
        reg = {"d1": _rec("文档", "approved", "d1",
                          _grant("d1", revoked=True),
                          ocr_text="深圳内容")}
        idx = si.build_index(reg)
        # Build-time: revoked grant → visible=False
        # Search-time: Phase 2 re-check also fails
        r = _search(idx, reg, "深圳")
        assert r["results"] == []


class TestChinesePrecision:
    """Cat-2 #1: single-char CJK should not match multi-char queries."""

    def test_single_char_doc_not_matched(self):
        reg = {
            "d_single": _rec("青", "approved", "d_single",
                             _grant("d_single")),
            "d_full": _rec("青春风暴纪录片", "approved", "d_full",
                           _grant("d_full")),
        }
        idx = si.build_index(reg)
        r = _search(idx, reg, "青春风暴")
        matched = [x["file_id"] for x in r["results"]]
        assert "d_full" in matched
        assert "d_single" not in matched

    def test_bigram_query_still_works(self):
        reg = {"d1": _rec("深圳男篮", "approved", "d1", _grant("d1"))}
        idx = si.build_index(reg)
        r = _search(idx, reg, "深圳")
        assert [x["file_id"] for x in r["results"]] == ["d1"]

    def test_single_char_query_still_works(self):
        # Explicit single-char query should still match
        reg = {"d1": _rec("青", "approved", "d1", _grant("d1"))}
        idx = si.build_index(reg)
        r = _search(idx, reg, "青")
        assert [x["file_id"] for x in r["results"]] == ["d1"]


class TestTextsCliValidation:
    """Cat-2 #2: --texts CLI validation."""

    def _run_build(self, tmp_path, reg_data, texts_arg=None):
        reg_p = tmp_path / "reg.json"
        idx_p = tmp_path / "idx.json"
        reg_p.write_text(json.dumps(reg_data, ensure_ascii=False),
                         encoding="utf-8")
        cmd = [_sys.executable, "-m", "cba_kb.cli", "drive-build-index",
               "--registry", str(reg_p), "--index", str(idx_p)]
        if texts_arg:
            cmd += ["--texts", str(texts_arg)]
        return subprocess.run(cmd, capture_output=True, text=True,
                              cwd=SRC_DIR)

    def test_missing_texts_file_fails(self, tmp_path):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        r = self._run_build(tmp_path, reg, tmp_path / "nonexistent.json")
        assert r.returncode != 0
        assert "TEXTS_FILE_NOT_FOUND" in r.stderr

    def test_texts_directory_fails(self, tmp_path):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        r = self._run_build(tmp_path, reg, tmp_path)
        assert r.returncode != 0
        assert "TEXTS_FILE_NOT_FOUND" in r.stderr

    def test_malformed_json_fails(self, tmp_path):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        r = self._run_build(tmp_path, reg, bad)
        assert r.returncode != 0
        assert "TEXTS_FILE_INVALID" in r.stderr

    def test_non_object_json_fails(self, tmp_path):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        arr = tmp_path / "arr.json"
        arr.write_text('["not", "an", "object"]', encoding="utf-8")
        r = self._run_build(tmp_path, reg, arr)
        assert r.returncode != 0
        assert "TEXTS_FILE_NOT_OBJECT" in r.stderr

    def test_valid_texts_file_succeeds(self, tmp_path):
        reg = {"d1": _rec("标题", "approved", "d1", _grant("d1"))}
        texts = tmp_path / "texts.json"
        texts.write_text(json.dumps({"d1": "正文内容"}),
                         encoding="utf-8")
        r = self._run_build(tmp_path, reg, texts)
        assert r.returncode == 0, r.stderr
