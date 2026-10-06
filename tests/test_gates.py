import json
import pytest
from cba_kb.gates import authorize_plan
from cba_kb.common import save
from test_release import FakeDrive


class Instance:
 def __init__(self,configs):self.configs=configs
 def read_json(self,name):return self.configs[name]


def test_production_requires_complete_explicit_policy(tmp_path):
 d=FakeDrive();policy={'enabled':True,'status_id':'status','archive_id':'archive','dependency_ids':['input'],'targets':{'a':{'mime':'text/plain','allowed_parents':['sandbox']}}}
 instance=Instance({'production.json':policy})
 plan={'status_id':'status','archive_id':'archive','entries':[{'id':'a','mime':'text/plain'}],'dependencies':[{'id':'input'}]}
 authorize_plan(d,tmp_path,plan,'production',instance)
 plan['entries']=[]
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan,'production',instance)
 plan['entries']=[{'id':'a','mime':'text/plain','publish_parent':'unapproved'}]
 with pytest.raises(RuntimeError,match='move'):authorize_plan(d,tmp_path,plan,'production',instance)


def test_production_allows_retired_targets_omission_from_plan(tmp_path):
 d=FakeDrive()
 d.add('status', json.dumps({'artifacts':[{'id':'a'},{'id':'future'},{'id':'nowret'}]}).encode(), 'application/json')
 d.add('a', b'x')
 d.add('future', b'x')
 d.add('nowret', b'x')
 d.add('retired', b'x')
 policy={
     'enabled':True,'status_id':'status','archive_id':'archive','dependency_ids':['input'],
     'targets':{
         'a':{'mime':'text/plain','allowed_parents':['sandbox']},
         'future':{'mime':'text/plain','allowed_parents':['sandbox'],'retire_in_release':'v9.9.9-9'},
         'nowret':{'mime':'text/plain','allowed_parents':['sandbox'],'retire_in_release':'v2.0.1-1'},
         'retired':{'mime':'text/plain','allowed_parents':['sandbox'],'retire_in_release':'v1.9.0-1'},
     }
 }
 instance=Instance({'production.json':policy})

 # 1. plan entries=[a,future] -> PASS (historical + current retirement omitted)
 plan={
     'release_id':'v2.0.1-1','status_id':'status','archive_id':'archive',
     'entries':[{'id':'a','mime':'text/plain'},{'id':'future','mime':'text/plain'}],
     'dependencies':[{'id':'input'}],
 }
 authorize_plan(d,tmp_path,plan,'production',instance)

 # 2. plan entries=[a] -> 必须 REJECT (future marked active missing, blocker regression)
 plan_missing_future={
     'release_id':'v2.0.1-1','status_id':'status','archive_id':'archive',
     'entries':[{'id':'a','mime':'text/plain'}],
     'dependencies':[{'id':'input'}],
 }
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan_missing_future,'production',instance)

 # 3. plan entries=[] -> 必须 REJECT (missing live targets)
 plan_empty={
     'release_id':'v2.0.1-1','status_id':'status','archive_id':'archive',
     'entries':[],
     'dependencies':[{'id':'input'}],
 }
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan_empty,'production',instance)

 # 4. plan entries=[a,future,unauthorized] -> 必须 REJECT (unauthorized extra target)
 plan_unauthorized={
     'release_id':'v2.0.1-1','status_id':'status','archive_id':'archive',
     'entries':[{'id':'a','mime':'text/plain'},{'id':'future','mime':'text/plain'},{'id':'unauthorized','mime':'text/plain'}],
     'dependencies':[{'id':'input'}],
 }
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan_unauthorized,'production',instance)

 # 5. add target 'bad' (marker=True, non-string, in artifacts) missing -> 必须 REJECT (fail-closed)
 d.add('status', json.dumps({'artifacts':[{'id':'a'},{'id':'future'},{'id':'nowret'},{'id':'bad'}]}).encode(), 'application/json')
 d.add('bad', b'x')
 policy['targets']['bad']={'mime':'text/plain','allowed_parents':['sandbox'],'retire_in_release':True}
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan,'production',instance)

 # 6. marker==release_id 但不在上轮 active 集合 → 必须 REJECT（非法退休状态）
 policy['targets']['ghost']={'mime':'text/plain','allowed_parents':['sandbox'],'retire_in_release':'v2.0.1-1'}
 with pytest.raises(RuntimeError,match='Retirement target'):authorize_plan(d,tmp_path,plan,'production',instance)


def test_legacy_upload_entry_is_disabled():
 import subprocess,sys
 from pathlib import Path
 result=subprocess.run([sys.executable,str(Path('legacy/upload_drive.py'))],capture_output=True,text=True)
 assert result.returncode!=0 and 'Legacy uploader is disabled' in result.stderr
