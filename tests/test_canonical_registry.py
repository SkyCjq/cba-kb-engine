import copy
from pathlib import Path

import pytest
import yaml

from cba_kb.canonical_registry import (
    canonical_routes, for_release, load_registry, route, validate_registry,
    validate_responsibilities,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


def registry():
    return {
        'registry_version': '1', 'registry_release_id': 'v1.5.4-test',
        'products': [
            {
                'product_id': 'events', 'semantic_grain': 'registration_event',
                'authority': 'canonical', 'business_key': ['event_key'],
                'artifact_key': 'facts/events.jsonl', 'generated_from': [],
                'deprecated_by': None, 'read_policy': 'canonical_only',
                'current_eligible': True, 'independent_write_allowed': True,
            },
            {
                'product_id': 'old_events', 'semantic_grain': 'registration_event',
                'authority': 'compatibility', 'business_key': ['event_key'],
                'artifact_key': 'facts/old_events.jsonl', 'generated_from': ['events'],
                'deprecated_by': 'events', 'read_policy': 'compatibility_only',
                'current_eligible': True, 'independent_write_allowed': False,
                'projection': {'source': 'events', 'fields': {'event_key': 'event_key', 'date': 'date'}},
            },
        ],
    }


def manifest():
    return [
        {'uid': 'facts/events.jsonl', 'drive_file_id': 'event-file', 'content_hash': 'b' * 64},
        {'uid': 'facts/old_events.jsonl', 'drive_file_id': 'compat-file', 'content_hash': 'c' * 64},
    ]


def test_registry_routes_only_canonical_grains():
    result = canonical_routes(registry(), manifest())
    assert result == {'registration_event': {
        'product_id': 'events', 'artifact_key': 'facts/events.jsonl',
        'file_id': 'event-file', 'selector': None,
    }}
    with pytest.raises(ValueError, match='UNRESOLVED'):
        route(registry(), manifest(), 'unknown')


@pytest.mark.parametrize('field', sorted({
    'product_id', 'semantic_grain', 'business_key', 'artifact_key', 'authority',
    'generated_from', 'deprecated_by', 'read_policy', 'current_eligible', 'independent_write_allowed',
}))
def test_registry_required_fields(field):
    data = registry()
    del data['products'][0][field]
    with pytest.raises(ValueError, match='REQUIRED_FIELD'):
        validate_registry(data)


@pytest.mark.parametrize('change,expected', [
    ({'business_key': []}, 'BUSINESS_KEY'),
    ({'business_key': 'event_key'}, 'FIELD_TYPE'),
    ({'current_eligible': 'false'}, 'BOOLEAN'),
    ({'authority': 'UNRESOLVED'}, 'AUTHORITY'),
    ({'read_policy': 'read_notes'}, 'POLICY'),
    ({'artifact_key': 'ai/README.md'}, 'DOCUMENT_AS_CANONICAL'),
])
def test_schema_and_machine_authority(change, expected):
    data = registry()
    data['products'][0].update(change)
    with pytest.raises(ValueError, match=expected):
        validate_registry(data)


def test_duplicate_product_and_canonical_grain():
    data = registry()
    data['products'].append(copy.deepcopy(data['products'][0]))
    with pytest.raises(ValueError, match='DUPLICATE_PRODUCT'):
        validate_registry(data)
    data['products'][-1]['product_id'] = 'competing'
    with pytest.raises(ValueError, match='MULTIPLE_CURRENT_CANONICAL'):
        validate_registry(data)


def test_manifest_foreign_key_and_duplicate_key():
    with pytest.raises(ValueError, match='ARTIFACT_UNRESOLVED'):
        validate_registry(registry(), manifest()[1:])
    with pytest.raises(ValueError, match='MANIFEST_DUPLICATE'):
        validate_registry(registry(), manifest() + manifest()[:1])


@pytest.mark.parametrize('field', ['sha256', 'drive_file_id', 'content_hash', 'synced_at', 'extraction_method', 'processing_status'])
def test_registry_cannot_become_transport_or_source_truth(field):
    data = registry()
    data['products'][0][field] = 'value'
    with pytest.raises(ValueError, match='RESPONSIBILITY_DRIFT'):
        validate_registry(data)


@pytest.mark.parametrize('kind,field', [
    ('source', 'authority'), ('source', 'canonical_product_id'),
    ('manifest', 'processing_status'), ('manifest', 'acceptance_status'),
    ('manifest', 'canonical_authority'),
])
def test_responsibility_contract_in_nested_records(kind, field):
    data = [{'nested': {field: 'value'}}]
    with pytest.raises(ValueError, match='RESPONSIBILITY_DRIFT'):
        validate_responsibilities(registry(), data if kind == 'manifest' else [], data if kind == 'source' else [])


def test_duplicate_yaml_is_rejected():
    with pytest.raises(ValueError, match='DUPLICATE'):
        load_registry('registry_version: 1\nregistry_version: 2\n')


def test_checked_in_registry_is_unreleased_and_matches_code_grains():
    data = load_registry((ROOT / 'config/canonical_products.yaml').read_bytes())
    from cba_kb.registration_domain import TABLES
    from cba_kb.domain_xlsx import SHEET_NAMES
    assert data['registry_release_id'] == 'UNRELEASED'
    products = validate_registry(data)
    for name, columns in TABLES.items():
        assert products[name]['business_key'] == [columns[0]]
        assert products[name]['selector'] == SHEET_NAMES[name]
    bound = for_release(data, 'v1.5.4-test')
    assert bound['registry_release_id'] == 'v1.5.4-test'
    assert data['registry_release_id'] == 'UNRELEASED'


def test_frozen_task_contract_and_changed_paths():
    import subprocess
    task = yaml.safe_load((ROOT / 'requirements/REQ-154-CANONSEC-01/task.yaml').read_text())
    assert task['base_branch'] == 'codex/v1.5.2-draft2'
    assert task['feature_branch'] == 'codex/req-154-canonsec-01'
    assert task['requirement_revision_or_hash'] == 'REQ-154-CANONSEC-01/v1.1-r2-WEB03-2026-09-12'
    assert task['refreeze_ledger_sync'] == 'VERIFIED_READBACK'
    assert task['refreeze_ledger_section'] == '8.3'
    assert len(task['allowed_paths']) == 17
    assert {'src/cba_kb/cli.py', '.github/workflows/offline-tests.yml'} <= set(task['allowed_paths'])
    assert 'src/cba_kb/drive.py' not in task['allowed_paths']
    assert len(task['frozen_spec_sha256']) == 64
    assert task['production_access'] == 'forbidden'
    assert task['full_regression_owner'] == 'github_actions'
    assert task['acceptance_ids'] == [f'A{i:02}' for i in range(1, 19)]
    subprocess.run(['git', 'cat-file', '-e', task['baseline_code_commit'] + '^{commit}'], cwd=ROOT, check=True)
    changed = subprocess.check_output(
        ['git', 'diff', '--name-only', task['baseline_code_commit'], 'a3af8eab'], cwd=ROOT, text=True,
    ).splitlines()
    assert set(changed) <= set(task['allowed_paths'])
    assert all((ROOT / path).is_file() for path in task['focused_tests'])


def test_compatibility_follow_up_stays_within_its_contract():
    import subprocess
    task = yaml.safe_load(
        (ROOT / 'requirements/REQ-154-CANONSEC-02-COMPATMAP-01/task.yaml').read_text()
    )
    assert task['parent_req_id'] == 'REQ-154-CANONSEC-01'
    assert task['baseline_commit'] == 'a3af8eab75841b34a3633ddda51c09950da2459c'
    assert task['feature_branch'] == 'codex/req-154-compatmap-01'
    assert task['production_access'] == 'forbidden'
    assert task['publish_allowed'] is False
    changed = set(subprocess.check_output(
        ['git', 'diff', '--name-only', task['baseline_commit']], cwd=ROOT, text=True,
    ).splitlines())
    assert changed <= set(task['allowed_paths']) | {'config/production.json'}
