"""Build a register-first proposal for the v1.5.1 sources.

The proposal keeps the real source_registry.csv schema. It is written to the
staging area only: registering a source is a controlled registry update, not a
side effect of extraction, and a source is never IMPORted by being discovered.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from cba_kb.build_facts import collect
from cba_kb.source_policy import excluded_from_current

COLUMNS = ['source_id', 'drive_file_id', 'source_title', 'source_url', 'current_parent_id',
           'original_discovery_path', 'source_type', 'source_role', 'season', 'club_id',
           'content_hash', 'file_size_bytes', 'business_status', 'extraction_status',
           'extraction_method', 'validation_status', 'records_generated', 'records_imported',
           'discovered_at', 'registered_at', 'processed_at', 'verified_at', 'notes']

PLAN = {
    '八一球员_2021赛季中期转会核验.md': {
        'season': '2020-2021', 'role': 'midseason_transfer_verification',
        'adapter': 'midseason_md', 'status': 'markdown_extracted',
        'method': 'markdown block parser + strict club resolution',
        'validation': 'human_verified', 'record_types': ('domestic', 'events')},
    '2024-2025': {
        'season': '2024-2025', 'role': 'foreign_registration_snapshot_primary',
        'adapter': 'foreign_xlsx', 'status': 'table_extracted',
        'method': 'xlsx merge-header segmentation + season-aware cancellation parsing',
        'validation': 'auto_validated', 'record_types': ('snapshots', 'events')},
}


def plan_for(name):
    for key, value in PLAN.items():
        if key in name:
            return value
    raise ValueError(f'No registry plan for {name}')


def counts_for(records, season, types):
    for run in records['runs']:
        report = run['report'] or {}
        if (run.get('season') or report.get('season')) != season:
            continue
        summary = run.get('summary') or {}
        if all(name in summary for name in types):
            return [f'{summary[name]["rows"]} {name}' for name in types]
        return [f'{report.get(name, 0)} {name}' for name in types if report.get(name)]
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inbox', default='1wKERXv7u_BcxSAdPXZpKL_zrv7IcVpPf')
    arguments = parser.parse_args()
    sources = json.loads(arguments.sources.read_text())
    records = collect(sorted(path.parent for path in arguments.staging.glob('*/records.json')))
    rows = []
    for source in sources:
        if excluded_from_current(source):
            continue
        plan = plan_for(source['name'])
        counts = counts_for(records, plan['season'], plan['record_types'])
        rows.append({
            'source_id': f"drive:{source['id']}", 'drive_file_id': source['id'],
            'source_title': source['name'],
            'source_url': f"https://drive.google.com/file/d/{source['id']}/view",
            'current_parent_id': arguments.inbox,
            'original_discovery_path': f"00_inbox_待处理/{source['name']}",
            'source_type': source['mime'], 'source_role': plan['role'], 'season': plan['season'],
            'club_id': '', 'content_hash': source.get('sha256', ''),
            'file_size_bytes': str(source.get('size', '')), 'business_status': 'DISCOVERED',
            'extraction_status': plan['status'], 'extraction_method': plan['method'],
            'validation_status': plan['validation'],
            'records_generated': '; '.join(counts), 'records_imported': '',
            'discovered_at': source.get('modifiedTime', ''), 'registered_at': '',
            'processed_at': '', 'verified_at': '',
            'notes': 'Registered by the v1.5.1 source proposal; not yet imported to production.'})
    arguments.output.mkdir(parents=True, exist_ok=True)
    (arguments.output/'registry-proposal.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    (arguments.output/'registry-proposal.csv').write_text(stream.getvalue())
    print(json.dumps([{'source': row['source_title'], 'season': row['season'],
                       'status': row['extraction_status'],
                       'records': row['records_generated']} for row in rows],
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
