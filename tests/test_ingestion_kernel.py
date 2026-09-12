import copy

from cba_kb.ingestion import (
    build_candidate, build_snapshot, canonical_bytes, diff_snapshots,
    publish_decision, validate_candidate,
)


SOURCE = {'source_id': 'synthetic', 'season': '2026-2027', 'article_id': 'example'}
KEY = ('season', 'article_id', 'raw_player_name')


def row(name, team='A'):
    return {'season': '2026-2027', 'article_id': 'example',
            'raw_player_name': name, 'raw_team_name': team,
            'event_date': None, 'business_event': None}


def test_snapshot_diff_candidate_are_deterministic_and_review_only():
    before = build_snapshot(SOURCE, b'{"v":1}', [row('B'), row('A')], key_fields=KEY)
    after = build_snapshot(SOURCE, b'{"v":2}', [row('A', 'C'), row('D')], key_fields=KEY)
    first = diff_snapshots(before, after)
    second = diff_snapshots(copy.deepcopy(before), copy.deepcopy(after))
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first['counts'] == {'add': 1, 'delete': 1, 'modify': 1, 'schema_drift': 0}
    candidate = build_candidate(first)
    assert all(item['canonical_write_allowed'] is False for item in candidate['changes'])
    assert all(item['business_event'] is None and item['business_event_date'] is None
               for item in candidate['changes'])
    validation = validate_candidate(candidate, first)
    assert validation['result'] == 'FAIL_CLOSED'
    assert publish_decision(candidate, validation)['canonical_write_performed'] is False


def test_same_snapshot_has_no_changes_and_passes_without_publish_permission():
    snapshot = build_snapshot(SOURCE, b'{}', [row('A')], key_fields=KEY)
    diff = diff_snapshots(snapshot, snapshot)
    validation = validate_candidate(build_candidate(diff), diff)
    assert diff['counts'] == dict(add=0, delete=0, modify=0, schema_drift=0)
    assert validation == {
        'schema_version': 1, 'source_id': 'synthetic', 'result': 'PASS',
        'canonical_publish_allowed': False, 'failure_reason': None,
        'manual_intervention_count': 0,
    }
