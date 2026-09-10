"""Deterministic single-sheet XLSX writer for the fact products."""
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook

STAMP = datetime(2026, 1, 1, 0, 0, 0)
CREATOR = 'cba-kb-engine'


def write(path, headers, rows, sheet):
    if not headers or len(set(headers)) != len(headers):
        raise ValueError('Fact product headers must be unique and non-empty')
    book = Workbook()
    page = book.active
    page.title = sheet
    page.append(list(headers))
    for index, row in enumerate(rows, 1):
        if sorted(row) != sorted(headers):
            raise ValueError(f'Row {index} does not match the product schema')
        page.append([row[name] for name in headers])
    book.properties.created = STAMP
    book.properties.modified = STAMP
    book.properties.creator = CREATOR
    book.properties.lastModifiedBy = CREATOR
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path
