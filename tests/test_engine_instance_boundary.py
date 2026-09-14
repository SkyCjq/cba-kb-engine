import json
from pathlib import Path
import re

import pytest

from cba_kb.instance import load_instance, require_mapping


ROOT = Path(__file__).resolve().parents[1]
DRIVE_ID = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")


def test_public_engine_config_and_catalog_have_no_real_drive_ids():
    paths = [
        'config/runtime.json', 'config/production.json', 'config/sandbox.json',
        'config/drive_map.yaml', 'config/v1.5.2_registry_proposal.json',
        'config/v1.5.2_registry_status.json', 'docs/import_inventory.json',
        'src/cba_kb/catalog.py', 'src/cba_kb/source_policy.py',
        'src/cba_kb/consumer_projection.py', 'src/cba_kb/evidence_ledger.py',
        'src/cba_kb/operational_qualification.py',
        'config/consumer_golden_questions_v1.yaml',
        'scripts/prepare_production.py', 'scripts/prepare_v1_5_3_production.py',
    ]
    findings = {path: DRIVE_ID.findall((ROOT/path).read_text()) for path in paths}
    assert {path: ids for path, ids in findings.items() if ids} == {}


def test_instance_is_explicit_external_and_fail_closed(tmp_path):
    engine = tmp_path/'engine'; engine.mkdir()
    with pytest.raises(RuntimeError, match='PRIVATE_INSTANCE_REQUIRED'):
        load_instance(engine, environ={})
    nested = engine/'instance'; (nested/'config').mkdir(parents=True)
    with pytest.raises(RuntimeError, match='OUTSIDE_ENGINE'):
        load_instance(engine, nested)
    external = tmp_path/'instance'; (external/'config').mkdir(parents=True)
    instance = load_instance(engine, external)
    assert instance.root == external.resolve()
    assert instance.document_input_root == (external/'inbox/documents').resolve()
    assert instance.document_archive_root == (
        external/'data/document_lane/archive'
    ).resolve()
    assert instance.document_review_root == (
        external/'data/document_lane/review'
    ).resolve()
    assert instance.document_report_root == (
        external/'data/document_lane/reports'
    ).resolve()
    with pytest.raises(RuntimeError, match='CONFIG_REQUIRED'):
        instance.read_json('runtime.json')


def test_private_mapping_requires_every_frozen_field():
    with pytest.raises(RuntimeError, match='PRIVATE_MAPPING_REQUIRED'):
        require_mapping({'parents': {}}, ('parents', 'artifacts'), label='catalog')
