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
            super().__init__();self.add('doc',b'',DOC);self.native_copies=[]
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
