import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from .common import atomic, digest, read, save
from .master import inspect, build
from .drive import Drive, credentials
from .release import snapshot, prepare, publish, verify, restore
from .transport import stage
from .instance import load_instance


def main():
    p=argparse.ArgumentParser(prog='cba-kb')
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--instance-root',type=Path)
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
    q=sub.add_parser('domain')
    q.add_argument('--domestic-source',type=Path,required=True)
    q.add_argument('--rights-source',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--domestic-source-id',required=True)
    q.add_argument('--rights-source-id',required=True)
    q.add_argument('--lenient-clubs',action='store_true')
    q=sub.add_parser('catalog');q.add_argument('--candidate',type=Path,required=True);q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('watch');q.add_argument('--output',type=Path,required=True);q.add_argument('--previous-root',type=Path)
    q=sub.add_parser('document-ingest')
    q.add_argument('--capture-channel',choices=(
        'wechat_browser_clip','ima_file_export','local_file','google_drive_doc'))
    q.add_argument('--source',type=Path)
    q.add_argument('--drive-id')
    q.add_argument('--captured-at')
    q.add_argument('--published-at')
    q.add_argument('--canonical-url')
    q.add_argument('--title')
    q.add_argument('--tag',action='append')
    q.add_argument('--rights-classification',choices=(
        'public','copyrighted','private','unknown'))
    q.add_argument('--public-export-allowed',action='store_true',default=None)
    q.add_argument('--rights-evidence',action='append')
    q.add_argument('--latency-seconds',type=float)
    q=sub.add_parser('document-batch')
    q.add_argument('--manifest',type=Path,required=True)
    q.add_argument('--captured-at')
    q.add_argument('--latency-seconds',type=float)
    q=sub.add_parser('plan'); q.add_argument('--entries',type=Path,required=True); q.add_argument('--release-id',required=True)
    q.add_argument('--status-id',required=True); q.add_argument('--archive-id',required=True)
    q.add_argument('--dependencies',type=Path);q.add_argument('--environment',choices=['sandbox','production'],default='sandbox')
    q.add_argument('--carry-forward-artifacts',action='store_true')
    q.add_argument('--closure',type=Path,help='Frozen canonical/security closure JSON (required for v1.5.4)')
    for cmd in ('publish','verify','restore'):
        q=sub.add_parser(cmd); q.add_argument('--release',type=Path,required=True)
        if cmd!='verify':q.add_argument('--single-writer',action='store_true')
    a=p.parse_args(); root=a.root.resolve()
    def private_instance():
        return load_instance(root,a.instance_root)
    stage(a.command,'start')
    try:
        if a.command=='auth':
            instance=private_instance()
            credentials(root,interactive=True,credentials_store=instance.credentials_store)
            result={'authorization':'complete'}
        elif a.command=='doctor':
            from . import __version__
            configured=a.instance_root is not None or bool(os.environ.get('CBA_KB_INSTANCE_ROOT'))
            instance=private_instance() if configured else None
            runtime=instance.read_json('runtime.json') if instance else {}
            production=instance.read_json('production.json') if instance else {}
            result={'engine_version':__version__,'python':__import__('sys').version.split()[0], 'root':str(root),
                    'instance_configured':instance is not None,
                    'oauth_client_present':bool(instance and (instance.credentials_store/'credentials.json').exists()),
                    'oauth_token_present':bool(instance and (instance.credentials_store/'token.json').exists()),
                    'production_enabled':production.get('enabled') is True,
                    'sandbox_folder_id':runtime.get('sandbox_folder_id'),
                    'fact_products':['MASTER.xlsx','CBA_外籍球员注册_SNAPSHOTS.xlsx','CBA_球员注册_EVENTS.xlsx'],
                    'note':'Production target allowlist is enforced from Private Instance config/production.json.'
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
            instance=private_instance();drive=Drive(root,instance);mapping=instance.read_json('runtime.json');result={}
            a.output.mkdir(parents=True)
            for name,item in mapping['inputs'].items():
                data,meta=snapshot(drive,item['id'])
                stage('pull',f'{name} {meta["id"]} {meta.get("version")}')
                if meta['mimeType']!=item['mime']: raise ValueError('Input MIME mismatch')
                atomic(a.output/name,data); result[name]={'metadata':meta,'sha256':digest(data)}
                save(a.output/'snapshot.json',result)
        elif a.command=='catalog':
            from .catalog import catalog
            artifacts=catalog(root,a.candidate,a.output,private_instance())
            result={'artifacts':len(artifacts),'unassigned_ids':sum(not e['id'] for e in artifacts),
                    'catalog':str(a.output/'artifact_catalog.json')}
        elif a.command=='watch':
            from .source_watcher import run_planned
            instance=private_instance()
            result=run_planned(instance,a.output,previous_root=a.previous_root)
        elif a.command in {'document-ingest','document-batch'}:
            from .document_lane import DocumentLane
            if a.command=='document-ingest' and not a.capture_channel:
                raise ValueError('DOCUMENT_CAPTURE_CHANNEL_REQUIRED')
            instance=private_instance()
            lane=DocumentLane(root,instance)
            started=time.perf_counter()
            options={
                'captured_at':a.captured_at,
                'latency_seconds':a.latency_seconds,
            }
            if a.command=='document-batch':
                options['latency_seconds'] = (
                    a.latency_seconds
                    if a.latency_seconds is not None
                    else time.perf_counter()-started
                )
                result=lane.ingest_batch(
                    a.manifest,
                    drive_factory=lambda: Drive(root,instance),
                    **options,
                )
            else:
                if bool(a.source) == bool(a.drive_id):
                    raise ValueError('DOCUMENT_EXACTLY_ONE_SOURCE_REQUIRED')
                if a.source and a.capture_channel not in {
                    'wechat_browser_clip','ima_file_export','local_file',
                }:
                    raise ValueError('DOCUMENT_FILE_CHANNEL_REQUIRED')
                if a.drive_id and a.capture_channel != 'google_drive_doc':
                    raise ValueError('DOCUMENT_DRIVE_CHANNEL_REQUIRED')
                options.update({
                    'capture_channel':a.capture_channel,
                    'published_at':a.published_at,
                    'canonical_url':a.canonical_url,
                    'title':a.title,
                    'tags':a.tag,
                    'rights_classification':a.rights_classification,
                    'public_export_allowed':a.public_export_allowed,
                    'rights_evidence':a.rights_evidence,
                })
                if options['latency_seconds'] is None:
                    options['latency_seconds']=time.perf_counter()-started
                if a.drive_id:
                    result=lane.ingest_drive_document(
                        Drive(root,instance),a.drive_id,**options,
                    )
                else:
                    result=lane.ingest_file(a.source,**options)
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
        elif a.command=='domain':
            from .aliases import Clubs
            from .domain_xlsx import write as write_domain
            from .registration_domain import parse_domestic_movement, parse_foreign_rights, validate_domain
            if a.output.exists() or a.output.with_suffix('.validation.json').exists():
                raise ValueError('Choose a new candidate workbook path')
            clubs=Clubs(root, strict=not a.lenient_clubs)
            domestic_source={'id':a.domestic_source_id,
                             'url':f'https://drive.google.com/file/d/{a.domestic_source_id}/view',
                             'type':'gdrive'}
            rights_source={'id':a.rights_source_id,
                           'url':f'https://drive.google.com/file/d/{a.rights_source_id}/view',
                           'type':'gdrive'}
            records={}
            records.update(parse_domestic_movement(a.domestic_source.read_text(),domestic_source,clubs))
            foreign_records=parse_foreign_rights(a.rights_source.read_text(),rights_source,clubs)
            for key,value in foreign_records.items():
                if key == 'report':
                    continue
                records.setdefault(key,[]).extend(value)
            summary=validate_domain(records)
            write_domain(a.output,records)
            result={'candidate_workbook':str(a.output),'summary':summary,
                    'status':'CANDIDATE_PARTIAL','production_eligible':False,
                    'unresolved_clubs':clubs.report()['unresolved'],
                    'pending_sources':['2020-2021 foreign PNG','2022-2023 foreign PNG'],
                    'remaining_scope':['Complete window coverage and domestic correction lifecycle',
                                       'Bayi detail enrichment and baseline domestic MASTER merge',
                                       '2023-2024 media snapshot; foreign usage activation and cancellation integration',
                                       'Native Sheet authoritative read/build/publish integration'],
                    'parser_reports':{'domestic':records.get('report'), 'foreign':foreign_records.get('report')}}
            save(a.output.with_suffix('.validation.json'),result)
        elif a.command=='plan':
            from .current_state import clean
            def read_plan_input(path):
                data=path.read_bytes()
                clean(data,str(path))
                return json.loads(data)
            entries=read_plan_input(a.entries)
            dependencies=read_plan_input(a.dependencies) if a.dependencies else []
            closure=read_plan_input(a.closure) if a.closure else None
            if a.release_id.startswith('v1.5.4') and closure is None:
                raise ValueError('CANONICAL_CLOSURE_REQUIRED')
            if a.closure and not isinstance(closure,dict):
                raise ValueError('CLOSURE_CONTRACT_REQUIRED')
            if not isinstance(entries,list) or not entries or not all(isinstance(entry,dict) for entry in entries):
                raise ValueError('Release entries must be a nonempty list of objects')
            for entry in entries:
                clean(Path(entry['path']).read_bytes(),entry['name'])
            instance=private_instance();drive=Drive(root,instance)
            from .gates import authorize_plan
            request={'entries':entries,'status_id':a.status_id,'archive_id':a.archive_id,'dependencies':dependencies}
            authorize_plan(drive,root,request,a.environment,instance)
            result=prepare(
                drive,
                root/'workspace/outbox'/a.release_id,
                a.release_id,
                entries,
                a.archive_id,
                a.status_id,
                dependencies,
                carry_forward_artifacts=a.carry_forward_artifacts,
                closure=closure,
                environment=a.environment,
            )
        else:
            instance=private_instance();drive=Drive(root,instance)
            plan=read(a.release/'plan.json')
            from .gates import authorize_plan
            authorize_plan(drive,root,plan,plan.get('environment','sandbox'),instance)
            fn={'publish':publish,'verify':verify,'restore':restore}[a.command]
            result=fn(drive,a.release,**({'single_writer':a.single_writer} if a.command!='verify' else {}))
        stage(a.command,'done')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        # OAuth/HTTP errors may contain sensitive parameters: never log raw transport errors.
        if isinstance(exc,(ValueError,RuntimeError)):p.exit(1,str(exc)+'\n')
        p.exit(1,f'{type(exc).__name__}: operation failed; check credentials/network and retry.\n')


if __name__=='__main__': main()
