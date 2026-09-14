"""Real-source acceptance for v1.5.1 (plan sections A1/A2).

Run from the deployment root after each real source has been staged:

    PYTHONPATH=src .venv/bin/python scripts/verify_v1_5_1_sources.py \
        --staging workspace/staging --master workspace/inputs/<run>/MASTER.xlsx \
        --report workspace/reports/v1.5.1-source-acceptance.json

Counts are asserted against the plan. A mismatch fails the run: never delete
rows to make the expected numbers fit.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from cba_kb.build_facts import collect
from cba_kb.master import inspect
from cba_kb.merge import merge_domestic

EXPECTED = {'teams': 20, 'snapshots': 73, 'events': 59, 'observed_registrations': 132,
            'snapshot_as_of': '2025-03-31'}


def staged(staging):
    runs = sorted(Path(staging).glob('*/records.json'))
    if not runs:
        raise SystemExit(f'No staged runs under {staging}')
    return collect([run.parent for run in runs])


def check_foreign(records):
    report = [run for run in records['runs'] if (run['report'] or {}).get('season') == '2024-2025']
    if len(report) != 1:
        return {'status': 'MISSING', 'detail': 'exactly one 2024-2025 staged run required'}
    report = report[0]['report']
    mismatches = {key: {'expected': value, 'actual': report.get(key)}
                  for key, value in EXPECTED.items() if report.get(key) != value}
    return {'status': 'PASS' if not mismatches else 'FAIL', 'report': report,
            'mismatches': mismatches, 'unresolved_clubs': report.get('unresolved_clubs'),
            'review': report.get('review')}


def check_domestic(records, baseline_rows):
    bayi = [row for row in records['domestic'] if row['season'] == '2020-2021']
    merged, report = merge_domestic(baseline_rows, records['domestic'])
    events = [row for row in records['events'] if row['player_type'] == 'domestic']
    issues = []
    if len(bayi) != 14:
        issues.append(f'expected 14 midseason records, got {len(bayi)}')
    if any(row['notice_deadline'] for row in bayi):
        issues.append('notice_deadline must stay empty for the midseason records')
    if {row['registration_window_deadline'] for row in events} != {'2021-02-27'}:
        issues.append('registration_window_deadline must be 2021-02-27, separate from notice_deadline')
    if {row['verification_level'] for row in bayi} != {'human_verified'}:
        issues.append('midseason verification_level must normalize to human_verified')
    return {'status': 'PASS' if not issues else 'FAIL', 'records': len(bayi), 'events': len(events),
            'collisions': report['collisions'], 'candidate_rows': report['candidate_rows'],
            'issues': issues}


def check_images(records):
    seasons = sorted({(run['report'] or {}).get('season') for run in records['runs']
                      if (run['report'] or {}).get('ocr')})
    pending = [season for season in ('2020-2021', '2022-2023') if season not in seasons]
    return {'status': 'PENDING' if pending else 'PASS', 'ocr_seasons': seasons, 'pending': pending}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--release-id', default='v1.5.1-source-acceptance')
    arguments = parser.parse_args()
    baseline_rows, baseline = inspect(arguments.master)
    records = staged(arguments.staging)
    report = {'release_id': arguments.release_id, 'state': 'SOURCE_ACCEPTANCE',
              'baseline': {'rows': baseline['rows'], 'unique_keys': baseline['unique_keys'],
                           'sha256': baseline['sha256']},
              'staged_runs': [run['path'] for run in records['runs']],
              'foreign_2024_2025': check_foreign(records),
              'domestic_2020_2021': check_domestic(records, baseline_rows),
              'foreign_images': check_images(records),
              'note': ('Real-source acceptance only. A source that is not staged stays PENDING; '
                       'this report implies no production release and no production write.')}
    statuses = [report['foreign_2024_2025']['status'], report['domestic_2020_2021']['status']]
    report['state'] = 'FAIL' if 'FAIL' in statuses else (
        'PASS_WITH_PENDING_SOURCES' if report['foreign_images']['status'] == 'PENDING' else 'PASS')
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['state'] == 'FAIL' else 0


if __name__ == '__main__':
    sys.exit(main())
