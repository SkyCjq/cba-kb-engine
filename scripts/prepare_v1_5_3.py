"""Build a read-only v1.5.3 registration-event candidate.

The command composes frozen local copies of the current six-table product and
the merged domestic source.  It never calls Drive or writes production facts.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))

from cba_kb.aliases import Clubs
from cba_kb.domain_xlsx import write as write_domain
from cba_kb.event_closure import (
    compose_candidate_records,
    cross_table_qa,
    reconcile_legacy_events,
    semantic_fingerprint,
    validate_event_closure,
    write_reconciliation_reports,
)
from cba_kb.registration_domain import TABLES, parse_domestic_movement, validate_domain
from scripts.reconcile_v1_5_3 import (
    freeze_report,
    load_domain,
    sha256,
    sheet_rows,
    write_json,
)


DOMESTIC_SOURCE_ID = '1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR'
DOMESTIC_SOURCE_URL = f'https://drive.google.com/file/d/{DOMESTIC_SOURCE_ID}/view'
FOCUS_WINDOWS = {
    '2024-2025': 3,
    '2025-2026': 3,
}


def _workbook_value(value):
    if value == '':
        return None
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def validate_candidate_controls(live, candidate, legacy_summary, source_windows):
    """Fail closed when a v1.5.3 candidate changes a protected control."""
    live_counts = {name: len(live.get(name) or []) for name in TABLES}
    candidate_counts = {name: len(candidate.get(name) or []) for name in TABLES}
    protected = (
        'domestic_registrations',
        'foreign_registration_snapshots',
        'foreign_priority_right_snapshots',
        'foreign_priority_right_transactions',
    )
    for table in protected:
        if candidate_counts[table] != live_counts[table]:
            raise ValueError(f'Protected table count changed: {table}')

    expected_window_keys = {
        row.get('window_key') for row in (live.get('domestic_transaction_windows') or [])
    } | {row.get('window_key') for row in source_windows}
    candidate_window_keys = {
        row.get('window_key') for row in candidate['domestic_transaction_windows']
    }
    if candidate_window_keys != expected_window_keys:
        raise ValueError('Domestic transaction-window control changed')
    if candidate_counts['registration_status_events'] < live_counts['registration_status_events']:
        raise ValueError('Canonical event count regressed')

    windows = Counter(
        row['season'] for row in candidate['domestic_transaction_windows']
    )
    for season, expected in FOCUS_WINDOWS.items():
        if windows[season] != expected:
            raise ValueError(f'Domestic window closure failed for {season}')

    domains = Counter(
        row.get('event_domain') for row in candidate['registration_status_events']
    )
    for domain in ('domestic_movement', 'foreign_registration', 'foreign_usage'):
        if not domains[domain]:
            raise ValueError(f'Missing required event domain: {domain}')

    if legacy_summary['legacy_row_count'] != legacy_summary['mapped_legacy_count']:
        raise ValueError('Legacy reconciliation has unmapped rows')
    if legacy_summary['unexplained_conflict_count']:
        raise ValueError('Legacy reconciliation has unexplained conflicts')
    return {
        'live_counts': live_counts,
        'candidate_counts': candidate_counts,
        'event_domain_counts': dict(sorted(domains.items())),
        'protected_tables_unchanged': list(protected),
    }


def git_state():
    commit = subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
    ).strip()
    dirty = bool(subprocess.check_output(
        ['git', 'status', '--porcelain'], cwd=ROOT, text=True,
    ).strip())
    return commit, dirty


def index_text(validation, provenance):
    return (
        '# CBA-KB v1.5.3 candidate INDEX\n\n'
        'GENERATED / READ-ONLY CANDIDATE\n\n'
        '## AI consumption rules\n\n'
        '- 某赛季登记状态：读取 MASTER / Snapshot。\n'
        '- 什么时候发生什么：读取 canonical registration_status_events。\n'
        '- 为什么这么判断：读取 10_sources_原始证据。\n'
        '- 人工研究判断：读取 30_notes_人工知识。\n'
        '- 快速定位与语义阅读：读取 40_ai_投喂与索引，不把其正文当精确计数来源。\n'
        '- 旧 CBA_球员注册_EVENTS.xlsx 只用于兼容与历史审计，不是 current truth。\n'
        '- 不同 event_domain 不得直接相加；只有明确询问全部事件总数时才汇总，并同时给分域计数。\n'
        '- registration_method_official 与 research_movement_label 不得互换。\n\n'
        '## Candidate controls\n\n'
        f'- table_counts: {json.dumps(validation["table_counts"], ensure_ascii=False, sort_keys=True)}\n'
        f'- event_domain_counts: {json.dumps(validation["event_domain_counts"], ensure_ascii=False, sort_keys=True)}\n'
        f'- semantic_sha256: {validation["semantic_sha256"]}\n'
        f'- code_commit: {provenance["code_commit"]}\n'
        f'- code_dirty: {str(provenance["code_dirty"]).lower()}\n'
        '- production_eligible: false until acceptance, merge, CI, readback, and GO-NO-GO complete.\n'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', type=Path, required=True)
    parser.add_argument('--legacy', type=Path, required=True)
    parser.add_argument('--master', type=Path)
    parser.add_argument('--domestic-source', type=Path, required=True)
    parser.add_argument('--release-status', type=Path)
    parser.add_argument('--source-registry', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Choose a new candidate output directory')

    live_records, _ = load_domain(args.domain)
    domestic = parse_domestic_movement(
        args.domestic_source.read_text(encoding='utf-8'),
        {
            'id': DOMESTIC_SOURCE_ID,
            'url': DOMESTIC_SOURCE_URL,
            'type': 'gdrive',
            'method': 'merged markdown table parser',
        },
        Clubs(ROOT),
    )
    records = compose_candidate_records(live_records, domestic)
    validate_domain(records)
    validate_event_closure(records)

    _, legacy_rows = sheet_rows(args.legacy, 'EVENTS')
    master_rows = sheet_rows(args.master, 'MASTER')[1] if args.master else []
    edges, reconciliation = reconcile_legacy_events(
        legacy_rows, records,
        legacy_source_id=sha256(args.legacy)[:16],
    )
    controls = validate_candidate_controls(
        live_records, records, reconciliation,
        domestic['domestic_transaction_windows'],
    )
    qa = cross_table_qa(records, master_rows, Clubs(ROOT))

    args.output.mkdir(parents=True)
    workbook = args.output / 'domain.xlsx'
    write_domain(workbook, records)
    readback, _ = load_domain(workbook)
    expected = {
        table: [{header: _workbook_value(row.get(header)) for header in TABLES[table]}
                for row in records.get(table) or []]
        for table in TABLES
    }
    actual = {
        table: [{header: row.get(header) for header in TABLES[table]}
                for row in readback.get(table) or []]
        for table in TABLES
    }
    if actual != expected:
        raise ValueError('Candidate workbook round-trip mismatch')

    csv_path, summary_path = write_reconciliation_reports(
        edges, reconciliation, args.output,
    )
    freeze = freeze_report(
        workbook, args.legacy, args.master, readback, legacy_rows, master_rows,
        [
            {
                'window_key': row['window_key'],
                'season': row['season'],
                'window_no': row['window_no'],
                'window_start': row['window_start'],
                'window_end': row['window_end'],
                'completeness_status': row['completeness_status'],
                'source_page_or_row': row['source_page_or_row'],
            }
            for row in records['domestic_transaction_windows']
        ],
    )
    commit, dirty = git_state()
    provenance = {
        'version': '1.5.3.dev0',
        'code_commit': commit,
        'code_dirty': dirty,
        'domain_input': str(args.domain),
        'domain_input_sha256': sha256(args.domain),
        'legacy_input': str(args.legacy),
        'legacy_input_sha256': sha256(args.legacy),
        'master_input': str(args.master) if args.master else None,
        'master_input_sha256': sha256(args.master) if args.master else None,
        'domestic_source': str(args.domestic_source),
        'domestic_source_sha256': sha256(args.domestic_source),
        'release_status_input': str(args.release_status) if args.release_status else None,
        'release_status_sha256': sha256(args.release_status) if args.release_status else None,
        'source_registry_input': str(args.source_registry) if args.source_registry else None,
        'source_registry_sha256': sha256(args.source_registry) if args.source_registry else None,
        'candidate_workbook': workbook.name,
        'candidate_workbook_sha256': sha256(workbook),
        'semantic_sha256': semantic_fingerprint(records),
    }
    validation = {
        'status': 'PASS_V1_5_3_CANDIDATE',
        'production_eligible': False,
        'table_counts': {name: len(records.get(name) or []) for name in TABLES},
        'event_domain_counts': freeze['event_domain_counts'],
        'event_status_counts': freeze['event_status_counts'],
        'windows_by_season': freeze['windows_by_season'],
        'controls': controls,
        'event_closure': validate_event_closure(records),
        'reconciliation': reconciliation,
        'qa': qa,
        'semantic_sha256': provenance['semantic_sha256'],
    }
    write_json(args.output / 'v1.5.3-phase0-freeze.json', freeze)
    write_json(args.output / 'v1.5.3-cross-table-qa.json', qa)
    write_json(args.output / 'v1.5.3-event-schema.json', {
        'headers': {name: list(headers) for name, headers in TABLES.items()},
        'upgraded_event_headers': list(TABLES['registration_status_events']),
    })
    write_json(args.output / 'v1.5.3-candidate-validation.json', validation)
    write_json(args.output / 'provenance.json', provenance)
    (args.output / 'INDEX.md').write_text(
        index_text(validation, provenance), encoding='utf-8',
    )
    print(json.dumps({
        'status': validation['status'],
        'production_eligible': False,
        'workbook': str(workbook),
        'workbook_sha256': provenance['candidate_workbook_sha256'],
        'table_counts': validation['table_counts'],
        'event_domain_counts': validation['event_domain_counts'],
        'reconciliation': reconciliation,
        'qa_signal_count': qa['signal_count'],
        'qa_warning_count': qa['warning_count'],
        'outputs': [
            str(workbook), str(csv_path), str(summary_path),
            str(args.output / 'v1.5.3-phase0-freeze.json'),
            str(args.output / 'v1.5.3-cross-table-qa.json'),
            str(args.output / 'v1.5.3-event-schema.json'),
            str(args.output / 'v1.5.3-candidate-validation.json'),
            str(args.output / 'provenance.json'),
            str(args.output / 'INDEX.md'),
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
