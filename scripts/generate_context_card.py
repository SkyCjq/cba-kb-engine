"""Build secret-free v1.5.4 control candidates from explicit frozen files."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import yaml
from cba_kb.canonical_registry import for_release, load_registry
from cba_kb.current_state import (
    clean, generate_context_card, replace_current_block, target_metadata,
    validate_current_state,
)


def generate(registry, manifest, release_id, code_commit, documents, counts=None, blockers=None):
    registry = for_release(registry, release_id)
    metadata = target_metadata(release_id, code_commit, registry)
    rendered = {role: replace_current_block(text, metadata) for role, text in documents.items()}
    rendered['context_card'] = generate_context_card(metadata, registry, manifest, counts, blockers)
    validate_current_state(
        {'state': 'COMPLETE', 'current_release_id': release_id, 'code_commit': code_commit},
        registry, manifest, rendered, counts, blockers,
    )
    return registry, rendered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=ROOT / 'config/canonical_products.yaml')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--code-commit', required=True)
    parser.add_argument('--readme', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--version', type=Path, help='Legacy version document')
    parser.add_argument('--current-version-doc', type=Path, help='Modern current version document')
    parser.add_argument('--controls', type=Path, help='Frozen JSON counts and blocker identifiers')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.version and not args.current_version_doc:
        parser.error('Must provide --current-version-doc or --version')
    if args.output.exists():
        parser.error('Choose a new candidate directory')
    inputs = {}
    for name in ('registry', 'manifest', 'readme', 'index', 'version', 'current_version_doc', 'controls'):
        path = getattr(args, name, None)
        if path:
            data = path.read_bytes()
            clean(data, path.name)
            inputs[name] = data
    controls = json.loads(inputs['controls']) if 'controls' in inputs else {}
    doc_inputs = {
        'readme': inputs['readme'].decode('utf-8'),
        'index': inputs['index'].decode('utf-8'),
    }
    is_legacy = (
        args.release_id in {'v1.5.0', 'v1.5.1-1', 'v1.5.1-2', 'v1.5.2-1', 'v1.5.3-1', 'v1.5.3-2', 'v1.5.4-test', 'v1.5.5-1', 'v1.6.0-1'}
        and 'current_version_doc' not in inputs
    )
    if 'current_version_doc' in inputs:
        doc_inputs['current_version_doc'] = inputs['current_version_doc'].decode('utf-8')
    if 'version' in inputs:
        if is_legacy:
            doc_inputs['version'] = inputs['version'].decode('utf-8')
        else:
            doc_inputs.setdefault('current_version_doc', inputs['version'].decode('utf-8'))
    registry, documents = generate(
        load_registry(inputs['registry']), inputs['manifest'], args.release_id,
        args.code_commit, doc_inputs,
        controls.get('counts'), controls.get('blockers'),
    )
    outputs = {
        'canonical_products.yaml': yaml.safe_dump(registry, allow_unicode=True, sort_keys=False),
        'README.md': documents['readme'], 'INDEX.md': documents['index'],
        'CONTEXT_CARD.md': documents['context_card'],
    }
    if 'current_version_doc' in documents:
        outputs['CURRENT_VERSION_DOC.md'] = documents['current_version_doc']
    if 'version' in documents:
        outputs['version.md'] = documents['version']
    for name, value in outputs.items():
        clean(value.encode('utf-8'), name)
    args.output.mkdir(parents=True)
    for name, value in outputs.items():
        (args.output / name).write_text(value, encoding='utf-8')
    print(json.dumps({'status': 'CANDIDATE_NOT_PUBLISHED', 'files': sorted(outputs),
                      'context_card_bytes': len(documents['context_card'].encode('utf-8'))}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError):
        # No third-party parsing exception or document text is printed.
        raise SystemExit('CONTROL_CANDIDATE_FAILED') from None
