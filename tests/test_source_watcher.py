from datetime import datetime, timezone
import json
from pathlib import Path
import random
from urllib.error import HTTPError

import pytest

from cba_kb.aliases import Clubs
from cba_kb.adapters import cba_registration
from cba_kb.source_watcher import run_source


ROOT = Path(__file__).resolve().parents[1]
INDEX_ID = '66b1de8bab'
TEAMS = (
    '北京控股', '北京首钢', '福建浔兴', '广东宏远', '广州龙狮',
    '江苏肯帝亚', '吉林九台农商行', '辽宁沈阳三生', '南京同曦',
    '宁波富邦', '青岛国信海天', '山东高速', '上海久事', '山西汾酒',
    '深圳新世纪', '四川锦城', '天津荣钢', '新疆广汇', '浙江稠州',
    '浙江广厦',
)


def source():
    return next(iter(cba_registration.registry().values()))


def child_raw(team, marker, contract_field='合同剩余年限', title_team=None):
    title = f'2026-2027赛季CBA联赛国内球员注册信息（{title_team or team}，9月1日）'
    html = f'''<table>
<tr><td colspan="9">{title}</td></tr>
<tr><td colspan="9">更新时间：2026年9月1日17:00</td></tr>
<tr><th>序 号</th><th>运动员</th><th>注册类型</th><th>合同类别</th>
<th>{contract_field}</th><th>原CBA俱乐部</th><th>公示截止时间</th><th>备 注</th></tr>
<tr><td>1</td><td>Player {marker}</td><td>延续</td><td>续约</td><td>C类</td>
<td>2027年7月31日</td><td>/</td><td>2026年9月1日17:00</td><td></td></tr>
</table>'''
    return json.dumps({
        'code': 200,
        'message': 'ok',
        'data': {'title': title, 'detail_content': html},
    }, ensure_ascii=False).encode()


def payloads(*, supplemental=False, contract_field='合同剩余年限',
             team_count=20, unknown_team=False):
    links = []
    child_payloads = {}
    for index, team in enumerate(TEAMS[:team_count]):
        article_id = f'{index + 1:010x}'
        anchor = team
        if unknown_team and index == team_count - 1:
            anchor = '中央陆军'
            team = '中央陆军'
        links.append(
            f'<p><a href="https://www.cbaleague.com/#/news-register/detail/'
            f'{article_id}">{anchor}</a></p>'
        )
        child_payloads[article_id] = child_raw(team, article_id, contract_field)
    if supplemental:
        article_id = 'ffffffffff'
        links.append(
            '<p><a href="https://www.cbaleague.com/#/news-register/detail/'
            f'{article_id}">吉林东北虎</a></p>'
        )
        child_payloads[article_id] = child_raw(
            '吉林九台农商行', article_id, contract_field, '吉林九台农商行',
        )
    index = json.dumps({
        'code': 200,
        'message': 'ok',
        'data': {'detail_content': ''.join(links)},
    }, ensure_ascii=False).encode()
    return {INDEX_ID: index, **child_payloads}


def fetcher_for(payload):
    def fetch(source):
        return payload[source['article_id']], 1
    return fetch


def fixed_source():
    value = source()
    return {**value, 'article_id': INDEX_ID}


def fixed_time():
    return datetime(2026, 9, 12, tzinfo=timezone.utc)


def test_official_adapter_contract_and_dynamic_discovery():
    contracts = cba_registration.registry()
    assert set(contracts) == {
        'cba-registration:2024-2025', 'cba-registration:2025-2026',
        'cba-registration:2026-2027',
    }
    payload = payloads()
    children = cba_registration.discover(payload[INDEX_ID], fixed_source())
    assert [child['article_id'] for child in children] == sorted(
        child['article_id'] for child in children
    )
    assert len(children) == 20
    rows = cba_registration.normalize_children(
        payload, children, fixed_source(), clubs=Clubs(ROOT),
    )
    assert len(rows) == 20
    assert len({row['canonical_team_id'] for row in rows}) == 20
    assert all(row['event_date'] is None and row['business_event'] is None for row in rows)


@pytest.mark.parametrize('contract_field', ('合同剩余年限', '合同到期日'))
def test_watcher_supports_known_contract_variants(tmp_path, contract_field):
    payload = payloads(contract_field=contract_field)
    result = run_source(
        fixed_source(), tmp_path/'run', fetcher=fetcher_for(payload),
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=lambda _: None,
    )
    report = result['report']
    assert report['validation_result'] == 'REVIEW_REQUIRED'
    assert report['candidate_count'] == 20
    assert report['child_count'] == 20
    assert report['canonical_team_count'] == 20
    assert report['canonical_publish_allowed'] is False
    candidate = json.loads((tmp_path/'run/candidate.json').read_text())
    assert candidate['observed_change_is_business_event'] is False
    assert all(change['canonical_write_allowed'] is False for change in candidate['changes'])
    snapshot = json.loads((tmp_path/'run/snapshot.json').read_text())
    assert snapshot['source_contract_revision'] == 'r5-20260912-detail-discovery'
    assert snapshot['row_key_fields'] == ['season', 'child_article_id', 'row_index']
    assert len(snapshot['children']) == 20
    for child in snapshot['children']:
        assert (tmp_path/'run'/'children'/f"{child['article_id']}.json").is_file()
    publish = json.loads((tmp_path/'run/publish.json').read_text())
    assert publish['result'] == 'NOT_PUBLISHED' and publish['publish_attempted'] is False


def test_supplemental_publication_is_retained_with_20_canonical_teams(tmp_path):
    payload = payloads(supplemental=True)
    result = run_source(
        fixed_source(), tmp_path/'run', fetcher=fetcher_for(payload),
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=lambda _: None,
    )
    assert result['report']['child_count'] == 21
    assert result['report']['canonical_team_count'] == 20
    snapshot = json.loads((tmp_path/'run/snapshot.json').read_text())
    assert len(snapshot['children']) == 21
    assert result['report']['canonical_publish_allowed'] is False


def test_unknown_alias_and_incomplete_coverage_fail_closed(tmp_path):
    unknown = run_source(
        fixed_source(), tmp_path/'unknown', fetcher=fetcher_for(payloads(unknown_team=True)),
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=lambda _: None,
    )
    assert unknown['report']['validation_result'] == 'FAIL_CLOSED'
    assert 'SOURCE_TEAM_ALIAS_UNRESOLVED' in unknown['report']['failure_reason']
    assert unknown['report']['canonical_publish_allowed'] is False

    incomplete = run_source(
        fixed_source(), tmp_path/'incomplete', fetcher=fetcher_for(payloads(team_count=19)),
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=lambda _: None,
    )
    assert incomplete['report']['validation_result'] == 'FAIL_CLOSED'
    assert 'CANONICAL_TEAM_COUNT_NOT_20' in incomplete['report']['failure_reason']
    assert incomplete['report']['canonical_publish_allowed'] is False


def test_child_failure_is_not_silently_skipped(tmp_path):
    payload = payloads()
    failed_id = sorted(payload)[1]
    base = fetcher_for(payload)

    def fetch(source):
        if source['article_id'] == failed_id:
            raise cba_registration.SourceFetchClosed('SOURCE_HTTP_429', 1)
        return base(source)

    result = run_source(
        fixed_source(), tmp_path/'run', fetcher=fetch,
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=lambda _: None,
    )
    assert result['report']['validation_result'] == 'FAIL_CLOSED'
    assert result['report']['failure_reason'] == 'fetch_fail_closed:SOURCE_HTTP_429'
    assert result['report']['canonical_publish_allowed'] is False
    assert not (tmp_path/'run'/'children'/f'{failed_id}.json').exists()


def test_rate_limit_is_sequential_and_uses_synthetic_sleeper(tmp_path):
    payload = payloads()
    seen = []
    sleeps = []
    base = fetcher_for(payload)

    def fetch(source):
        seen.append(source['article_id'])
        return base(source)

    run_source(
        fixed_source(), tmp_path/'run', fetcher=fetch,
        started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
        sleeper=sleeps.append, randomizer=random.Random(7),
    )
    assert len(sleeps) == 20
    assert all(1.5 <= delay <= 2.0 for delay in sleeps)
    assert seen[0] == INDEX_ID
    assert seen[1:] == sorted(seen[1:])


def test_discovery_rejects_malformed_or_duplicate_child_links():
    duplicate = json.dumps({'code': 200, 'data': {'detail_content':
        '<a href="https://www.cbaleague.com/#/news-register/detail/abc">A</a>'
        '<a href="https://www.cbaleague.com/#/news-register/detail/abc">B</a>'}}).encode()
    with pytest.raises(cba_registration.SourceSchemaDrift, match='DUPLICATE'):
        cba_registration.discover(duplicate, fixed_source())

    malformed = json.dumps({'code': 200, 'data': {'detail_content':
        '<a href="https://example.com/#/news-register/detail/abc">A</a>'}}).encode()
    with pytest.raises(cba_registration.SourceSchemaDrift, match='MALFORMED'):
        cba_registration.discover(malformed, fixed_source())


def test_same_frozen_synthetic_inputs_are_deterministic(tmp_path):
    payload = payloads(supplemental=True)
    for name in ('run1', 'run2'):
        run_source(
            fixed_source(), tmp_path/name, fetcher=fetcher_for(payload),
            started_at=fixed_time(), ended_at=fixed_time(), clubs=Clubs(ROOT),
            sleeper=lambda _: None,
        )
    for name in ('raw.json', 'snapshot.json', 'diff.json', 'candidate.json',
                 'validation.json', 'publish.json', 'run_report.json'):
        assert (tmp_path/'run1'/name).read_bytes() == (tmp_path/'run2'/name).read_bytes()
    assert (tmp_path/'run1'/'children/ffffffffff.json').read_bytes() == (
        tmp_path/'run2'/'children/ffffffffff.json'
    ).read_bytes()


def test_403_and_429_fail_closed_without_retry():
    class Denied:
        def __call__(self, request, timeout):
            raise HTTPError(request.full_url, 429, 'rate limited', {}, None)
    sleeps = []
    with pytest.raises(cba_registration.SourceFetchClosed, match='429'):
        cba_registration.fetch(fixed_source(), opener=Denied(), sleeper=sleeps.append)
    assert sleeps == []
