"""Regressions found by comparing DRAFT-2 staging output to the source cells."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

from openpyxl import load_workbook
import openpyxl.writer.excel
import pytest

from cba_kb.aliases import Clubs, UnresolvedClub
from cba_kb.registration_domain import (
    parse_domestic_movement, parse_foreign_rights, validate_domain, _date_field, TABLES,
)
from cba_kb.domain_xlsx import write as domain_write
from cba_kb.xlsx import write as xlsx_write

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'test-source', 'url': 'https://example.invalid/audit'}

DOMESTIC = """# 4. 2020-2021 赛季
- window_start: 2021-02-24
- window_end: 2021-02-27
- allowed_methods: 自由球员认领、球员互换
- ordinary_transfer_allowed: false

## 4.2 完成注册
| 日期/节点 | 球员 | 前一俱乐部/状态 | 新俱乐部 | 方式 |
|---|---|---|---|---|
| 2021-02-25 | 田桂森 | 山西 | 福建 | 自由球员认领 |
"""
FOREIGN = """## 0.4 历史交易
| 目标赛季 | 官方更新/事件日期 | 外籍球员 | 原权利俱乐部 | 交易后权利俱乐部 |
|---|---|---|---|---|
| 2026-2027 | 2026-08-05 | 巴里·布朗 | 浙江广厦 | 北京首钢 |

## 2026-2027赛季优先续约权交易信息
* **更新时间**：2026年8月5日
| 外籍球员 | 享有优先续约权俱乐部 | 交易后获得优先续约权俱乐部 |
|---|---|---|
| 巴里·布朗 (BARRY BROWN) | 浙江广厦 | **北京首钢** |
| TY LEAF / 泰·利夫 | 南京同曦 | / |
"""
RENEWAL = """## 俱乐部行使2021-2022赛季外籍球员优先续约权名单
* **发布日期**：2021年10月8日
| 球队 | 外籍球员（英文名 / 中文名） |
|---|---|
| 辽宁本钢 | KYLE FOGG / 凯尔·弗格 |

## 俱乐部对2024-2025赛季注册外籍球员行使优先续约权名单公示
* **发布日期**：2025年7月21日
| 俱乐部 | 外籍球员 | 续约权状态 |
|---|---|---|
| 青岛国信海天 | JORDAN MICKEY / 乔丹·米奇 | 双方已完成续约 |
"""
SNAPSHOT = """## 2024-2025赛季外籍球员注册信息（截至3月31日）
* **发布日期**：2025年3月29日（3月31日上午10点注册截止）
| 俱乐部 | 外籍球员（英文名 / 中文名） | 国籍 | 位置 | 球衣号码 |
|---|---|---|---|---|
| **北京北汽** | GEORGE KELL Ⅲ / 乔治·凯尔三世 | 美国 | C | 00 |
"""


@pytest.mark.parametrize('value', ['2024-12-04~12-10', '约2024-12-04', '2025-01-16前完成', '2025-11-24官宣'])
def test_imprecise_dates_stay_pending(value):
    assert _date_field(value) == (None, 'pending_evidence')


def test_invalid_calendar_date_is_rejected():
    with pytest.raises(ValueError):
        _date_field('2025-02-30')


def test_domestic_date_and_source_locator():
    result = parse_domestic_movement(DOMESTIC, SOURCE, Clubs(ROOT))
    event = result['registration_status_events'][0]
    window = result['domestic_transaction_windows'][0]
    assert event['event_date'] == '2021-02-25'
    assert event['registration_window_deadline'] == '2021-02-27'
    assert window['source_page_or_row'] == 'Markdown line 2'
    assert window['allowed_methods'] == ['free_agent_claim', 'player_swap']
    assert window['ordinary_transfer_allowed'] is False
    assert window['loan_allowed'] is None
    assert event['raw_event_text'] == DOMESTIC.splitlines()[-1]
    assert event['source_authority'] == 'B2_secondary_cross_check'
    validate_domain(result)


def test_announcement_not_registration_date():
    text = DOMESTIC.replace('2021-02-25', '2025-11-24官宣')
    result = parse_domestic_movement(text, SOURCE, Clubs(ROOT))
    event = result['registration_status_events'][0]
    assert event['club_announcement_date'] == '2025-11-24'
    assert event['event_date'] is None
    assert event['registration_submission_date'] is None
    assert event['registration_completed_date'] is None


def test_window_label_does_not_hide_date_and_research_label_is_separate():
    text = '''# 9. 2025-2026 赛季
## 9.2 当前已确认/较高置信度流动
| 窗口 | 球员 | 前一状态 | 新俱乐部 | 研究标签 | 关键节点 |
|---|---|---|---|---|---|
| Window 1 | 黄荣奇 | 自由球员 | 南京同曦 | 自由球员认领 | 2025-11-24官宣 |
| Window 2 | 罗汉琛 | 自由球员 | 北京控股 | release_then_claim | 2026-01-16 |
'''
    records = parse_domestic_movement(text, SOURCE, Clubs(ROOT))
    first, second = records['registration_status_events']
    assert first['club_announcement_date'] == '2025-11-24'
    assert first['event_date'] is None
    assert second['event_date'] == '2026-01-16'
    assert second['event_type'] == 'release_then_claim'
    assert second['registration_method'] == '自由球员认领'
    assert second['verification_status'] == 'auto_validated'
    assert records['domestic_registrations'][1]['registration_method_official'] == '自由球员认领'
    validate_domain(records)


def test_malformed_rows_and_strict_unknown_club_do_not_silently_disappear():
    with pytest.raises(ValueError, match='Malformed'):
        parse_domestic_movement(DOMESTIC.replace(' | 自由球员认领 |', ' |'), SOURCE, Clubs(ROOT))
    with pytest.raises(UnresolvedClub):
        parse_domestic_movement(DOMESTIC.replace('福建', '未知球队'), SOURCE, Clubs(ROOT))


def test_historical_transaction_preserves_both_provenance_rows():
    result = parse_foreign_rights(FOREIGN, SOURCE, Clubs(ROOT))
    assert len(result['foreign_priority_right_transactions']) == 1
    assert len(result['foreign_priority_right_snapshots']) == 2  # history is not an invented snapshot
    tx = result['foreign_priority_right_transactions'][0]
    assert tx['transaction_date'] == '2026-08-05'
    assert tx['player_name_en_raw'] == 'BARRY BROWN'
    assert tx['player_name_zh_raw'] == '巴里·布朗'
    assert len(tx['raw_transaction_text'].splitlines()) == 2
    rights = result['foreign_priority_right_snapshots']
    assert rights[0]['club_id'] == 'beijing_shougang'  # actual holder after transfer
    assert rights[0]['snapshot_date'] == '2026-08-05'
    assert rights[1]['transaction_status'] == 'not_shown_as_transferred'
    assert rights[1]['right_status'] is None  # '/' does not say "continued"
    validate_domain(result)


def test_distinct_dated_transfers_are_not_deduped():
    extra = '| 2026-2027 | 2026-07-01 | 巴里·布朗 | 浙江广厦 | 北京首钢 |'
    text = FOREIGN.replace('\n\n## 2026', '\n' + extra + '\n\n## 2026', 1)
    result = parse_foreign_rights(text, SOURCE, Clubs(ROOT))
    dated = [r for r in result['foreign_priority_right_transactions'] if r['transaction_date']]
    assert len(dated) == 2


def test_statusless_renewal_table_and_next_season():
    result = parse_foreign_rights(RENEWAL, SOURCE, Clubs(ROOT))
    rows = result['foreign_priority_right_snapshots']
    assert len(rows) == 2
    assert [(r['source_registration_season'], r['right_target_season']) for r in rows] == [
        ('2020-2021', '2021-2022'), ('2024-2025', '2025-2026')]
    assert [r['right_status'] for r in rows] == ['exercised', 'renewal_completed']
    assert rows[1]['renewal_completed'] is True
    validate_domain(result)


def test_combined_snapshot_names_date_and_text_number():
    rows = parse_foreign_rights(SNAPSHOT, SOURCE, Clubs(ROOT))['foreign_registration_snapshots']
    assert rows[0]['name_en_raw'] == 'GEORGE KELL Ⅲ'
    assert rows[0]['name_en_normalized'] == 'GEORGE KELL III'
    assert rows[0]['name_zh_raw'] == '乔治·凯尔三世'
    assert rows[0]['jersey_number'] == '00'
    assert rows[0]['snapshot_as_of'] == '2025-03-31'
    assert rows[0]['raw_row_text'] == SNAPSHOT.splitlines()[-1]


@pytest.mark.parametrize('field,value', [
    ('source_authority', 'official_maybe'), ('event_date', '2021-02-30'),
    ('event_date_status', 'unknown'), ('registration_submission_date', 'pending_official_snapshot'),
])
def test_validator_rejects_inconsistent_fact(field, value):
    result = parse_domestic_movement(DOMESTIC, SOURCE, Clubs(ROOT))
    result['registration_status_events'][0][field] = value
    with pytest.raises(ValueError):
        validate_domain(result)


@pytest.mark.parametrize('kind', ['single', 'domain'])
def test_xlsx_reproducible_across_save_times_and_literal_strings(tmp_path, monkeypatch, kind):
    outputs = []
    for day in (1, 2):
        class Clock:
            @classmethod
            def now(cls, **kwargs):
                return datetime(2026, 9, day, tzinfo=timezone.utc)
        monkeypatch.setattr(openpyxl.writer.excel, 'datetime', SimpleNamespace(datetime=Clock, timezone=timezone))
        path = tmp_path / f'{day}.xlsx'
        if kind == 'single':
            xlsx_write(path, ['name'], [{'name': '=1+1'}], 'facts')
        else:
            result = parse_foreign_rights(SNAPSHOT, SOURCE, Clubs(ROOT))
            result['foreign_registration_snapshots'][0]['raw_row_text'] = '=1+1'
            domain_write(path, result)
        outputs.append(path.read_bytes())
        book = load_workbook(path, data_only=False)
        assert any(c.value == '=1+1' and c.data_type == 's'
                   for sheet in book for row in sheet for c in row)
        book.close()
    assert outputs[0] == outputs[1]


def test_cli_writes_partial_validation_and_reports_unresolved_clubs(tmp_path):
    domestic = tmp_path / 'domestic.md'
    foreign = tmp_path / 'foreign.md'
    domestic.write_text(DOMESTIC.replace('福建', '未知球队'))
    foreign.write_text(SNAPSHOT)
    target = tmp_path / 'domain.xlsx'
    command = [sys.executable, '-m', 'cba_kb.cli', 'domain',
               '--domestic-source', str(domestic), '--rights-source', str(foreign),
               '--domestic-source-id', 'domestic-fixture', '--rights-source-id', 'foreign-fixture',
               '--output', str(target), '--lenient-clubs']
    import os, json
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src')}
    process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    report = json.loads(target.with_suffix('.validation.json').read_text())
    assert report['status'] == 'CANDIDATE_PARTIAL'
    assert report['production_eligible'] is False
    assert report['unresolved_clubs'] == [{'name': '未知球队', 'season': '2020-2021'}]
    assert report['remaining_scope']
    frozen = target.read_bytes()
    assert subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode != 0
    assert target.read_bytes() == frozen
