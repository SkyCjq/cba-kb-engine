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
