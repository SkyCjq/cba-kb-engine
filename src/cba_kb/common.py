import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode())


def read(path):
    return json.loads(Path(path).read_text())


def child(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Path escapes working directory')
    return path


@contextlib.contextmanager
def lock(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another local run holds the lock') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
