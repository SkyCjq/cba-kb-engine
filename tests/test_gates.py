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


def test_legacy_upload_entry_is_disabled():
 import subprocess,sys
 from pathlib import Path
 result=subprocess.run([sys.executable,str(Path('legacy/upload_drive.py'))],capture_output=True,text=True)
 assert result.returncode!=0 and 'Legacy uploader is disabled' in result.stderr
