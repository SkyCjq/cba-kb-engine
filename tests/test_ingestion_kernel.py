import copy

from cba_kb.ingestion import (
    build_candidate, build_snapshot, canonical_bytes, diff_snapshots,
    publish_decision, validate_candidate,
)


SOURCE = {'source_id': 'synthetic', 'season': '2026-2027', 'article_id': 'index'}
KEY = ('season', 'child_article_id', 'row_index')
REVISION = 'r5-20260912-detail-discovery'


def row(child, index, name, team='A'):
    return {'season': '2026-2027', 'child_article_id': child, 'row_index': index,
            'raw_player_name': name, 'canonical_team_id': team,
            'event_date': None, 'business_event': None}


def test_snapshot_identity_covers_order_independent_child_responses():
    children = {'bbb': b'{"v":2}', 'aaa': b'{"v":1}'}
    first = build_snapshot(
        SOURCE, b'{"index":1}', [row('aaa', 1, 'A')], key_fields=KEY,
        children=children, source_contract_revision=REVISION,
    )
    second = build_snapshot(
        SOURCE, b'{"index":1}', [row('aaa', 1, 'A')], key_fields=KEY,
        children={'aaa': b'{"v":1}', 'bbb': b'{"v":2}'},
        source_contract_revision=REVISION,
    )
    assert first['snapshot_identity'] == second['snapshot_identity']
    assert [item['article_id'] for item in first['children']] == ['aaa', 'bbb']

    added = build_snapshot(
        SOURCE, b'{"index":1}', [row('aaa', 1, 'A')], key_fields=KEY,
        children={**children, 'ccc': b'{"v":3}'},
        source_contract_revision=REVISION,
    )
    changed = build_snapshot(
        SOURCE, b'{"index":1}', [row('aaa', 1, 'A')], key_fields=KEY,
        children={'aaa': b'{"v":1}', 'bbb': b'{"v":99}'},
        source_contract_revision=REVISION,
    )
    assert added['snapshot_identity'] != first['snapshot_identity']
    assert changed['snapshot_identity'] != first['snapshot_identity']


def test_snapshot_diff_candidate_are_deterministic_and_review_only():
    before = build_snapshot(
        SOURCE, b'{"v":1}', [row('old', 1, 'B'), row('old', 2, 'A')],
        key_fields=KEY, children={'old': b'{"child":1}'},
        source_contract_revision=REVISION,
    )
    after = build_snapshot(
        SOURCE, b'{"v":2}', [row('new', 1, 'A', 'C'), row('new', 2, 'D')],
        key_fields=KEY, children={'new': b'{"child":2}'},
        source_contract_revision=REVISION,
    )
    first = diff_snapshots(before, after)
    second = diff_snapshots(copy.deepcopy(before), copy.deepcopy(after))
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first['counts'] == {'add': 2, 'delete': 2, 'modify': 0, 'schema_drift': 0}
    candidate = build_candidate(first)
    assert all(item['canonical_write_allowed'] is False for item in candidate['changes'])
    assert all(item['business_event'] is None and item['business_event_date'] is None
               for item in candidate['changes'])
    validation = validate_candidate(candidate, first)
    assert validation['result'] == 'FAIL_CLOSED'
    assert publish_decision(candidate, validation)['canonical_write_performed'] is False


def test_same_snapshot_has_no_changes_and_passes_without_publish_permission():
    snapshot = build_snapshot(
        SOURCE, b'{}', [row('child', 1, 'A')], key_fields=KEY,
        children={'child': b'{}'}, source_contract_revision=REVISION,
    )
    diff = diff_snapshots(snapshot, snapshot)
    validation = validate_candidate(build_candidate(diff), diff)
    assert diff['counts'] == dict(add=0, delete=0, modify=0, schema_drift=0)
    assert validation == {
        'schema_version': 1, 'source_id': 'synthetic', 'result': 'PASS',
        'canonical_publish_allowed': False, 'failure_reason': None,
        'manual_intervention_count': 0,
    }
