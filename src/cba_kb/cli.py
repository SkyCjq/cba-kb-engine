import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .common import atomic, digest, read, save
from .master import inspect, build
from .drive import Drive, credentials
from .release import snapshot, prepare, publish, verify, restore
from .transport import stage


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
    q=sub.add_parser('extract')
    q.add_argument('--source',type=Path,required=True)
    q.add_argument('--season',required=True)
    q.add_argument('--adapter')
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--source-id'); q.add_argument('--source-url')
    q.add_argument('--source-type',default='gdrive')
    q.add_argument('--title'); q.add_argument('--page')
    q.add_argument('--lenient-clubs',action='store_true')
    q=sub.add_parser('facts')
    q.add_argument('--staging',type=Path,action='append',required=True)
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--release-id',required=True)
    q.add_argument('--generated-at',default=None)
    q=sub.add_parser('catalog');q.add_argument('--candidate',type=Path,required=True);q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('plan'); q.add_argument('--entries',type=Path,required=True); q.add_argument('--release-id',required=True)
    q.add_argument('--status-id',required=True); q.add_argument('--archive-id',required=True)
    q.add_argument('--dependencies',type=Path);q.add_argument('--environment',choices=['sandbox','production'],default='sandbox')
    for cmd in ('publish','verify','restore'):
        q=sub.add_parser(cmd); q.add_argument('--release',type=Path,required=True)
        if cmd!='verify':q.add_argument('--single-writer',action='store_true')
    a=p.parse_args(); root=a.root.resolve()
    stage(a.command,'start')
    try:
        if a.command=='auth':
            credentials(root,interactive=True); result={'authorization':'complete'}
        elif a.command=='doctor':
            from . import __version__
            runtime=read(root/'config/runtime.json') if (root/'config/runtime.json').exists() else {}
            production=read(root/'config/production.json') if (root/'config/production.json').exists() else {}
            result={'engine_version':__version__,'python':__import__('sys').version.split()[0], 'root':str(root),
                    'deployment_root':runtime.get('deployment_root'),
                    'oauth_client_present':(root/'.credentials/credentials.json').exists(),
                    'oauth_token_present':(root/'.credentials/token.json').exists(),
                    'production_enabled':production.get('enabled') is True,
                    'sandbox_folder_id':runtime.get('sandbox_folder_id'),
                    'fact_products':['MASTER.xlsx','CBA_外籍球员注册_SNAPSHOTS.xlsx','CBA_球员注册_EVENTS.xlsx'],
                    'note':'Production target allowlist is enforced from config/production.json.'
                        if production.get('enabled') is True else
                        'Production writes stay disabled until config/production.json is enabled for the complete approved target set.'}
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
                stage('pull',f'{name} {meta["id"]} {meta.get("version")}')
                if meta['mimeType']!=item['mime']: raise ValueError('Input MIME mismatch')
                atomic(a.output/name,data); result[name]={'metadata':meta,'sha256':digest(data)}
                save(a.output/'snapshot.json',result)
        elif a.command=='catalog':
            from .catalog import catalog
            artifacts=catalog(root,a.candidate,a.output)
            result={'artifacts':len(artifacts),'unassigned_ids':sum(not e['id'] for e in artifacts),
                    'catalog':str(a.output/'artifact_catalog.json')}
        elif a.command=='extract':
            from . import facts as definitions
            from .adapters import adapter_for
            from .aliases import Clubs
            if a.output.exists():raise ValueError('Choose a new staging directory')
            module=adapter_for(a.adapter,a.source)
            source={'id':a.source_id,'type':a.source_type,'page':a.page,
                    'url':a.source_url or (f'https://drive.google.com/file/d/{a.source_id}/view' if a.source_id else None)}
            clubs=Clubs(root,strict=not a.lenient_clubs)
            options={'title':a.title} if module.__name__.endswith('foreign_image') else {}
            records=module.extract(a.source,a.season,source,clubs=clubs,strict=not a.lenient_clubs,**options)
            summary={}
            for name,check in (('domestic',definitions.validate_domestic),
                               ('snapshots',definitions.validate_snapshots),
                               ('events',definitions.validate_events)):
                if records.get(name):summary[name]=check(records[name])
            a.output.mkdir(parents=True)
            save(a.output/'records.json',{'adapter':module.__name__.rsplit('.',1)[-1],'season':a.season,
                                          'source':source,'records':records,'report':records.get('report'),
                                          'summary':summary,'clubs':clubs.report()})
            result={'adapter':module.__name__.rsplit('.',1)[-1],'summary':summary,
                    'report':records.get('report'),'unresolved_clubs':clubs.report()['unresolved'],
                    'staging':str(a.output/'records.json')}
        elif a.command=='facts':
            from .build_facts import build as build_products, collect
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            changes=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
            if changes: raise RuntimeError('Commit working tree before producing a release candidate')
            baseline_rows,baseline_summary=inspect(a.master)
            result=build_products(collect(a.staging),baseline_rows,baseline_summary,a.output,a.release_id,
                commit,a.generated_at or datetime.now(timezone.utc).isoformat())
        elif a.command=='plan':
            entries=read(a.entries)
            dependencies=read(a.dependencies) if a.dependencies else []
            drive=Drive(root)
            from .gates import authorize_plan
            request={'entries':entries,'status_id':a.status_id,'archive_id':a.archive_id,'dependencies':dependencies}
            authorize_plan(drive,root,request,a.environment)
            result=prepare(drive,root/'workspace/outbox'/a.release_id,a.release_id,entries,a.archive_id,a.status_id,dependencies)
            result['environment']=a.environment
            save(root/'workspace/outbox'/a.release_id/'plan.json',result)
        else:
            drive=Drive(root)
            plan=read(a.release/'plan.json')
            from .gates import authorize_plan
            authorize_plan(drive,root,plan,plan.get('environment','sandbox'))
            fn={'publish':publish,'verify':verify,'restore':restore}[a.command]
            result=fn(drive,a.release,**({'single_writer':a.single_writer} if a.command!='verify' else {}))
        stage(a.command,'done')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        # OAuth/HTTP errors may contain sensitive parameters: never log raw transport errors.
        if isinstance(exc,(ValueError,RuntimeError)):p.exit(1,str(exc)+'\n')
        p.exit(1,f'{type(exc).__name__}: operation failed; check credentials/network and retry.\n')


if __name__=='__main__': main()
