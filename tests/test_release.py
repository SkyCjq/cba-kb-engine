import copy
import json
from pathlib import Path
import pytest
from cba_kb.common import digest, read
from cba_kb.release import prepare,publish,restore,verify,FOLDER


class FakeDrive:
    def __init__(self):
        self.files={}; self.sequence=0; self.fail=None; self.calls=[]
        self.add('a',b'old-a');self.add('b',b'old-b')
        self.add('status',json.dumps({'state':'INITIAL','current_release_id':'v1.1'}).encode(),'application/json')
    def add(self,fid,data,mime='text/plain'):
        self.files[fid]={'data':data,'id':fid,'name':fid,'version':'1','mimeType':mime,'parents':['sandbox']}
    def meta(self,fid):return {k:copy.deepcopy(v) for k,v in self.files[fid].items() if k!='data'}
    def get(self,fid):return self.files[fid]['data']
    def put(self,fid,data,mime):
        assert self.files[fid]['mimeType']==mime
        self.calls.append(fid); self.files[fid]['data']=data
        self.files[fid]['version']=str(int(self.files[fid]['version'])+1)
        if self.fail==fid:self.fail=None;raise OSError('Lost response after accepted write')
    def ensure(self,parent,key,name,mime,content=None):
        fid=parent+'/'+key
        if fid not in self.files:self.add(fid,content or b'',mime)
        elif content is not None:assert self.get(fid)==content
        return fid


def setup(tmp_path):
    drive=FakeDrive(); entries=[]
    for fid in ['a','b']:
        p=tmp_path/fid;p.write_bytes(('new-'+fid).encode())
        entries.append({'id':fid,'name':fid,'mime':'text/plain','path':str(p)})
    root=tmp_path/'release'
    prepare(drive,root,'r1',entries,'archive','status')
    return drive,root


def test_publish_repeat_and_rollback(tmp_path):
    d,r=setup(tmp_path);publish(d,r,True);before=len(d.calls)
    publish(d,r,True);assert len(d.calls)==before
    assert verify(d,r)=={'verified':2}
    restore(d,r,True);assert d.get('a')==b'old-a';assert d.get('b')==b'old-b'
    assert json.loads(d.get('status'))['current_release_id']=='v1.1'


def test_conflict_prevents_all_writes(tmp_path):
    d,r=setup(tmp_path);d.put('b',b'external','text/plain');d.calls=[]
    with pytest.raises(RuntimeError,match='conflict'):publish(d,r,True)
    assert d.calls==[] and d.get('a')==b'old-a'


def test_lost_response_resumes_without_duplicate(tmp_path):
    d,r=setup(tmp_path);d.fail='a'
    with pytest.raises(OSError):publish(d,r,True)
    assert read(r/'journal.json')['state']=='FAILED'
    publish(d,r,True);assert d.calls.count('a')==1
    assert read(r/'journal.json')['state']=='COMPLETE'


def test_rollback_refuses_external_change(tmp_path):
    d,r=setup(tmp_path);publish(d,r,True);d.put('b',b'external','text/plain');d.calls=[]
    with pytest.raises(RuntimeError):restore(d,r,True)
    assert not d.calls


def test_frozen_candidate_cannot_change(tmp_path):
    d,r=setup(tmp_path);(r/'candidate/0').write_bytes(b'tampered')
    with pytest.raises(RuntimeError,match='altered'):publish(d,r,True)
    assert not d.calls


def test_single_writer_required(tmp_path):
    d,r=setup(tmp_path)
    with pytest.raises(RuntimeError,match='Single-writer'):publish(d,r)
    assert not d.calls


def test_patch_release_carries_forward_unchanged_artifacts(tmp_path):
    d=FakeDrive()
    d.put('status',json.dumps({
        'state':'COMPLETE',
        'current_release_id':'r1',
        'pending_release_id':None,
        'previous_release_id':'r0',
        'artifacts':[
            {'id':'a','name':'a','sha256':'old-a'},
            {'id':'b','name':'b','sha256':'old-b'},
        ],
    }).encode(),'application/json')
    candidate=tmp_path/'new-a';candidate.write_bytes(b'new-a')
    entries=[{
        'id':'a',
        'name':'a',
        'mime':'text/plain',
        'path':str(candidate),
    }]
    root=tmp_path/'patch'
    prepare(d,root,'r2',entries,'archive','status',carry_forward_artifacts=True)
    publish(d,root,True)
    status=json.loads(d.get('status'))
    assert status['current_release_id']=='r2'
    assert status['previous_release_id']=='r1'
    assert status['artifacts']==[
        {'id':'a','name':'a','sha256':digest(b'new-a')},
        {'id':'b','name':'b','sha256':'old-b'},
    ]


def test_native_target_rejected(tmp_path):
    d=FakeDrive();d.files['a']['mimeType']='application/vnd.google-apps.spreadsheet'
    p=tmp_path/'payload';p.write_bytes(b'csv')
    with pytest.raises(ValueError,match='MIME'):
        prepare(d,tmp_path/'r','r',[{'id':'a','name':'a','path':str(p),'mime':'text/csv'}],'arc','status')


def test_restore_after_partial_failure(tmp_path):
    d,r=setup(tmp_path);d.fail='a'
    with pytest.raises(OSError):publish(d,r,True)
    restore(d,r,True)
    assert d.get('a')==b'old-a' and d.get('b')==b'old-b'


def test_failure_before_first_status_write_is_resumable(tmp_path):
    d,r=setup(tmp_path); original=d.put
    def offline(fid,data,mime):
        raise OSError('Network unavailable')
    d.put=offline
    with pytest.raises(OSError):publish(d,r,True)
    d.put=original
    publish(d,r,True)
    assert read(r/'journal.json')['state']=='COMPLETE'


def test_complete_release_cannot_mask_external_status(tmp_path):
    d,r=setup(tmp_path);publish(d,r,True)
    d.put('status',b'{"state":"COMPLETE","current_release_id":"other"}','application/json')
    with pytest.raises(RuntimeError,match='status'):publish(d,r,True)


def test_managed_native_doc_publish_and_rollback(tmp_path):
    from cba_kb.native import wrap
    from cba_kb.release import DOC
    class NativeDrive(FakeDrive):
        def __init__(self):
            from types import SimpleNamespace
            super().__init__();self.add('doc',b'',DOC);self.native_copies=[]
            self.docs = SimpleNamespace(document=lambda fid: {'body': self.files[fid]['data'].decode()})
        def get_managed_doc(self,fid):return self.files[fid]['data']
        def put_managed_doc(self,fid,data):return self.put(fid,data,DOC)
        def ensure_copy(self,parent,key,fid,name):
            self.native_copies.append(fid);return 'full-native-backup'
    d=NativeDrive();p=tmp_path/'native.txt';p.write_text(wrap('Current facts'))
    r=tmp_path/'native-release'
    prepare(d,r,'nr',[{'id':'doc','name':'INDEX','mime':DOC,'mode':'managed_doc','path':str(p)}],'arc','status')
    publish(d,r,True)
    assert d.get_managed_doc('doc')==p.read_bytes()
    assert d.native_copies==['doc']
    assert json.loads(d.get('status'))['previous_snapshot'][0]['snapshot_id']=='full-native-backup'
    restore(d,r,True)
    assert d.get_managed_doc('doc')==b''


def test_rollback_exposes_in_progress_and_repeats(tmp_path):
    d,r=setup(tmp_path);publish(d,r,True);original=d.put;seen=[]
    def put(fid,data,mime):
        if fid in ('a','b'):seen.append(json.loads(d.get('status'))['state'])
        return original(fid,data,mime)
    d.put=put;restore(d,r,True)
    assert seen==['ROLLING_BACK','ROLLING_BACK']
    restore(d,r,True)


def test_rollback_lost_status_response_resumes(tmp_path):
    d,r=setup(tmp_path);publish(d,r,True);original=d.put
    def put(fid,data,mime):
        original(fid,data,mime)
        if fid=='status' and json.loads(data)['state']=='ROLLED_BACK':raise OSError('response lost')
    d.put=put
    with pytest.raises(OSError):restore(d,r,True)
    d.put=original;restore(d,r,True)
    assert read(r/'journal.json')['state']=='ROLLED_BACK'


def test_dependency_change_blocks_before_writes(tmp_path):
    from cba_kb.release import fingerprint
    d,r=setup(tmp_path);d.add('input',b'frozen')
    plan=read(r/'plan.json');plan['dependencies']=[dict(id='input',sha256=digest(b'frozen'),meta=fingerprint(d.meta('input')))]
    from cba_kb.common import save
    save(r/'plan.json',plan);d.put('input',b'changed','text/plain');d.calls=[]
    with pytest.raises(RuntimeError,match='dependency'):publish(d,r,True)
    assert not d.calls


def test_new_object_moves_and_restores(tmp_path):
    from cba_kb.common import save
    d,r=setup(tmp_path);plan=read(r/'plan.json')
    plan['entries'][0].update(staging_parent='sandbox',publish_parent='live')
    save(r/'plan.json',plan)
    def move(fid,destination,previous):d.files[fid]['parents']=[destination]
    d.move=move;publish(d,r,True)
    assert d.meta('a')['parents']==['live']
    restore(d,r,True);assert d.meta('a')['parents']==['sandbox']


def closure_setup(tmp_path):
    """A real byte-level control transaction using only a credential-free fake."""
    import csv
    import io
    import yaml
    from cba_kb.current_state import generate_context_card, render_current_state, target_metadata
    from test_canonical_registry import SHA, registry

    class ClosureDrive(FakeDrive):
        def list(self, parent):
            return [self.meta(fid) for fid in self.files if parent in self.files[fid]['parents']]

        def ensure(self, parent, key, name, mime, content=None):
            fid = parent + '/' + key
            if fid not in self.files:
                self.add(fid, content or b'', mime)
                self.files[fid].update(name=name, parents=[parent])
            elif content is not None:
                assert self.get(fid) == content
            return fid

        def move(self, fid, destination, previous):
            self.calls.append('move:' + fid)
            self.files[fid]['parents'] = [destination]
            self.files[fid]['version'] = str(int(self.files[fid]['version']) + 1)

    d = ClosureDrive()
    for unused in ('a', 'b'):
        del d.files[unused]
    d.files['status']['parents'] = ['control']
    d.files['status']['data'] = json.dumps({
        'state': 'COMPLETE', 'current_release_id': 'v1.5.3-2',
        'code_commit': 'b' * 40, 'artifacts': [{'id': 'master', 'name': 'MASTER', 'sha256': digest(b'master')}],
    }).encode()
    source = json.dumps({'event_key': 'first', 'date': None, 'official': 'known'}).encode() + b'\n'
    compat = json.dumps({'event_key': 'first', 'date': None}).encode() + b'\n'
    for fid, data, parent in [
        ('event-file', source, 'data'), ('compat-file', compat, 'data'),
        ('master', b'master', 'data'),
        ('sources', b'source_file_id,processing_status\nevidence,accepted\n', 'control'),
        ('evidence', b'unchanged original evidence', 'evidence-folder'),
    ]:
        d.add(fid, data)
        d.files[fid]['parents'] = [parent]
    reg = registry()
    meta = target_metadata('v1.5.4-test', SHA, reg)
    manifest_rows = [
        {'uid': 'facts/events.jsonl', 'drive_file_id': 'event-file', 'content_hash': digest(source)},
        {'uid': 'facts/old_events.jsonl', 'drive_file_id': 'compat-file', 'content_hash': digest(compat)},
    ]
    docs = {'readme': 'docs/readme', 'index': 'docs/index',
            'version': 'docs/version', 'context_card': 'ai/context'}
    content = {
        'control/registry': yaml.safe_dump(reg).encode(),
        **{key: render_current_state(meta).encode() for key in docs.values()},
    }
    content[docs['context_card']] = generate_context_card(meta, reg, manifest_rows).encode()
    names = {'control/registry': 'canonical_products.yaml', 'control/manifest': 'manifest.csv',
             'docs/readme': 'README.md', 'docs/index': 'INDEX.md',
             'docs/version': 'version.md', 'ai/context': 'CONTEXT_CARD.md'}
    for key, data in content.items():
        manifest_rows.append({'uid': key, 'drive_file_id': key, 'content_hash': digest(data)})
    manifest_rows.append({
        'uid': 'evidence/new-evidence', 'drive_file_id': 'new-evidence',
        'content_hash': digest(b'new protected evidence'),
    })
    manifest_rows.append({'uid': 'control/manifest', 'drive_file_id': 'control/manifest', 'content_hash': ''})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=['uid', 'drive_file_id', 'content_hash'])
    writer.writeheader()
    writer.writerows(manifest_rows)
    content['control/manifest'] = stream.getvalue().encode()
    entries = []
    for index, (key, data) in enumerate(content.items()):
        d.add(key, b'previous safe bytes')
        d.files[key].update(name=names[key], parents=['control'])
        path = tmp_path / str(index)
        path.write_bytes(data)
        entry = {'id': key, 'name': names[key], 'mime': 'text/plain', 'path': str(path), 'logical_key': key}
        if key in {'control/registry', 'ai/context'}:
            d.files[key]['data'] = b''
            d.files[key]['parents'] = ['staging']
            entry.update(staging_parent='staging', publish_parent='control')
        entries.append(entry)
    d.add('new-evidence', b'new protected evidence')
    d.files['new-evidence'].update(name='new-evidence', parents=['staging'])
    new_path = tmp_path / 'new-evidence'
    new_path.write_bytes(b'new protected evidence')
    entries.append({
        'id': 'new-evidence', 'name': 'new-evidence', 'mime': 'text/plain',
        'path': str(new_path), 'logical_key': 'evidence/new-evidence',
        'staging_parent': 'staging', 'publish_parent': 'evidence-folder',
    })
    request = {
        'code_commit': SHA, 'previous_code_commit': 'b' * 40, 'baseline_release_id': 'v1.5.3-2',
        'registry_key': 'control/registry', 'manifest_key': 'control/manifest',
        'documents': docs,
        'zones': {'current': ['control', 'data'], 'history': ['archive'],
                  'staging': ['staging'], 'evidence': ['evidence-folder']},
        'protected': [
            {'id': fid, 'name': fid, 'sha256': digest(d.get(fid)), 'kind': kind}
            for fid, kind in [('master', 'master'), ('event-file', 'six_table'),
                              ('compat-file', 'compatibility'), ('sources', 'source_registry'),
                              ('evidence', 'evidence'), ('new-evidence', 'evidence')]
        ],
    }
    root = tmp_path / 'closure-release'
    prepare(d, root, 'v1.5.4-test', entries, 'archive', 'status', closure=request)
    return d, root


def test_canonical_release_happy_path_and_new_control_rollback(tmp_path):
    from cba_kb.current_state import consumer_artifacts
    d, r = closure_setup(tmp_path)
    publish(d, r, True)
    status = json.loads(d.get('status'))
    assert status['state'] == 'COMPLETE'
    assert status['code_commit'] == 'a' * 40
    assert d.meta('ai/context')['parents'] == ['control']
    assert len(consumer_artifacts(status)) == 8
    count = len(d.calls)
    publish(d, r, True)
    assert len(d.calls) == count
    evidence_ids = {key for key in d.files if key.startswith('archive/')}
    restore(d, r, True)
    assert json.loads(d.get('status'))['state'] == 'ROLLED_BACK'
    assert json.loads(d.get('status'))['code_commit'] == 'b' * 40
    assert d.meta('ai/context')['parents'] == ['staging']
    assert d.get('ai/context') == b''
    assert d.get('docs/readme') == b'previous safe bytes'
    assert evidence_ids <= set(d.files)
    assert (r / 'before').is_dir() and (r / 'candidate').is_dir()
    restore(d, r, True)


def test_v154_cannot_use_legacy_plan_without_closure(tmp_path):
    d = FakeDrive()
    path = tmp_path / 'a'
    path.write_bytes(b'new')
    with pytest.raises(ValueError, match='CLOSURE_REQUIRED'):
        prepare(d, tmp_path / 'r', 'v1.5.4-1',
                [{'id': 'a', 'name': 'a', 'mime': 'text/plain', 'path': str(path)}],
                'archive', 'status')
    assert not d.calls


@pytest.mark.parametrize('location', ['candidate', 'before', 'report'])
def test_secret_preflight_fails_before_any_archive_or_current_write(tmp_path, location):
    from cba_kb.common import save
    d, r = setup(tmp_path)
    value = '_'.join(['FAKE', 'TOKEN', 'TEST', 'ONLY'])
    blob = ('access_' + 'token=' + value).encode()
    if location in {'candidate', 'before'}:
        path = r / location / '0'
        path.write_bytes(blob)
        plan = read(r / 'plan.json')
        plan['entries'][0]['after_hash' if location == 'candidate' else 'before_hash'] = digest(blob)
        save(r / 'plan.json', plan)
    else:
        (r / 'generated-report.md').write_bytes(blob)
    files_before = set(d.files)
    status_before = d.get('status')
    with pytest.raises(RuntimeError, match='SECRET_GUARD') as error:
        publish(d, r, True)
    assert value not in str(error.value)
    assert d.calls == [] and set(d.files) == files_before
    assert d.get('status') == status_before


def test_secret_at_prepare_never_creates_outbox_or_writes(tmp_path):
    d = FakeDrive()
    candidate = tmp_path / 'candidate.md'
    candidate.write_text('refresh_' + 'token=' + '_'.join(['FAKE', 'TOKEN']))
    with pytest.raises(RuntimeError, match='SECRET_GUARD'):
        prepare(d, tmp_path / 'r', 'r',
                [{'id': 'a', 'name': 'report.md', 'mime': 'text/plain', 'path': str(candidate)}],
                'archive', 'status')
    assert not d.calls
    assert not (tmp_path / 'r').exists()


def test_stale_index_blocks_complete_and_keeps_previous_snapshot(tmp_path):
    d, r = closure_setup(tmp_path)
    original = d.put

    def drop_index(fid, data, mime):
        if fid == 'docs/index':
            return
        return original(fid, data, mime)

    d.put = drop_index
    with pytest.raises(RuntimeError, match='Readback'):
        publish(d, r, True)
    status = json.loads(d.get('status'))
    assert status['state'] == 'FAILED'
    assert status['current_release_id'] == 'v1.5.3-2'
    assert len(status['previous_snapshot']) == 12
    d.put = original
    restore(d, r, True)


def test_readback_secret_blocks_complete_and_does_not_echo_value(tmp_path):
    d, r = closure_setup(tmp_path)
    original = d.put
    value = '_'.join(['FAKE', 'READBACK', 'TOKEN'])

    def corrupt(fid, data, mime):
        if fid == 'docs/index':
            data = ('access_' + 'token=' + value).encode()
        return original(fid, data, mime)

    d.put = corrupt
    with pytest.raises(RuntimeError, match='SECRET_GUARD') as error:
        publish(d, r, True)
    assert value not in str(error.value)
    assert json.loads(d.get('status'))['state'] == 'FAILED'
    assert read(r / 'journal.json')['state'] == 'FAILED'


@pytest.mark.parametrize('fid', ['master', 'event-file', 'sources', 'evidence'])
def test_protected_business_or_evidence_change_prevents_all_writes(tmp_path, fid):
    d, r = closure_setup(tmp_path)
    d.files[fid]['data'] += b'changed'
    files_before = set(d.files)
    with pytest.raises(ValueError, match='ZERO_BUSINESS_FACT_DELTA'):
        publish(d, r, True)
    assert not d.calls and set(d.files) == files_before


def test_late_history_contamination_blocks_complete(tmp_path):
    d, r = closure_setup(tmp_path)
    original = d.move

    def contaminate(fid, destination, previous):
        original(fid, destination, previous)
        d.add('stray', b'history')
        d.files['stray'].update(name='before_report.md', parents=['control'])

    d.move = contaminate
    with pytest.raises(ValueError, match='HISTORICAL_IN_CURRENT'):
        publish(d, r, True)
    assert json.loads(d.get('status'))['state'] == 'FAILED'


def test_closure_response_loss_retry_is_idempotent(tmp_path):
    d, r = closure_setup(tmp_path)
    d.fail = 'docs/readme'
    with pytest.raises(OSError):
        publish(d, r, True)
    publish(d, r, True)
    assert d.calls.count('docs/readme') == 1
    assert json.loads(d.get('status'))['state'] == 'COMPLETE'


def test_code_mirror_requires_merged_and_clean_executing_commit(tmp_path):
    import subprocess
    import yaml
    from cba_kb.release import verify_code_provenance
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    directory = repo / 'requirements/REQ-154-CANONSEC-01'
    directory.mkdir(parents=True)
    (directory / 'task.yaml').write_text(yaml.safe_dump({'base_branch': 'approved-base'}))
    (repo / 'module.py').write_text('value = 1\n')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'fixture'], cwd=repo, check=True)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    with pytest.raises(ValueError, match='NOT_MERGED'):
        verify_code_provenance(repo, sha)
    subprocess.run(['git', 'update-ref', 'refs/remotes/origin/approved-base', sha], cwd=repo, check=True)
    assert verify_code_provenance(repo, sha)['status'] == 'PASS'
    (repo / 'module.py').write_text('value = 2\n')
    with pytest.raises(ValueError, match='NOT_MERGED'):
        verify_code_provenance(repo, sha)


def test_incomplete_evidence_inventory_cannot_pass_closure(tmp_path):
    d, r = closure_setup(tmp_path)
    d.add('unfrozen-evidence', b'original evidence')
    d.files['unfrozen-evidence']['parents'] = ['evidence-folder']
    with pytest.raises(ValueError, match='EVIDENCE_BASELINE_COVERAGE'):
        publish(d, r, True)
    assert not d.calls


def test_security_preflight_uses_logical_path_for_candidate(tmp_path):
    from cba_kb.release import security_preflight
    candidate = tmp_path / 'candidate/0'
    candidate.parent.mkdir(parents=True)
    candidate.write_text('access_token = get_access_token()\n')
    security_preflight(tmp_path, {
        'release_id': 'v1.5.4-test',
        'environment': 'sandbox',
        'closure': {'code_commit': 'a' * 40},
        'entries': [{
            'logical_key': 'code/scripts/check_secrets.py',
            'name': 'check_secrets.py',
            'candidate': 'candidate/0',
        }],
    })


def test_new_control_move_lost_response_can_resume_rollback(tmp_path):
    d, r = closure_setup(tmp_path)
    publish(d, r, True)
    original = d.move
    fired = False

    def lose_response(fid, destination, previous):
        nonlocal fired
        original(fid, destination, previous)
        if not fired:
            fired = True
            raise OSError('lost move response')

    d.move = lose_response
    with pytest.raises(OSError):
        restore(d, r, True)
    d.move = original
    restore(d, r, True)
    assert json.loads(d.get('status'))['state'] == 'ROLLED_BACK'
    assert d.meta('ai/context')['parents'] == ['staging']
    assert d.meta('control/registry')['parents'] == ['staging']


def test_native_unmanaged_secret_is_rejected_before_any_backup(tmp_path):
    from types import SimpleNamespace
    from cba_kb.native import wrap
    from cba_kb.release import DOC
    d = FakeDrive()
    d.add('doc', wrap('Safe prefix').encode(), DOC)
    d.get_managed_doc = lambda fid: d.get(fid)
    value = '_'.join(['FAKE', 'NATIVE', 'TOKEN'])
    d.docs = SimpleNamespace(document=lambda fid: {
        'body': 'Historical text: ' + 'access_' + 'token=' + value,
    })
    path = tmp_path / 'doc.txt'
    path.write_text(wrap('New safe prefix'))
    with pytest.raises(RuntimeError, match='SECRET_GUARD') as error:
        prepare(d, tmp_path / 'r', 'r', [
            {'id': 'doc', 'name': 'INDEX', 'mime': DOC, 'mode': 'managed_doc', 'path': str(path)},
        ], 'archive', 'status')
    assert value not in str(error.value)
    assert not d.calls
    assert not any(fid.startswith('archive/') for fid in d.files)


def cli_plan_arguments(tmp_path, entries, closure=None, environment='sandbox', release_id='v1.5.4-test'):
    entries_path = tmp_path / 'entries.json'
    entries_path.write_text(json.dumps(entries))
    arguments = ['cba-kb', '--root', str(tmp_path / 'runtime'), 'plan',
                 '--entries', str(entries_path), '--release-id', release_id,
                 '--archive-id', 'archive', '--status-id', 'status',
                 '--environment', environment]
    if closure is not None:
        closure_path = tmp_path / 'closure.json'
        closure_path.write_text(json.dumps(closure))
        arguments.extend(['--closure', str(closure_path)])
    return arguments


@pytest.mark.parametrize('environment', ['sandbox', 'production'])
def test_plan_cli_forwards_closure_and_environment_before_freeze(tmp_path, monkeypatch, capsys, environment):
    import sys
    from cba_kb import cli, gates
    path = tmp_path / 'document.md'
    path.write_text('Safe control document')
    entries = [{'id': 'doc', 'name': 'document.md', 'mime': 'text/plain', 'path': str(path)}]
    closure = {'code_commit': 'a' * 40}
    events = []
    drive = object()
    monkeypatch.setattr(cli, 'Drive', lambda root: drive)
    def authorize(actual, root, request, selected):
        assert actual is drive and request['entries'] == entries
        assert selected == environment
        events.append('authorized')
    def freeze(actual, root, release_id, supplied_entries, archive, status, dependencies, **options):
        assert events == ['authorized']
        assert actual is drive
        assert supplied_entries == entries
        assert root == tmp_path / 'runtime/workspace/outbox/v1.5.4-test'
        assert options['closure'] == closure
        assert options['environment'] == environment
        events.append('frozen')
        return {'environment': environment, 'release_id': release_id}
    monkeypatch.setattr(gates, 'authorize_plan', authorize)
    monkeypatch.setattr(cli, 'prepare', freeze)
    monkeypatch.setattr(sys, 'argv', cli_plan_arguments(tmp_path, entries, closure, environment))
    cli.main()
    assert events == ['authorized', 'frozen']
    assert json.loads(capsys.readouterr().out)['environment'] == environment


def test_plan_cli_builds_real_closure_plan_with_fake_transport(tmp_path, monkeypatch):
    import sys
    from cba_kb import cli, gates
    drive, frozen_root = closure_setup(tmp_path)
    frozen = read(frozen_root / 'plan.json')
    entries = [
        dict(entry, path=str(frozen_root / entry['candidate']))
        for entry in frozen['entries']
    ]
    monkeypatch.setattr(cli, 'Drive', lambda root: drive)
    monkeypatch.setattr(gates, 'authorize_plan', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', cli_plan_arguments(tmp_path, entries, frozen['closure']))
    cli.main()
    actual = read(tmp_path / 'runtime/workspace/outbox/v1.5.4-test/plan.json')
    assert actual['closure']['registry_key'] == frozen['closure']['registry_key']
    assert actual['closure']['code_commit'] == 'a' * 40
    assert actual['environment'] == 'sandbox'
    assert read(tmp_path / 'runtime/workspace/outbox/v1.5.4-test/journal.json')['state'] == 'PREPARED'
    assert not drive.calls


@pytest.mark.parametrize('case', ['missing', 'wrong_type', 'secret', 'candidate_secret'])
def test_plan_cli_rejects_bad_input_before_transport(tmp_path, monkeypatch, capsys, case):
    import sys
    from cba_kb import cli
    def forbidden(root):
        pytest.fail('Invalid closure/candidate reached authenticated transport')
    monkeypatch.setattr(cli, 'Drive', forbidden)
    value = '_'.join(['FAKE', 'CLI', 'TOKEN'])
    path = tmp_path / 'document.md'
    path.write_text('Safe control document')
    entries = [{'id': 'doc', 'name': 'document.md', 'mime': 'text/plain', 'path': str(path)}]
    closure = None if case == 'missing' else ([] if case == 'wrong_type' else {})
    if case == 'secret':
        closure = dict(zip(['access_token'], [value]))
    if case == 'candidate_secret':
        path.write_text('access_' + 'token=' + value)
    monkeypatch.setattr(sys, 'argv', cli_plan_arguments(tmp_path, entries, closure))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert value not in capsys.readouterr().err
