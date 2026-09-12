import copy

import pytest

from cba_kb.current_state import audit_current_history
from cba_kb.canonical_registry import validate_registry
from test_canonical_registry import manifest, registry


def zones():
    return {'current': ['root', 'data', 'ai'], 'history': ['archive'], 'staging': ['staging'],
            'evidence': ['sources'], 'folders': {'nested': ['data'], 'archive': ['root'], 'sources': ['root']}}


def items():
    return [
        {'id': 'event-file', 'name': 'events', 'parents': ['data']},
        {'id': 'compat-file', 'name': 'compat', 'parents': ['data']},
    ]


def test_evidence_is_not_history_and_nested_current_is_current():
    records = items() + [{'id': 'source', 'name': 'original_before.xlsx', 'parents': ['sources']}]
    records[0]['parents'] = ['nested']
    assert audit_current_history(records, zones(), manifest(), registry())['violations'] == 0


@pytest.mark.parametrize('role', ['candidate', 'before', 'rollback', 'staging', 'prechange', 'historical-only'])
def test_innocent_filename_cannot_hide_noncurrent_role(role):
    records = items() + [{'id': 'wrong', 'name': 'friendly.md', 'parents': ['ai'], 'artifact_role': role}]
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), manifest(), registry())


def test_filename_is_additional_evidence_not_only_classifier():
    records = items() + [{'id': 'wrong', 'name': 'report_before.json', 'parents': ['ai']}]
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), manifest(), registry())


def test_archive_under_root_is_not_current_but_dual_parent_is_forbidden():
    records = items() + [{'id': 'old', 'name': 'candidate.md', 'parents': ['archive']}]
    assert audit_current_history(records, zones(), manifest(), registry())['violations'] == 0
    records[-1]['parents'].append('ai')
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), manifest(), registry())


def test_canonical_product_cannot_be_physically_archived():
    records = items()
    records[0]['parents'] = ['archive']
    with pytest.raises(ValueError, match='CANONICAL_OUTSIDE_CURRENT'):
        audit_current_history(records, zones(), manifest(), registry())


def test_historical_registry_product_never_current_eligible():
    data = registry()
    data['products'][1].update(authority='historical', read_policy='history_only')
    with pytest.raises(ValueError, match='HISTORICAL_CURRENT'):
        validate_registry(data)
    data['products'][1]['current_eligible'] = False
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(items(), zones(), manifest(), data)


def test_unknown_and_missing_folder_identity_fail_closed():
    records = items()
    records[0]['parents'] = []
    with pytest.raises(ValueError, match='FOLDER_ID_REQUIRED'):
        audit_current_history(records, zones(), manifest(), registry())
    records[0]['parents'] = ['unclassified']
    with pytest.raises(ValueError, match='FOLDER_ROLE_UNRESOLVED'):
        audit_current_history(records, zones(), manifest(), registry())
