import pytest

from cba_kb.current_state import generate_context_card, target_metadata, validate_context_card
from test_canonical_registry import SHA, manifest, registry


def test_context_is_deterministic_bounded_and_routes_canonical_only():
    reg = registry()
    meta = target_metadata('v1.5.4-test', SHA, reg)
    card = generate_context_card(meta, reg, manifest(), {'events': 131})
    assert card == generate_context_card(meta, reg, manifest(), {'events': 131})
    assert len(card.encode('utf-8')) <= 4096
    assert 'facts/events.jsonl' in card and 'facts/old_events.jsonl' not in card
    assert validate_context_card(card, meta, reg, manifest(), {'events': 131})['status'] == 'PASS'


@pytest.mark.parametrize('extra', [
    '\nPlanned watcher is implemented.\n',
    '\nCurrent truth is in 90_archive.\n',
    '\nCanonical truth is facts/old_events.jsonl.\n',
])
def test_appended_uncontrolled_claims_are_rejected(extra):
    meta = target_metadata('v1.5.4-test', SHA, registry())
    card = generate_context_card(meta, registry(), manifest())
    with pytest.raises(ValueError, match='DRIFT'):
        validate_context_card(card + extra, meta, registry(), manifest())


def test_context_byte_bound_counts_utf8_not_characters():
    meta = target_metadata('v1.5.4-test', SHA, registry())
    with pytest.raises(ValueError, match='TOO_LARGE'):
        validate_context_card('中' * 2000, meta, registry(), manifest())


def test_long_routes_cannot_silently_truncate_context():
    reg = registry()
    reg['products'][0]['semantic_grain'] = 'long' * 2000
    meta = target_metadata('v1.5.4-test', SHA, reg)
    with pytest.raises(ValueError, match='TOO_LARGE'):
        generate_context_card(meta, reg, manifest())


def test_metadata_and_registry_freshness():
    reg = registry()
    meta = target_metadata('v1.5.4-test', SHA, reg)
    card = generate_context_card(meta, reg, manifest())
    meta['code_commit'] = 'c' * 40
    with pytest.raises(ValueError, match='DRIFT'):
        validate_context_card(card, meta, reg, manifest())
    meta['code_commit'] = SHA
    reg['registry_version'] = '2'
    with pytest.raises(ValueError, match='DRIFT'):
        validate_context_card(card, meta, reg, manifest())


def test_cli_generates_reviewable_candidates_without_changing_inputs(tmp_path):
    import csv
    import subprocess
    import sys
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    reg = tmp_path / 'registry.yaml'
    reg.write_text(yaml.safe_dump(registry()))
    transport = tmp_path / 'manifest.csv'
    with transport.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest()[0]))
        writer.writeheader()
        writer.writerows(manifest())
    arguments = []
    for name in ('readme', 'index', 'version'):
        path = tmp_path / (name + '.md')
        path.write_text(name + ' historical body\n')
        arguments.extend(['--' + name, str(path)])
    result = subprocess.run(
        [sys.executable, str(root / 'scripts/generate_context_card.py'),
         '--registry', str(reg), '--manifest', str(transport),
         '--release-id', 'v1.5.4-test', '--code-commit', SHA,
         '--output', str(tmp_path / 'output'), *arguments],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'CANDIDATE_NOT_PUBLISHED' in result.stdout
    assert (tmp_path / 'output/README.md').read_text().endswith('readme historical body\n')
    assert (tmp_path / 'readme.md').read_text() == 'readme historical body\n'
    card = (tmp_path / 'output/CONTEXT_CARD.md').read_text()
    assert len(card.encode('utf-8')) <= 4096
