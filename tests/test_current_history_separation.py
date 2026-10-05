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


def test_published_manifest_authority_suppresses_filename_only_historical_hint():
    # Test A: Synthetic manifest-mapped non-code artifact:
    # uid data/data_candidate_manifest.json, name data_candidate_manifest.json,
    # parent Current, status published, non-empty published_release => PASS.
    docs = manifest() + [{
        'uid': 'data/data_candidate_manifest.json',
        'drive_file_id': 'synth-candidate-manifest',
        'content_hash': 'd' * 64,
        'status': 'published',
        'published_release': 'v1.9.0-2',
    }]
    records = items() + [{
        'id': 'synth-candidate-manifest',
        'name': 'data_candidate_manifest.json',
        'parents': ['data'],
    }]
    assert audit_current_history(records, zones(), docs, registry())['violations'] == 0


@pytest.mark.parametrize('row_patch', [
    None,  # unmapped (no row in manifest)
    {'status': 'draft', 'published_release': 'v1.9.0-2'},  # not published status
    {'status': 'published', 'published_release': ''},  # empty published_release
    {'status': 'published', 'published_release': '   '},  # whitespace published_release
    {'status': 'published'},  # missing published_release
])
def test_candidate_filename_without_explicit_published_authority_fails(row_patch):
    # Test B: Same filename/current placement without explicit published manifest authority => HISTORICAL_IN_CURRENT.
    docs = manifest()
    if row_patch is not None:
        row = {
            'uid': 'data/data_candidate_manifest.json',
            'drive_file_id': 'synth-candidate-manifest',
            'content_hash': 'd' * 64,
        }
        row.update(row_patch)
        docs.append(row)
    records = items() + [{
        'id': 'synth-candidate-manifest',
        'name': 'data_candidate_manifest.json',
        'parents': ['data'],
    }]
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), docs, registry())


def test_published_row_with_explicit_noncurrent_role_fails():
    # Test C: Same published row plus artifact_role=candidate => HISTORICAL_IN_CURRENT.
    docs = manifest() + [{
        'uid': 'data/data_candidate_manifest.json',
        'drive_file_id': 'synth-candidate-manifest',
        'content_hash': 'd' * 64,
        'status': 'published',
        'published_release': 'v1.9.0-2',
    }]
    records = items() + [{
        'id': 'synth-candidate-manifest',
        'name': 'data_candidate_manifest.json',
        'parents': ['data'],
        'artifact_role': 'candidate',
    }]
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), docs, registry())


def test_published_row_dual_parent_current_and_history_fails():
    # Test D: Same published row dual-parent Current + History => HISTORICAL_IN_CURRENT.
    docs = manifest() + [{
        'uid': 'data/data_candidate_manifest.json',
        'drive_file_id': 'synth-candidate-manifest',
        'content_hash': 'd' * 64,
        'status': 'published',
        'published_release': 'v1.9.0-2',
    }]
    records = items() + [{
        'id': 'synth-candidate-manifest',
        'name': 'data_candidate_manifest.json',
        'parents': ['data', 'archive'],
    }]
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        audit_current_history(records, zones(), docs, registry())


def test_published_row_physically_under_history_remains_non_current():
    # Test E: Published row physically under History remains non-current and does not become Current from manifest evidence.
    docs = manifest() + [{
        'uid': 'data/data_candidate_manifest.json',
        'drive_file_id': 'synth-candidate-manifest',
        'content_hash': 'd' * 64,
        'status': 'published',
        'published_release': 'v1.9.0-2',
    }]
    records = items() + [{
        'id': 'synth-candidate-manifest',
        'name': 'data_candidate_manifest.json',
        'parents': ['archive'],
    }]
    assert audit_current_history(records, zones(), docs, registry())['violations'] == 0
