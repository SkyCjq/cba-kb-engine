import json
from pathlib import Path
import subprocess

import pytest

from cba_kb.catalog import catalog


class Instance:
    def __init__(self, mapping):
        self.mapping = mapping

    def read_json(self, name):
        assert name == 'import_inventory.json'
        return self.mapping


def repo(tmp_path):
    root = tmp_path/'repo'; root.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
    (root/'Makefile').write_text('test:\n\ttrue\n')
    subprocess.run(['git', 'add', 'Makefile'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)
    candidate = tmp_path/'candidate'; candidate.mkdir()
    (candidate/'result.json').write_text('{}')
    return root, candidate


def mapping():
    return {
        'parents': {'ai': 'private-ai', 'scripts': 'private-scripts',
                    'config': 'private-config', 'archive': 'private-archive'},
        'existing_docs': {},
        'artifacts': [{'name': 'Makefile', 'id': 'private-makefile', 'mime': 'text/plain'}],
    }


def test_catalog_requires_private_mapping_before_writing(tmp_path):
    root, candidate = repo(tmp_path)
    with pytest.raises(RuntimeError, match='PRIVATE_CATALOG_MAPPING_REQUIRED'):
        catalog(root, candidate, tmp_path/'missing')
    assert not (tmp_path/'missing').exists()


def test_catalog_uses_private_inventory_and_public_schema_has_no_ids(tmp_path):
    root, candidate = repo(tmp_path)
    output = tmp_path/'output'
    artifacts = catalog(root, candidate, output, Instance(mapping()))
    make = next(item for item in artifacts if item['logical_key'] == 'code/Makefile')
    assert make['id'] == 'private-makefile'
    assert make['parent_id'] == 'private-scripts'
    schema = json.loads((Path(__file__).resolve().parents[1]/'docs/import_inventory.json').read_text())
    assert schema['visibility'] == 'public_safe_example'
