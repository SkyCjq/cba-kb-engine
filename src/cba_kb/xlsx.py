"""Deterministic single-sheet XLSX writer for the fact products."""
from datetime import datetime
from pathlib import Path
import os
import re
import zipfile
from openpyxl import Workbook

STAMP = datetime(2026, 1, 1, 0, 0, 0)
CREATOR = 'cba-kb-engine'


def normalize_archive(path):
    """Freeze ZIP entry metadata so identical facts hash identically."""
    path = Path(path)
    temporary = path.with_name(path.name + '.deterministic')
    with zipfile.ZipFile(path, 'r') as source, zipfile.ZipFile(
            temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for info in source.infolist():
            fixed = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = info.external_attr
            fixed.internal_attr = info.internal_attr
            fixed.comment = info.comment
            fixed.extra = info.extra
            data = source.read(info.filename)
            if info.filename == 'docProps/core.xml':
                # openpyxl overwrites modified at save time, even after the
                # caller sets it. ZIP timestamps alone are not sufficient.
                data = re.sub(rb'(<dcterms:(?:created|modified)[^>]*>)[^<]+',
                              rb'\g<1>2026-01-01T00:00:00Z', data)
            target.writestr(fixed, data)
    os.replace(temporary, path)
    return path


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
        for cell in page[page.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    book.properties.created = STAMP
    book.properties.modified = STAMP
    book.properties.creator = CREATOR
    book.properties.lastModifiedBy = CREATOR
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return normalize_archive(path)
