from openpyxl import Workbook
import pytest
from pathlib import Path
from cba_kb.adapters import foreign_xlsx, foreign
from cba_kb.aliases import Clubs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'file-id', 'url': 'https://drive.google.com/file/d/file-id/view', 'type': 'gdrive'}
SEASON = '2024-2025'

TABLE = [
    ['2024-2025赛季CBA联赛外籍球员注册信息（3月31日上午10点注册截止）', None, None, None, None, None],
    ['球队', '外籍球员', None, '国籍（以注册时提供护照为准）', '场上位置', '球衣号码'],
    [None, '英文名', '中文名', None, None, None],
    ['宁波町渥', 'KOUAT NOI', '库阿特·诺伊', '澳大利亚', 'PF', 23],
    [None, 'RONALD MARCH', '罗纳德·马奇', '美国', 'SF', '01'],
    ['青岛国信制药', 'GEORGE KELL Ⅲ', '乔治·凯尔三世', '美国', 'C', 0],
    ['备注：8月21日天津先行者取消外籍球员DONELL COOPER（唐奈尔·库珀）的注册', None, None, None, None, None],
    ['4月5日浙江方兴渡取消外籍球员VLADYSLAV\u00a0 KORENIUK（科伦纽克）的注册', None, None, None, None, None],
    ['1月5日宁波町渥取消外籍球员LEONARD RANDALLⅡ（兰道尔二世）的注册', None, None, None, None, None],
]


def workbook(tmp_path, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    for row in rows:
        sheet.append(row)
    path = tmp_path/'foreign.xlsx'
    book.save(path)
    return path


def extract(tmp_path, rows=TABLE, strict=True):
    return foreign_xlsx.extract(workbook(tmp_path, rows), SEASON, SOURCE,
                                clubs=Clubs(ROOT, strict=strict), strict=strict)


def test_merged_header_forward_fill_and_text_codes(tmp_path):
    result = extract(tmp_path)
    snapshots = result['snapshots']
    assert [row['club_id'] for row in snapshots] == ['ningbo_fubang', 'ningbo_fubang', 'qingdao_guoxin_haitian']
    assert [row['jersey_number'] for row in snapshots] == ['23', '01', '0']
    assert result['report']['teams'] == 2
    assert result['report']['snapshot_as_of'] == '2025-03-31'
    first = snapshots[0]
    assert (first['name_en_raw'], first['name_zh_raw'], first['nationality'], first['position']) == \
           ('KOUAT NOI', '库阿特·诺伊', '澳大利亚', 'PF')
    assert first['club_source_name'] == '宁波町渥'
    assert first['snapshot_key'] == '2024-2025|2025-03-31|ningbo_fubang|KOUAT NOI|澳大利亚'


def test_raw_name_kept_while_normalized_name_is_cleaned(tmp_path):
    result = extract(tmp_path)
    assert result['snapshots'][2]['name_en_raw'] == 'GEORGE KELL Ⅲ'
    assert result['snapshots'][2]['name_en_normalized'] == 'GEORGE KELL III'
    assert result['events'][1]['player_name_en_raw'] == 'VLADYSLAV\u00a0 KORENIUK'
    assert result['events'][1]['player_name_en_normalized'] == 'VLADYSLAV KORENIUK'


def test_cancellation_events_and_year_inference(tmp_path):
    result = extract(tmp_path)
    events = result['events']
    assert [row['event_date'] for row in events] == ['2024-08-21', '2025-04-05', '2025-01-05']
    assert {row['event_type'] for row in events} == {'registration_cancelled'}
    assert {row['date_year_inferred'] for row in events} == {True}
    assert events[2]['player_name_en_normalized'] == 'LEONARD RANDALLII'
    assert result['report']['observed_registrations'] == 6
    assert [item['reason'] for item in result['report']['review']] == \
           ['event month outside the Oct-Mar window']


def test_strict_clubs_refuse_unknown_sponsors(tmp_path):
    rows = list(TABLE)
    rows[3] = ['未知球队', 'KOUAT NOI', '库阿特·诺伊', '澳大利亚', 'PF', 23]
    with pytest.raises(Exception, match='Unknown club name'):
        extract(tmp_path, rows, strict=True)
    lenient = extract(tmp_path, rows, strict=False)
    assert lenient['snapshots'][0]['club_id'] is None
    assert lenient['report']['unresolved_clubs'] == [{'name': '未知球队', 'season': SEASON}]


def test_header_change_is_rejected(tmp_path):
    rows = [row[:] for row in TABLE]
    rows[1] = ['球队', '外籍球员', None, '国籍', '位置', '球衣号码']
    with pytest.raises(ValueError, match='header changed'):
        foreign.columns(rows, 1)


def test_infer_year_boundaries():
    assert foreign.infer_year('2024-2025', 8) == (2024, True, False)
    assert foreign.infer_year('2024-2025', 12) == (2024, True, False)
    assert foreign.infer_year('2024-2025', 1) == (2025, True, False)
    assert foreign.infer_year('2024-2025', 3) == (2025, True, False)
    assert foreign.infer_year('2024-2025', 4) == (2025, True, True)
    with pytest.raises(ValueError):
        foreign.infer_year('2024-2025', 13)


def test_source_literal_text_is_not_reconstructed_from_normalized_values(tmp_path):
    rows = [row[:] for row in TABLE]
    rows[3] = ['宁波町渥', 'GEORGE KELL Ⅲ', '乔治·凯尔三世', '美国', 'C', '00']
    result = extract(tmp_path, rows)
    assert result['snapshots'][0]['jersey_number'] == '00'
    assert result['snapshots'][0]['raw_row_text'].endswith('C | 00')


def test_numeric_jersey_with_zero_padding_format(tmp_path):
    from openpyxl import load_workbook
    path = workbook(tmp_path, TABLE)
    book = load_workbook(path)
    book.active['F4'] = 0
    book.active['F4'].number_format = '00'
    book.save(path)
    book.close()
    result = foreign_xlsx.extract(path, SEASON, SOURCE, clubs=Clubs(ROOT))
    assert result['snapshots'][0]['jersey_number'] == '00'
    assert result['snapshots'][0]['raw_row_text'].endswith('PF | 00')
    assert result['snapshots'][2]['jersey_number'] == '0'
