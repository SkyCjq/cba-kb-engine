import json
from pathlib import Path
import pytest
from cba_kb import xlsx
from cba_kb.build_facts import EVENTS_FILE, MASTER_FILE, SNAPSHOTS_FILE, build, collect
from cba_kb.common import digest, read
from cba_kb.facts import DOMESTIC, EVENT, SNAPSHOT, snapshot_key, event_key
from cba_kb.master import inspect


def baseline(path):
    rows = []
    for index, player in enumerate(['范子铭', '孙昊锋'], 1):
        row = dict.fromkeys(DOMESTIC)
        row.update({'record_key': f'2020-2021|beijing_shougang|{player}', 'season': '2020-2021',
                    'club_id': 'beijing_shougang', 'club_official': '北京首钢', 'sequence': str(index),
                    'player': player, 'source_url': 'https://example.invalid/roster',
                    'verification_level': 'auto_validated'})
        rows.append(row)
    xlsx.write(path, DOMESTIC, rows, 'MASTER')
    return rows


def staging(tmp_path):
    addition = dict.fromkeys(DOMESTIC)
    addition.update({'record_key': '2020-2021|beijing_shougang|田宇翔', 'season': '2020-2021',
                     'club_id': 'beijing_shougang', 'club_official': '北京首钢', 'sequence': '1',
                     'player': '田宇翔', 'source_url': 'https://example.invalid/transfer',
                     'verification_level': 'human_verified'})
    snapshot = dict.fromkeys(SNAPSHOT)
    snapshot.update({'season': '2024-2025', 'snapshot_as_of': '2025-03-31', 'club_id': 'ningbo_fubang',
                     'name_en_raw': 'KOUAT NOI', 'name_en_normalized': 'KOUAT NOI',
                     'nationality': '澳大利亚', 'jersey_number': '01', 'source_type': 'gdrive',
                     'verification_level': 'auto_validated'})
    snapshot['snapshot_key'] = snapshot_key(snapshot)
    event = dict.fromkeys(EVENT)
    event.update({'season': '2024-2025', 'player_type': 'foreign', 'club_id': 'ningbo_fubang',
                  'event_type': 'registration_cancelled', 'event_date': '2024-08-21',
                  'date_year_inferred': True, 'source_type': 'gdrive',
                  'verification_level': 'auto_validated', 'provisional_player_key': 'DONELL COOPER',
                  'sequence': '77'})
    event['event_key'] = event_key(event)
    directory = tmp_path/'staging'/'run-1'
    directory.mkdir(parents=True)
    (directory/'records.json').write_text(json.dumps(
        {'records': {'domestic': [addition], 'snapshots': [snapshot], 'events': [event]}}, ensure_ascii=False))
    return directory


def test_candidate_build_writes_three_products(tmp_path):
    master_path = tmp_path/'baseline.xlsx'
    rows = baseline(master_path)
    baseline_rows, baseline_summary = inspect(master_path)
    assert baseline_rows[0]['sequence'] == '1'
    collected = collect([staging(tmp_path)])
    output = tmp_path/'candidate'
    result = build(collected, baseline_rows, baseline_summary, output, 'v1.5.1-test', 'commit-sha',
                   '2026-09-10T00:00:00+00:00')
    assert result['products'] == {MASTER_FILE: 3, SNAPSHOTS_FILE: 1, EVENTS_FILE: 1}
    assert result['merge'] == {'baseline_rows': 2, 'added': 1, 'collisions': 0}
    validation = read(output/'validation.json')
    assert validation['rows'] == 3
    assert validation['fact_products']['snapshots']['rows'] == 1
    provenance = read(output/'provenance.json')
    assert provenance['products'] == [MASTER_FILE, SNAPSHOTS_FILE, EVENTS_FILE]
    assert provenance['merge']['added'] == ['2020-2021|beijing_shougang|田宇翔']
    assert provenance['fact_product_sha256'][MASTER_FILE] == digest((output/MASTER_FILE).read_bytes())
    assert 'v1.5.1 事实产品' in (output/'INDEX.md').read_text()
    assert (output/'MASTER.csv').exists() and (output/'MASTER.jsonl').exists()
    assert (output/'CBA_注册_2020-2021.md').exists()
    assert len(list((output).glob('*.xlsx'))) == 3


def test_build_refuses_an_existing_candidate_directory(tmp_path):
    master_path = tmp_path/'baseline.xlsx'
    baseline(master_path)
    baseline_rows, baseline_summary = inspect(master_path)
    output = tmp_path/'candidate'
    output.mkdir()
    (output/'keep.txt').write_text('frozen')
    with pytest.raises(ValueError, match='must be new'):
        build({'domestic': [], 'snapshots': [], 'events': []}, baseline_rows, baseline_summary, output,
              'v1.5.1-test', 'commit-sha', '2026-09-10T00:00:00+00:00')


def test_xlsx_writer_is_deterministic(tmp_path):
    rows = [dict.fromkeys(DOMESTIC, 'x') for _ in range(2)]
    first, second = tmp_path/'a.xlsx', tmp_path/'b.xlsx'
    xlsx.write(first, DOMESTIC, rows, 'MASTER')
    xlsx.write(second, DOMESTIC, rows, 'MASTER')
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(ValueError, match='does not match the product schema'):
        xlsx.write(tmp_path/'c.xlsx', DOMESTIC, [{'record_key': 'x'}], 'MASTER')
