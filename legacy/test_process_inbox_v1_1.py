#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for CBA-KB v1.1 register-first source semantics."""

import tempfile
import unittest
from pathlib import Path

import process_inbox


class SourceRegistryLocationTest(unittest.TestCase):
    def test_default_source_registry_is_in_config(self):
        self.assertEqual(
            process_inbox.DEFAULT_SOURCE_REGISTRY,
            process_inbox.CONFIG_DIR / "source_registry.csv",
        )


class SourceRegistryTest(unittest.TestCase):
    def item(self, file_id="abc123", parent="inbox-parent"):
        return {
            "id": file_id,
            "name": "2023-2024赛季CBA联赛球员注册信息.pdf",
            "mimeType": "application/pdf",
            "size": "12345",
            "modifiedTime": "2026-09-08T00:00:00Z",
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            "parents": [parent],
            "relative_path": "CBA联赛球员注册信息/2023-2024赛季CBA联赛球员注册信息.pdf",
        }

    def test_registry_is_idempotent_by_drive_file_id(self):
        rows = []
        item = self.item()
        process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="not_started",
        )
        process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="needs_ocr",
            extraction_method="pdf_text_layer_probe",
            processed=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "drive:abc123")
        self.assertEqual(rows[0]["drive_file_id"], "abc123")

    def test_parent_change_does_not_change_source_identity(self):
        rows = []
        item = self.item()
        first = process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="extracted",
            extraction_method="pdf_text_layer",
            processed=True,
        )
        second = process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="extracted",
            extraction_method="pdf_text_layer",
            processed=True,
            current_parent_id="sources-parent",
        )
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(second["current_parent_id"], "sources-parent")

    def test_processing_never_downgrades_imported(self):
        rows = []
        item = self.item()
        row = process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="extracted",
            extraction_method="pdf_text_layer",
            processed=True,
        )
        row["business_status"] = "IMPORTED"
        row["records_imported"] = "344"
        process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="needs_ocr",
            extraction_method="pdf_text_layer_probe",
            processed=True,
        )
        self.assertEqual(rows[0]["business_status"], "IMPORTED")
        self.assertEqual(rows[0]["records_imported"], "344")

    def test_registry_round_trip(self):
        rows = []
        item = self.item()
        process_inbox.register_source(
            rows,
            item,
            relative_path=item["relative_path"],
            extraction_status="extracted",
            extraction_method="pdf_text_layer",
            processed=True,
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_registry.csv"
            process_inbox.save_source_registry(path, rows)
            loaded = process_inbox.load_source_registry(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["drive_file_id"], "abc123")
        self.assertEqual(loaded[0]["business_status"], "EXTRACTED")


class CanarySelectionTest(unittest.TestCase):
    def test_canary_requires_adversarial_classes(self):
        items = [
            {"id": "1", "name": "root.xlsx", "relative_path": "root.xlsx"},
            {"id": "2", "name": "2024-2025赛季CBA联赛球员注册信息.pdf", "relative_path": "CBA/2024-2025赛季CBA联赛球员注册信息.pdf"},
            {"id": "3", "name": "2022-2023赛季CBA联赛球员注册信息.pdf", "relative_path": "CBA/2022-2023赛季CBA联赛球员注册信息.pdf"},
            {"id": "4", "name": "深圳.png", "relative_path": "CBA/2022-2023/深圳.png"},
            {"id": "5", "name": "深圳-OCR.xlsx", "relative_path": "CBA/2023-2024/深圳-OCR.xlsx"},
        ]
        chosen = process_inbox.choose_canary(items)
        self.assertEqual([x["id"] for x in chosen], ["1", "2", "3", "4", "5"])


if __name__ == "__main__":
    unittest.main()
