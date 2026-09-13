"""Generated current metadata, bounded context cards and current-zone guards."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import re

from .canonical_registry import canonical_routes, manifest_index, validate_registry
from .common import digest

BEGIN = '[CBA-KB CURRENT STATE BEGIN]\n'
END = '[CBA-KB CURRENT STATE END]\n'
CURRENT_VERSION_DOC = 'CURRENT_VERSION_DOC'
LEGACY_CURRENT_VERSION_DOC_ID = '1ZebJR9YPKX37cMDdz0xznHDa45at_q65'
CURRENT_VERSION_DOC_MIGRATION_RELEASE = 'v1.6.1-1'
CURRENT_DOCUMENT_SURFACES = frozenset({
    'readme', 'index', 'context_card', 'version', 'current_version_doc',
})
TRANSITIONAL = {'PUBLISHING', 'VERIFYING', 'FAILED', 'ROLLING_BACK'}
NON_CURRENT = {'candidate', 'before', 'rollback', 'staging', 'prechange', 'historical', 'historical-only'}
HISTORY_NAME = re.compile(r'(?i)(?:^|[_. /-])(candidate|before|rollback|staging|prechange|historical)(?:$|[_. /-])')
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:/-]+\Z')


@lru_cache(maxsize=1)
def security():
    """Load the existing repository scanner without maintaining a second rule set."""
    path = Path(__file__).resolve().parents[2] / 'scripts/check_secrets.py'
    spec = importlib.util.spec_from_file_location('cba_kb_secret_scanner', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('SECRET_SCANNER_UNAVAILABLE')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean(data, path):
    security().require_clean(data, path)


def target_metadata(release_id, code_commit, registry):
    if (not isinstance(release_id, str) or not IDENTIFIER.fullmatch(release_id)
            or not isinstance(code_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', code_commit)):
        raise ValueError('CURRENT_METADATA_INVALID')
    validate_registry(registry)
    if registry['registry_release_id'] != release_id:
        raise ValueError('CURRENT_STATE_DRIFT')
    return {'release_id': release_id, 'code_commit': code_commit,
            'registry_version': registry['registry_version'], 'state': 'COMPLETE'}


def render_current_state(metadata):
    if set(metadata) != {'release_id', 'code_commit', 'registry_version', 'state'} or metadata['state'] != 'COMPLETE':
        raise ValueError('CURRENT_METADATA_INVALID')
    content = BEGIN + json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n' + END
    clean(content.encode(), 'current-state')
    return content


def replace_current_block(text, metadata):
    """Replace exactly one marked span; retain every surrounding byte."""
    replacement = render_current_state(metadata)
    starts, ends = text.count(BEGIN), text.count(END)
    if starts == ends == 0:
        from .native import BEGIN as NATIVE_BEGIN, END as NATIVE_END, wrap
        if NATIVE_BEGIN in text or NATIVE_END in text:
            if not text.startswith(NATIVE_BEGIN) or text.count(NATIVE_BEGIN) != 1 or text.count(NATIVE_END) != 1:
                raise ValueError('CURRENT_MARKER_INVALID')
            # Replace the old managed current prefix, retaining historical content.
            return wrap(replacement) + text[text.index(NATIVE_END) + len(NATIVE_END):]
        return replacement + text
    if starts != 1 or ends != 1 or text.index(BEGIN) > text.index(END):
        raise ValueError('CURRENT_MARKER_INVALID')
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + replacement + text[end:]


def read_current_block(text):
    clean(text.encode('utf-8'), 'current-document')
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) >= text.index(END):
        raise ValueError('CURRENT_STATE_DRIFT')
    start = text.index(BEGIN) + len(BEGIN)
    try:
        value = json.loads(text[start:text.index(END)])
    except (ValueError, TypeError):
        raise ValueError('CURRENT_STATE_DRIFT') from None
    if not isinstance(value, dict):
        raise ValueError('CURRENT_STATE_DRIFT')
    return value


def consumer_artifacts(status):
    """A partially published current namespace is never a consumer read source."""
    state = status.get('state')
    if state in TRANSITIONAL:
        snapshots = status.get('previous_snapshot')
        if not snapshots:
            raise ValueError('PREVIOUS_SNAPSHOT_UNAVAILABLE')
        return snapshots
    if state not in {'COMPLETE', 'ROLLED_BACK'} or not status.get('artifacts'):
        raise ValueError('CURRENT_RELEASE_UNAVAILABLE')
    return status['artifacts']


def generate_context_card(metadata, registry, manifest, counts=None, blockers=None):
    validate_registry(registry, manifest)
    if metadata != target_metadata(metadata.get('release_id'), metadata.get('code_commit'), registry):
        raise ValueError('CURRENT_STATE_DRIFT')
    counts, blockers = counts or {}, blockers or []
    if (not isinstance(counts, dict)
            or any(not isinstance(k, str) or not IDENTIFIER.fullmatch(k) or type(v) is not int or v < 0 for k, v in counts.items())
            or not isinstance(blockers, list)
            or any(not isinstance(v, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', v) for v in blockers)):
        raise ValueError('CONTEXT_CONTROLS_INVALID')
    lines = [
        '# CBA-KB CONTEXT CARD',
        render_current_state(metadata).rstrip(),
        'Navigation only. Read release_status first; while PUBLISHING/FAILED/ROLLING_BACK use previous_snapshot.',
        'Code truth: private GitHub. Drive 50_scripts is a read-only mirror of merged code.',
        'Data authority: canonical_products. Artifact IDs/hashes: manifest. Source lifecycle/provenance: source_registry and 10_sources evidence.',
        'Canonical routes (artifact_key#worksheet; resolve file IDs in manifest):',
    ]
    for grain, target in sorted(canonical_routes(registry, manifest).items()):
        selector = '#' + target['selector'] if target['selector'] else ''
        lines.append(f"- {grain}: {target['artifact_key']}{selector}")
    if counts:
        lines.append('Counts: ' + json.dumps(counts, sort_keys=True, separators=(',', ':')))
    lines.extend([
        'Open blockers: ' + (', '.join(sorted(set(blockers))) or 'NONE'),
        'Hard rules: compatibility is read-only; never import it into canonical products.',
        '90_archive is history, never current truth. Planned capabilities are unavailable.',
        'No credentials in Git, mirrors, reports or AI context. Unknown facts/dates stay unknown.',
    ])
    card = '\n'.join(lines) + '\n'
    clean(card.encode('utf-8'), 'CONTEXT_CARD.md')
    if len(card.encode('utf-8')) > 4096:
        raise ValueError('CONTEXT_CARD_TOO_LARGE')
    return card


def validate_context_card(card, metadata, registry, manifest, counts=None, blockers=None):
    if len(card.encode('utf-8')) > 4096:
        raise ValueError('CONTEXT_CARD_TOO_LARGE')
    clean(card.encode('utf-8'), 'CONTEXT_CARD.md')
    if card != generate_context_card(metadata, registry, manifest, counts, blockers):
        raise ValueError('CONTEXT_CARD_DRIFT')
    return {'status': 'PASS', 'utf8_bytes': len(card.encode('utf-8'))}


def control_document_identities(status, registry, documents):
    if status.get('state') not in {'COMPLETE', 'ROLLED_BACK'}:
        raise ValueError('CURRENT_RELEASE_NOT_READABLE')
    commit = status.get('code_commit') or status.get('published_code_commit')
    if status.get('published_code_commit', commit) != commit:
        raise ValueError('CURRENT_STATE_DRIFT')
    metadata = target_metadata(
        status.get('current_release_id'), commit, registry,
    )
    required = (
        CURRENT_DOCUMENT_SURFACES
        if status.get('current_release_id') == 'v1.6.1-1'
        else {'readme', 'index', 'context_card', 'version'}
    )
    if not required <= set(documents):
        raise ValueError('CURRENT_DOCUMENT_MISSING')
    identities = {'release_status': metadata}
    for surface, text in sorted(documents.items()):
        if read_current_block(text) != metadata:
            raise ValueError('CURRENT_STATE_DRIFT')
        identities[surface] = read_current_block(text)
    return {'status': 'PASS', 'identities': identities}



def validate_current_state(status, registry, manifest, documents, counts=None, blockers=None):
    if status.get('state') not in {'COMPLETE', 'ROLLED_BACK'}:
        raise ValueError('CURRENT_RELEASE_NOT_READABLE')
    commit = status.get('code_commit') or status.get('published_code_commit')
    if status.get('published_code_commit', commit) != commit:
        raise ValueError('CURRENT_STATE_DRIFT')
    metadata = target_metadata(status.get('current_release_id'), commit, registry)
    required = (
        CURRENT_DOCUMENT_SURFACES
        if status.get('current_release_id') == 'v1.6.1-1'
        else {'readme', 'index', 'context_card', 'version'}
    )
    if not required <= set(documents):
        raise ValueError('CURRENT_DOCUMENT_MISSING')
    for text in documents.values():
        if read_current_block(text) != metadata:
            raise ValueError('CURRENT_STATE_DRIFT')
    validate_context_card(documents['context_card'], metadata, registry, manifest, counts, blockers)
    return {
        'status': 'PASS',
        'release_id': metadata['release_id'],
        'code_commit': commit,
        'surfaces': sorted(['release_status', *CURRENT_DOCUMENT_SURFACES]),
    }


def current_version_document_migration(manifest, release_id):
    """Resolve the frozen legacy version document to the stable logical key.

    The migration is intentionally bounded to the v1.6.1 release planning
    boundary. It keeps the existing Drive object identity and only renames
    its logical key in a candidate manifest view.
    """
    if not isinstance(manifest, list) or any(
        not isinstance(row, dict) for row in manifest
    ):
        raise ValueError('CURRENT_VERSION_DOC_MANIFEST_INVALID')
    rows = [dict(row) for row in manifest]
    report = {
        'status': 'NOT_APPLICABLE',
        'release_id': release_id,
        'logical_key': CURRENT_VERSION_DOC,
        'drive_file_id': None,
        'existing_object_reused': False,
        'source_logical_key': None,
    }
    if release_id != CURRENT_VERSION_DOC_MIGRATION_RELEASE:
        return {'manifest': rows, 'report': report}
    current = [row for row in rows if row.get('uid') == CURRENT_VERSION_DOC]
    if len(current) > 1:
        raise ValueError('CURRENT_VERSION_DOC_MANIFEST_DUPLICATE')
    historical = [
        row for row in rows
        if row.get('drive_file_id') == LEGACY_CURRENT_VERSION_DOC_ID
    ]
    if current:
        row = current[0]
        if row.get('drive_file_id') != LEGACY_CURRENT_VERSION_DOC_ID:
            raise ValueError('CURRENT_VERSION_DOC_IDENTITY_CONFLICT')
        if len(historical) != 1 or historical[0] is not row:
            raise ValueError('CURRENT_VERSION_DOC_IDENTITY_DUPLICATE')
        report.update({
            'status': 'ALREADY_MIGRATED',
            'drive_file_id': LEGACY_CURRENT_VERSION_DOC_ID,
            'existing_object_reused': True,
            'source_logical_key': row.get('local_path'),
        })
        return {'manifest': rows, 'report': report}
    if not historical:
        raise ValueError('CURRENT_VERSION_DOC_LEGACY_IDENTITY_MISSING')
    if len(historical) > 1:
        raise ValueError('CURRENT_VERSION_DOC_LEGACY_IDENTITY_DUPLICATE')
    source = historical[0]
    index = rows.index(source)
    migrated = dict(source)
    migrated['uid'] = CURRENT_VERSION_DOC
    rows[index] = migrated
    report.update({
        'status': 'MIGRATED',
        'drive_file_id': LEGACY_CURRENT_VERSION_DOC_ID,
        'existing_object_reused': True,
        'source_logical_key': source.get('uid') or source.get('local_path'),
    })
    return {'manifest': rows, 'report': report}


def migrate_current_version_document(manifest, release_id):
    return current_version_document_migration(manifest, release_id)['manifest']


def current_version_document(manifest, *, release_id=None):
    if release_id == CURRENT_VERSION_DOC_MIGRATION_RELEASE:
        manifest = migrate_current_version_document(manifest, release_id)
    index = manifest_index(manifest)
    row = index.get(CURRENT_VERSION_DOC)
    if row is None:
        raise ValueError('CURRENT_VERSION_DOC_MISSING')
    return row


def audit_current_history(items, zones, manifest, registry):
    """Classify by folder identities, ancestry, manifest roles AND registry roles.

    Caller supplies a complete bounded inventory from the configured zones.
    Unknown folder ownership is an error, never implicitly current.
    """
    products = validate_registry(registry, manifest)
    index = manifest_index(manifest)
    required = {'current', 'history', 'staging', 'evidence'}
    if not required <= set(zones) or any(not isinstance(zones[key], list) for key in required):
        raise ValueError('CURRENT_ZONE_CONFIGURATION')
    if not zones['current'] or not zones['history']:
        raise ValueError('CURRENT_ZONE_CONFIGURATION')
    all_zones = [fid for key in required for fid in zones[key]]
    if len(all_zones) != len(set(all_zones)):
        raise ValueError('CURRENT_ZONE_OVERLAP')
    folders = zones.get('folders', {})
    by_id = {row.get('drive_file_id') or row.get('id'): row for row in index.values()}
    roles = {}
    for product in products.values():
        roles.setdefault(product['artifact_key'], []).append(product)
    findings = []

    def ancestry(parents):
        seen, stack = set(), list(parents)
        while stack:
            parent = stack.pop()
            if parent in seen:
                continue
            seen.add(parent)
            stack.extend(folders.get(parent, []))
        return seen

    seen_ids = set()
    for item in items:
        if not isinstance(item, dict) or not item.get('id') or item['id'] in seen_ids:
            raise ValueError('CURRENT_INVENTORY_INVALID')
        seen_ids.add(item['id'])
        if item.get('mimeType') == 'application/vnd.google-apps.folder':
            continue
        parents = item.get('parents')
        if not isinstance(parents, list) or not parents:
            raise ValueError('CURRENT_FOLDER_ID_REQUIRED')
        locations = ancestry(parents)
        is_history = bool(locations & (set(zones['history']) | set(zones['staging'])))
        # A history subtree under root is not current; direct cross-parenting is.
        direct_current = bool(set(parents) & set(zones['current']))
        evidence = bool(locations & set(zones['evidence']))
        current = direct_current or (bool(locations & set(zones['current'])) and not is_history and not evidence)
        row = by_id.get(item['id'], {})
        key = row.get('uid') or row.get('artifact_key')
        product_roles = roles.get(key, [])
        role = item.get('artifact_role') or row.get('artifact_role')
        history = (is_history or role in NON_CURRENT
                   or any(p['authority'] == 'historical' for p in product_roles)
                   or bool(HISTORY_NAME.search(item.get('name', ''))))
        if current and history:
            findings.append({'id': item['id'], 'rule_id': 'HISTORICAL_IN_CURRENT'})
        if not current and any(p['authority'] == 'canonical' and p['current_eligible'] for p in product_roles):
            findings.append({'id': item['id'], 'rule_id': 'CANONICAL_OUTSIDE_CURRENT'})
        if not (current or is_history or evidence):
            findings.append({'id': item['id'], 'rule_id': 'FOLDER_ROLE_UNRESOLVED'})
    if findings:
        raise ValueError('CURRENT_HISTORY_VIOLATION: ' + json.dumps(findings, sort_keys=True))
    return {'status': 'PASS', 'inspected': len(seen_ids), 'violations': 0}


def zero_business_delta(before, after):
    """Compare protected bytes (stronger than row equality); include evidence."""
    if not before or set(before) != set(after):
        raise ValueError('ZERO_BUSINESS_FACT_DELTA')
    if any(digest(before[key]) != digest(after[key]) for key in before):
        raise ValueError('ZERO_BUSINESS_FACT_DELTA')
    return {'status': 'PASS', 'protected_artifacts': len(before)}
