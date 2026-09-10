import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .common import atomic, digest, read, save
from .master import inspect, build
from .drive import Drive, credentials
from .release import snapshot, prepare, publish, verify, restore


def main():
    p=argparse.ArgumentParser(prog='cba-kb')
    p.add_argument('--root',type=Path,default=Path.cwd())
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('auth'); sub.add_parser('doctor')
    for cmd in ('validate','build'):
        q=sub.add_parser(cmd); q.add_argument('--master',type=Path,required=True)
        if cmd=='build':
            q.add_argument('--output',type=Path,required=True); q.add_argument('--release-id',required=True)
            q.add_argument('--generated-at',default=None)
    q=sub.add_parser('pull'); q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('catalog');q.add_argument('--candidate',type=Path,required=True);q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('plan'); q.add_argument('--entries',type=Path,required=True); q.add_argument('--release-id',required=True)
    q.add_argument('--status-id',required=True); q.add_argument('--archive-id',required=True)
    for cmd in ('publish','verify','restore'):
        q=sub.add_parser(cmd); q.add_argument('--release',type=Path,required=True)
        if cmd!='verify':q.add_argument('--single-writer',action='store_true')
    a=p.parse_args(); root=a.root.resolve()
    try:
        if a.command=='auth':
            credentials(root,interactive=True); result={'authorization':'complete'}
        elif a.command=='doctor':
            result={'python':__import__('sys').version.split()[0], 'root':str(root),
                    'oauth_client_present':(root/'.credentials/credentials.json').exists(),
                    'oauth_token_present':(root/'.credentials/token.json').exists(),
                    'production_enabled':False,
                    'note':'Production requires local OAuth sandbox exercise, complete catalog, and AI acceptance.'}
        elif a.command=='validate':
            _,result=inspect(a.master)
        elif a.command=='build':
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            changes=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
            if changes: raise RuntimeError('Commit working tree before producing a release candidate')
            result=build(a.master,a.output,a.release_id,commit,
                a.generated_at or datetime.now(timezone.utc).isoformat())
        elif a.command=='pull':
            if a.output.exists():raise ValueError('Choose new output directory; inputs are immutable')
            drive=Drive(root); mapping=read(root/'config/runtime.json'); result={}
            a.output.mkdir(parents=True)
            for name,item in mapping['inputs'].items():
                data,meta=snapshot(drive,item['id'])
                if meta['mimeType']!=item['mime']: raise ValueError('Input MIME mismatch')
                atomic(a.output/name,data); result[name]={'metadata':meta,'sha256':digest(data)}
            save(a.output/'snapshot.json',result)
        elif a.command=='catalog':
            from .catalog import catalog
            artifacts=catalog(root,a.candidate,a.output)
            result={'artifacts':len(artifacts),'unassigned_ids':sum(not e['id'] for e in artifacts),
                    'catalog':str(a.output/'artifact_catalog.json')}
        elif a.command=='plan':
            entries=read(a.entries)
            # Initial deployment is restricted to a sandbox folder, configured after OAuth.
            mapping=read(root/'config/runtime.json'); sandbox=mapping.get('sandbox_folder_id')
            if not sandbox:raise RuntimeError('Sandbox folder must be configured before planning writes')
            drive=Drive(root)
            for fid in [e['id'] for e in entries]+[a.status_id,a.archive_id]:
                meta=drive.meta(fid)
                if sandbox not in meta.get('parents',[]):raise RuntimeError('Only sandbox targets currently enabled')
            result=prepare(drive,root/'workspace/outbox'/a.release_id,a.release_id,entries,a.archive_id,a.status_id)
        else:
            drive=Drive(root)
            # Do not bypass the sandbox gate with a hand-written plan.
            plan=read(a.release/'plan.json'); sandbox=read(root/'config/runtime.json').get('sandbox_folder_id')
            for fid in [e['id'] for e in plan['entries']]+[plan['status_id'],plan['archive_id']]:
                if not sandbox or sandbox not in drive.meta(fid).get('parents',[]):raise RuntimeError('Sandbox targets required')
            fn={'publish':publish,'verify':verify,'restore':restore}[a.command]
            result=fn(drive,a.release,**({'single_writer':a.single_writer} if a.command!='verify' else {}))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        # OAuth/HTTP errors may contain sensitive parameters: never log raw transport errors.
        if isinstance(exc,(ValueError,RuntimeError)):p.exit(1,str(exc)+'\n')
        p.exit(1,f'{type(exc).__name__}: operation failed; check credentials/network and retry.\n')


if __name__=='__main__': main()
