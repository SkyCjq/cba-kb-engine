"""Source adapters: probe a source, extract records, emit staging.

Adapters never write to Drive and never touch production data. Each adapter
reads one registered source and returns grain-separated records plus a QA report.
"""
from . import cba_registration, foreign_image, foreign_xlsx, midseason_md

ADAPTERS = {'midseason_md': midseason_md, 'foreign_xlsx': foreign_xlsx,
            'foreign_image': foreign_image}
WATCHER_ADAPTERS = {'cba_registration': cba_registration}


def watcher_adapter_for(name):
    if name not in WATCHER_ADAPTERS:
        raise ValueError(f'Unknown watcher adapter {name!r}')
    return WATCHER_ADAPTERS[name]


def adapter_for(name=None, path=None):
    if name:
        if name not in ADAPTERS:
            raise ValueError(f'Unknown adapter {name!r}')
        return ADAPTERS[name]
    if path is None:
        raise ValueError('Adapter or source path required')
    return ADAPTERS[probe(path)]


def probe(path):
    suffix = str(path).lower().rsplit('.', 1)[-1]
    if suffix == 'md':
        return 'midseason_md'
    if suffix in ('xlsx', 'xlsm'):
        return 'foreign_xlsx'
    if suffix in ('png', 'jpg', 'jpeg', 'pdf'):
        return 'foreign_image'
    if suffix in ('json', 'jsonl', 'csv'):
        return 'foreign_image'
    raise ValueError(f'No adapter claims {path}')
