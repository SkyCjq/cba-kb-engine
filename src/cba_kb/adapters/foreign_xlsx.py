"""Adapter for the CBA foreign player registration workbook."""
from pathlib import Path
from openpyxl import load_workbook
from . import foreign

METHOD = 'xlsx merge-header segmentation + season-aware cancellation parsing'


def rows(path, sheet=None):
    book = load_workbook(Path(path), read_only=True, data_only=True)
    try:
        name = sheet or book.sheetnames[0]
        if book.sheetnames != [name]:
            raise ValueError('Foreign source must stay a single-sheet file')
        return name, [list(values) for values in book[name].values]
    finally:
        book.close()


def extract(path, season, source, clubs=None, strict=True, sheet=None):
    page, table = rows(path, sheet)
    title = str(table[0][0]) if table and table[0] and table[0][0] else None
    source = {**source, 'method': source.get('method') or METHOD}
    return foreign.build(table, season, source, clubs=clubs, strict=strict,
                         title=title, source_page=page)
