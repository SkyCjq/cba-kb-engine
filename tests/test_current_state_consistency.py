import json

import pytest

from cba_kb.current_state import (
    BEGIN, CURRENT_VERSION_DOC, END, audit_current_history, consumer_artifacts,
    control_document_identities, current_version_document,
    current_version_document_migration, migrate_current_version_document,
    generate_context_card, read_current_block, render_current_state,
    replace_current_block, target_metadata, validate_current_state,
    zero_business_delta,
)
from test_canonical_registry import SHA, manifest, registry


def state_documents():
    reg = registry()
    meta = target_metadata('v1.5.4-test', SHA, reg)
    block = render_current_state(meta)
    docs = {'readme': block + 'README history\n', 'index': block + 'INDEX history\n',
            'version': block + 'Version history\n',
            'current_version_doc': block + 'Current version history\n',
            'context_card': generate_context_card(meta, reg, manifest())}
    status = {'state': 'COMPLETE', 'current_release_id': 'v1.5.4-test', 'code_commit': SHA}
    return status, reg, docs


def test_current_state_agrees_across_all_surfaces():
    status, reg, docs = state_documents()
    assert validate_current_state(status, reg, manifest(), docs)['status'] == 'PASS'


@pytest.mark.parametrize('surface', ['readme', 'index', 'version', 'current_version_doc', 'context_card'])
def test_stale_release_on_any_surface_is_blocked(surface):
    status, reg, docs = state_documents()
    docs[surface] = docs[surface].replace('v1.5.4-test', 'v1.5.3-1')
    with pytest.raises(ValueError, match='DRIFT'):
        validate_current_state(status, reg, manifest(), docs)


def test_stale_registry_or_code_commit():
    status, reg, docs = state_documents()
    reg['registry_release_id'] = 'v1.5.3-2'
    with pytest.raises(ValueError, match='DRIFT'):
        validate_current_state(status, reg, manifest(), docs)
    status, reg, docs = state_documents()
    status['published_code_commit'] = 'b' * 40
    with pytest.raises(ValueError, match='DRIFT'):
        validate_current_state(status, reg, manifest(), docs)


def test_renderer_replaces_only_controlled_span():
    meta = target_metadata('v1.5.4-test', SHA, registry())
    original = 'Historical heading\n' + BEGIN + '{}\n' + END + '历史正文\n'
    updated = replace_current_block(original, meta)
    assert updated == 'Historical heading\n' + render_current_state(meta) + '历史正文\n'
    assert replace_current_block(updated, meta) == updated


def test_legacy_managed_prefix_migration_preserves_unmanaged_history():
    from cba_kb.native import wrap
    meta = target_metadata('v1.5.4-test', SHA, registry())
    text = wrap('Current Release: v1.5.3-1') + 'Unmanaged history\n'
    updated = replace_current_block(text, meta)
    assert 'v1.5.3-1' not in updated
    assert updated.endswith('Unmanaged history\n')
    assert read_current_block(updated) == meta


@pytest.mark.parametrize('text', [
    BEGIN + '{}\n' + END + BEGIN + '{}\n' + END, END + BEGIN, BEGIN, END,
])
def test_malformed_markers_fail_closed(text):
    with pytest.raises(ValueError):
        replace_current_block(text, target_metadata('v1.5.4-test', SHA, registry()))


@pytest.mark.parametrize('state', ['PUBLISHING', 'FAILED', 'ROLLING_BACK'])
def test_consumer_reads_previous_snapshot_during_transitions(state):
    previous = [{'snapshot_id': 'safe'}]
    assert consumer_artifacts({'state': state, 'previous_snapshot': previous, 'artifacts': [{'id': 'partial'}]}) == previous
    with pytest.raises(ValueError, match='SNAPSHOT_UNAVAILABLE'):
        consumer_artifacts({'state': state, 'artifacts': [{'id': 'partial'}]})


@pytest.mark.parametrize('state', ['COMPLETE', 'ROLLED_BACK'])
def test_consumer_reads_restored_or_complete_artifacts(state):
    assert consumer_artifacts({'state': state, 'artifacts': [{'id': 'current'}]}) == [{'id': 'current'}]


def test_zero_business_delta_includes_evidence_and_source_registry():
    before = {'MASTER': b'rows', 'six_table': b'six', 'evidence': b'bytes', 'sources': b'rows'}
    assert zero_business_delta(before, dict(before))['protected_artifacts'] == 4
    for key in before:
        after = dict(before)
        after[key] += b'change'
        with pytest.raises(ValueError, match='ZERO_BUSINESS_FACT_DELTA'):
            zero_business_delta(before, after)
    with pytest.raises(ValueError):
        zero_business_delta(before, {})


def test_candidate_named_code_mirror_remains_current():
    docs = manifest() + [{
        'uid': 'code/scripts/sync_v1_5_2_candidate.py',
        'drive_file_id': 'code-candidate',
        'content_hash': 'd' * 64,
    }]
    records = [
        {'id': 'event-file', 'name': 'events', 'parents': ['data']},
        {'id': 'compat-file', 'name': 'compat', 'parents': ['data']},
        {
            'id': 'code-candidate',
            'name': 'scripts__sync_v1_5_2_candidate.py',
            'parents': ['scripts'],
        },
    ]
    zones = {
        'current': ['root', 'data', 'ai', 'scripts'],
        'history': ['archive'],
        'staging': ['staging'],
        'evidence': ['sources'],
        'folders': {'archive': ['root'], 'sources': ['root']},
    }
    assert audit_current_history(
        records, zones, docs, registry(),
    )['violations'] == 0


def test_five_current_surfaces_share_release_and_code_identity():
    status, reg, docs = state_documents()
    result = control_document_identities(status, reg, docs)
    assert result['status'] == 'PASS'
    assert set(result['identities']) == {
        'release_status', 'readme', 'index', 'version',
        'current_version_doc', 'context_card',
    }
    assert set(validate_current_state(status, reg, manifest(), docs)['surfaces']) == {
        'release_status', 'readme', 'index', 'version',
        'current_version_doc', 'context_card',
    }


def test_current_version_doc_uses_stable_logical_key():
    rows = manifest() + [{
        'uid': CURRENT_VERSION_DOC,
        'drive_file_id': 'current-version-file',
        'content_hash': 'd' * 64,
    }]
    assert current_version_document(rows)['drive_file_id'] == 'current-version-file'
    with pytest.raises(ValueError, match='CURRENT_VERSION_DOC_MISSING'):
        current_version_document(manifest())

def test_v161_requires_current_version_doc_surface():
    status, reg, docs = state_documents()
    status['current_release_id'] = 'v1.6.1-1'
    reg['registry_release_id'] = 'v1.6.1-1'
    docs = {
        key: value.replace('v1.5.4-test', 'v1.6.1-1')
        for key, value in docs.items()
    }
    del docs['current_version_doc']
    with pytest.raises(ValueError, match='CURRENT_DOCUMENT_MISSING'):
        validate_current_state(status, reg, manifest(), docs)


def legacy_version_row():
    return {
        'uid': 'ai/CBA-KB_v1.5.3.md',
        'local_path': 'ai/CBA-KB_v1.5.3.md',
        'drive_file_id': '1ZebJR9YPKX37cMDdz0xznHDa45at_q65',
        'content_hash': 'a' * 64,
    }


def test_v161_migrates_frozen_legacy_version_identity_without_duplicate():
    legacy = legacy_version_row()
    result = current_version_document_migration([legacy], 'v1.6.1-1')
    assert result['report']['status'] == 'MIGRATED'
    assert result['report']['existing_object_reused'] is True
    assert result['report']['drive_file_id'] == legacy['drive_file_id']
    assert len(result['manifest']) == 1
    assert result['manifest'][0]['uid'] == CURRENT_VERSION_DOC
    assert result['manifest'][0]['drive_file_id'] == legacy['drive_file_id']
    assert current_version_document(
        [legacy], release_id='v1.6.1-1',
    )['drive_file_id'] == legacy['drive_file_id']


def test_v161_migration_is_idempotent_and_conflicts_fail_closed():
    migrated = migrate_current_version_document([legacy_version_row()], 'v1.6.1-1')
    repeated = current_version_document_migration(migrated, 'v1.6.1-1')
    assert repeated['report']['status'] == 'ALREADY_MIGRATED'
    assert repeated['manifest'] == migrated
    conflict = dict(migrated[0])
    conflict['drive_file_id'] = 'different-version-object'
    with pytest.raises(ValueError, match='IDENTITY_CONFLICT'):
        migrate_current_version_document([conflict], 'v1.6.1-1')
    with pytest.raises(ValueError, match='LEGACY_IDENTITY_MISSING'):
        migrate_current_version_document([{'uid': 'other', 'drive_file_id': 'x'}], 'v1.6.1-1')


def test_non_v161_current_version_lookup_remains_strict():
    with pytest.raises(ValueError, match='CURRENT_VERSION_DOC_MISSING'):
        current_version_document([legacy_version_row()], release_id='v1.6.0-1')


def test_v161_current_surface_set_excludes_legacy_version():
    status, reg, docs = state_documents()
    status['current_release_id'] = 'v1.6.1-1'
    reg['registry_release_id'] = 'v1.6.1-1'
    docs = {
        key: value.replace('v1.5.4-test', 'v1.6.1-1')
        for key, value in docs.items()
        if key != 'version'
    }
    result = validate_current_state(status, reg, manifest(), docs)
    assert result['status'] == 'PASS'
    assert set(result['surfaces']) == {
        'release_status', 'readme', 'index', 'context_card',
        'current_version_doc',
    }


def test_v161_stale_current_version_doc_is_pre_mutation_failure():
    status, reg, docs = state_documents()
    status['current_release_id'] = 'v1.6.1-1'
    reg['registry_release_id'] = 'v1.6.1-1'
    docs = {
        key: value.replace('v1.5.4-test', 'v1.6.1-1')
        for key, value in docs.items()
        if key != 'version'
    }
    docs['current_version_doc'] = docs['current_version_doc'].replace(
        'v1.6.1-1', 'v1.5.4-1',
    )
    with pytest.raises(ValueError, match='DRIFT'):
        validate_current_state(status, reg, manifest(), docs)


@pytest.mark.parametrize('release_id', ['v1.8.0-1', 'v1.8.1-1', 'v1.9.0-1'])
def test_v181_and_future_releases_use_modern_current_surfaces(release_id):
    status, reg, docs = state_documents()
    status['current_release_id'] = release_id
    reg['registry_release_id'] = release_id
    docs = {
        key: value.replace('v1.5.4-test', release_id)
        for key, value in docs.items()
        if key != 'version'
    }
    result = validate_current_state(status, reg, manifest(), docs)
    assert result['status'] == 'PASS'
    assert set(result['surfaces']) == {
        'release_status', 'readme', 'index', 'context_card',
        'current_version_doc',
    }
    del docs['current_version_doc']
    with pytest.raises(ValueError, match='CURRENT_DOCUMENT_MISSING'):
        validate_current_state(status, reg, manifest(), docs)

