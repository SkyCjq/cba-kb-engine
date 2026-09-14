"""Adapter for OCR-derived foreign registration tables (PNG sources).

OCR only produces table rows; club resolution, year inference and event
segmentation are shared with the workbook adapter. OCR output never rises
above auto_validated and never replaces targeted low-confidence review.
"""
import csv
import json
from pathlib import Path
from . import foreign

METHOD = 'paddleocr doc parsing + targeted recognition'


def load_rows(path):
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(newline='', encoding='utf-8-sig') as stream:
            return [row for row in csv.reader(stream)]
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        payload = payload.get('rows') or payload.get('tables') or []
    return [list(row) for row in payload]


def extract(path, season, source, clubs=None, strict=True, title=None, review=None):
    table = load_rows(path)
    source = {**source, 'method': source.get('method') or METHOD}
    result = foreign.build(table, season, source, clubs=clubs, strict=strict,
                           title=title, source_page=source.get('page', 'ocr'))
    result['report']['ocr'] = {'rows': len(table), 'low_confidence': review or []}
    return result
