import json
from pathlib import Path

import pytest

from scripts.sync_v1_5_2_candidate import (
    SHEET_NAMES,
    compare_values,
    local_checks,
    pad_row,
    trim_empty_rows,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'workspace/candidates/v1.5.2-verified-20260911/domain.xlsx'
ACCEPTANCE = ROOT / 'docs/validation/v1.5.2-source-acceptance-20260911.json'


@pytest.mark.skipif(not CANDIDATE.exists(),
                    reason='real DRAFT-2 candidate is intentionally not in Git')
def test_candidate_acceptance_contract():
    values, checks = local_checks(CANDIDATE, ACCEPTANCE)
    assert checks['rows_total'] == 600
    assert checks['production_eligible'] is False
    counts = {table: len(values[sheet]) - 1 for table, sheet in SHEET_NAMES.items()}
    assert counts == checks['counts']
    compare_values(values, values)


@pytest.mark.skipif(not CANDIDATE.exists(),
                    reason='real DRAFT-2 candidate is intentionally not in Git')
def test_pad_row_normalizes_empty_cells():
    assert pad_row(['x', '', 1.0], 4) == ['x', None, 1, None]
    assert trim_empty_rows([['x'], [None], ['']]) == [['x']]
    assert json.loads(ACCEPTANCE.read_text())['workbook_sha256'] == checksum(CANDIDATE)


def checksum(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
