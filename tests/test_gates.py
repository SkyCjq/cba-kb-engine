import pytest
from cba_kb.gates import authorize_plan
from cba_kb.common import save
from test_release import FakeDrive


def test_production_requires_complete_explicit_policy(tmp_path):
 d=FakeDrive();policy={'enabled':True,'status_id':'status','archive_id':'archive','dependency_ids':['input'],'targets':{'a':{'mime':'text/plain','allowed_parents':['sandbox']}}}
 save(tmp_path/'config/production.json',policy)
 plan={'status_id':'status','archive_id':'archive','entries':[{'id':'a','mime':'text/plain'}],'dependencies':[{'id':'input'}]}
 authorize_plan(d,tmp_path,plan,'production')
 plan['entries']=[]
 with pytest.raises(RuntimeError,match='complete'):authorize_plan(d,tmp_path,plan,'production')
 plan['entries']=[{'id':'a','mime':'text/plain','publish_parent':'unapproved'}]
 with pytest.raises(RuntimeError,match='move'):authorize_plan(d,tmp_path,plan,'production')
