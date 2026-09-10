import json
import subprocess
import pytest
from cba_kb.catalog import catalog


def test_catalog_freezes_code_and_preserves_imported_ids(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):
        return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
    git('init')
    (root/'docs').mkdir();(root/'legacy').mkdir()
    (root/'docs/import_inventory.json').write_text(json.dumps([
        dict(name='Makefile',id='make-id',mime='text/plain'),
        dict(name='fetch.py',id='fetch-id',mime='text/x-python')]))
    (root/'Makefile').write_text('new')
    (root/'legacy/Makefile').write_text('old')
    (root/'legacy/fetch.py').write_text('original')
    git('add','.')
    git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
    candidate=tmp_path/'candidate';candidate.mkdir()
    (candidate/'INDEX.md').write_text('hello')
    entries=catalog(root,candidate,tmp_path/'catalog')
    mapped={e['logical_key']:e for e in entries}
    assert mapped['code/Makefile']['id']=='make-id'
    assert mapped['code/legacy/Makefile']['id'] is None
    assert mapped['code/legacy/fetch.py']['id']=='fetch-id'
    assert mapped['ai/INDEX.md']['mode']=='managed_doc'
    (root/'legacy/fetch.py').write_text('changed')
    from pathlib import Path
    assert Path(mapped['code/legacy/fetch.py']['path']).read_text()=='original'
    with pytest.raises(ValueError,match='Commit'):
        catalog(root,candidate,tmp_path/'rejected')
