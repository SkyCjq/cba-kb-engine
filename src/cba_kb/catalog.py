"""Create a reviewable deployment artifact inventory, without remote writes."""
import mimetypes
import subprocess
from pathlib import Path
from .common import digest,save,read
from .native import wrap

AI='1y-JlN327UARvZ4A8QN0EDDxg_0oggiUp'
SCRIPTS='1oaJW9FDUjmIbFDafpt6aqtlvvzqQYhdC'
CONFIG='1ccYmxy8NKt79pgyhDnyHUE14C6_wsn83'
ARCHIVE='1GjDMkAYs7JpIrI9_kQduxlqygZmxeb9M'
DOC='application/vnd.google-apps.document'
EXISTING_DOCS={
    'INDEX.md':'1VqnFtMRSlOV9K7eVCAJKQ5HngWMl60MibbopNclxN9c',
    'CBA_注册_2024-2025.md':'1FES4xgC_qZh2pdrjyWvlQdMLQYjNtBht4fAZdF197BI',
    'CBA_注册_2025-2026.md':'1rDxJzgbBQspuiM21-ZvPeVllccyPqRMj0jLJs7OZy3U',
    'CBA_注册_2026-2027.md':'13LUNwVgVhtWSIz1xOdYmsGzH6PrNszWaTz15Pi8Fnag'}


def catalog(root,candidate,output):
    root,candidate,output=map(Path,(root,candidate,output))
    if output.exists():raise ValueError('Catalog output must be a new directory')
    if subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip():
        raise ValueError('Commit working tree before freezing a catalog')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    inventory={entry['name']:entry for entry in read(root/'docs/import_inventory.json')}
    output.mkdir(parents=True)
    result=[]
    for path in sorted(candidate.iterdir()):
        if not path.is_file():continue
        target=AI
        if path.name=='validation.json':target=ARCHIVE
        if path.name=='provenance.json':target=CONFIG
        native=path.name in EXISTING_DOCS
        content=wrap(path.read_text()).encode() if native else path.read_bytes()
        local=output/path.name
        local.write_bytes(content)
        result.append({'logical_key':'ai/'+path.name,'name':path.name.removesuffix('.md') if native else path.name,
            'id':EXISTING_DOCS.get(path.name),'mode':'managed_doc' if native else 'binary',
            'mime':DOC if native else (mimetypes.guess_type(path.name)[0] or 'application/octet-stream'),
            'parent_id':target,'path':str(local.resolve()),'sha256':digest(content)})
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
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
        result.append({'logical_key':'code/'+relative,'name':existing['name'] if existing else relative.replace('/','__'),
            'id':existing['id'] if existing else None,'mode':'binary',
            'mime':existing['mime'] if existing else (mimetypes.guess_type(path.name)[0] or 'text/plain'),
            'parent_id':SCRIPTS,'path':str(frozen.resolve()),'sha256':digest(data),
            'note':'Resolve stable Drive ID from import inventory or assigned IDs before publication'})
    save(output/'artifact_catalog.json',{'state':'DRAFT_NOT_PUBLISHED','code_commit':commit,'artifacts':result})
    return result
