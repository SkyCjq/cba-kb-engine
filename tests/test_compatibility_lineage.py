import pytest

from cba_kb.canonical_registry import project_compatibility, reconcile_compatibility, validate_registry
from test_canonical_registry import registry


def test_projection_deterministic_preserves_unknown_dates():
    data = registry()
    rows = {'events': [
        {'event_key': 'two', 'date': None, 'extra': 'official'},
        {'event_key': 'one', 'date': '2026-01-01', 'extra': 'official'},
    ]}
    projected = project_compatibility(data, 'old_events', rows)
    assert projected == [{'event_key': 'one', 'date': '2026-01-01'}, {'event_key': 'two', 'date': None}]
    assert projected == project_compatibility(data, 'old_events', {'events': list(reversed(rows['events']))})
    assert reconcile_compatibility(data, 'old_events', rows, projected)['rows'] == 2
    assert 'extra' in rows['events'][0]


def test_independent_compatibility_edits_fail():
    rows = {'events': [{'event_key': 'one', 'date': None}]}
    with pytest.raises(ValueError, match='SEMANTIC_DRIFT'):
        reconcile_compatibility(registry(), 'old_events', rows, [{'event_key': 'one', 'date': 'guessed'}])


@pytest.mark.parametrize('change,expected', [
    ({'independent_write_allowed': True}, 'INDEPENDENT_WRITE'),
    ({'generated_from': []}, 'LINEAGE_REQUIRED'),
    ({'generated_from': ['missing']}, 'LINEAGE_UNRESOLVED'),
    ({'deprecated_by': None}, 'DEPRECATION_REQUIRED'),
    ({'deprecated_by': 'old_events'}, 'DEPRECATION_UNRESOLVED'),
])
def test_compatibility_requires_safe_lineage(change, expected):
    data = registry()
    data['products'][1].update(change)
    with pytest.raises(ValueError, match=expected):
        validate_registry(data)


def test_compatibility_cannot_become_canonical_input():
    data = registry()
    data['products'][0]['generated_from'] = ['old_events']
    with pytest.raises(ValueError, match='REVERSE_COMPATIBILITY'):
        validate_registry(data)


def test_lineage_cycle_is_blocked():
    data = registry()
    data['products'][1].update(authority='derived', read_policy='derived_read_only', deprecated_by=None)
    data['products'][0]['generated_from'] = ['old_events']
    with pytest.raises(ValueError, match='LINEAGE_CYCLE'):
        validate_registry(data)


def test_missing_migration_mapping_is_not_invented():
    data = registry()
    del data['products'][1]['projection']
    with pytest.raises(ValueError, match='PROJECTION_UNRESOLVED'):
        project_compatibility(data, 'old_events', {'events': []})


def test_projection_rejects_duplicate_or_missing_business_keys():
    for rows in [[{'date': None}], [{'event_key': 'one'}, {'event_key': 'one'}]]:
        with pytest.raises(ValueError, match='BUSINESS_KEY'):
            project_compatibility(registry(), 'old_events', {'events': rows})


def test_projection_cannot_target_canonical():
    with pytest.raises(ValueError, match='TARGET_FORBIDDEN'):
        project_compatibility(registry(), 'events', {'events': []})
