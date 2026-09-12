"""Create a reviewable deployment inventory from caller-owned mappings."""
import mimetypes
import subprocess
from pathlib import Path
from .common import digest,save,read
from .instance import require_mapping
from .native import wrap

DOC='application/vnd.google-apps.document'
LEGACY_MANAGED_DOCS = frozenset({
    'INDEX.md',
    'CBA_注册_2024-2025.md',
    'CBA_注册_2025-2026.md',
    'CBA_注册_2026-2027.md',
})


def _private_catalog(root, instance):
    if instance is None:
        path = root/'docs/import_inventory.json'
        if not path.is_file():
            raise RuntimeError('PRIVATE_CATALOG_MAPPING_REQUIRED')
        rows = read(path)
        if not isinstance(rows, list):
            raise RuntimeError('PRIVATE_CATALOG_MAPPING_REQUIRED')
        inventory = {}
        for entry in rows:
            require_mapping(entry, ('name', 'id', 'mime'), label='import_inventory.artifact')
            if entry['name'] in inventory:
                raise RuntimeError('PRIVATE_CATALOG_DUPLICATE_NAME')
            inventory[entry['name']] = entry
        parents = {name: f'legacy-{name}' for name in ('ai', 'scripts', 'config', 'archive')}
        return parents, {name: None for name in LEGACY_MANAGED_DOCS}, inventory
    config = instance.read_json('import_inventory.json')
    if not isinstance(config, dict) or any(
            field not in config for field in ('parents', 'existing_docs', 'artifacts')):
        raise RuntimeError('PRIVATE_CATALOG_MAPPING_REQUIRED: import_inventory.json')
    parents = require_mapping(
        config['parents'], ('ai', 'scripts', 'config', 'archive'),
        label='import_inventory.parents',
    )
    if not isinstance(config['existing_docs'], dict) or not isinstance(config['artifacts'], list):
        raise RuntimeError('PRIVATE_CATALOG_MAPPING_INVALID')
    inventory = {}
    for entry in config['artifacts']:
        require_mapping(entry, ('name', 'id', 'mime'), label='import_inventory.artifact')
        if entry['name'] in inventory:
            raise RuntimeError('PRIVATE_CATALOG_DUPLICATE_NAME')
        inventory[entry['name']] = entry
    for name, file_id in config['existing_docs'].items():
        if not name or not file_id:
            raise RuntimeError('PRIVATE_EXISTING_DOC_MAPPING_REQUIRED')
    return parents, config['existing_docs'], inventory


def catalog(root,candidate,output,instance=None):
    root,candidate,output=map(Path,(root,candidate,output))
    if output.exists():raise ValueError('Catalog output must be a new directory')
    if subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip():
        raise ValueError('Commit working tree before freezing a catalog')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    parents,existing_docs,inventory=_private_catalog(root,instance)
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    required = {
        Path(relative).name
        for relative in filter(None, tracked)
        if relative.startswith('legacy/') and Path(relative).name!='Makefile'
    } | ({'Makefile'} if 'Makefile' in tracked else set())
    missing = sorted(required - inventory.keys())
    if missing:
        raise RuntimeError('PRIVATE_CATALOG_MAPPING_REQUIRED: '+','.join(missing))
    output.mkdir(parents=True)
    result=[]
    for path in sorted(candidate.iterdir()):
        if not path.is_file():continue
        target=parents['ai']
        if path.name=='validation.json':target=parents['archive']
        if path.name=='provenance.json':target=parents['config']
        native=path.name in existing_docs
        content=wrap(path.read_text()).encode() if native else path.read_bytes()
        local=output/path.name
        local.write_bytes(content)
        result.append({'logical_key':'ai/'+path.name,'name':path.name.removesuffix('.md') if native else path.name,
            'id':existing_docs.get(path.name),'mode':'managed_doc' if native else 'binary',
            'mime':DOC if native else (mimetypes.guess_type(path.name)[0] or 'application/octet-stream'),
            'parent_id':target,'path':str(local.resolve()),'sha256':digest(content)})
    for relative in filter(None,tracked):
        path=root/relative
        if not path.is_file():raise ValueError('Tracked file missing')
        if relative.startswith(('workspace/','.credentials/','.venv/')):raise ValueError('Forbidden code mirror path')
        data=path.read_bytes()
        frozen=output/'code'/relative
        frozen.parent.mkdir(parents=True,exist_ok=True)
        frozen.write_bytes(data)
        existing=inventory.get(path.name) if relative.startswith('legacy/') and path.name!='Makefile' else None
        if relative=='Makefile':existing=inventory.get('Makefile')
        required_entry = relative=='Makefile' or (
            relative.startswith('legacy/') and path.name!='Makefile'
        )
        if required_entry and existing is None:
            raise RuntimeError('PRIVATE_CATALOG_MAPPING_REQUIRED: '+relative)
        result.append({'logical_key':'code/'+relative,'name':existing['name'] if existing else relative.replace('/','__'),
            'id':existing['id'] if existing else None,'mode':'binary',
            'mime':existing['mime'] if existing else (mimetypes.guess_type(path.name)[0] or 'text/plain'),
            'parent_id':parents['scripts'],'path':str(frozen.resolve()),'sha256':digest(data),
            'note':'Resolve stable Drive ID from import inventory or assigned IDs before publication'})
    save(output/'artifact_catalog.json',{'state':'DRAFT_NOT_PUBLISHED','code_commit':commit,'artifacts':result})
    return result
