from pathlib import Path
import pytest
from cba_kb.adapters import midseason_md
from cba_kb.aliases import Clubs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'md-file-id', 'url': 'https://drive.google.com/file/d/md-file-id/view', 'type': 'gdrive'}
REAL = ROOT/'.staging/review-0910/bayi.md'

SAMPLE = '''# 原八一男篮球员 2020-2021 赛季中期转会核验

## 北京首钢 / 田宇翔
- record_key: 2020-2021|beijing_shougang|田宇翔
- season: 2020-2021
- club_id: beijing_shougang
- club_official: 北京首钢
- sequence: 2
- player: 田宇翔
- registration_stage: 变更
- registration_method: 自由球员认领 / 签约
- contract_category: C类
- contract_term_official: 2年5个月
- former_club: 八一男篮
- transaction_date: 2021-02-26
- club_announcement_date: 2021-03-01
- notice_deadline: 2021-02-27（该赛季中期国内球员注册窗口截止日）
- registration_status: 完成注册
- remarks: 3月1日正式官宣。
- source_url: https://example.invalid/a
- source_url_secondary: https://example.invalid/b
- source_type: web_cross_checked
- extraction_method: manual web verification + source cross-check
- verification_level: manually_verified
'''


def test_block_fields_and_grain_mapping(tmp_path):
    path = tmp_path/'bayi.md'
    path.write_text(SAMPLE)
    result = midseason_md.extract(path, '2020-2021', SOURCE, clubs=Clubs(ROOT), strict=True)
    domestic, event = result['domestic'][0], result['events'][0]
    assert domestic['record_key'] == '2020-2021|beijing_shougang|田宇翔'
    assert domestic['former_club'] == '八一男篮'
    assert domestic['notice_deadline'] is None
    assert domestic['registration_stage'] == '变更'
    assert domestic['verification_level'] == 'human_verified'
    assert domestic['source_page'] is None
    assert event['event_type'] == 'registration_change'
    assert event['registration_window_deadline'] == '2021-02-27'
    assert event['event_date'] == '2021-02-26'
    assert event['club_announcement_date'] == '2021-03-01'
    assert event['date_year_inferred'] is False
    assert event['from_club_id'] == 'bayi'
    assert event['source_url_primary'] == 'https://example.invalid/a'
    assert event['source_url_secondary'] == 'https://example.invalid/b'
    assert result['report']['notice_deadline_written'] == 0


def test_record_key_mismatch_is_rejected(tmp_path):
    path = tmp_path/'bayi.md'
    path.write_text(SAMPLE.replace('2020-2021|beijing_shougang|田宇翔', '2020-2021|bayi|田宇翔'))
    with pytest.raises(ValueError, match='record_key'):
        midseason_md.extract(path, '2020-2021', SOURCE, clubs=Clubs(ROOT))


def test_missing_window_deadline_is_rejected(tmp_path):
    path = tmp_path/'bayi.md'
    path.write_text(SAMPLE.replace('- notice_deadline: 2021-02-27（该赛季中期国内球员注册窗口截止日）\n', ''))
    with pytest.raises(ValueError, match='window deadline'):
        midseason_md.extract(path, '2020-2021', SOURCE, clubs=Clubs(ROOT))


@pytest.mark.skipif(not REAL.exists(), reason='real midseason source not staged locally')
def test_real_source_records():
    result = midseason_md.extract(REAL, '2020-2021', SOURCE, clubs=Clubs(ROOT), strict=True)
    assert result['report']['records'] == 14
    assert len(result['events']) == 14
    assert {row['registration_window_deadline'] for row in result['events']} == {'2021-02-27'}
    assert {row['notice_deadline'] for row in result['domestic']} == {None}
    assert {row['verification_level'] for row in result['domestic']} == {'human_verified'}
    assert len({row['club_id'] for row in result['domestic']}) == 9
