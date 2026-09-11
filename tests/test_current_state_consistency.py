import json

import pytest

from cba_kb.current_state import (
    BEGIN, END, consumer_artifacts, generate_context_card, read_current_block,
    render_current_state, replace_current_block, target_metadata, validate_current_state,
    zero_business_delta,
)
from test_canonical_registry import SHA, manifest, registry


def state_documents():
    reg = registry()
    meta = target_metadata('v1.5.4-test', SHA, reg)
    block = render_current_state(meta)
    docs = {'readme': block + 'README history\n', 'index': block + 'INDEX history\n',
            'version': block + 'Version history\n',
            'context_card': generate_context_card(meta, reg, manifest())}
    status = {'state': 'COMPLETE', 'current_release_id': 'v1.5.4-test', 'code_commit': SHA}
    return status, reg, docs


def test_current_state_agrees_across_all_surfaces():
    status, reg, docs = state_documents()
    assert validate_current_state(status, reg, manifest(), docs)['status'] == 'PASS'


@pytest.mark.parametrize('surface', ['readme', 'index', 'version', 'context_card'])
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
