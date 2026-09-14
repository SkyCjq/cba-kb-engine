import copy
from pathlib import Path

import pytest

from cba_kb.canonical_registry import (
    load_registry, project_compatibility, reconcile_compatibility,
    reconciliation_report, validate_registry,
)
from cba_kb.facts import EVENT, SNAPSHOT
from test_canonical_registry import registry


ROOT = Path(__file__).resolve().parents[1]


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


def checked_in_registry():
    return load_registry((ROOT / 'config/canonical_products.yaml').read_bytes())


def foreign_fixture(registry_data):
    key = '2024-2025|2025-03-31|zhejiang_guangsha|JAMES NUNNALLY|美国'
    canonical = dict.fromkeys(SNAPSHOT)
    canonical.update({
        'snapshot_key': key,
        'season': '2024-2025',
        'snapshot_as_of': '2025-03-31',
        'club_id': 'zhejiang_guangsha',
        'club_source_name': '浙江广厦',
        'name_en_raw': 'JAMES NUNNALLY',
        'name_en_normalized': 'JAMES NUNNALLY',
        'name_zh_raw': '詹姆斯·纳纳利',
        'nationality': '美国',
        'position': 'SF',
        'jersey_number': '00',
        'status_at_snapshot': 'registered',
    })
    extra = copy.deepcopy(canonical)
    extra['snapshot_key'] = '2020-2021|2021-04-11|guangdong_hongyuan|CLARENCE WEEMS|美国'
    extra.update({'season': '2020-2021', 'snapshot_as_of': '2021-04-11',
                  'name_en_normalized': 'CLARENCE WEEMS'})
    second = copy.deepcopy(canonical)
    second.update({
        'snapshot_key': '2024-2025|2025-03-31|liaoning_shenyang_sansheng|CAMERON OLIVER|美国',
        'club_id': 'liaoning_shenyang_sansheng',
        'club_source_name': '辽宁沈阳三生',
        'name_en_raw': 'CAMERON OLIVER',
        'name_en_normalized': 'CAMERON OLIVER',
        'name_zh_raw': '卡梅隆·奥利弗',
    })
    records = {'foreign_registration_snapshots': [canonical, second, extra]}
    expected = project_compatibility(registry_data, 'legacy_foreign_snapshots', records)
    return records, expected


def test_legacy_foreign_projection_is_transform_aware_and_evidence_bounded():
    registry_data = checked_in_registry()
    records, expected = foreign_fixture(registry_data)
    assert {row['snapshot_key']: row for row in expected}[
        records['foreign_registration_snapshots'][0]['snapshot_key']
    ]['jersey_number'] == '0'
    assert {row['status_at_snapshot'] for row in expected} == {'注册在册'}
    report = reconciliation_report(
        registry_data, 'legacy_foreign_snapshots', records, expected
    )
    assert report['status'] == 'PASS'
    assert report['expected_rows'] == report['actual_rows'] == 2
    assert {item['reason'] for item in report['explained_rendering_differences']} == {
        'frozen_original_xlsx_numeric_storage'
    }
    assert report['canonical_coverage']['foreign_registration_snapshots'][
        'out_of_projection_rows'
    ] == [records['foreign_registration_snapshots'][2]['snapshot_key']]

    drifted = copy.deepcopy(expected)
    drifted[0]['jersey_number'] = '00'
    with pytest.raises(ValueError, match='SEMANTIC_DRIFT'):
        reconcile_compatibility(
            registry_data, 'legacy_foreign_snapshots', records, drifted
        )


def event_fixture(registry_data):
    product = next(
        item for item in registry_data['products']
        if item['product_id'] == 'legacy_registration_events'
    )
    canonical_key = '2020-2021|domestic|beijing_konggu|石颜博|free_agent_claim|2021-02-27|118'
    legacy_key = '2020-2021|domestic|beijing_konggu|石颜博|registration_change|2021-02-27|4'
    product['projection']['domestic_event_aliases'] = {canonical_key: legacy_key}

    event = dict.fromkeys(EVENT)
    event.update({
        'event_key': canonical_key,
        'season': '2020-2021',
        'player_type': 'domestic',
        'club_id': 'beijing_konggu',
        'player_name_zh': '石颜博',
        'event_type': 'free_agent_claim',
        'event_date': '2021-02-27',
        'date_year_inferred': False,
        'from_club_id': 'bayi',
        'event_domain': 'domestic_movement',
        'event_status': 'confirmed',
        'club_announcement_date': None,
    })
    relation = {
        'record_key': '2020-2021|beijing_konggu|石颜博',
        'season': '2020-2021',
        'club_id': 'beijing_konggu',
        'player': '石颜博',
        'from_club_id': 'bayi',
    }
    snapshot = {
        'record_key': '2020-2021|beijing_konggu|石颜博',
        'season': '2020-2021',
        'club_id': 'beijing_konggu',
        'player': '石颜博',
        'club_official': '北京控股',
        'contract_category': 'C类',
        'contract_term_official': None,
        'registration_method': '认领',
        'registration_status': '注册',
        'remarks': None,
    }
    annotation = {
        'event_key': legacy_key,
        'season': '2020-2021',
        'club_id': 'beijing_konggu',
        'player_name_zh': '石颜博',
        'event_date': '2021-02-27',
        'date_year_inferred': False,
        'club_announcement_date': '2021-02-28',
    }
    records = {
        'registration_status_events': [event],
        'domestic_registrations': [relation],
        'domestic_season_snapshots': [snapshot],
        'evidence/bayi_legacy_context.md': [annotation],
    }
    return records, canonical_key, legacy_key


def test_legacy_event_projection_preserves_source_annotation_without_changing_canonical():
    registry_data = checked_in_registry()
    records, canonical_key, legacy_key = event_fixture(registry_data)
    projected = project_compatibility(
        registry_data, 'legacy_registration_events', records
    )
    assert projected[0]['event_key'] == legacy_key
    assert projected[0]['event_type'] == 'registration_change'
    assert projected[0]['club_source_name'] == '北京控股'
    assert projected[0]['club_announcement_date'] == '2021-02-28'
    assert records['registration_status_events'][0]['club_announcement_date'] is None
    report = reconciliation_report(
        registry_data, 'legacy_registration_events', records, projected
    )
    assert report['status'] == 'PASS'
    assert report['mapping_edges'][0]['legacy_key'] == legacy_key
    assert report['canonical_coverage']['domestic_registrations'][
        'referenced_rows'
    ] == 1
    assert records['registration_status_events'][0]['event_key'] == canonical_key


def test_legacy_event_projection_fails_closed_without_pinned_annotation():
    registry_data = checked_in_registry()
    records, _, _ = event_fixture(registry_data)
    del records['evidence/bayi_legacy_context.md']
    with pytest.raises(ValueError, match='LEGACY_ANNOTATION_EVIDENCE_REQUIRED'):
        project_compatibility(
            registry_data, 'legacy_registration_events', records
        )


def test_legacy_event_projection_rejects_invented_date():
    registry_data = checked_in_registry()
    records, _, _ = event_fixture(registry_data)
    records['evidence/bayi_legacy_context.md'][0]['club_announcement_date'] = None
    with pytest.raises(ValueError, match='LEGACY_ANNOTATION_FIELD_MISSING'):
        project_compatibility(
            registry_data, 'legacy_registration_events', records
        )
