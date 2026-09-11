"""Reserve and freeze the v1.5.2 production release.

This release publishes the implemented six-table registration-domain scope as
a binary XLSX product while preserving the existing MASTER, SNAPSHOTS, and
EVENTS products. The acceptance report remains explicit that the full DRAFT-2
scope is not complete; PNG OCR and the other documented boundaries are not
silently promoted to production facts.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))

from cba_kb.common import atomic, digest, read, save
from cba_kb.drive import Drive
from cba_kb.master import inspect
from cba_kb.release import fingerprint, snapshot
from scripts import prepare_v1_5_1 as base


RELEASE = 'v1.5.2-1'
DATA = base.DATA
ARCHIVE = base.ARCHIVE
XLSX = base.XLSX
DOMAIN_PRODUCT = 'CBA_注册领域_六表.xlsx'
DOMAIN_ACCEPTANCE = ROOT / 'docs/validation/v1.5.2-source-acceptance-20260911.json'
DOMAIN_WORKBOOK = ROOT / 'workspace/candidates/v1.5.2-verified-20260911/domain.xlsx'
DOMAIN_WORKBOOK_SHA256 = '4124c4e6f8e3f62d7353180a5bbd4baca51e3196e2b7fac6b06148042fce2849'
DOMAIN_COUNTS = {
    'domestic_registrations': 46,
    'domestic_transaction_windows': 4,
    'registration_status_events': 72,
    'foreign_registration_snapshots': 244,
    'foreign_priority_right_snapshots': 227,
    'foreign_priority_right_transactions': 7,
}
DOMAIN_ROW_TOTAL = sum(DOMAIN_COUNTS.values())
REPORT = 'v1.5.2-release-report.md'
ACCEPTANCE_REPORT = 'v1.5.2-source-acceptance.json'

base.PRODUCTS = base.PRODUCTS + (DOMAIN_PRODUCT,)
base.EXTRAS = {
    RELEASE: [
        {
            'logical_key': 'report/' + REPORT,
            'name': REPORT,
            'parent': ARCHIVE,
            'mime': 'text/markdown',
        },
        {
            'logical_key': 'report/' + ACCEPTANCE_REPORT,
            'name': ACCEPTANCE_REPORT,
            'parent': ARCHIVE,
            'mime': 'application/json',
        },
    ]
}


def validate_domain_candidate():
    """Fail closed on every input control for the six-table product."""
    if not DOMAIN_WORKBOOK.is_file():
        raise RuntimeError(f'Missing six-table candidate: {DOMAIN_WORKBOOK}')
    if not DOMAIN_ACCEPTANCE.is_file():
        raise RuntimeError(f'Missing six-table acceptance report: {DOMAIN_ACCEPTANCE}')
    workbook = DOMAIN_WORKBOOK.read_bytes()
    if digest(workbook) != DOMAIN_WORKBOOK_SHA256:
        raise RuntimeError('Six-table candidate SHA-256 changed')
    acceptance = read(DOMAIN_ACCEPTANCE)
    if acceptance.get('workbook_sha256') != DOMAIN_WORKBOOK_SHA256:
        raise RuntimeError('Acceptance report does not pin the six-table candidate')
    if acceptance.get('counts') != DOMAIN_COUNTS:
        raise RuntimeError('Acceptance table counts changed')
    if acceptance.get('status') != 'PASS_IMPLEMENTED_TABLE_SCOPE':
        raise RuntimeError('Acceptance status changed')
    if acceptance.get('production_eligible') is not False:
        raise RuntimeError('Partial-scope acceptance must remain production_eligible=false')
    if acceptance.get('source_hashes') != {
            'domestic': '11fda375f3e41c29a02918d924bd026e78c62b44635a6b8b37209f7da3846476',
            'foreign': 'fe2d47a0cdcb16777eabe5838ad3e8b19eb0657224434b941bb003af00f889ae'}:
        raise RuntimeError('Merged-source hashes changed')
    return workbook, acceptance


def merged_registry(root, raw):
    """Merge every committed registry proposal without duplicating source IDs."""
    rows = list(base.csv.DictReader(base.io.StringIO(raw.decode('utf-8-sig'))))
    added = []
    proposals = [
        root / 'config/v1.5.2_registry_proposal.json',
    ]
    proposal_rows = [row for path in proposals for row in read(path)]
    columns = list(rows[0]) if rows else list(proposal_rows[0])
    known = {row['source_id'] for row in rows}
    for row in proposal_rows:
        if row['source_id'] in known:
            continue
        rows.append({key: row.get(key, '') for key in columns})
        known.add(row['source_id'])
        added.append(row['source_id'])
    stream = base.io.StringIO(newline='')
    writer = base.csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig'), added


def release_report(release, commit, result, acceptance, added, applied):
    products = result['products']
    return f"""# CBA-KB {release} production release

- Release ID: `{release}`
- Code commit: `{commit}`
- Published at: {acceptance.get('verified_at', 'deterministic freeze from the accepted candidate')}
- Scope: implemented six-table registration-domain release; not full DRAFT-2 and not STABLE.

## Fact products

- `CBA_注册领域_六表.xlsx`: {DOMAIN_ROW_TOTAL} rows across six tables. Workbook SHA-256
  `{DOMAIN_WORKBOOK_SHA256}`.
- `MASTER.xlsx`: {products['MASTER.xlsx']} domestic registration rows.
- `CBA_外籍球员注册_SNAPSHOTS.xlsx`: {products['CBA_外籍球员注册_SNAPSHOTS.xlsx']} rows.
- `CBA_球员注册_EVENTS.xlsx`: {products['CBA_球员注册_EVENTS.xlsx']} rows.

The six table counts are 46 / 4 / 72 / 244 / 227 / 7. These are differently
shaped records and must not be added together as a count of people.

## Source and registry records

- Domestic merged source: `1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR`.
- Foreign merged source: `1eGrMDep9YMfPNy66M6-6Jwm9iYaF_gQj`.
- Registry rows added: {json.dumps(added, ensure_ascii=False)}.
- Registry statuses applied: {json.dumps(applied, ensure_ascii=False)}.

## Release controls

- Strict six-table acceptance passed with 600/600 rows, source locators,
  literal evidence, deterministic workbook generation, and complete cell
  round-trip checks.
- The two PNG sources remain `DISCOVERED / ocr_deferred` and are excluded from
  this release by explicit user decision.
- The acceptance object is intentionally
  `production_eligible=false`: it verifies the implemented table scope only.

## Known boundaries

- Domestic transaction-window coverage remains incomplete, especially
  2024-2025 and 2025-2026.
- Correction, withdrawal, system-error, and incomplete-event lifecycles remain
  outside the implemented table scope.
- Bayi evidence enrichment and the former domestic MASTER merge remain open.
- The 2023-2024 media snapshot and two foreign activation events remain open;
  the legacy 59 cancellation records are not merged into this six-table file.
- Native Google Sheet authoritative-source integration remains open.

This release preserves the existing MASTER, SNAPSHOTS, and EVENTS objects and
adds the accepted six-table workbook without representing the unfinished
DRAFT-2 scope as complete.
"""


def freeze(root, release, staging_runs):
    if release != RELEASE:
        raise RuntimeError(f'This preparer is pinned to {RELEASE}')
    if base.git('status', '--porcelain'):
        raise RuntimeError('Clean committed tree required')
    commit = base.git('rev-parse', 'HEAD')
    domain_workbook, acceptance = validate_domain_candidate()
    drive = Drive(root)
    area = root / 'workspace/production' / release
    allocation = read(area / 'allocation.json')
    definitions = allocation['definitions']
    inputs = area / 'inputs'
    inputs.mkdir(exist_ok=True)
    raw, dependencies = {}, []
    copies = {
        item.get('appProperties', {}).get('cba_key'): item['id']
        for item in drive.list(allocation['staging_id'])
    }
    for name, item in read(root / 'config/runtime.json')['inputs'].items():
        content, _ = snapshot(drive, item['id'])
        atomic(inputs / name, content)
        raw[name] = content
        if name in allocation['expected']:
            if digest(content) != allocation['expected'][name]:
                raise RuntimeError(f'{name} changed since reserve; re-check the baseline')
            copy_id = copies.get('input/' + name)
            if not copy_id or copy_id not in allocation['dependency_ids']:
                raise RuntimeError(f'Frozen input copy missing for {name}')
            frozen_content, frozen_meta = snapshot(drive, copy_id)
            if digest(frozen_content) != allocation['expected'][name]:
                raise RuntimeError(f'Frozen input copy changed for {name}')
            dependencies.append({
                'id': copy_id,
                'sha256': digest(frozen_content),
                'meta': fingerprint(frozen_meta),
            })
    registry, added = merged_registry(root, raw['source_registry.csv'])
    registry, applied = base.apply_status(
        registry, root / 'config/v1.5.2_registry_status.json'
    )
    baseline_rows, baseline = inspect(inputs / 'MASTER.xlsx')
    candidate = area / 'candidate'
    result = base.build_products(
        base.collect([Path(root) / run for run in staging_runs]),
        baseline_rows,
        baseline,
        candidate,
        release,
        commit,
        base.datetime.now(base.timezone.utc).isoformat(),
    )
    domain_copy = candidate / DOMAIN_PRODUCT
    atomic(domain_copy, domain_workbook)
    save(area / 'dependencies.json', dependencies)
    urls = {item['logical_key']: drive.meta(item['id'])['webViewLink'] for item in definitions}
    status_url = drive.meta(allocation['status_id'])['webViewLink']
    scope = (
        '本发布是已实现的六表注册域范围，不是完整 DRAFT-2，也不等同于 STABLE。'
        '两张 PNG 来源按用户决定不纳入本轮，保持 DISCOVERED/ocr_deferred。'
        '国内窗口覆盖、纠错生命周期、八一/MASTER 合并、2023-2024 媒体快照、'
        '旧取消记录整合和原生 Sheet 权威源集成仍未完成。'
    )
    nav = (
        f'当前发布：{release}。代码提交：{commit}。\n'
        f'首先读取发布状态：{status_url}\n'
        '仅 COMPLETE 使用当前对象；PUBLISHING/FAILED/ROLLING_BACK 使用状态中的 '
        'previous_snapshot；ROLLED_BACK 使用已恢复上一发布。读取前后状态变化则重试。\n'
        '本地执行，GitHub 私有仓库 https://github.com/SkyCjq/cba-kb-engine 管理代码，'
        'Drive 为已发布事实入口。\n'
        'Gemini 请直接提供所需文件链接；入口递归读取与自动刷新不保证。'
        'auto_validated 不是人工核验，空值不是零，记录数不是独立球员数，'
        '快照数不是注册人数。\n'
        + scope + '\n'
    )
    links = '\n'.join(
        f'- {key}: {value}'
        for key, value in urls.items()
        if key.startswith(('derived/', 'input/', 'facts/', 'report/', 'ai/'))
    )
    index = (
        (candidate / 'INDEX.md').read_text()
        + '\n\n'
        + nav
        + '\n'
        + links
        + '\n代码镜像：'
        + urls['entry/code']
        + '\n'
    )
    code = [
        '# 当前代码镜像',
        f'GitHub: https://github.com/SkyCjq/cba-kb-engine/tree/{commit}',
        f'commit: {commit}',
        'legacy/ 是历史脚本，禁止用旧 uploader/wechat/sync 向生产写入。'
        '当前命令入口是 Makefile 和 src/cba_kb/。',
    ]
    for item in definitions:
        if item['logical_key'].startswith('code/'):
            data = (root / item['source']).read_bytes()
            code.append(f"- {item['source']} | SHA256 {digest(data)} | {urls[item['logical_key']]}")
    manifest = list(base.csv.DictReader(base.io.StringIO(raw['manifest.csv'].decode('utf-8-sig'))))
    columns = list(manifest[0])
    mapping = base.yaml.safe_load(drive.get('1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf'))
    mapping['v1_5_release'] = {
        'release_id': release,
        'status_id': allocation['status_id'],
        'code_commit': commit,
        'targets': {item['logical_key']: item['id'] for item in definitions},
    }
    frozen = area / 'frozen'
    frozen.mkdir(exist_ok=True)
    generated_report = release_report(
        release, commit, result, acceptance, added, applied
    ).encode()
    entries = []
    for index_number, item in enumerate(definitions):
        key = item['logical_key']
        if key == 'derived/INDEX.md':
            data = index.encode()
        elif key.startswith('derived/'):
            data = (candidate / item['candidate_name']).read_bytes()
        elif key == 'input/MASTER.xlsx':
            data = (candidate / 'MASTER.xlsx').read_bytes()
        elif key == 'input/source_registry.csv':
            data = registry
        elif key == 'input/manifest.csv':
            data = raw['manifest.csv']
        elif key == 'facts/' + DOMAIN_PRODUCT:
            data = domain_workbook
        elif key.startswith('facts/'):
            data = (candidate / item['name']).read_bytes()
        elif key == 'report/' + REPORT:
            data = generated_report
        elif key == 'report/' + ACCEPTANCE_REPORT:
            data = DOMAIN_ACCEPTANCE.read_bytes()
        elif key.startswith('report/'):
            data = (root / base.REPORT_DIR / item['name']).read_bytes()
        elif key == 'control/drive_map.yaml':
            data = base.yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False).encode()
        elif key == 'entry/code':
            data = ('\n'.join(code) + '\n').encode()
        elif key == 'entry/README':
            data = (
                nav
                + '\n'
                + links
                + '\n代码镜像：'
                + urls['entry/code']
                + '\n'
                + f'v1.5.2 发布 {release}：六表工作簿 {DOMAIN_ROW_TOTAL} 条，'
                + f'SHA-256 {DOMAIN_WORKBOOK_SHA256}；MASTER '
                + f'{result["products"]["MASTER.xlsx"]} 条，外籍快照 '
                + f'{result["products"]["CBA_外籍球员注册_SNAPSHOTS.xlsx"]} 条，'
                + f'注册事件 {result["products"]["CBA_球员注册_EVENTS.xlsx"]} 条。'
                + '本轮明确忽略两张外籍 PNG，其他未完成边界见发布报告。'
            ).encode()
        elif key == 'entry/context':
            data = (
                nav
                + '\n'
                + links
                + '\n'
                + (root / 'docs/OPERATIONS_V1_5.md').read_text()
                + '\n'
                + (root / 'docs/CBA-KB_v1.5.2.md').read_text()
            ).encode()
        elif key == 'entry/crossseason':
            data = (
                nav
                + '\n以下历史跨赛季内容不作为当前事实。请改用当前 MASTER/赛季阅读版/事实产品：\n'
                + links
            ).encode()
        else:
            data = (root / item['source']).read_bytes()
        if item['mode'] == 'managed_doc':
            data = base.wrap(data.decode()).encode()
        path = frozen / str(index_number)
        atomic(path, data)
        entry = {k: v for k, v in item.items() if k not in ('source', 'candidate_name', 'allowed_parents')}
        entry['path'] = str(path)
        entries.append(entry)
    by_id = {row.get('drive_file_id'): row for row in manifest}
    for entry in entries:
        row = by_id.get(entry['id'])
        if row is None:
            row = {column: '' for column in columns}
            manifest.append(row)
        row.update(
            uid=entry['logical_key'],
            asset_type='release_artifact',
            source_type='generated',
            local_path=entry['logical_key'],
            drive_file_id=entry['id'],
            status='published',
            synced_at=base.datetime.now(base.timezone.utc).isoformat(),
            content_hash='' if entry['logical_key'] == 'input/manifest.csv' else digest(Path(entry['path']).read_bytes()),
        )
        row['published_release'] = release
        row['hash_scope'] = 'managed_prefix' if entry['mode'] == 'managed_doc' else 'bytes'
    stream = base.io.StringIO(newline='')
    writer = base.csv.DictWriter(
        stream, fieldnames=columns + ['published_release', 'hash_scope'], lineterminator='\n'
    )
    writer.writeheader()
    writer.writerows(manifest)
    target = next(entry for entry in entries if entry['logical_key'] == 'input/manifest.csv')
    atomic(target['path'], stream.getvalue().encode('utf-8-sig'))
    save(area / 'entries.json', entries)
    save(area / 'release-links.json', {
        'release_id': release,
        'commit': commit,
        'status': status_url,
        'urls': urls,
    })
    print(json.dumps({
        'release': release,
        'commit': commit,
        'targets': len(entries),
        'fact_products': list(base.PRODUCTS),
        'registry_rows_added': added,
        'registry_status_applied': applied,
        'domain_product': {
            'name': DOMAIN_PRODUCT,
            'sha256': DOMAIN_WORKBOOK_SHA256,
            'counts': DOMAIN_COUNTS,
            'rows_total': DOMAIN_ROW_TOTAL,
        },
        'products': result['products'],
        'merge': result['merge'],
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=['reserve', 'freeze', 'refresh-dependencies'])
    parser.add_argument('--release', default=RELEASE)
    parser.add_argument(
        '--staging',
        action='append',
        default=[
            'workspace/staging/bayi-2020-2021',
            'workspace/staging/foreign-2024-2025',
        ],
    )
    arguments = parser.parse_args()
    root = Path.cwd()
    if arguments.step == 'reserve':
        base.reserve(root, arguments.release)
    elif arguments.step == 'refresh-dependencies':
        base.refresh_dependencies(root, arguments.release)
    else:
        freeze(root, arguments.release, arguments.staging)


if __name__ == '__main__':
    main()
