import pytest
from cba_kb import facts
from cba_kb.merge import merge_domestic


def snapshot_row(**overrides):
    row = dict.fromkeys(facts.SNAPSHOT)
    row.update({'season': '2024-2025', 'snapshot_as_of': '2025-03-31', 'club_id': 'ningbo_fubang',
                'name_en_raw': 'KOUAT NOI', 'name_en_normalized': 'KOUAT NOI',
                'nationality': '澳大利亚', 'jersey_number': '01', 'source_type': 'gdrive',
                'verification_level': 'auto_validated'})
    row.update(overrides)
    row['snapshot_key'] = facts.snapshot_key(row)
    return row


def event_row(**overrides):
    row = dict.fromkeys(facts.EVENT)
    row.update({'season': '2024-2025', 'player_type': 'foreign', 'club_id': 'ningbo_fubang',
                'event_type': 'registration_cancelled', 'event_date': '2024-08-21',
                'date_year_inferred': True, 'source_type': 'gdrive', 'verification_level': 'auto_validated',
                'provisional_player_key': 'DONELL COOPER', 'sequence': '77'})
    row.update(overrides)
    row['event_key'] = facts.event_key(row)
    return row


def test_keys_are_stable_and_grain_specific():
    snapshot = snapshot_row()
    assert snapshot['snapshot_key'] == '2024-2025|2025-03-31|ningbo_fubang|KOUAT NOI|澳大利亚'
    event = event_row()
    assert event['event_key'] == '2024-2025|foreign|ningbo_fubang|DONELL COOPER|registration_cancelled|2024-08-21|77'
    assert facts.validate_snapshots([snapshot]) == {'rows': 1, 'unique': 1}
    assert facts.validate_events([event]) == {'rows': 1, 'unique': 1}


def test_duplicate_keys_and_unsafe_events_are_rejected():
    with pytest.raises(ValueError, match='Duplicate snapshot_key'):
        facts.validate_snapshots([snapshot_row(), snapshot_row()])
    with pytest.raises(ValueError, match='Duplicate event_key'):
        facts.validate_events([event_row(), event_row()])
    with pytest.raises(ValueError, match='Uncontrolled event_type'):
        facts.validate_events([event_row(event_type='free_text')])
    with pytest.raises(ValueError, match='requires event_date'):
        facts.validate_events([event_row(event_date=None, sequence='78')])
    with pytest.raises(ValueError, match='Uncontrolled verification_level'):
        facts.validate_events([event_row(verification_level='ocr_passed')])


def test_text_codes_survive_numeric_export():
    assert facts.text_number('01') == '01'
    assert facts.text_number(1) == '1'
    assert facts.text_number(0) == '0'
    assert facts.text_number(None) is None
    with pytest.raises(ValueError):
        facts.text_number(True)
    with pytest.raises(ValueError):
        facts.text_number(1.5)
    with pytest.raises(ValueError, match='Jersey number lost text type'):
        facts.validate_snapshots([snapshot_row(jersey_number=23)])


def test_name_normalization_never_overwrites_raw():
    assert facts.name_key('VLADYSLAV\u00a0 KORENIUK') == 'VLADYSLAV KORENIUK'
    assert facts.name_key('GEORGE KELL Ⅲ') == 'GEORGE KELL III'
    assert facts.name_key('  ') is None


def test_domestic_merge_renumbers_and_reports_collisions():
    baseline = [dict.fromkeys(facts.DOMESTIC)]
    baseline[0].update({'record_key': '2020-2021|beijing_shougang|范子铭', 'season': '2020-2021',
                        'club_id': 'beijing_shougang', 'player': '范子铭', 'sequence': '19',
                        'source_url': 'https://example.invalid/x', 'verification_level': 'auto_validated'})
    addition = dict.fromkeys(facts.DOMESTIC)
    addition.update({'record_key': '2020-2021|beijing_shougang|田宇翔', 'season': '2020-2021',
                     'club_id': 'beijing_shougang', 'player': '田宇翔', 'sequence': '2',
                     'source_url': 'https://example.invalid/y', 'verification_level': 'human_verified'})
    duplicate = dict(addition, record_key='2020-2021|beijing_shougang|范子铭', player='范子铭')
    merged, report = merge_domestic(baseline, [addition, duplicate])
    assert merged[0] == baseline[0]
    assert merged[1]['sequence'] == '20'
    assert report['added'] == ['2020-2021|beijing_shougang|田宇翔']
    assert report['candidate_rows'] == 2
    assert report['collisions'][0]['record_key'] == '2020-2021|beijing_shougang|范子铭'
