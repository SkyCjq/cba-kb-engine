from cba_kb.common import child,lock
import pytest

def test_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError):child(tmp_path,'../escape')

def test_concurrent_lock_rejected(tmp_path):
    with lock(tmp_path/'lock'):
        with pytest.raises(RuntimeError):
            with lock(tmp_path/'lock'):pass
