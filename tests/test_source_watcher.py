from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from cba_kb.adapters import cba_registration
from cba_kb.source_watcher import run_source


def source():
    return next(iter(cba_registration.registry().values()))


def payload(team_count=20):
    rows = ''.join(f'<tr><td>Team {index:02}</td><td>Player {index:02}</td></tr>'
                   for index in range(team_count))
    return json.dumps({'data': {'content': '<table><tr><th>球队</th><th>球员</th></tr>'
                                + rows + '</table>'}}, ensure_ascii=False).encode()


def test_official_adapter_contract_and_cardinality():
    contracts = cba_registration.registry()
    assert set(contracts) == {
        'cba-registration:2024-2025', 'cba-registration:2025-2026',
        'cba-registration:2026-2027',
    }
    rows = cba_registration.normalize(payload(), source())
    assert len(rows) == 20
    assert all(row['event_date'] is None and row['business_event'] is None for row in rows)
    with pytest.raises(cba_registration.SourceSchemaDrift, match='COUNT_NOT_20'):
        cba_registration.normalize(payload(19), source())


def test_watcher_writes_deterministic_evidence_and_never_publishes(tmp_path):
    raw = payload()
    fixed = datetime(2026, 9, 12, tzinfo=timezone.utc)
    result = run_source(source(), tmp_path/'run', fetcher=lambda _: (raw, 1),
                        started_at=fixed, ended_at=fixed)
    report = result['report']
    assert report['validation_result'] == 'REVIEW_REQUIRED'
    assert report['candidate_count'] == 20
    assert report['canonical_publish_allowed'] is False
    candidate = json.loads((tmp_path/'run/candidate.json').read_text())
    assert candidate['observed_change_is_business_event'] is False
    assert all(change['canonical_write_allowed'] is False for change in candidate['changes'])
    assert (tmp_path/'run/raw.json').read_bytes() == raw

    result2 = run_source(source(), tmp_path/'run2', fetcher=lambda _: (raw, 1),
                         previous_snapshot=tmp_path/'run/snapshot.json',
                         started_at=fixed, ended_at=fixed)
    assert result2['report']['validation_result'] == 'PASS'
    result3 = run_source(source(), tmp_path/'run3', fetcher=lambda _: (raw, 1),
                         previous_snapshot=tmp_path/'run/snapshot.json',
                         started_at=fixed, ended_at=fixed)
    publish = json.loads((tmp_path/'run/publish.json').read_text())
    assert publish['result'] == 'NOT_PUBLISHED' and publish['publish_attempted'] is False
    for name in ('raw.json', 'snapshot.json', 'diff.json', 'candidate.json',
                 'validation.json', 'publish.json'):
        assert (tmp_path/'run2'/name).read_bytes() == (tmp_path/'run3'/name).read_bytes()


def test_403_and_429_fail_closed_without_retry():
    class Denied:
        def __call__(self, request, timeout):
            raise HTTPError(request.full_url, 429, 'rate limited', {}, None)
    sleeps = []
    with pytest.raises(cba_registration.SourceFetchClosed, match='429'):
        cba_registration.fetch(source(), opener=Denied(), sleeper=sleeps.append)
    assert sleeps == []
