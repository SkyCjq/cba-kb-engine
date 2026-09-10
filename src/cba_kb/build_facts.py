"""Assemble the three grain-separated fact product candidates."""
import tempfile
from pathlib import Path
from . import master as master_module
from . import xlsx
from .common import digest, read, save
from .facts import (DOMESTIC, EVENT, SNAPSHOT, fingerprint, validate_domestic,
                    validate_events, validate_snapshots)
from .merge import merge_domestic

SNAPSHOTS_FILE = 'CBA_外籍球员注册_SNAPSHOTS.xlsx'
EVENTS_FILE = 'CBA_球员注册_EVENTS.xlsx'
MASTER_FILE = 'MASTER.xlsx'


def collect(staging_paths):
    """Merge several staged extraction runs into one record set."""
    merged = {'domestic': [], 'snapshots': [], 'events': [], 'runs': []}
    for path in staging_paths:
        path = Path(path)
        staged = read(path/'records.json' if path.is_dir() else path)
        payload = staged.get('records', staged)
        for name in ('domestic', 'snapshots', 'events'):
            merged[name].extend(payload.get(name) or [])
        merged['runs'].append({'path': str(path), 'report': payload.get('report'),
                               'summary': staged.get('summary'),
                               'season': staged.get('season') or (payload.get('report') or {}).get('season')})
    return merged


def build(staging, baseline_rows, baseline_summary, output, release_id, commit, generated_at):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Build directory must be new; preserve frozen candidates')
    rows, merge_report = merge_domestic(baseline_rows, staging.get('domestic') or [])
    domestic_summary = validate_domestic(rows)
    snapshots = staging.get('snapshots') or []
    snapshot_summary = validate_snapshots(snapshots)
    events = sorted(staging.get('events') or [],
                    key=lambda r: (r['season'], r['event_type'], r['club_id'],
                                   r['event_date'], r['sequence'], r['event_key']))
    event_summary = validate_events(events)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        staged_master = Path(scratch)/MASTER_FILE
        xlsx.write(staged_master, DOMESTIC, rows, 'MASTER')
        summary = master_module.build(staged_master, output, release_id, commit, generated_at)
    if summary['rows'] != domestic_summary['rows']:
        raise ValueError('Written MASTER row count changed during export')
    xlsx.write(output/MASTER_FILE, DOMESTIC, rows, 'MASTER')
    xlsx.write(output/SNAPSHOTS_FILE, SNAPSHOT, snapshots, 'SNAPSHOTS')
    xlsx.write(output/EVENTS_FILE, EVENT, events, 'EVENTS')
    provenance = read(output/'provenance.json')
    provenance.update({'products': [MASTER_FILE, SNAPSHOTS_FILE, EVENTS_FILE],
                       'baseline': {'rows': baseline_summary['rows'],
                                    'sha256': baseline_summary['sha256'],
                                    'semantic_sha256': baseline_summary['semantic_sha256']},
                       'merge': merge_report, 'fact_product_sha256':
                           {MASTER_FILE: digest((output/MASTER_FILE).read_bytes()),
                            SNAPSHOTS_FILE: digest((output/SNAPSHOTS_FILE).read_bytes()),
                            EVENTS_FILE: digest((output/EVENTS_FILE).read_bytes())}})
    save(output/'provenance.json', provenance)
    validation = read(output/'validation.json')
    validation['fact_products'] = {'domestic': domestic_summary, 'snapshots': snapshot_summary,
                                   'events': event_summary}
    validation['fingerprints'] = {'domestic': fingerprint(baseline_rows),
                                  'snapshots': fingerprint(snapshots), 'events': fingerprint(events)}
    save(output/'validation.json', validation)
    index = (output/'INDEX.md').read_text()
    index += ('\n## v1.5.1 事实产品\n'
              f'- {SNAPSHOTS_FILE}：{snapshot_summary["rows"]} 条外籍注册快照；grain 为赛季+快照时点+俱乐部+球员。\n'
              f'- {EVENTS_FILE}：{event_summary["rows"]} 条注册事件；grain 为单次注册/取消事件，不等于 roster 关系。\n'
              f'- {MASTER_FILE}：{domestic_summary["rows"]} 条国内注册关系（{merge_report["baseline_rows"]} 条基线 + '
              f'{len(merge_report["added"])} 条新增）。\n'
              '新增对象在 release_status 为 COMPLETE 前不作为当前事实；事件日期由赛季边界推导时 date_year_inferred=true。\n')
    (output/'INDEX.md').write_text(index)
    return {'release_id': release_id, 'rows': summary['rows'],
            'products': {MASTER_FILE: domestic_summary['rows'], SNAPSHOTS_FILE: snapshot_summary['rows'],
                         EVENTS_FILE: event_summary['rows']},
            'merge': {'baseline_rows': merge_report['baseline_rows'],
                      'added': len(merge_report['added']),
                      'collisions': len(merge_report['collisions'])},
            'provenance': str(output/'provenance.json'), 'validation': str(output/'validation.json')}
