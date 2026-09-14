"""Formal-ID acceptance of the published v1.5.1 fact products (Codex side).

Reads only the production objects by their Drive IDs, exactly as an external AI
consumer would, and writes workspace/reports/<release>-acceptance-codex.json.
"""
import argparse
import io
import json
import sys
from pathlib import Path
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from cba_kb.common import digest, save
from cba_kb.drive import Drive
from cba_kb.master import HEADERS
from cba_kb.native import BEGIN, END

MASTER_ID = '1Nb4-4rrySjKW7GSA_PLh1SCkPgW9kJtc'
SNAPSHOTS_ID = '1avAxhNUTgm14MIdkgkHhia6_adSDGwLD'
EVENTS_ID = '1WtE63GIQxYPUBsfcRlhKCH8lJgyTZeGF'
STATUS_ID = '1FQmbZIJxCkTkpr6ovKh5-CoBbpT0YMwV'
REGISTRY_ID = '106NV6lV4mPLNjohMcSLdOyPRct0uOUsx'
INDEX_ID = '1VqnFtMRSlOV9K7eVCAJKQ5HngWMl60MibbopNclxN9c'


def sheet(blob, name):
    book = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    page = book[book.sheetnames[0]]
    values = list(page.values)
    head = list(values[0])
    return book.sheetnames, head, [dict(zip(head, row)) for row in values[1:]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', default='v1.5.1-1')
    parser.add_argument('--report', type=Path, default=None)
    parser.add_argument('--baseline', type=Path,
                        default=Path('workspace/inputs/oauth-001/MASTER.xlsx'))
    arguments = parser.parse_args()
    report_path = arguments.report or Path('workspace/reports')/f'{arguments.release}-acceptance-codex.json'
    drive = Drive(Path.cwd())
    checks, evidence = [], {}

    def check(name, passed, detail):
        checks.append({'check': name, 'status': 'PASS' if passed else 'FAIL', 'detail': detail})

    status = json.loads(drive.get(STATUS_ID))
    evidence['release_status'] = {'state': status.get('state'),
                                  'current_release_id': status.get('current_release_id'),
                                  'previous_release_id': status.get('previous_release_id'),
                                  'artifacts': len(status.get('artifacts', [])),
                                  'url': f'https://drive.google.com/file/d/{STATUS_ID}/view'}
    check('release_status is COMPLETE for this release',
          status.get('state') == 'COMPLETE' and status.get('current_release_id') == arguments.release,
          evidence['release_status'])

    master_blob = drive.get(MASTER_ID)
    names, head, master = sheet(master_blob, 'MASTER')
    baseline_names, baseline_head, baseline = sheet(arguments.baseline.read_bytes(), 'MASTER')
    baseline_doc = json.loads((Path('docs/baseline_2026-09-10.json')).read_text())
    evidence['master'] = {'url': f'https://drive.google.com/file/d/{MASTER_ID}/view',
                          'rows': len(master), 'columns': len(head), 'sha256': digest(master_blob),
                          'unique_keys': len({row['record_key'] for row in master})}
    check('MASTER schema unchanged (20 columns)', head == list(HEADERS), head)
    check('MASTER carries baseline rows verbatim',
          all(all(row[key] == base[key] for key in head) for row, base in zip(master, baseline)),
          f'{len(baseline)} baseline rows compared')
    check('MASTER keys unique and rows = baseline + additions',
          len(master) == len(baseline) + 14 and len({row['record_key'] for row in master}) == len(master),
          f'rows={len(master)}')
    jiahao = [row for row in master if row['player'] == '贾昊' and row['season'] == '2024-2025']
    check('Jia Hao deadline regression',
          jiahao and jiahao[0]['notice_deadline'] == baseline_doc['jiahao'],
          jiahao[0]['notice_deadline'] if jiahao else 'record missing')

    snapshot_names, snapshot_head, snapshots = sheet(drive.get(SNAPSHOTS_ID), 'SNAPSHOTS')
    evidence['snapshots'] = {'url': f'https://drive.google.com/file/d/{SNAPSHOTS_ID}/view',
                             'sheets': snapshot_names, 'rows': len(snapshots), 'columns': len(snapshot_head),
                             'unique_keys': len({row['snapshot_key'] for row in snapshots}),
                             'seasons': sorted({row['season'] for row in snapshots})}
    check('SNAPSHOTS single sheet, 73 rows, unique keys',
          snapshot_names == ['SNAPSHOTS'] and len(snapshots) == 73
          and len({row['snapshot_key'] for row in snapshots}) == 73,
          evidence['snapshots'])
    check('SNAPSHOTS keeps raw and normalized names plus text jersey numbers',
          all(isinstance(row['jersey_number'], str) for row in snapshots
              if row['jersey_number'] is not None)
          and any(row['name_en_raw'] != row['name_en_normalized'] for row in snapshots)
          and all(row['source_file_id'] for row in snapshots),
          [(row['name_en_raw'], row['name_en_normalized']) for row in snapshots
           if row['name_en_raw'] != row['name_en_normalized']])

    event_names, event_head, events = sheet(drive.get(EVENTS_ID), 'EVENTS')
    cancellations = [row for row in events if row['event_type'] == 'registration_cancelled']
    changes = [row for row in events if row['event_type'] == 'registration_change']
    evidence['events'] = {'url': f'https://drive.google.com/file/d/{EVENTS_ID}/view',
                          'sheets': event_names, 'rows': len(events),
                          'unique_keys': len({row['event_key'] for row in events}),
                          'cancellations': len(cancellations), 'changes': len(changes)}
    check('EVENTS single sheet, 73 rows, unique keys, 59 + 14 split',
          event_names == ['EVENTS'] and len(events) == 73
          and len({row['event_key'] for row in events}) == 73
          and len(cancellations) == 59 and len(changes) == 14,
          evidence['events'])
    check('Cancellations have dates with inferred-year flag; no fabricated timestamps',
          all(row['event_date'] and row['date_year_inferred'] is True for row in cancellations)
          and all(len(row['event_date']) == 10 for row in events),
          sorted({row['event_date'] for row in cancellations})[:3])
    split = [{'player': row['player_name_zh'], 'transaction': row['event_date'],
              'announcement': row['club_announcement_date']} for row in changes
             if row['club_announcement_date'] and row['event_date'] != row['club_announcement_date']]
    check('Midseason window deadline stays separate from notice_deadline',
          {row['registration_window_deadline'] for row in changes} == {'2021-02-27'}
          and all(row['club_announcement_date'] for row in changes) and bool(split),
          {'window_deadline': sorted({row['registration_window_deadline'] for row in changes}),
           'announcement_dates_distinct_from_transaction': split})
    check('Snapshot and event grains stay separate',
          not ({row['snapshot_key'] for row in snapshots} & {row['event_key'] for row in events})
          and 'snapshot_key' not in event_head and 'event_key' not in snapshot_head,
          'no key overlap')

    registry = drive.get(REGISTRY_ID).decode('utf-8-sig').strip().splitlines()
    evidence['registry'] = {'url': f'https://drive.google.com/file/d/{REGISTRY_ID}/view',
                            'rows': len(registry) - 1}
    check('Source registry registered the four new sources',
          all(any(source in line for line in registry)
              for source in ('1C8BpmY_MMjFGnSOdllRsnVWlwPdfKViV', '16H0cxiItoSlGALeHce-E06pD_a3RFAmd',
                             '1D72_i0dzR9TmeV_XH8KeBr_BTxxwFPIM', '1WYVqi94bqVGOLsD7t1nmMXyTMTU4TOlr')),
          evidence['registry'])

    prefix = drive.get_managed_doc(INDEX_ID).decode()
    evidence['index'] = {'url': f'https://docs.google.com/document/d/{INDEX_ID}/edit',
                         'prefix_bytes': len(prefix.encode())}
    check('INDEX entry point exposes the v1.5.1 products and coverage limits',
          prefix.lstrip().startswith(BEGIN.strip()) and END.strip() in prefix
          and 'CBA_外籍球员注册_SNAPSHOTS.xlsx' in prefix and 'CBA_球员注册_EVENTS.xlsx' in prefix
          and '尚未完成验收的来源' in prefix,
          'managed prefix inspected')

    report = {'release': arguments.release, 'consumer': 'codex (local OAuth, formal Drive IDs)',
              'state': 'PASS' if all(item['status'] == 'PASS' for item in checks) else 'FAIL',
              'checks': checks, 'evidence': evidence,
              'note': ('Formal-ID consumption acceptance. It does not replace the second AI result '
                       'and does not by itself authorise a STABLE tag.')}
    save(report_path, report)
    print(json.dumps({'report': str(report_path), 'state': report['state'],
                      'checks': {item['check']: item['status'] for item in checks}},
                     ensure_ascii=False, indent=2))
    return 0 if report['state'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
