"""Reserve and freeze the v1.5.1 production release.

reserve: create the staging folder, reserve the new fact-product objects, pin
immutable input-dependency copies (so the live MASTER/registry may legitimately
change in this release), then write config/production.json.
freeze: build the merged products and freeze every artifact into entries.json.
Publication stays the CLI's job: plan -> publish --single-writer -> verify.
"""
import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from cba_kb.build_facts import build as build_products, collect
from cba_kb.common import atomic, digest, read, save
from cba_kb.drive import Drive
from cba_kb.master import inspect
from cba_kb.native import wrap
from cba_kb.release import fingerprint, snapshot

ROOT = '1bQybVHV_RRtZvvFuFhLpM-rXbVsNT2Dq'
AI = '1y-JlN327UARvZ4A8QN0EDDxg_0oggiUp'
SCRIPTS = '1oaJW9FDUjmIbFDafpt6aqtlvvzqQYhdC'
CONFIG = '1ccYmxy8NKt79pgyhDnyHUE14C6_wsn83'
ARCHIVE = '1GjDMkAYs7JpIrI9_kQduxlqygZmxeb9M'
DATA = '1nL4xomHNQInbskeYdqa45hHfleHhExW-'
DOC = 'application/vnd.google-apps.document'
FOLDER = 'application/vnd.google-apps.folder'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
DERIVED = (['INDEX.md'] + [f'CBA_注册_{y}-{y+1}.md' for y in range(2017, 2027)]
           + ['MASTER.csv', 'MASTER.jsonl', 'provenance.json', 'validation.json'])
PRODUCTS = ('CBA_外籍球员注册_SNAPSHOTS.xlsx', 'CBA_球员注册_EVENTS.xlsx')


def git(*command):
    return subprocess.check_output(['git', *command], text=True).strip()


def base_definitions(root, policy):
    allocation = read(root/'workspace/production/v1.5.0/allocation.json')
    by_id = {item['id']: item for item in allocation['definitions']}
    definitions = []
    for file_id, item in policy['targets'].items():
        source = by_id.get(file_id, {})
        definition = {'logical_key': source.get('logical_key', 'target/' + file_id),
                      'name': source.get('name', file_id), 'id': file_id, **item}
        for key in ('candidate_name', 'source'):
            if key in source:
                definition[key] = source[key]
        definitions.append(definition)
    return definitions


def reserve(root, release):
    drive = Drive(root)
    area = root/'workspace/production'/release
    if (area/'allocation.json').exists():
        raise RuntimeError('Allocation exists; inspect or resume instead of reserving again')
    area.mkdir(parents=True, exist_ok=True)
    policy = read(root/'config/production.json')
    definitions = base_definitions(root, policy)
    known = {item.get('source') for item in definitions}
    staging = drive.ensure(ARCHIVE, release + '-staging', release + '_staging', FOLDER)
    for relative in sorted(filter(None, git('ls-files', '-z').split('\0'))):
        if relative in known:
            continue
        file_id = drive.ensure(staging, 'code/' + relative, relative.replace('/', '__'), 'text/plain', b'')
        definitions.append({'logical_key': 'code/' + relative, 'name': relative.replace('/', '__'),
                            'id': file_id, 'mime': 'text/plain', 'mode': 'binary',
                            'allowed_parents': [SCRIPTS, staging], 'staging_parent': staging,
                            'publish_parent': SCRIPTS, 'source': relative})
    for name in PRODUCTS:
        file_id = drive.ensure(staging, 'facts/' + name, name, XLSX, b'')
        definitions.append({'logical_key': 'facts/' + name, 'name': name, 'id': file_id,
                            'mime': XLSX, 'mode': 'binary', 'allowed_parents': [DATA, staging],
                            'staging_parent': staging, 'publish_parent': DATA})
    expected, dependencies = {}, []
    for name, item in read(root/'config/runtime.json')['inputs'].items():
        if name not in ('MASTER.xlsx', 'source_registry.csv'):
            continue
        content, meta = snapshot(drive, item['id'])
        copy_id = drive.ensure(staging, 'input/' + name, 'frozen_' + name, item['mime'], content)
        expected[name] = digest(content)
        dependencies.append(copy_id)
    save(area/'allocation.json', {'release_id': release, 'status_id': policy['status_id'],
                                  'archive_id': ARCHIVE, 'staging_id': staging,
                                  'dependency_ids': dependencies, 'expected': expected,
                                  'definitions': definitions})
    save(root/'config/production.json', {'enabled': True, 'status_id': policy['status_id'],
         'archive_id': ARCHIVE, 'dependency_ids': dependencies,
         'targets': {item['id']: {k: v for k, v in item.items()
                                  if k in ('mime', 'mode', 'allowed_parents', 'staging_parent',
                                           'publish_parent')} for item in definitions}})
    print(f'Reserved {len(definitions)} targets ({len(PRODUCTS)} fact products, '
          f'{len(dependencies)} frozen input copies); commit policy and code before freeze')


def merged_registry(root, raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    columns = list(rows[0])
    known = {row['source_id'] for row in rows}
    proposal = json.loads((root/'workspace/staging/registry-proposal/registry-proposal.json').read_text())
    added = []
    for row in proposal:
        if row['source_id'] in known:
            continue
        rows.append({key: row.get(key, '') for key in columns})
        added.append(row['source_id'])
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig'), added


def freeze(root, release, staging_runs):
    if git('status', '--porcelain'):
        raise RuntimeError('Clean committed tree required')
    commit = git('rev-parse', 'HEAD')
    drive = Drive(root)
    area = root/'workspace/production'/release
    allocation = read(area/'allocation.json')
    definitions = allocation['definitions']
    inputs = area/'inputs'
    inputs.mkdir(exist_ok=True)
    raw, dependencies = {}, []
    for name, item in read(root/'config/runtime.json')['inputs'].items():
        content, meta = snapshot(drive, item['id'])
        atomic(inputs/name, content)
        raw[name] = content
        if name in allocation['expected']:
            if digest(content) != allocation['expected'][name]:
                raise RuntimeError(f'{name} changed since reserve; re-check the baseline')
            dependencies.append({'id': item['id'], 'sha256': digest(content), 'meta': fingerprint(meta)})
    registry, added = merged_registry(root, raw['source_registry.csv'])
    baseline_rows, baseline = inspect(inputs/'MASTER.xlsx')
    candidate = area/'candidate'
    result = build_products(collect([Path(root)/run for run in staging_runs]), baseline_rows, baseline,
                            candidate, release, commit, datetime.now(timezone.utc).isoformat())
    save(area/'dependencies.json', dependencies)
    urls = {item['logical_key']: drive.meta(item['id'])['webViewLink'] for item in definitions}
    status_url = drive.meta(allocation['status_id'])['webViewLink']
    nav = (f'当前发布：{release}。代码提交：{commit}。\n首先读取发布状态：{status_url}\n'
           '仅 COMPLETE 使用当前对象；PUBLISHING/FAILED/ROLLING_BACK 使用状态中的 previous_snapshot；'
           'ROLLED_BACK 使用已恢复上一发布。读取前后状态变化则重试。\n'
           '本地执行，GitHub 私有仓库 https://github.com/SkyCjq/cba-kb-engine 管理代码，Drive 为已发布事实入口。\n'
           'Gemini 请直接提供所需文件链接；入口递归读取与自动刷新不保证。'
           'auto_validated 不是人工核验，空值不是零，记录数不是独立球员数，快照数不是注册人数。\n')
    links = '\n'.join(f'- {key}: {value}' for key, value in urls.items()
                      if key.startswith(('derived/', 'input/', 'facts/')))
    index = (candidate/'INDEX.md').read_text() + '\n\n' + nav + '\n' + links + '\n代码镜像：' + urls['entry/code'] + '\n'
    code = ['# 当前代码镜像', f'GitHub: https://github.com/SkyCjq/cba-kb-engine/tree/{commit}',
            f'commit: {commit}',
            'legacy/ 是历史脚本，禁止用旧 uploader/wechat/sync 向生产写入。当前命令入口是 Makefile 和 src/cba_kb/。']
    for item in definitions:
        if item['logical_key'].startswith('code/'):
            data = (root/item['source']).read_bytes()
            code.append(f"- {item['source']} | SHA256 {digest(data)} | {urls[item['logical_key']]}")
    manifest = list(csv.DictReader(io.StringIO(raw['manifest.csv'].decode('utf-8-sig'))))
    columns = list(manifest[0])
    mapping = yaml.safe_load(drive.get('1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf'))
    mapping['v1_5_release'] = {'release_id': release, 'status_id': allocation['status_id'],
                               'code_commit': commit,
                               'targets': {item['logical_key']: item['id'] for item in definitions}}
    frozen = area/'frozen'
    frozen.mkdir(exist_ok=True)
    entries = []
    for index_number, item in enumerate(definitions):
        key = item['logical_key']
        if key == 'derived/INDEX.md':
            data = index.encode()
        elif key.startswith('derived/'):
            data = (candidate/item['candidate_name']).read_bytes()
        elif key == 'input/MASTER.xlsx':
            data = (candidate/'MASTER.xlsx').read_bytes()
        elif key == 'input/source_registry.csv':
            data = registry
        elif key == 'input/manifest.csv':
            data = raw['manifest.csv']
        elif key.startswith('facts/'):
            data = (candidate/item['name']).read_bytes()
        elif key == 'control/drive_map.yaml':
            data = yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False).encode()
        elif key == 'entry/code':
            data = ('\n'.join(code) + '\n').encode()
        elif key == 'entry/README':
            data = (nav + '\n' + links + '\n代码镜像：' + urls['entry/code'] + '\n'
                    f'v1.5.1 发布 {release}：国内注册关系 {result["products"]["MASTER.xlsx"]} 条，'
                    f'外籍快照 {result["products"]["CBA_外籍球员注册_SNAPSHOTS.xlsx"]} 条，'
                    f'注册事件 {result["products"]["CBA_球员注册_EVENTS.xlsx"]} 条。'
                    '2020-2021 与 2022-2023 外籍球员图片来源仍在 OCR 验收，未包含在本次事实产品内。'
                    '本发布不等于 v1.5.1 STABLE。').encode()
        elif key == 'entry/context':
            data = (nav + '\n' + links + '\n' + (root/'docs/OPERATIONS_V1_5.md').read_text() + '\n'
                    + (root/'docs/CBA-KB_v1.5.1.md').read_text()).encode()
        elif key == 'entry/crossseason':
            data = (nav + '\n以下历史跨赛季内容不作为当前事实。请改用当前 MASTER/赛季阅读版/事实产品：\n' + links).encode()
        else:
            data = (root/item['source']).read_bytes()
        if item['mode'] == 'managed_doc':
            data = wrap(data.decode()).encode()
        path = frozen/str(index_number)
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
        row.update(uid=entry['logical_key'], asset_type='release_artifact', source_type='generated',
                   local_path=entry['logical_key'], drive_file_id=entry['id'], status='published',
                   synced_at=datetime.now(timezone.utc).isoformat(),
                   content_hash='' if entry['logical_key'] == 'input/manifest.csv'
                   else digest(Path(entry['path']).read_bytes()))
        row['published_release'] = release
        row['hash_scope'] = 'managed_prefix' if entry['mode'] == 'managed_doc' else 'bytes'
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns + ['published_release', 'hash_scope'],
                            lineterminator='\n')
    writer.writeheader()
    writer.writerows(manifest)
    target = next(entry for entry in entries if entry['logical_key'] == 'input/manifest.csv')
    atomic(target['path'], stream.getvalue().encode('utf-8-sig'))
    save(area/'entries.json', entries)
    save(area/'release-links.json', {'release_id': release, 'commit': commit,
                                     'status': status_url, 'urls': urls})
    print(json.dumps({'release': release, 'commit': commit, 'targets': len(entries),
                      'registry_rows_added': added, 'products': result['products'],
                      'merge': result['merge']}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=['reserve', 'freeze'])
    parser.add_argument('--release', default='v1.5.1-1')
    parser.add_argument('--staging', action='append',
                        default=['workspace/staging/bayi-2020-2021',
                                 'workspace/staging/foreign-2024-2025'])
    arguments = parser.parse_args()
    root = Path.cwd()
    if arguments.step == 'reserve':
        reserve(root, arguments.release)
    else:
        freeze(root, arguments.release, arguments.staging)


if __name__ == '__main__':
    main()
