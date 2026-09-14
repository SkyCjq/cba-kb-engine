from copy import deepcopy
from pathlib import Path

import pytest

from cba_kb.aliases import Clubs
from cba_kb.event_closure import (
    compose_candidate_records,
    cross_table_qa,
    reconcile_legacy_events,
    semantic_fingerprint,
    upgrade_records,
    validate_event_closure,
    write_reconciliation_reports,
)
from cba_kb.registration_domain import STATUS_EVENTS
from scripts.prepare_v1_5_3 import validate_candidate_controls


ROOT = Path(__file__).resolve().parents[1]


def event(**overrides):
    row = dict.fromkeys(STATUS_EVENTS)
    row.update({
        'event_key': '2025-2026|domestic|beijing_konggu|罗汉琛|free_agent_claim|2026-01-16|1',
        'event_domain': 'domestic_movement',
        'event_type': 'free_agent_claim',
        'event_status': 'confirmed',
        'season': '2025-2026',
        'player_type': 'domestic',
        'club_id': 'beijing_konggu',
        'club_source_name': '北京控股',
        'player_name_zh': '罗汉琛',
        'to_club_id': 'beijing_konggu',
        'from_club_source_name': '深圳注册关系结束→自由球员',
        'event_date': '2026-01-16',
        'event_date_status': 'known',
        'event_date_precision': 'day',
        'date_year_inferred': False,
        'club_announcement_date_status': 'unknown',
        'registration_submission_date_status': 'pending_evidence',
        'registration_completed_date_status': 'pending_evidence',
        'registration_window_deadline_status': 'unknown',
        'registration_method_official': '认领',
        'research_movement_label': 'release_then_claim',
        'source_file_id': 'source-1',
        'source_url_primary': 'https://example.invalid/source-1',
        'source_page_or_row': 'row 1',
        'source_type': 'official',
        'extraction_method': 'fixture',
        'verification_status': 'human_verified',
        'source_authority': 'A1_official_direct',
        'raw_event_text': 'official registration row',
    })
    row.update(overrides)
    return row


def luo_master():
    return [
        {
            'record_key': '2024-2025|shanghai_jiushi|罗汉琛',
            'season': '2024-2025',
            'club_id': 'shanghai_jiushi',
            'player': '罗汉琛',
            'former_club': '/',
        },
        {
            'record_key': '2025-2026|beijing_konggu|罗汉琛',
            'season': '2025-2026',
            'club_id': 'beijing_konggu',
            'player': '罗汉琛',
            'former_club': '深圳新世纪',
        },
        {
            'record_key': '2026-2027|beijing_konggu|罗汉琛',
            'season': '2026-2027',
            'club_id': 'beijing_konggu',
            'player': '罗汉琛',
            'former_club': '/',
        },
    ]


def test_u01_former_club_does_not_create_snapshot():
    master = luo_master()
    before = deepcopy(master)
    records = {'registration_status_events': [event()]}
    result = cross_table_qa(records, master, Clubs(ROOT))
    assert result['signals'] == [{
        'signal_type': 'relationship_gap_signal',
        'severity': 'research',
        'event_domain': 'domestic_movement',
        'player': '罗汉琛',
        'season': '2025-2026',
        'from_snapshot_season': '2024-2025',
        'to_snapshot_season': '2025-2026',
        'from_club_id': 'shanghai_jiushi',
        'to_club_id': 'beijing_konggu',
        'evidence_hint': 'former_cba_club = shenzhen_xinshiji',
        'inferred_event_date': None,
        'creates_snapshot': False,
    }]
    assert master == before


def test_u02_research_label_does_not_replace_official_method():
    row = event()
    validate_event_closure({'registration_status_events': [row]})
    row['registration_method_official'] = 'release_then_claim'
    row['research_movement_label'] = 'release_then_claim'
    with pytest.raises(ValueError, match='remain separate'):
        validate_event_closure({'registration_status_events': [row]})


def test_u03_deadline_does_not_become_event_date():
    row = event(
        event_date=None,
        event_date_status='unknown',
        event_date_precision='unknown',
        registration_window_deadline='2026-01-18',
        registration_window_deadline_status='known',
    )
    validate_event_closure({'registration_status_events': [row]})
    assert row['event_date'] is None
    assert row['registration_window_deadline'] == '2026-01-18'


def test_u04_announcement_does_not_become_completion_date():
    row = event(
        event_date=None,
        event_date_status='unknown',
        event_date_precision='unknown',
        club_announcement_date='2025-11-24',
        club_announcement_date_status='known',
    )
    validate_event_closure({'registration_status_events': [row]})
    assert row['registration_completed_date'] is None
    assert row['registration_completed_date_status'] == 'pending_evidence'


def test_u05_lifecycle_correction_preserves_history():
    old = event(event_key='old')
    old['event_status'] = 'corrected'
    new = event(event_key='new', supersedes_event_key='old', correction_reason='official correction')
    result = validate_event_closure({'registration_status_events': [old, new]})
    assert result['rows'] == 2
    assert result['effective_rows'] == 1
    assert new['event_status'] == 'confirmed'


def test_u06_withdrawal_and_cancellation_keep_original_rows():
    original = event(event_key='original')
    withdrawn = event(
        event_key='withdrawn',
        event_type='registration_cancelled',
        event_status='withdrawn',
        to_club_id=None,
    )
    cancelled = event(
        event_key='cancelled',
        event_type='registration_cancelled',
        event_status='cancelled',
        to_club_id=None,
    )
    validate_event_closure({
        'registration_status_events': [original, withdrawn, cancelled],
    })
    assert len({original['event_key'], withdrawn['event_key'], cancelled['event_key']}) == 3


@pytest.mark.parametrize('field', [
    'registration_method_official',
    'registration_completed_date',
    'registration_submission_date',
    'research_movement_label',
])
def test_u07_unknown_fields_are_not_inferred(field):
    row = event(event_date=None, event_date_status='unknown', event_date_precision='unknown')
    row[field] = None
    validate_event_closure({'registration_status_events': [row]})
    assert row[field] is None


def test_g01_luo_hanchen_relationship_gap_is_positive_but_not_a_fact():
    records = {'registration_status_events': [event()]}
    result = cross_table_qa(records, luo_master(), Clubs(ROOT))
    signal = next(row for row in result['signals']
                  if row['signal_type'] == 'relationship_gap_signal')
    assert 'shenzhen_xinshiji' in signal['evidence_hint']
    assert signal['inferred_event_date'] is None
    assert signal['creates_snapshot'] is False


def test_g02_same_club_continuation_does_not_emit_movement_signal():
    master = [
        {'season': '2025-2026', 'club_id': 'beijing_konggu', 'player': '球员甲'},
        {'season': '2026-2027', 'club_id': 'beijing_konggu', 'player': '球员甲'},
    ]
    result = cross_table_qa({'registration_status_events': []}, master, Clubs(ROOT))
    assert result['signals'] == []
    assert result['warnings'] == []


def test_g03_transfer_event_and_snapshot_remain_distinct():
    master = luo_master()[:2]
    records = {'registration_status_events': [event()]}
    result = cross_table_qa(records, master, Clubs(ROOT))
    assert not any(row['signal_type'] == 'event_gap_candidate'
                   for row in result['warnings'])
    assert records['registration_status_events'][0]['event_date'] == '2026-01-16'
    assert master[1]['season'] == '2025-2026'


def test_g04_correction_chain_exposes_current_effective_event():
    old = event(event_key='old', event_date='2026-01-15')
    old['event_status'] = 'corrected'
    new = event(event_key='new', event_date='2026-01-16', supersedes_event_key='old')
    result = validate_event_closure({'registration_status_events': [old, new]})
    assert result['effective_rows'] == 1
    assert new['supersedes_event_key'] == 'old'
    assert old['event_status'] == 'corrected'


def _full_reconciliation_fixture():
    events = []
    relations = []
    legacy = []
    bayi_players = [f'八一球员{i:02d}' for i in range(1, 15)]
    for index, player in enumerate(bayi_players, 1):
        key = f'2020-2021|beijing_konggu|{player}'
        movement = event(
            event_key=f'{key}|free_agent_claim|2021-02-25|{index}',
            season='2020-2021',
            club_id='beijing_konggu',
            player_name_zh=player,
            from_club_id='bayi',
            from_club_source_name='八一男篮',
            event_date='2021-02-25',
            research_movement_label=None,
            registration_method_official='自由球员认领',
        )
        events.append(movement)
        relations.append({
            'record_key': key,
            'season': '2020-2021',
            'club_id': 'beijing_konggu',
            'player': player,
        })
        legacy.append({
            'event_key': f'legacy-bayi-{index}',
            'season': '2020-2021',
            'player_type': 'domestic',
            'club_id': 'beijing_konggu',
            'player_name_zh': player,
            'event_type': 'registration_change',
            'from_club_id': 'bayi',
        })

    clubs = [
        'beijing_konggu', 'beijing_shougang', 'fujian_xunxing', 'guangdong_hongyuan',
        'guangzhou_longshi', 'jiangsu_kendiya', 'jilin_jiutai',
        'liaoning_shenyang_sansheng', 'nanjing_tongxi', 'ningbo_fubang',
        'qingdao_guoxin_haitian', 'shandong_gaosu', 'shanghai_jiushi',
        'shanxi_fenjiu', 'shenzhen_xinshiji', 'sichuan_jincheng',
        'tianjin_ronggang', 'xinjiang_guanghui', 'zhejiang_chouzhou',
        'zhejiang_guangsha',
    ]
    for index in range(1, 60):
        club = clubs[(index - 1) % len(clubs)]
        player = f'FOREIGN PLAYER {index:02d}'
        canonical = event(
            event_key=f'2024-2025|foreign|{club}|{player}|registration_cancelled|2025-01-01|{index}',
            event_domain='foreign_registration',
            event_type='registration_cancelled',
            season='2024-2025',
            player_type='foreign',
            club_id=club,
            player_name_zh=None,
            player_name_en_raw=player,
            player_name_en_normalized=player,
            to_club_id=None,
            from_club_id=None,
            event_date='2025-01-01',
            registration_method_official=None,
            research_movement_label=None,
        )
        events.append(canonical)
        legacy.append({
            'event_key': f'legacy-foreign-{index}',
            'season': '2024-2025',
            'player_type': 'foreign',
            'club_id': club,
            'player_name_en_raw': player,
            'event_type': 'registration_cancelled',
        })
    return legacy, {
        'registration_status_events': events,
        'domestic_registrations': relations,
    }


def test_reconciliation_control_73_rows_and_87_edges(tmp_path):
    legacy, records = _full_reconciliation_fixture()
    edges, summary = reconcile_legacy_events(legacy, records, legacy_source_id='legacy-events')
    assert summary == {
        'legacy_row_count': 73,
        'mapping_edge_count': 87,
        'mapped_legacy_count': 73,
        'unmapped_legacy_count': 0,
        'exact_match_count': 59,
        'semantic_match_count': 0,
        'canonical_split_count': 14,
        'conflict_count': 0,
        'unexplained_conflict_count': 0,
        'deprecated_count': 0,
        'legacy_only_count': 0,
        'counts_by_target_entity': {
            'domestic_registrations': 14,
            'registration_status_events': 73,
        },
        'counts_by_mapping_role': {
            'event_projection': 73,
            'relationship_projection': 14,
        },
        'unresolved': [],
    }
    csv_path, json_path = write_reconciliation_reports(edges, summary, tmp_path)
    assert csv_path.name == 'v1.5.3-event-reconciliation.csv'
    assert json_path.name == 'v1.5.3-event-reconciliation-summary.json'
    assert len(csv_path.read_text().splitlines()) == 88


def test_reconciliation_does_not_force_unknown_legacy_into_wrong_target():
    legacy = [{
        'event_key': 'legacy-unknown',
        'season': '2024-2025',
        'player_type': 'foreign',
        'club_id': 'beijing_konggu',
        'player_name_en_raw': 'UNKNOWN',
        'event_type': 'registration_cancelled',
    }]
    edges, summary = reconcile_legacy_events(legacy, {'registration_status_events': []})
    assert edges[0]['target_entity'] == 'none'
    assert edges[0]['match_status'] == 'legacy_only'
    assert summary['unmapped_legacy_count'] == 1


def test_event_provenance_requires_source_and_locator():
    row = event(source_page_or_row=None)
    with pytest.raises(ValueError, match='source_page_or_row'):
        validate_event_closure({'registration_status_events': [row]})
    row = event(source_url_primary=None)
    with pytest.raises(ValueError, match='source URL'):
        validate_event_closure({'registration_status_events': [row]})


def test_domain_scoped_qa_does_not_apply_domestic_transition_rules_to_foreign():
    foreign = event(
        event_key='foreign-cancel',
        event_domain='foreign_registration',
        event_type='registration_cancelled',
        player_type='foreign',
        player_name_zh=None,
        player_name_en_raw='PLAYER ONE',
        player_name_en_normalized='PLAYER ONE',
        to_club_id=None,
    )
    usage = event(
        event_key='usage',
        event_domain='foreign_usage',
        event_type='usage_suspended',
        player_type='foreign',
        player_name_zh=None,
        player_name_en_raw='PLAYER TWO',
        player_name_en_normalized='PLAYER TWO',
        to_club_id=None,
    )
    result = cross_table_qa({'registration_status_events': [foreign, usage]})
    assert result['warnings'] == []
    assert result['counts_by_domain'] == {
        'foreign_registration': 1,
        'foreign_usage': 1,
    }


def test_event_closure_is_deterministic():
    records = {'registration_status_events': [event()]}
    first = semantic_fingerprint(records)
    upgraded = upgrade_records(records)
    second = semantic_fingerprint(upgraded)
    assert first == second


def test_candidate_composition_replaces_domestic_and_preserves_foreign():
    live = {
        'domestic_registrations': [{'record_key': 'old'}],
        'domestic_transaction_windows': [{'window_key': 'old'}],
        'registration_status_events': [
            event(event_key='old-domestic'),
            event(
                event_key='foreign-cancel',
                event_domain='foreign_registration',
                event_type='registration_cancelled',
                player_type='foreign',
                player_name_zh=None,
                player_name_en_raw='PLAYER ONE',
                player_name_en_normalized='PLAYER ONE',
                to_club_id=None,
            ),
        ],
    }
    domestic = {
        'domestic_registrations': [{'record_key': 'new'}],
        'domestic_transaction_windows': [
            {'window_key': 'old'},
            {'window_key': 'new'},
        ],
        'registration_status_events': [event(event_key='new-domestic')],
    }
    result = compose_candidate_records(live, domestic)
    assert result['domestic_registrations'] == [{'record_key': 'new'}]
    assert result['domestic_transaction_windows'] == [
        {'window_key': 'old'}, {'window_key': 'new'},
    ]
    assert [row['event_key'] for row in result['registration_status_events']] == [
        'new-domestic', 'foreign-cancel',
    ]


def test_candidate_controls_fail_closed_on_protected_table_change():
    live = {
        'domestic_registrations': [],
        'domestic_transaction_windows': [
            {'window_key': 'historic', 'season': '2023-2024'},
        ],
        'registration_status_events': [event(event_key='live-domestic')],
        'foreign_registration_snapshots': [],
        'foreign_priority_right_snapshots': [],
        'foreign_priority_right_transactions': [],
    }
    candidate = {
        name: [dict(row) for row in rows]
        for name, rows in live.items()
    }
    candidate['domestic_transaction_windows'] += [
        {'window_key': f'2024-2025|{number}', 'season': '2024-2025'}
        for number in range(1, 4)
    ] + [
        {'window_key': f'2025-2026|{number}', 'season': '2025-2026'}
        for number in range(1, 4)
    ]
    candidate['registration_status_events'] += [
        event(event_key='candidate-domestic'),
        event(
            event_key='foreign-registration',
            event_domain='foreign_registration',
            event_type='registration_cancelled',
            player_type='foreign',
            player_name_zh=None,
            player_name_en_raw='PLAYER ONE',
            player_name_en_normalized='PLAYER ONE',
            to_club_id=None,
        ),
        event(
            event_key='foreign-usage',
            event_domain='foreign_usage',
            event_type='usage_suspended',
            player_type='foreign',
            player_name_zh=None,
            player_name_en_raw='PLAYER TWO',
            player_name_en_normalized='PLAYER TWO',
            to_club_id=None,
        ),
    ]
    reconciliation = {
        'legacy_row_count': 1,
        'mapped_legacy_count': 1,
        'unexplained_conflict_count': 0,
    }

    controls = validate_candidate_controls(
        live, candidate, reconciliation,
        [row for row in candidate['domestic_transaction_windows']
         if row['window_key'] != 'historic'],
    )
    assert controls['candidate_counts']['domestic_transaction_windows'] == 7

    candidate['foreign_registration_snapshots'].append({'snapshot_key': 'unexpected'})
    with pytest.raises(ValueError, match='Protected table count changed'):
        validate_candidate_controls(
            live, candidate, reconciliation,
            [row for row in candidate['domestic_transaction_windows']
             if row['window_key'] != 'historic'],
        )
