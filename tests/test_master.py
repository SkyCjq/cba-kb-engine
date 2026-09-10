import json
from pathlib import Path
import pytest
from cba_kb.master import inspect,build
from cba_kb.common import digest

MASTER=Path('workspace/inputs/bootstrap/MASTER.xlsx')


@pytest.mark.skipif(not MASTER.exists(),reason='Production data is intentionally not in Git; run make pull first')
def test_live_baseline_and_deterministic_exports(tmp_path):
    rows,s=inspect(MASTER)
    baseline=json.loads(Path('docs/baseline_2026-09-10.json').read_text())
    assert s['semantic_sha256']==baseline['semantic_sha256']
    for target in ['one','two']:build(MASTER,tmp_path/target,'test','abc','2026-09-10T00:00:00Z')
    a={p.name:digest(p.read_bytes()) for p in (tmp_path/'one').iterdir()}
    b={p.name:digest(p.read_bytes()) for p in (tmp_path/'two').iterdir()}
    assert a==b
    actual=[json.loads(line) for line in (tmp_path/'one/MASTER.jsonl').read_text().splitlines()]
    assert actual==rows
    assert s['rows']==3451 and s['unique_keys']==3451
    assert len(a)==15
