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
    q=sub.add_parser('consumer-validate')
    q.add_argument('--config',type=Path,required=True)
    q=sub.add_parser('consumer-project')
    q.add_argument('--input',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--release-id',required=True)
    q.add_argument('--as-of',required=True)
    q.add_argument('--provenance',required=True)
    q=sub.add_parser('consumer-profile')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--selectors',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--release-id',required=True)
    q.add_argument('--as-of',required=True)
    q.add_argument('--generator-sha')
    q.add_argument('--source-master-file-id')
    q=sub.add_parser('consumer-profile-v2')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--player-uid',required=True)
    q.add_argument('--identity-registry',type=Path,required=True)
    q.add_argument('--mention-artifact',type=Path,required=True)
    q.add_argument('--events',type=Path)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--release-id',required=True)
    q.add_argument('--as-of',required=True)
    q.add_argument('--generator-sha')
    q.add_argument('--source-master-file-id')
    q=sub.add_parser('consumer-name-candidates')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--name',required=True)
    q.add_argument('--output',type=Path)
    q=sub.add_parser('consumer-package')
    q.add_argument('--profile',type=Path,required=True)
    q.add_argument('--documents',type=Path,required=True)
    q.add_argument('--sources',type=Path,required=True)
    q.add_argument('--events',type=Path)
    q.add_argument('--authorizations',type=Path)
    q.add_argument('--authorized-evidence',type=Path)
    q.add_argument('--transport-routes',type=Path)
    q.add_argument('--limits',type=Path)
    q.add_argument('--output',type=Path,required=True)
    q.add_argument('--release-id',required=True)
    q.add_argument('--as-of',required=True)
    q.add_argument('--provenance',required=True)
    q=sub.add_parser('consumer-acceptance-validate')
    q.add_argument('--golden',type=Path,required=True)
    q.add_argument('--oracle',type=Path)
    q=sub.add_parser('consumer-acceptance-evaluate')
    q.add_argument('--golden',type=Path,required=True)
    q.add_argument('--oracle',type=Path,required=True)
    q.add_argument('--intakes',type=Path,required=True)
    q.add_argument('--matrix',type=Path,required=True)
    q.add_argument('--output',type=Path)
    q=sub.add_parser('consumer-usage-validate')
    q.add_argument('--records',type=Path,required=True)
    q.add_argument('--minimum',type=int,default=10)
    q.add_argument('--next-step-decision',choices=(
        'CONTINUE_V1_8','SPLIT_NEW_REQUIREMENT','HOLD_AND_KEEP_USING'))
    q.add_argument('--output',type=Path)
    q=sub.add_parser('identity-validate')
    q.add_argument('--input',type=Path,required=True)
    sub.add_parser('identity-read')
    q=sub.add_parser('identity-write')
    q.add_argument('--input',type=Path,required=True)
    q.add_argument('--expected-current-sha256')
    q=sub.add_parser('identity-candidates')
    q.add_argument('--name',required=True)
    q=sub.add_parser('identity-coverage-inventory')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--identity-registry',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('identity-coverage-candidates')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--identity-registry',type=Path,required=True)
    q.add_argument('--hints',type=Path)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('identity-review-prepare')
    q.add_argument('--candidates',type=Path,required=True)
    q.add_argument('--output-json',type=Path,required=True)
    q.add_argument('--output-csv',type=Path,required=True)
    q=sub.add_parser('identity-review-validate')
    q.add_argument('--packet',type=Path,required=True)
    q.add_argument('--reviewed-csv',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('identity-review-apply')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--base-registry',type=Path,required=True)
    q.add_argument('--packet',type=Path,required=True)
    q.add_argument('--reviewed-decisions',type=Path,required=True)
    q.add_argument('--output-registry',type=Path,required=True)
    q.add_argument('--output-manifest',type=Path,required=True)
    q.add_argument('--output-ledger',type=Path,required=True)
    q.add_argument('--created-at')
    q=sub.add_parser('identity-coverage-reconcile')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--ledger',type=Path,required=True)
    q.add_argument('--final-registry',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('identity-coverage-certify')
    q.add_argument('--master',type=Path,required=True)
    q.add_argument('--base-registry',type=Path,required=True)
    q.add_argument('--final-registry',type=Path,required=True)
    q.add_argument('--review-packet',type=Path,required=True)
    q.add_argument('--reviewed-decisions',type=Path,required=True)
    q.add_argument('--candidate-registry-manifest',type=Path,required=True)
    q.add_argument('--ledger',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('mention-validate')
    q.add_argument('--input',type=Path,required=True)
    q=sub.add_parser('mention-build')
    q.add_argument('--documents',type=Path,required=True)
    q.add_argument('--mentions',type=Path,required=True)
    q.add_argument('--authorizations',type=Path)
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('qualification-validate')
    q.add_argument('--ledger',type=Path,required=True)
    q=sub.add_parser('evidence-verify')
    q.add_argument('--ledger-root',type=Path,required=True)
    q.add_argument('--requirement-id',required=True)
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
        elif a.command=='consumer-validate':
            from .consumer_projection import load_golden_questions, validate_golden_questions
            result=validate_golden_questions(load_golden_questions(a.config))
        elif a.command=='consumer-project':
            from .consumer_projection import project_documents
            documents=json.loads(a.input.read_text())
            result={'documents':project_documents(documents,{
                'release_id':a.release_id,'as_of':a.as_of,
                'provenance':a.provenance,
            })}
            save(a.output,result)
        elif a.command=='consumer-profile':
            from .player_profile import (
                build_profile_from_master,
                load_record_key_selector,
            )
            selectors=load_record_key_selector(a.selectors)
            result=build_profile_from_master(
                a.master,
                record_keys=selectors,
                release_id=a.release_id,
                as_of=a.as_of,
                generator_sha=a.generator_sha,
                source_master_file_id=a.source_master_file_id,
            )
            save(a.output,result)
        elif a.command=='consumer-profile-v2':
            from .document_mentions import load_mention_artifact
            from .player_identity import load_registry
            from .player_profile import build_profile_v2
            instance=private_instance()
            instance_root=Path(instance.root).resolve()
            def private_path(path):
                candidate=Path(path)
                if not candidate.is_absolute():
                    candidate=instance_root/candidate
                candidate=candidate.resolve()
                if not candidate.is_relative_to(instance_root):
                    raise ValueError('PROFILE_V2_PATH_OUTSIDE_PRIVATE_INSTANCE')
                return candidate
            master=private_path(a.master)
            identity_registry=load_registry(
                private_path(a.identity_registry),
            )
            mention_artifact=load_mention_artifact(
                private_path(a.mention_artifact),
            )
            rows,summary=inspect(master)
            event_spec=(
                json.loads(private_path(a.events).read_text())
                if a.events else None
            )
            result=build_profile_v2(
                rows,
                player_uid=a.player_uid,
                identity_registry=identity_registry,
                mention_artifact=mention_artifact,
                release_id=a.release_id,
                as_of=a.as_of,
                source_master_sha256=summary['sha256'],
                event_spec=event_spec,
                generator_sha=a.generator_sha,
                source_master_file_id=a.source_master_file_id,
            )
            save(private_path(a.output),result)
        elif a.command=='consumer-name-candidates':
            from .player_profile import discover_exact_name
            rows,_=inspect(a.master)
            result=discover_exact_name(rows,a.name)
            if a.output:
                save(a.output,result)
        elif a.command=='consumer-package':
            from .consumer_package import (
                build_consumer_payload,
                build_target_packages,
                payload_bytes,
                write_packages,
            )
            output=a.output
            if output.exists() and any(output.iterdir()):
                raise ValueError('Choose an empty package output directory')
            profile=read(a.profile)
            documents=json.loads(a.documents.read_text())
            sources=json.loads(a.sources.read_text())
            events=json.loads(a.events.read_text()) if a.events else None
            authorizations=(
                json.loads(a.authorizations.read_text())
                if a.authorizations else []
            )
            authorized_evidence=(
                json.loads(a.authorized_evidence.read_text())
                if a.authorized_evidence else []
            )
            transport_routes=(
                json.loads(a.transport_routes.read_text())
                if a.transport_routes else {}
            )
            limits=json.loads(a.limits.read_text()) if a.limits else {}
            payload=build_consumer_payload(
                release_scope={
                    'release_id':a.release_id,
                    'as_of':a.as_of,
                    'provenance':a.provenance,
                },
                profile=profile,
                documents=documents,
                sources=sources,
                event_spec=events,
            )
            packages=build_target_packages(
                payload,
                authorizations=authorizations,
                authorized_evidence=authorized_evidence,
                transport_routes=transport_routes,
                limits=limits,
            )
            atomic(output/'canonical_consumer_payload.json',payload_bytes(payload))
            result=write_packages(packages,output/'targets')
            result['output']=str(output)
        elif a.command=='consumer-acceptance-validate':
            from .consumer_acceptance import load_golden_v2,validate_golden_v2
            golden=load_golden_v2(a.golden,a.oracle)
            oracle=json.loads(a.oracle.read_text()) if a.oracle else None
            result=validate_golden_v2(golden,oracle)
        elif a.command=='consumer-acceptance-evaluate':
            from .consumer_acceptance import (
                evaluate_consumer_acceptance,
                load_golden_v2,
            )
            golden=load_golden_v2(a.golden,a.oracle)
            oracle=json.loads(a.oracle.read_text())
            intakes=read(a.intakes)
            matrix=read(a.matrix)
            result=evaluate_consumer_acceptance(
                golden=golden,
                oracle=oracle,
                intakes=intakes,
                matrix=matrix,
            )
            if a.output:
                save(a.output,result)
        elif a.command=='consumer-usage-validate':
            from .consumer_acceptance import validate_usage_gate
            result=validate_usage_gate(
                read(a.records),
                minimum=a.minimum,
                next_step_decision=a.next_step_decision,
            )
            if a.output:
                save(a.output,result)
        elif a.command in {
            'identity-validate',
            'identity-read',
            'identity-write',
            'identity-candidates',
        }:
            from .player_identity import (
                IdentityStore,
                discover_candidates,
                identity_summary,
                load_registry,
            )
            store=IdentityStore(private_instance())
            if a.command=='identity-validate':
                result=identity_summary(load_registry(a.input))
            else:
                if a.command=='identity-read':
                    result=store.read()
                elif a.command=='identity-candidates':
                    result=discover_candidates(store.read(),a.name)
                else:
                    result=store.write(
                        load_registry(a.input),
                        expected_current_sha256=a.expected_current_sha256,
                    )
        elif a.command.startswith('identity-coverage-') or (
            a.command.startswith('identity-review-')
        ):
            from .identity_coverage import (
                apply_reviewed_decisions,
                build_candidate_registry_manifest,
                build_coverage_certificate,
                build_coverage_inventory,
                build_coverage_ledger,
                coverage_candidates,
                prepare_review_packet,
                reconcile_coverage,
                review_packet_to_csv,
                validate_reviewed_csv,
            )
            from .player_identity import (
                load_registry,
                serialize_registry,
            )
            instance=private_instance()
            instance_root=Path(instance.root).resolve()
            def private_path(path):
                candidate=Path(path)
                if not candidate.is_absolute():
                    candidate=instance_root/candidate
                candidate=candidate.resolve()
                if not candidate.is_relative_to(instance_root):
                    raise ValueError(
                        'IDENTITY_COVERAGE_PATH_OUTSIDE_PRIVATE_INSTANCE'
                    )
                return candidate
            if a.command=='identity-coverage-inventory':
                rows,_=inspect(private_path(a.master))
                result=build_coverage_inventory(
                    rows,
                    load_registry(private_path(a.identity_registry)),
                )
                save(private_path(a.output),result)
            elif a.command=='identity-coverage-candidates':
                rows,_=inspect(private_path(a.master))
                hints=(
                    json.loads(private_path(a.hints).read_text())
                    if a.hints else None
                )
                result=coverage_candidates(
                    rows,
                    load_registry(private_path(a.identity_registry)),
                    candidate_hints=hints,
                )
                save(private_path(a.output),result)
            elif a.command=='identity-review-prepare':
                proposals=json.loads(
                    private_path(a.candidates).read_text()
                )
                packet=prepare_review_packet(proposals)
                save(private_path(a.output_json),packet)
                atomic(
                    private_path(a.output_csv),
                    review_packet_to_csv(packet),
                )
                result={
                    'review_packet_sha256':packet[
                        'review_packet_sha256'
                    ],
                    'review_count':len(packet['reviews']),
                }
            elif a.command=='identity-review-validate':
                packet=json.loads(private_path(a.packet).read_text())
                reviewed=validate_reviewed_csv(
                    packet,
                    private_path(a.reviewed_csv).read_bytes(),
                )
                save(private_path(a.output),reviewed)
                result=reviewed
            elif a.command=='identity-review-apply':
                rows,_=inspect(private_path(a.master))
                base_path=private_path(a.base_registry)
                base_registry=load_registry(base_path)
                packet=json.loads(private_path(a.packet).read_text())
                reviewed=json.loads(
                    private_path(a.reviewed_decisions).read_text()
                )
                candidate,groups=apply_reviewed_decisions(
                    rows,
                    base_registry,
                    packet,
                    reviewed,
                )
                candidate_bytes=serialize_registry(candidate)
                candidate_path=private_path(a.output_registry)
                atomic(candidate_path,candidate_bytes)
                ledger=build_coverage_ledger(
                    rows,
                    candidate,
                    packet,
                    reviewed,
                )
                ledger_path=private_path(a.output_ledger)
                save(ledger_path,ledger)
                manifest=build_candidate_registry_manifest(
                    base_registry_sha256=digest(base_path.read_bytes()),
                    candidate_registry_sha256=digest(candidate_bytes),
                    master_sha256=digest(private_path(a.master).read_bytes()),
                    master_authority_mode='FILE_SHA256_VERIFIED',
                    master_rows=len(rows),
                    master_unique_record_keys=len({
                        row['record_key'] for row in rows
                    }),
                    review_packet_sha256=digest(
                        private_path(a.packet).read_bytes()
                    ),
                    reviewed_decisions_sha256=digest(
                        private_path(a.reviewed_decisions).read_bytes()
                    ),
                    created_at=(
                        a.created_at
                        or datetime.now(timezone.utc).isoformat()
                    ),
                )
                save(private_path(a.output_manifest),manifest)
                result={
                    'candidate_registry_sha256':candidate[
                        'registry_sha256'
                    ],
                    'coverage_ledger_sha256':ledger[
                        'coverage_ledger_sha256'
                    ],
                    'candidate_registry_manifest_sha256':manifest[
                        'candidate_registry_manifest_sha256'
                    ],
                    'new_identity_group_count':len(groups),
                }
            elif a.command=='identity-coverage-reconcile':
                rows,_=inspect(private_path(a.master))
                ledger=json.loads(private_path(a.ledger).read_text())
                result=reconcile_coverage(
                    rows,
                    ledger,
                    load_registry(private_path(a.final_registry)),
                )
                save(private_path(a.output),result)
            else:
                rows,summary=inspect(private_path(a.master))
                base_path=private_path(a.base_registry)
                final_path=private_path(a.final_registry)
                packet_path=private_path(a.review_packet)
                decisions_path=private_path(a.reviewed_decisions)
                manifest_path=private_path(
                    a.candidate_registry_manifest
                )
                ledger_path=private_path(a.ledger)
                ledger=json.loads(ledger_path.read_text())
                reconciliation=reconcile_coverage(
                    rows,
                    ledger,
                    load_registry(final_path),
                )
                result=build_coverage_certificate(
                    master_sha256=summary['sha256'],
                    master_authority_mode='FILE_SHA256_VERIFIED',
                    master_rows=len(rows),
                    master_unique_record_keys=len({
                        row['record_key'] for row in rows
                    }),
                    base_registry_sha256=digest(base_path.read_bytes()),
                    final_registry_sha256=digest(final_path.read_bytes()),
                    review_packet_sha256=digest(packet_path.read_bytes()),
                    reviewed_decisions_sha256=digest(
                        decisions_path.read_bytes()
                    ),
                    candidate_registry_manifest_sha256=digest(
                        manifest_path.read_bytes()
                    ),
                    coverage_ledger_sha256=digest(ledger_path.read_bytes()),
                    reconciliation=reconciliation,
                )
                save(private_path(a.output),result)
        elif a.command.startswith('mention-'):
            from .document_mentions import (
                build_mention_artifact,
                load_mention_artifact,
                mention_summary,
                serialize_mention_artifact,
            )
            instance=private_instance()
            instance_root=Path(instance.root).resolve()
            def private_path(path):
                candidate=Path(path)
                if not candidate.is_absolute():
                    candidate=instance_root/candidate
                candidate=candidate.resolve()
                if not candidate.is_relative_to(instance_root):
                    raise ValueError('MENTION_PATH_OUTSIDE_PRIVATE_INSTANCE')
                return candidate
            if a.command=='mention-validate':
                result=mention_summary(load_mention_artifact(private_path(a.input)))
            else:
                documents=json.loads(private_path(a.documents).read_text())
                mentions=json.loads(private_path(a.mentions).read_text())
                authorizations=(
                    json.loads(private_path(a.authorizations).read_text())
                    if a.authorizations else None
                )
                artifact=build_mention_artifact(
                    documents,
                    mentions,
                    authorizations=authorizations,
                )
                output=private_path(a.output)
                if output.exists():
                    raise ValueError('MENTION_OUTPUT_EXISTS')
                atomic(output,serialize_mention_artifact(artifact))
                result=mention_summary(artifact)
                result['output']=str(output)
        elif a.command=='qualification-validate':
            from .operational_qualification import validate_ledger
            result=validate_ledger(read(a.ledger))
        elif a.command=='evidence-verify':
            from .evidence_ledger import verify_runtime_ledger
            result=verify_runtime_ledger(a.ledger_root,a.requirement_id)
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
