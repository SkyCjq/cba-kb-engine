"""Reserve and freeze the v1.5.3 production release.

The release publishes the accepted registration-event closure candidate while
preserving the existing MASTER, SNAPSHOTS, EVENTS and six-table object IDs.
Publication remains the release CLI's job: plan, publish --single-writer, verify.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))

from cba_kb.common import atomic, digest, read, save
from cba_kb.drive import Drive
from cba_kb.release import fingerprint, snapshot
from scripts import prepare_v1_5_1 as base
from scripts import prepare_v1_5_2 as prior


RELEASE = 'v1.5.3-1'
DOMAIN_PRODUCT = 'CBA_注册领域_六表.xlsx'
DOMAIN_WORKBOOK = (
    ROOT / 'workspace/candidates/v1.5.3-verified-20260911/domain.xlsx'
)
CANDIDATE_DIR = DOMAIN_WORKBOOK.parent
DOMAIN_WORKBOOK_SHA256 = (
    '0c9812e5e49f0824b74966f3f3941d8eee3711fdafc19d88774084a83742e66c'
)
DOMAIN_SEMANTIC_SHA256 = (
    '072f671553f86415f5462a74b0a83f5395b4a47ff2e1e124b33fd1bd944dacad'
)
CANDIDATE_CODE_COMMIT = '9dd3d3278566005feb7026b98a067149ae6e7489'
DOMAIN_COUNTS = {
    'domestic_registrations': 46,
    'domestic_transaction_windows': 10,
    'registration_status_events': 131,
    'foreign_registration_snapshots': 244,
    'foreign_priority_right_snapshots': 227,
    'foreign_priority_right_transactions': 7,
}
EVENT_DOMAIN_COUNTS = {
    'domestic_movement': 53,
    'foreign_registration': 59,
    'foreign_usage': 19,
}
RECONCILIATION_CONTROLS = {
    'legacy_row_count': 73,
    'mapping_edge_count': 87,
    'mapped_legacy_count': 73,
    'unmapped_legacy_count': 0,
    'exact_match_count': 59,
    'canonical_split_count': 14,
    'unexplained_conflict_count': 0,
}
QA_SIGNAL_COUNT = 6
QA_WARNING_COUNT = 329
VERSION_DOC_ID = '1ZebJR9YPKX37cMDdz0xznHDa45at_q65'
VERSION_DOC_PARENT = '1emuFQE0-sKDreyJ9tARuGeeFME-kzGyy'
VERSION_DOC_LOGICAL_KEY = 'ai/CBA-KB_v1.5.3.md'
DRIVE_MAP_ID = '1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf'
VERSION_DOC_BASE_SHA256 = (
    'dde6aaa043ecf07e8321103622303d92e248a820f3d479f0f8c77de2ad70db81'
)
REPORT = 'v1.5.3-release-report.md'
ACCEPTANCE_REPORT = 'v1.5.3-source-acceptance.json'
AI_ARTIFACTS = {
    'v1.5.3-event-reconciliation-summary.json',
    'v1.5.3-cross-table-qa.json',
    'v1.5.3-event-schema.json',
    'v1.5.3-phase0-freeze.json',
}
MIME_OVERRIDES = {
    '1OwjOa26nrMqmyzA5ZUoVURN2buBhdIOE': 'text/x-python-script',
    '1Qhga1BIcF_H1Ra4HCx0OX87GT7Z7qPwX': 'application/json',
    '1aefDZ5WNQiLoGR1p-bO-sJR9Y5Iuous9': 'text/x-python-script',
    '1xIcqerya95H5KzXxbLWYYkWMYf3CyrY-': 'text/x-python-script',
}

base.PRODUCTS = base.PRODUCTS + (DOMAIN_PRODUCT,)
base.EXTRAS = {
    RELEASE: [
        {
            'logical_key': 'report/' + REPORT,
            'name': REPORT,
            'parent': base.ARCHIVE,
            'mime': 'text/markdown',
        },
        {
            'logical_key': 'report/' + ACCEPTANCE_REPORT,
            'name': ACCEPTANCE_REPORT,
            'parent': base.ARCHIVE,
            'mime': 'application/json',
        },
        *[
            {
                'logical_key': 'ai/' + name,
                'name': name,
                'parent': base.AI,
                'mime': 'application/json',
            }
            for name in sorted(AI_ARTIFACTS)
        ],
    ]
}


def git(*command):
    return subprocess.check_output(
        ['git', *command], cwd=ROOT, text=True,
    ).strip()


def _read_candidate_json(name):
    path = CANDIDATE_DIR / name
    if not path.is_file():
        raise RuntimeError(f'Missing candidate control: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def validate_candidate():
    """Fail closed on the frozen workbook and every event-closure control."""
    if not DOMAIN_WORKBOOK.is_file():
        raise RuntimeError(f'Missing v1.5.3 candidate: {DOMAIN_WORKBOOK}')
    if digest(DOMAIN_WORKBOOK.read_bytes()) != DOMAIN_WORKBOOK_SHA256:
        raise RuntimeError('v1.5.3 candidate workbook SHA-256 changed')

    validation = _read_candidate_json('v1.5.3-candidate-validation.json')
    provenance = _read_candidate_json('provenance.json')
    reconciliation = _read_candidate_json(
        'v1.5.3-event-reconciliation-summary.json'
    )
    qa = _read_candidate_json('v1.5.3-cross-table-qa.json')
    if validation.get('status') != 'PASS_V1_5_3_CANDIDATE':
        raise RuntimeError('v1.5.3 candidate validation status changed')
    if validation.get('table_counts') != DOMAIN_COUNTS:
        raise RuntimeError('v1.5.3 candidate table counts changed')
    if validation.get('event_domain_counts') != EVENT_DOMAIN_COUNTS:
        raise RuntimeError('v1.5.3 event-domain counts changed')
    if validation.get('semantic_sha256') != DOMAIN_SEMANTIC_SHA256:
        raise RuntimeError('v1.5.3 semantic fingerprint changed')
    if provenance.get('code_commit') != CANDIDATE_CODE_COMMIT:
        raise RuntimeError('v1.5.3 candidate code commit changed')
    if provenance.get('code_dirty') is not False:
        raise RuntimeError('v1.5.3 candidate was not built from a clean tree')
    if provenance.get('candidate_workbook_sha256') != DOMAIN_WORKBOOK_SHA256:
        raise RuntimeError('Candidate provenance does not pin the workbook')
    for key, expected in RECONCILIATION_CONTROLS.items():
        if reconciliation.get(key) != expected:
            raise RuntimeError(f'v1.5.3 reconciliation control changed: {key}')
    if qa.get('signal_count') != QA_SIGNAL_COUNT:
        raise RuntimeError('v1.5.3 QA signal count changed')
    if qa.get('warning_count') != QA_WARNING_COUNT:
        raise RuntimeError('v1.5.3 QA warning count changed')
    if subprocess.run(
        ['git', 'merge-base', '--is-ancestor', CANDIDATE_CODE_COMMIT, 'HEAD'],
        cwd=ROOT,
    ).returncode:
        raise RuntimeError('Candidate code commit is not an ancestor of HEAD')
    return validation, provenance, reconciliation, qa


def reserve(root):
    """Reserve the complete v1.5.3 target set, including new code mirrors."""
    area = root / 'workspace/production' / RELEASE
    if (area / 'allocation.json').exists():
        raise RuntimeError('Allocation exists; inspect or resume instead of reserving')
    validate_candidate()
    base.reserve(root, RELEASE)

    allocation = read(area / 'allocation.json')
    if any(item['id'] == VERSION_DOC_ID for item in allocation['definitions']):
        raise RuntimeError('Version document target unexpectedly duplicated')
    definition = {
        'logical_key': VERSION_DOC_LOGICAL_KEY,
        'name': 'CBA-KB_v1.5.3.md',
        'id': VERSION_DOC_ID,
        'mime': 'text/markdown',
        'mode': 'binary',
        'allowed_parents': [VERSION_DOC_PARENT, allocation['staging_id']],
        'staging_parent': allocation['staging_id'],
        'publish_parent': VERSION_DOC_PARENT,
    }
    allocation['definitions'].append(definition)
    save(area / 'allocation.json', allocation)

    policy = read(root / 'config/production.json')
    policy['targets'][VERSION_DOC_ID] = {
        key: definition[key]
        for key in (
            'mime',
            'mode',
            'allowed_parents',
            'staging_parent',
            'publish_parent',
        )
    }
    save(root / 'config/production.json', policy)
    print(json.dumps({
        'status': 'RESERVED',
        'release': RELEASE,
        'targets': len(allocation['definitions']),
        'new_tracked_code_mirrors': sum(
            item['logical_key'].startswith('code/')
            and item.get('source') not in {
                entry.get('source')
                for entry in read(
                    root / 'workspace/production/v1.5.2-1/allocation.json'
                )['definitions']
            }
            for item in allocation['definitions']
        ),
        'version_document': VERSION_DOC_ID,
    }, ensure_ascii=False, indent=2))


def update_version_doc(text, commit):
    """Update only the v1.5.3 document metadata and add a release banner."""
    if not text.startswith('# CBA-KB v1.5.3'):
        raise RuntimeError('Unexpected v1.5.3 version document')
    old_meta = (
        '- **文档状态**：IMPLEMENTED_CANDIDATE / PR_OPEN / NOT_PRODUCTION\n'
        '- **方案制定日期**：2026-09-11\n'
        '- **最近修订日期**：2026-09-11（implementation closeout / PR #2 / candidate validation）\n'
        '- **实施日期**：2026-09-11（开发实现完成；PR 尚未合并；production 尚未发布）\n'
        '- **前置发布版本**：v1.5.2-2\n'
        '- **版本类型**：注册事件域收口 / semantics hardening / regression protection\n'
        '- **本次写入范围**：需求与实现状态记录；代码实现已完成并进入 PR；Drive production 与 release_status 尚未切换\n'
    )
    replacement = (
        '- **文档状态**：PRODUCTION_RELEASE_COMPLETE / v1.5.3-1\n'
        '- **方案制定日期**：2026-09-11\n'
        '- **最近修订日期**：2026-09-11（production release / merge commit / readback）\n'
        '- **实施日期**：2026-09-11\n'
        '- **前置发布版本**：v1.5.2-2\n'
        '- **版本类型**：注册事件域收口 / semantics hardening / regression protection\n'
        f'- **发布代码提交**：`{commit}`\n'
        f'- **生产六表 SHA-256**：`{DOMAIN_WORKBOOK_SHA256}`\n'
        f'- **semantic SHA-256**：`{DOMAIN_SEMANTIC_SHA256}`\n'
        '- **本次写入范围**：代码、六表事实、代码镜像、验收证据与 AI 文档；'
        'production publish 与 release_status 已由 v1.5.3-1 发布事务切换\n'
    )
    if old_meta not in text:
        raise RuntimeError('v1.5.3 document metadata block changed')
    text = text.replace(old_meta, replacement, 1)
    replacements = {
        '**IMPLEMENTED_CANDIDATE / PR_OPEN / NOT_PRODUCTION**':
            '**PRODUCTION_RELEASE_COMPLETE / v1.5.3-1**',
        '开发实现：IMPLEMENTED_CANDIDATE / PR_OPEN':
            '生产发布：PRODUCTION_RELEASE_COMPLETE / v1.5.3-1',
        '代码镜像：尚未同步 merge commit':
            '代码镜像：已同步发布 commit',
        '生产版本：v1.5.2-2':
            '生产版本：v1.5.3-1',
    }
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f'v1.5.3 document status marker changed: {old}')
        text = text.replace(old, new, 1)
    banner = (
        '\n> **生产发布状态（2026-09-11）**：`v1.5.3-1 / COMPLETE`。'
        f'发布代码提交 `{commit}`；六表为 '
        '`46 / 10 / 131 / 244 / 227 / 7`，workbook SHA-256 '
        f'`{DOMAIN_WORKBOOK_SHA256}`。发布仅覆盖注册事件域收口，'
        '不表示完整 DRAFT-2 或 STABLE。\n'
    )
    marker = '\n---\n\n\n## 0. 文档元数据'
    if marker not in text:
        raise RuntimeError('v1.5.3 document metadata marker changed')
    return text.replace(marker, banner + marker, 1)


def release_report(commit, products, added, applied, validation):
    return f"""# CBA-KB {RELEASE} production release

- Release ID: `{RELEASE}`
- Code commit: `{commit}`
- Candidate code commit: `{CANDIDATE_CODE_COMMIT}`
- Scope: registration-event domain closure; not full DRAFT-2 and not STABLE.

## Fact products

- `{DOMAIN_PRODUCT}`: {sum(DOMAIN_COUNTS.values())} records across six tables,
  workbook SHA-256 `{DOMAIN_WORKBOOK_SHA256}`, semantic SHA-256
  `{DOMAIN_SEMANTIC_SHA256}`.
- `MASTER.xlsx`: {products['MASTER.xlsx']} domestic registration rows.
- `CBA_外籍球员注册_SNAPSHOTS.xlsx`: {products['CBA_外籍球员注册_SNAPSHOTS.xlsx']} rows.
- `CBA_球员注册_EVENTS.xlsx`: {products['CBA_球员注册_EVENTS.xlsx']} rows.

Six-table counts are 46 / 10 / 131 / 244 / 227 / 7. The canonical event table
contains 131 records: domestic_movement={EVENT_DOMAIN_COUNTS['domestic_movement']},
foreign_registration={EVENT_DOMAIN_COUNTS['foreign_registration']},
foreign_usage={EVENT_DOMAIN_COUNTS['foreign_usage']}. These grains are not
interchangeable and must not be added as independent people.

## Event closure and reconciliation

- Legacy EVENTS: 73 rows mapped through 87 edges; unmapped=0.
- exact_match=59, canonical_split=14, unexplained_conflicts=0.
- Cross-table QA: {QA_SIGNAL_COUNT} research signals and {QA_WARNING_COUNT}
  existing historical movement gaps. Those warnings are not fabricated facts
  and remain explicit coverage limits.
- Domestic transaction windows are 10: 1 each for 2020-2021 through
  2023-2024, and 3 each for 2024-2025 and 2025-2026.

## Source and registry records

- Domestic merged source: `1dOvVJBVahuyq0L2fI4WRhRy7QJOxdOUR`.
- Foreign merged source: `1eGrMDep9YMfPNy66M6-6Jwm9iYaF_gQj`.
- Registry rows added: {json.dumps(added, ensure_ascii=False)}.
- Registry statuses applied: {json.dumps(applied, ensure_ascii=False)}.
- Candidate validation: `{validation['status']}`.

## Release controls

- The candidate workbook was rebuilt at merge commit `{CANDIDATE_CODE_COMMIT}`;
  consecutive builds produced the same workbook SHA-256.
- GitHub CI passed before merge. Production publication remains single-writer,
  snapshots every before/after object, verifies every payload, and provides
  rollback.
- PNG sources remain excluded from current processing. Historical records are
  not deleted.
- `CBA_球员注册_EVENTS.xlsx` remains a compatibility and historical-audit
  object; canonical current event truth is six-table
  `registration_status_events`.

## Known boundaries

- The 329 movement-gap warnings identify coverage gaps; they are not silently
  promoted to facts.
- Complete DRAFT-2, media-claim automation, player identity unification, native
  Sheet authority, and STABLE status remain outside this release.
- Empty values are not zero, event counts are not person counts, and snapshot
  counts are not registration counts.
"""


def acceptance_report(commit, validation, reconciliation, qa):
    return {
        'status': 'PASS_V1_5_3_EVENT_CLOSURE',
        'production_eligible': True,
        'scope': 'registration event domain closure; not full DRAFT-2 or STABLE',
        'release_id': RELEASE,
        'code_commit': commit,
        'candidate_code_commit': CANDIDATE_CODE_COMMIT,
        'candidate_workbook_sha256': DOMAIN_WORKBOOK_SHA256,
        'semantic_sha256': DOMAIN_SEMANTIC_SHA256,
        'table_counts': DOMAIN_COUNTS,
        'event_domain_counts': EVENT_DOMAIN_COUNTS,
        'validation_status': validation['status'],
        'reconciliation': reconciliation,
        'qa': {
            'signal_count': qa['signal_count'],
            'warning_count': qa['warning_count'],
            'warning_disposition': (
                'Existing historical movement gaps; recorded as coverage '
                'limits and not promoted to facts.'
            ),
        },
        'checks': {
            'clean_candidate_build': True,
            'deterministic_workbook': True,
            'strict_event_closure': True,
            'legacy_rows_fully_mapped': True,
            'protected_table_counts_unchanged': True,
            'merge_and_ci_completed': True,
            'production_publication_pending_readback': True,
        },
    }


def index_text(commit, version_url):
    return (
        '# CBA-KB v1.5.3 production release\n\n'
        f'- release: `{RELEASE}`\n'
        f'- code_commit: `{commit}`\n'
        f'- workbook_sha256: `{DOMAIN_WORKBOOK_SHA256}`\n'
        f'- semantic_sha256: `{DOMAIN_SEMANTIC_SHA256}`\n'
        f'- version document: {version_url}\n\n'
        '## AI consumption rules\n\n'
        '- 某赛季登记状态：读取 MASTER / Snapshot。\n'
        '- 什么时候发生什么：读取 canonical registration_status_events。\n'
        '- 为什么这么判断：读取 10_sources_原始证据。\n'
        '- 人工研究判断：读取 30_notes_人工知识。\n'
        '- 快速定位：读取 40_ai_投喂与索引，不把派生正文当精确计数来源。\n'
        '- 旧 CBA_球员注册_EVENTS.xlsx 仅用于兼容和历史审计。\n'
        '- 不同 event_domain 不得直接相加；空值不是零，记录数不是人数。\n'
    )


def freeze(root, staging_runs):
    if git('status', '--porcelain'):
        raise RuntimeError('Clean committed tree required')
    commit = git('rev-parse', 'HEAD')
    validation, _, reconciliation, qa = validate_candidate()
    drive = Drive(root)
    area = root / 'workspace/production' / RELEASE
    allocation = read(area / 'allocation.json')
    definitions = allocation['definitions']
    for item in definitions:
        if item['id'] in MIME_OVERRIDES:
            item['mime'] = MIME_OVERRIDES[item['id']]
    save(area / 'allocation.json', allocation)
    policy = read(root / 'config/production.json')
    if {item['id'] for item in definitions} != set(policy['targets']):
        raise RuntimeError('Production policy does not match the allocation target set')
    for item in definitions:
        if item['mime'] != policy['targets'][item['id']]['mime']:
            raise RuntimeError(f'Production MIME mismatch for {item["logical_key"]}')

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
                raise RuntimeError(f'{name} changed since reserve; re-check baseline')
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
    registry, added = prior.merged_registry(root, raw['source_registry.csv'])
    registry, applied = base.apply_status(
        registry, root / 'config/v1.5.2_registry_status.json'
    )
    _, baseline = base.inspect(inputs / 'MASTER.xlsx')
    candidate = area / 'candidate'
    result = base.build_products(
        base.collect([Path(root) / run for run in staging_runs]),
        base.inspect(inputs / 'MASTER.xlsx')[0],
        baseline,
        candidate,
        RELEASE,
        commit,
        base.datetime.now(base.timezone.utc).isoformat(),
    )
    domain_copy = candidate / DOMAIN_PRODUCT
    atomic(domain_copy, DOMAIN_WORKBOOK.read_bytes())
    save(area / 'dependencies.json', dependencies)

    version_before, _ = snapshot(drive, VERSION_DOC_ID)
    if digest(version_before) != VERSION_DOC_BASE_SHA256:
        raise RuntimeError('Drive v1.5.3 version document changed before freeze')
    version_after = update_version_doc(version_before.decode(), commit).encode()

    urls = {
        item['logical_key']: drive.meta(item['id'])['webViewLink']
        for item in definitions
    }
    status_url = drive.meta(allocation['status_id'])['webViewLink']
    nav = (
        f'当前发布：{RELEASE}。代码提交：{commit}。\n'
        f'首先读取发布状态：{status_url}\n'
        '仅 COMPLETE 使用当前对象；PUBLISHING/FAILED/ROLLING_BACK 使用状态中的 '
        'previous_snapshot；ROLLED_BACK 使用已恢复上一发布。读取前后状态变化则重试。\n'
        '本地执行，GitHub 私有仓库 https://github.com/SkyCjq/cba-kb-engine 管理代码，'
        'Drive 为已发布事实入口。\n'
        'Gemini 请直接提供所需文件链接；入口递归读取与自动刷新不保证。'
        'auto_validated 不是人工核验，空值不是零，记录数不是独立球员数，'
        '快照数不是注册人数。\n'
        '本发布只完成注册事件域收口，不代表完整 DRAFT-2 或 STABLE。\n'
    )
    links = '\n'.join(
        f'- {key}: {value}'
        for key, value in urls.items()
        if key.startswith(('derived/', 'input/', 'facts/', 'report/', 'ai/'))
    )
    index = (
        index_text(commit, urls[VERSION_DOC_LOGICAL_KEY])
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
            code.append(
                f"- {item['source']} | SHA256 {digest(data)} | "
                f"{urls[item['logical_key']]}"
            )

    manifest = list(base.csv.DictReader(
        base.io.StringIO(raw['manifest.csv'].decode('utf-8-sig'))
    ))
    columns = list(manifest[0])
    mapping = base.yaml.safe_load(drive.get(DRIVE_MAP_ID))
    mapping['v1_5_release'] = {
        'release_id': RELEASE,
        'status_id': allocation['status_id'],
        'code_commit': commit,
        'targets': {
            item['logical_key']: item['id'] for item in definitions
        },
    }
    generated_report = release_report(
        commit, result['products'], added, applied, validation
    ).encode()
    generated_acceptance = json.dumps(
        acceptance_report(commit, validation, reconciliation, qa),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode() + b'\n'

    frozen = area / 'frozen'
    frozen.mkdir(exist_ok=True)
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
            data = DOMAIN_WORKBOOK.read_bytes()
        elif key.startswith('facts/'):
            data = (candidate / item['name']).read_bytes()
        elif key == 'report/' + REPORT:
            data = generated_report
        elif key == 'report/' + ACCEPTANCE_REPORT:
            data = generated_acceptance
        elif key.startswith('ai/') and item['name'] in AI_ARTIFACTS:
            data = (CANDIDATE_DIR / item['name']).read_bytes()
        elif key == VERSION_DOC_LOGICAL_KEY:
            data = version_after
        elif key == 'report/v1.5.2-source-acceptance.json':
            data = (
                root / 'docs/validation/v1.5.2-source-acceptance-20260911.json'
            ).read_bytes()
        elif key.startswith('report/'):
            data = (root / base.REPORT_DIR / item['name']).read_bytes()
        elif key.startswith('ai/'):
            data = (root / base.REPORT_DIR / item['name']).read_bytes()
        elif key == 'control/drive_map.yaml':
            data = base.yaml.safe_dump(
                mapping, allow_unicode=True, sort_keys=False
            ).encode()
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
                + f'v1.5.3 发布 {RELEASE}：六表 '
                + f'{sum(DOMAIN_COUNTS.values())} 条，SHA-256 '
                + f'{DOMAIN_WORKBOOK_SHA256}；MASTER '
                + f'{result["products"]["MASTER.xlsx"]} 条，外籍快照 '
                + f'{result["products"]["CBA_外籍球员注册_SNAPSHOTS.xlsx"]} 条，'
                + f'旧注册事件 {result["products"]["CBA_球员注册_EVENTS.xlsx"]} 条。'
                + 'canonical current event truth 为六表 registration_status_events。'
            ).encode()
        elif key == 'entry/context':
            data = (
                nav
                + '\n'
                + links
                + '\n'
                + (root / 'docs/OPERATIONS_V1_5.md').read_text()
                + '\n'
                + (root / 'docs/CBA-KB_v1.5.3.md').read_text()
            ).encode()
        elif key == 'entry/crossseason':
            data = (
                nav
                + '\n以下历史跨赛季内容不作为当前事实。'
                '请改用当前 MASTER / 赛季阅读版 / 事实产品：\n'
                + links
            ).encode()
        else:
            data = (root / item['source']).read_bytes()
        if item['mode'] == 'managed_doc':
            data = base.wrap(data.decode()).encode()
        path = frozen / str(index_number)
        atomic(path, data)
        entry = {
            key: value
            for key, value in item.items()
            if key not in ('source', 'candidate_name', 'allowed_parents')
        }
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
            content_hash=(
                ''
                if entry['logical_key'] == 'input/manifest.csv'
                else digest(Path(entry['path']).read_bytes())
            ),
        )
        row['published_release'] = RELEASE
        row['hash_scope'] = (
            'managed_prefix' if entry['mode'] == 'managed_doc' else 'bytes'
        )
    stream = base.io.StringIO(newline='')
    writer = base.csv.DictWriter(
        stream,
        fieldnames=columns + ['published_release', 'hash_scope'],
        lineterminator='\n',
    )
    writer.writeheader()
    writer.writerows(manifest)
    target = next(
        entry for entry in entries if entry['logical_key'] == 'input/manifest.csv'
    )
    atomic(target['path'], stream.getvalue().encode('utf-8-sig'))

    save(area / 'entries.json', entries)
    save(area / 'release-links.json', {
        'release_id': RELEASE,
        'commit': commit,
        'status': status_url,
        'urls': urls,
    })
    print(json.dumps({
        'release': RELEASE,
        'commit': commit,
        'targets': len(entries),
        'fact_products': list(base.PRODUCTS),
        'domain_product': {
            'name': DOMAIN_PRODUCT,
            'sha256': DOMAIN_WORKBOOK_SHA256,
            'semantic_sha256': DOMAIN_SEMANTIC_SHA256,
            'counts': DOMAIN_COUNTS,
            'rows_total': sum(DOMAIN_COUNTS.values()),
        },
        'products': result['products'],
        'version_document': {
            'id': VERSION_DOC_ID,
            'sha256': digest(version_after),
        },
    }, ensure_ascii=False, indent=2))


def refresh_dependencies(root):
    """Re-pin immutable dependency metadata after uploads settle."""
    drive = Drive(root)
    area = root / 'workspace/production' / RELEASE
    dependencies = read(area / 'dependencies.json')
    refreshed = []
    for dependency in dependencies:
        content, meta = snapshot(drive, dependency['id'])
        if digest(content) != dependency['sha256']:
            raise RuntimeError(f"Input dependency bytes changed: {dependency['id']}")
        refreshed.append({
            'id': dependency['id'],
            'sha256': dependency['sha256'],
            'meta': fingerprint(meta),
        })
    save(area / 'dependencies.json', refreshed)
    print(json.dumps({
        'release': RELEASE,
        'dependencies': [item['id'] for item in refreshed],
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=['reserve', 'freeze', 'refresh-dependencies'])
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
        reserve(root)
    elif arguments.step == 'refresh-dependencies':
        refresh_dependencies(root)
    else:
        freeze(root, arguments.staging)


if __name__ == '__main__':
    main()
