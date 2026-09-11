from pathlib import Path

import pytest

from cba_kb.aliases import Clubs
from cba_kb.registration_domain import (
    EVENT_DOMAINS, EVENT_STATUSES, EVENT_TYPES, RIGHT_STATUSES,
    parse_domestic_movement, parse_foreign_rights, validate_domain,
)
from cba_kb.domain_xlsx import write as write_domain


ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'source-id', 'url': 'https://example.invalid/source', 'type': 'gdrive'}


def test_domestic_parser_separates_window_relation_and_event():
    text = '''# 4. 2020-2021 赛季
## 4.2 完成注册的22名球员
| 日期/节点 | 球员 | 前一俱乐部/状态 | 新俱乐部 | 方式 | 分组 |
|---|---|---|---|---|---|
| 2021-02-25 | 田桂森 | 山西 | 福建 | 自由球员认领 | 非八一 |
## 4.5 窗口期未完成注册的7人
| 球员 | 最近/原俱乐部 | 结果 |
|---|---|---|
| 张祖铭 | 八一 | 未完成注册 |
'''
    clubs = Clubs(ROOT, strict=False)
    result = parse_domestic_movement(text, SOURCE, clubs)
    assert len(result['domestic_registrations']) == 1
    assert len(result['registration_status_events']) == 2
    assert result['registration_status_events'][1]['event_status'] == 'not_completed'
    assert result['domestic_registrations'][0]['movement_type'] == 'free_agent_claim'
    validate_domain(result)


def test_foreign_right_parser_does_not_turn_slash_into_transfer():
    text = '''## 2026-2027赛季（最新优先续约权交易信息）
### 1. 交易信息
| 序号 | 外籍球员 | 享有优先续约权俱乐部 | 交易后获得优先续约权俱乐部 |
|---|---|---|---|
| 1 | BARRY BROWN / 巴里·布朗 | 浙江广厦 | 北京首钢 |
| 2 | TY LEAF / 泰·利夫 | 南京同曦 | / |
'''
    clubs = Clubs(ROOT, strict=False)
    result = parse_foreign_rights(text, SOURCE, clubs)
    assert len(result['foreign_priority_right_transactions']) == 1
    assert len(result['foreign_priority_right_snapshots']) == 2
    assert result['foreign_priority_right_transactions'][0]['to_club_id'] == 'beijing_shougang'
    validate_domain(result)


def test_domain_validation_rejects_uncontrolled_status():
    text = '''## 2026-2027赛季（最新优先续约权交易信息）
| 序号 | 外籍球员 | 享有优先续约权俱乐部 | 交易后获得优先续约权俱乐部 |
|---|---|---|---|
| 1 | TY LEAF / 泰·利夫 | 南京同曦 | / |
'''
    records = parse_foreign_rights(text, SOURCE, Clubs(ROOT, strict=False))
    row = records['foreign_priority_right_snapshots'][0]
    row['right_status'] = 'free_text'
    with pytest.raises(ValueError, match='right status'):
        validate_domain(records)


def test_draft2_enums_are_explicit():
    assert 'foreign_usage' in EVENT_DOMAINS
    assert 'usage_suspended' in EVENT_TYPES
    assert 'system_error' in EVENT_STATUSES
    assert 'renewal_completed' in RIGHT_STATUSES


def test_usage_list_is_a_status_event_and_historical_transactions_are_deduped(tmp_path):
    text = '''## 2019-2020 复赛参赛俱乐部申请暂停使用外援及亚外名单
| 序号 | 姓名 | 类别 | 俱乐部（简称） |
|---|---|---|---|
| 1 | JAMES | 外援 | 北京 |
'''
    records = parse_foreign_rights(text, SOURCE, Clubs(ROOT, strict=False))
    assert records['registration_status_events'][0]['event_type'] == 'usage_suspended'
    assert records['registration_status_events'][0]['event_domain'] == 'foreign_usage'
    validate_domain(records)


def test_domain_workbook_has_six_tabs(tmp_path):
    records = parse_foreign_rights(
        '''## 2026-2027赛季（最新优先续约权交易信息）
| 序号 | 外籍球员 | 享有优先续约权俱乐部 | 交易后获得优先续约权俱乐部 |
|---|---|---|---|
| 1 | BARRY BROWN / 巴里·布朗 | 浙江广厦 | 北京首钢 |
''', SOURCE, Clubs(ROOT, strict=False))
    path = write_domain(tmp_path / 'domain.xlsx', {
        'domestic_registrations': [], 'domestic_transaction_windows': [],
        'registration_status_events': records['registration_status_events'],
        'foreign_registration_snapshots': records['foreign_registration_snapshots'],
        'foreign_priority_right_snapshots': records['foreign_priority_right_snapshots'],
        'foreign_priority_right_transactions': records['foreign_priority_right_transactions'],
    })
    from openpyxl import load_workbook
    assert load_workbook(path, read_only=True).sheetnames == [
        'domestic_registrations', 'domestic_transaction_windows', 'registration_status_events',
        'foreign_registration_snapshots', 'foreign_right_snapshots',
        'foreign_right_transactions']
