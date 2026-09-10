#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the saved real CBA API probe fixture."""

import json
import unittest
from pathlib import Path

import fetch_cba


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture_6a97f61ed5_20260908T114135Z.json"


class CBAParserRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.path, cls.html = fetch_cba.find_html(cls.payload)
        cls.rows = fetch_cba.parse_table(cls.html)

    def test_real_probe_schema(self):
        self.assertTrue({"code", "message", "data"}.issubset(self.payload.keys()))
        self.assertEqual(self.path, ".data.detail_content")
        self.assertIn("<table", self.html.lower())

    def test_real_probe_player_rows(self):
        self.assertEqual(len(self.rows), 14)
        self.assertEqual(
            [row["序 号"] for row in self.rows],
            [str(i) for i in range(1, 15)],
        )
        self.assertEqual(self.rows[0]["运动员"], "杨瀚森")
        self.assertEqual(self.rows[-1]["运动员"], "王奕博")

    def test_non_player_lines_not_parsed(self):
        for row in self.rows:
            self.assertTrue(row["序 号"].isdigit())
            self.assertNotIn("更新时间", row["运动员"])
            self.assertNotIn("注：", row["运动员"])


if __name__ == "__main__":
    unittest.main()
