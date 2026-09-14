"""Semantic authority contracts; manifest owns transport, sources own lifecycle."""
from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path

import yaml

AUTHORITIES = {'canonical', 'derived', 'compatibility', 'historical'}
POLICIES = {
    'canonical': 'canonical_only', 'derived': 'derived_read_only',
    'compatibility': 'compatibility_only', 'historical': 'history_only',
}
REQUIRED = {
    'product_id', 'semantic_grain', 'authority', 'business_key', 'artifact_key',
    'generated_from', 'deprecated_by', 'read_policy', 'current_eligible',
    'independent_write_allowed',
}
TRANSPORT_FIELDS = {'drive_file_id', 'file_id', 'sha256', 'content_hash', 'synced_at', 'sync_status'}
LIFECYCLE_FIELDS = {'extraction_method', 'processing_status', 'processed_at', 'acceptance_status', 'source_acceptance'}
AUTHORITY_FIELDS = {'authority', 'canonical_authority', 'canonical_product', 'canonical_product_id', 'read_policy', 'current_eligible', 'independent_write_allowed', 'semantic_grain'}


def _fail(code):
    # Schema errors must never interpolate untrusted input (which can be secret).
    raise ValueError(code)


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def validate_responsibilities(registry, manifest, sources):
    authority_metadata = copy.deepcopy(registry)
    if isinstance(authority_metadata, dict):
        for product in authority_metadata.get('products', []):
            if isinstance(product, dict) and isinstance(product.get('projection'), dict):
                # Column-name mappings refer to business data; they do not store
                # source lifecycle values or create a second processing registry.
                product['projection'].pop('fields', None)
    if set(_walk(authority_metadata)) & (TRANSPORT_FIELDS | LIFECYCLE_FIELDS):
        _fail('REGISTRY_RESPONSIBILITY_DRIFT')
    if set(_walk(manifest)) & (LIFECYCLE_FIELDS | AUTHORITY_FIELDS):
        _fail('MANIFEST_RESPONSIBILITY_DRIFT')
    if set(_walk(sources)) & AUTHORITY_FIELDS:
        _fail('SOURCE_REGISTRY_RESPONSIBILITY_DRIFT')


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            _fail('REGISTRY_DUPLICATE_OR_INVALID_KEY')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_registry(data):
    try:
        result = yaml.load(data, Loader=_UniqueLoader)
    except (yaml.YAMLError, TypeError):
        _fail('REGISTRY_INVALID_YAML')
    validate_registry(result)
    return result


def manifest_rows(data):
    """Existing manifests use uid/content_hash/drive_file_id; no second index."""
    if isinstance(data, bytes):
        data = data.decode('utf-8-sig')
    if isinstance(data, str):
        return list(csv.DictReader(io.StringIO(data)))
    if isinstance(data, dict):
        return data.get('artifacts', [])
    return data


def manifest_index(manifest):
    rows = manifest_rows(manifest)
    if not isinstance(rows, list):
        _fail('MANIFEST_INVALID')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail('MANIFEST_INVALID')
        key = row.get('uid') or row.get('artifact_key')
        if not key or key in result:
            _fail('MANIFEST_DUPLICATE_OR_MISSING_KEY')
        result[key] = row
    return result


def validate_registry(registry, manifest=None, sources=None):
    if not isinstance(registry, dict) or set(registry) != {'registry_version', 'registry_release_id', 'products'}:
        _fail('REGISTRY_SCHEMA')
    if not all(isinstance(registry[k], str) and registry[k] for k in ('registry_version', 'registry_release_id')):
        _fail('REGISTRY_METADATA')
    rows = registry['products']
    if not isinstance(rows, list) or not rows:
        _fail('REGISTRY_EMPTY')
    validate_responsibilities(registry, manifest_rows(manifest) if manifest is not None else [], sources or [])
    index = manifest_index(manifest) if manifest is not None else None
    products, current_grains = {}, set()
    for product in rows:
        if not isinstance(product, dict) or not REQUIRED <= set(product):
            _fail('REGISTRY_REQUIRED_FIELD')
        if set(product) - REQUIRED - {'selector', 'projection'}:
            _fail('REGISTRY_UNKNOWN_FIELD')
        for field in ('product_id', 'semantic_grain', 'authority', 'artifact_key', 'read_policy'):
            if not isinstance(product[field], str) or not product[field].strip():
                _fail('REGISTRY_FIELD_TYPE')
        pid, authority = product['product_id'], product['authority']
        if pid in products:
            _fail('REGISTRY_DUPLICATE_PRODUCT')
        if authority not in AUTHORITIES or product['read_policy'] != POLICIES[authority]:
            _fail('REGISTRY_AUTHORITY_OR_POLICY')
        for field in ('business_key', 'generated_from'):
            if (not isinstance(product[field], list)
                    or any(not isinstance(v, str) or not v for v in product[field])
                    or len(set(product[field])) != len(product[field])):
                _fail('REGISTRY_FIELD_TYPE')
        if not product['business_key']:
            _fail('REGISTRY_BUSINESS_KEY')
        for field in ('current_eligible', 'independent_write_allowed'):
            if type(product[field]) is not bool:
                _fail('REGISTRY_BOOLEAN')
        if product['deprecated_by'] is not None and not isinstance(product['deprecated_by'], str):
            _fail('REGISTRY_DEPRECATION')
        if 'selector' in product and (not isinstance(product['selector'], str) or not product['selector']):
            _fail('REGISTRY_SELECTOR')
        if authority == 'historical' and product['current_eligible']:
            _fail('HISTORICAL_CURRENT_FORBIDDEN')
        if authority != 'canonical' and product['independent_write_allowed']:
            _fail('INDEPENDENT_WRITE_FORBIDDEN')
        if authority in {'compatibility', 'derived'} and not product['generated_from']:
            _fail('LINEAGE_REQUIRED')
        if authority == 'compatibility' and not product['deprecated_by']:
            _fail('COMPATIBILITY_DEPRECATION_REQUIRED')
        if authority == 'canonical' and product['current_eligible']:
            grain = product['semantic_grain']
            if grain in current_grains:
                _fail('MULTIPLE_CURRENT_CANONICAL')
            current_grains.add(grain)
            # Documents and notes are navigation, never machine business truth.
            if product['artifact_key'].startswith(('ai/', 'notes/', 'report/')) or product['artifact_key'].lower().endswith(('.md', '.txt')):
                _fail('DOCUMENT_AS_CANONICAL_FORBIDDEN')
        if index is not None and product['current_eligible']:
            artifact = index.get(product['artifact_key'])
            if not artifact or not (artifact.get('drive_file_id') or artifact.get('id')):
                _fail('CANONICAL_ARTIFACT_UNRESOLVED')
        products[pid] = product

    visited, visiting = set(), set()

    def visit(pid):
        if pid in visiting:
            _fail('LINEAGE_CYCLE')
        if pid in visited:
            return
        visiting.add(pid)
        product = products[pid]
        for source in product['generated_from']:
            if source not in products:
                _fail('LINEAGE_UNRESOLVED')
            if products[source]['authority'] not in {'canonical', 'derived'}:
                _fail('REVERSE_COMPATIBILITY_WRITE_FORBIDDEN')
            if product['current_eligible'] and not products[source]['current_eligible']:
                _fail('HISTORICAL_LINEAGE_AS_CURRENT')
            if index is not None and product['current_eligible']:
                target_row = index[product['artifact_key']]
                source_row = index[products[source]['artifact_key']]
                target_id = target_row.get('drive_file_id') or target_row.get('id')
                source_id = source_row.get('drive_file_id') or source_row.get('id')
                if target_id == source_id:
                    _fail('PROJECTION_OVERWRITES_CANONICAL')
            visit(source)
        replacement = product['deprecated_by']
        if replacement:
            if replacement == pid or replacement not in products or products[replacement]['authority'] not in {'canonical', 'derived'}:
                _fail('DEPRECATION_UNRESOLVED')
        visiting.remove(pid)
        visited.add(pid)

    for pid in products:
        visit(pid)
    return products


def for_release(registry, release_id):
    result = copy.deepcopy(registry)
    result['registry_release_id'] = release_id
    validate_registry(result)
    return result


def canonical_routes(registry, manifest):
    products = validate_registry(registry, manifest)
    index = manifest_index(manifest)
    return {
        product['semantic_grain']: {
            'product_id': pid, 'artifact_key': product['artifact_key'],
            'file_id': index[product['artifact_key']].get('drive_file_id') or index[product['artifact_key']].get('id'),
            'selector': product.get('selector'),
        }
        for pid, product in sorted(products.items())
        if product['authority'] == 'canonical' and product['current_eligible']
    }


def route(registry, manifest, grain):
    routes = canonical_routes(registry, manifest)
    if grain not in routes:
        _fail('CANONICAL_AUTHORITY_UNRESOLVED')
    return routes[grain]


def _normalized(rows, keys):
    if not isinstance(rows, list):
        _fail('PROJECTION_ROWS_INVALID')
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or any(key not in row or row[key] in (None, '') for key in keys):
            _fail('PROJECTION_BUSINESS_KEY_MISSING')
        key = json.dumps([row[k] for k in keys], ensure_ascii=False, sort_keys=True)
        if key in seen:
            _fail('PROJECTION_DUPLICATE_BUSINESS_KEY')
        seen.add(key)
        result.append(copy.deepcopy(row))
    return sorted(result, key=lambda r: json.dumps([r[k] for k in keys], ensure_ascii=False, sort_keys=True))


def project_compatibility(registry, product_id, canonical_records):
    """Projection specifications are frozen code/config, never legacy input rows.

    A missing migration mapping fails closed. Do not invent mappings for existing
    0/1/N legacy events; freeze them with real-source reconciliation first.
    """
    products = validate_registry(registry)
    product = products.get(product_id)
    if not product or product['authority'] not in {'compatibility', 'derived'}:
        _fail('PROJECTION_TARGET_FORBIDDEN')
    specification = product.get('projection')
    if isinstance(specification, dict) and specification.get('kind') in LEGACY_KINDS:
        return _legacy_projection(registry, product_id, canonical_records)[0]
    if (not isinstance(specification, dict) or set(specification) != {'source', 'fields'}
            or specification['source'] not in product['generated_from']):
        _fail('COMPATIBILITY_PROJECTION_UNRESOLVED')
    source = products[specification['source']]
    if source['authority'] != 'canonical':
        _fail('PROJECTION_SOURCE_NOT_CANONICAL')
    fields = specification['fields']
    if not isinstance(fields, dict) or not fields or not set(product['business_key']) <= set(fields):
        _fail('PROJECTION_FIELDS_INVALID')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
        _fail('PROJECTION_FIELDS_INVALID')
    records = canonical_records.get(specification['source'])
    source_rows = _normalized(records, source['business_key'])
    result = []
    for row in source_rows:
        if not set(fields.values()) <= set(row):
            _fail('PROJECTION_SOURCE_FIELD_MISSING')
        result.append({target: row[field] for target, field in fields.items()})
    return _normalized(result, product['business_key'])


def reconcile_compatibility(registry, product_id, canonical_records, actual_rows):
    specification = validate_registry(registry)[product_id].get('projection') or {}
    if specification.get('kind') in LEGACY_KINDS:
        report = reconciliation_report(registry, product_id, canonical_records, actual_rows)
        if report['status'] != 'PASS':
            _fail('COMPATIBILITY_SEMANTIC_DRIFT')
        return {'status': 'PASS', 'rows': report['actual_rows']}
    expected = project_compatibility(registry, product_id, canonical_records)
    product = validate_registry(registry)[product_id]
    if product['artifact_key'].endswith('.csv'):
        # CSV's documented representation has empty cells for null values and
        # strings for scalars. No date/identity inference is permitted.
        expected = [{key: '' if value is None else str(value) for key, value in row.items()} for row in expected]
    actual = _normalized(actual_rows, product['business_key'])
    if json.dumps(actual, ensure_ascii=False, sort_keys=True) != json.dumps(expected, ensure_ascii=False, sort_keys=True):
        _fail('COMPATIBILITY_SEMANTIC_DRIFT')
    return {'status': 'PASS', 'rows': len(actual)}


# A legacy annotation may remain present in historical output, but cannot be
# invented from another canonical date or silently ignored by reconciliation.
LEGACY_METADATA_FIELDS = {
    'source_file_id', 'source_url', 'source_url_primary', 'source_url_secondary',
    'source_page_or_row', 'source_type', 'extraction_method', 'verification_level',
    'raw_row_text', 'raw_event_text',
}
LEGACY_KINDS = {'legacy_foreign_snapshots_v1', 'legacy_registration_events_v1'}


def _selected(rows, scope):
    return [row for row in rows if all(row.get(key) == value for key, value in scope.items())]


def _unique_join(rows, expected, label):
    matches = [row for row in rows if all(row.get(key) == value for key, value in expected.items())]
    if len(matches) != 1:
        _fail('COMPATIBILITY_' + label + ('_MISSING' if not matches else '_AMBIGUOUS'))
    return matches[0]


def _required_fields(row, fields):
    if not set(fields) <= set(row):
        _fail('PROJECTION_SOURCE_FIELD_MISSING')
    return {field: copy.deepcopy(row[field]) for field in fields}


def _legacy_projection(registry, product_id, canonical_records):
    """Project every legacy business field; leave unavailable canonical dates null.

    These are semantic views. Source/raw/verification columns retain their
    provenance role and are listed explicitly in the reconciliation report.
    """
    from .facts import SNAPSHOT, EVENT
    products = validate_registry(registry)
    product = products[product_id]
    spec = product['projection']
    kind = spec.get('kind')
    if kind not in LEGACY_KINDS or product['authority'] != 'compatibility':
        _fail('PROJECTION_TARGET_FORBIDDEN')
    if spec.get('source') not in product['generated_from']:
        _fail('PROJECTION_SOURCE_NOT_CANONICAL')
    source = products[spec['source']]
    if source['authority'] != 'canonical':
        _fail('PROJECTION_SOURCE_NOT_CANONICAL')
    rows = _normalized(canonical_records.get(spec['source']), source['business_key'])
    scope = spec.get('scope')
    if not isinstance(scope, dict) or not scope or any(value is None for value in scope.values()):
        _fail('PROJECTION_SCOPE_REQUIRED')
    selected = _selected(rows, scope)
    result, edges, exceptions = [], [], []
    represented = {spec['source']: set()}
    if kind == 'legacy_foreign_snapshots_v1':
        if set(spec) != {'kind', 'source', 'scope', 'status_labels', 'jersey_renderings', 'label_renderings'}:
            _fail('PROJECTION_CONTRACT_INVALID')
        fields = [field for field in SNAPSHOT if field not in LEGACY_METADATA_FIELDS]
        labels = spec['status_labels']
        renderings = spec['jersey_renderings']
        if not isinstance(labels, dict) or not isinstance(renderings, dict):
            _fail('PROJECTION_CONTRACT_INVALID')
        seen_exceptions = set()
        for row in selected:
            out = _required_fields(row, fields)
            key = row['snapshot_key']
            # Merged evidence decorates labels with Markdown bold delimiters;
            # removing those delimiters does not change club identity.
            for field in ('club_source_name', 'name_en_raw'):
                if isinstance(out[field], str):
                    out[field] = out[field].removeprefix('**').removesuffix('**')
                labels_for_field = spec['label_renderings'].get(field, {})
                out[field] = labels_for_field.get(out[field], out[field])
            if set(spec['label_renderings']) - {'club_source_name', 'name_en_raw'}:
                _fail('PROJECTION_LABEL_FIELD_FORBIDDEN')
            if row['status_at_snapshot'] not in labels:
                _fail('PROJECTION_STATUS_UNMAPPED')
            out['status_at_snapshot'] = labels[row['status_at_snapshot']]
            if key in renderings:
                rule = renderings[key]
                if (set(rule) != {'canonical', 'legacy', 'evidence'}
                        or not isinstance(rule['evidence'], str) or not rule['evidence']
                        or row['jersey_number'] != rule['canonical']):
                    _fail('PROJECTION_JERSEY_EVIDENCE_MISMATCH')
                out['jersey_number'] = rule['legacy']
                exceptions.append({'legacy_key': key, 'field': 'jersey_number',
                                   'canonical': rule['canonical'], 'legacy': rule['legacy'],
                                   'reason': 'frozen_original_xlsx_numeric_storage',
                                   'evidence': rule['evidence']})
                seen_exceptions.add(key)
            result.append(out)
            represented[spec['source']].add(key)
            edges.append({'legacy_key': key, 'product_id': spec['source'], 'canonical_key': key})
        if seen_exceptions != set(renderings):
            _fail('PROJECTION_JERSEY_SCOPE_CHANGED')
    else:
        if set(spec) != {'kind', 'source', 'scope', 'relationship_source', 'master_source',
                         'domestic_event_aliases', 'legacy_annotations'}:
            _fail('PROJECTION_CONTRACT_INVALID')
        relation_id, master_id = spec['relationship_source'], spec['master_source']
        for pid in (relation_id, master_id):
            if pid not in product['generated_from'] or products[pid]['authority'] != 'canonical':
                _fail('PROJECTION_SOURCE_NOT_CANONICAL')
        relations = _normalized(canonical_records.get(relation_id), products[relation_id]['business_key'])
        master = _normalized(canonical_records.get(master_id), products[master_id]['business_key'])
        represented[relation_id] = set()
        represented[master_id] = set()
        aliases = spec['domestic_event_aliases']
        annotations = spec['legacy_annotations']
        if (not isinstance(annotations, dict)
                or set(annotations) != {'artifact_key', 'fields', 'parser'}
                or annotations['parser'] != 'midseason_md'
                or annotations['fields'] != ['club_announcement_date']):
            _fail('LEGACY_ANNOTATION_CONTRACT_INVALID')
        evidence = canonical_records.get(annotations['artifact_key'])
        if not isinstance(evidence, list):
            _fail('LEGACY_ANNOTATION_EVIDENCE_REQUIRED')
        evidence = _normalized(evidence, ['event_key'])
        if not isinstance(aliases, dict) or not aliases or len(set(aliases.values())) != len(aliases):
            _fail('PROJECTION_EVENT_ALIASES_INVALID')
        fields = [field for field in EVENT if field not in LEGACY_METADATA_FIELDS]
        direct = ['season', 'player_type', 'club_id', 'club_source_name', 'player_name_zh',
                  'player_name_en_raw', 'player_name_en_normalized', 'event_date',
                  'date_year_inferred', 'from_club_id', 'from_club_source_name',
                  'contract_category', 'contract_term_official', 'club_announcement_date',
                  'registration_window_deadline']
        # This legacy product covers one original XLSX and the documented Bayi
        # transfer subset. All other canonical events are reported as out of scope.
        selected = [
            row for row in rows if (
                all(row.get(key) == value for key, value in scope['foreign'].items())
                or (row.get('event_key') in spec['domestic_event_aliases']
                    and all(row.get(key) == value for key, value in scope['domestic'].items()))
            )
        ]
        seen_aliases = set()
        for row in selected:
            _required_fields(row, direct + ['event_key', 'event_type', 'event_status'])
            out = dict.fromkeys(fields)
            out.update({field: row[field] for field in direct})
            if row['player_type'] == 'foreign':
                parts = row['event_key'].split('|')
                if len(parts) != 7 or parts[1] != 'foreign_registration' or not parts[-1].isdigit():
                    _fail('PROJECTION_EVENT_KEY_UNREPRESENTABLE')
                parts[1] = 'foreign'
                out.update(event_key='|'.join(parts), provisional_player_key=parts[3], sequence=parts[-1],
                           event_type=row['event_type'],
                           registration_method=row.get('registration_method_official'),
                           registration_status='取消注册' if row['event_status'] == 'confirmed' else None,
                           # No nationality or prose note was carried by the legacy
                           # cancellation schema. Nulls are never populated by inference.
                           nationality=None, notes=None)
                if row['event_status'] != 'confirmed':
                    _fail('PROJECTION_EVENT_STATE_CHANGED')
            else:
                canonical_key = row['event_key']
                legacy_key = aliases.get(canonical_key)
                if not isinstance(legacy_key, str):
                    _fail('PROJECTION_EVENT_ALIAS_MISSING')
                parts = legacy_key.split('|')
                expected = [str(row[key]) for key in ('season', 'player_type', 'club_id', 'player_name_zh')]
                if (len(parts) != 7 or parts[:4] != expected or parts[4] != 'registration_change'
                        or parts[5] != row['event_date'] or not parts[6].isdigit()
                        or row['event_type'] != 'free_agent_claim'):
                    _fail('PROJECTION_EVENT_ALIAS_CONFLICT')
                seen_aliases.add(canonical_key)
                join = {'season': row['season'], 'club_id': row['club_id'], 'player': row['player_name_zh']}
                relation = _unique_join(relations, join, 'RELATIONSHIP')
                snapshot = _unique_join(master, join, 'MASTER')
                if relation['from_club_id'] != row['from_club_id']:
                    _fail('PROJECTION_RELATIONSHIP_CONFLICT')
                mapping = {'club_source_name': 'club_official', 'contract_category': 'contract_category',
                           'contract_term_official': 'contract_term_official',
                           'registration_method': 'registration_method',
                           'registration_status': 'registration_status', 'notes': 'remarks'}
                _required_fields(snapshot, mapping.values())
                out.update({target: snapshot[source_field] for target, source_field in mapping.items()})
                out.update(event_key=legacy_key, provisional_player_key=row['player_name_zh'],
                           sequence=parts[6], event_type='registration_change', nationality=None)
                annotation = _unique_join(evidence, {'event_key': legacy_key}, 'LEGACY_ANNOTATION')
                for field in ('season', 'club_id', 'player_name_zh', 'event_date', 'date_year_inferred'):
                    if annotation.get(field) != row.get(field):
                        _fail('LEGACY_ANNOTATION_IDENTITY_CONFLICT')
                for field in annotations['fields']:
                    if field not in annotation or annotation[field] in (None, ''):
                        _fail('LEGACY_ANNOTATION_FIELD_MISSING')
                    canonical_value, documented_value = row[field], annotation[field]
                    if canonical_value is not None and canonical_value != documented_value:
                        _fail('LEGACY_ANNOTATION_CANONICAL_CONFLICT')
                    out[field] = documented_value
                    if canonical_value is None and documented_value is not None:
                        exceptions.append({'legacy_key': legacy_key, 'field': field,
                                           'canonical': None, 'legacy': documented_value,
                                           'reason': 'source_backed_legacy_annotation_not_canonical_fact',
                                           'evidence': annotations['artifact_key'],
                                           'canonical_value_unchanged': True})
                represented[relation_id].add(relation['record_key'])
                represented[master_id].add(snapshot['record_key'])
                edges.append({'legacy_key': legacy_key, 'product_id': relation_id,
                              'canonical_key': relation['record_key'], 'role': 'relationship_projection'})
            represented[spec['source']].add(row['event_key'])
            edges.append({'legacy_key': out['event_key'], 'product_id': spec['source'],
                          'canonical_key': row['event_key'], 'role': 'event_projection'})
            result.append(out)
        if seen_aliases != set(aliases):
            _fail('PROJECTION_EVENT_ALIAS_SCOPE_CHANGED')
    return _normalized(result, product['business_key']), edges, represented, exceptions


def reconciliation_report(registry, product_id, canonical_records, actual_rows):
    """Deterministic missing/extra/field-drift evidence without changing either side."""
    product = validate_registry(registry)[product_id]
    spec = product.get('projection') or {}
    if spec.get('kind') in LEGACY_KINDS:
        expected, edges, represented, exceptions = _legacy_projection(registry, product_id, canonical_records)
        actual = _normalized(actual_rows, product['business_key'])
        metadata_fields = sorted(set().union(*(set(row) for row in actual)) & LEGACY_METADATA_FIELDS) if actual else []
        actual = [{key: value for key, value in row.items() if key not in LEGACY_METADATA_FIELDS} for row in actual]
    else:
        expected = project_compatibility(registry, product_id, canonical_records)
        if product['artifact_key'].endswith('.csv'):
            expected = [{key: '' if value is None else str(value) for key, value in row.items()} for row in expected]
        actual = _normalized(actual_rows, product['business_key'])
        edges, represented, exceptions, metadata_fields = [], {}, [], []
    def keyed(rows):
        return {json.dumps([row[key] for key in product['business_key']], ensure_ascii=False): row for row in rows}
    expected_by_key, actual_by_key = keyed(expected), keyed(actual)
    missing = sorted(set(expected_by_key) - set(actual_by_key))
    extra = sorted(set(actual_by_key) - set(expected_by_key))
    drift = []
    for key in sorted(set(expected_by_key) & set(actual_by_key)):
        left, right = expected_by_key[key], actual_by_key[key]
        for field in sorted(set(left) | set(right)):
            if field not in left or field not in right or type(left[field]) is not type(right[field]) or left[field] != right[field]:
                drift.append({'business_key': key, 'field': field,
                              'expected': left.get(field), 'actual': right.get(field),
                              'reason': 'CANONICAL_VALUE_UNAVAILABLE' if field in left and left[field] is None and right.get(field) is not None else 'FIELD_DRIFT'})
    coverage = {}
    products = validate_registry(registry)
    for pid, used in represented.items():
        source_key = products[pid]['business_key']
        all_keys = {row[source_key[0]] for row in canonical_records[pid]}
        coverage[pid] = {'total_rows': len(all_keys), 'referenced_rows': len(used),
                         'out_of_projection_rows': sorted(all_keys - used)}
    return {'status': 'PASS' if not missing and not extra and not drift else 'FAIL_CLOSED',
            'product_id': product_id, 'expected_rows': len(expected), 'actual_rows': len(actual),
            'matched_business_keys': len(set(expected_by_key) & set(actual_by_key)),
            'missing_rows': missing, 'extra_rows': extra, 'unexplained_drift': drift,
            'canonical_coverage': coverage, 'mapping_edges': edges,
            'explained_rendering_differences': exceptions,
            'provenance_fields_outside_business_comparison': metadata_fields,
            'unknown_values_inferred': False}
