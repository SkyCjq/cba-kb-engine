import pytest
from cba_kb.native import BEGIN,END,wrap,units,current_prefix,update_requests

def doc(text,after=None):
    return {'revisionId':'r','tabs':[{'tabProperties':{'tabId':'t.0'},'documentTab':{'body':{'content':[
        {'sectionBreak':{},'startIndex':0,'endIndex':1},
        {'startIndex':1,'endIndex':1+units(text),'paragraph':{'elements':[{'textRun':{'content':text}}]}},
        *(after or [])]}}}]}

def test_first_insert_preserves_original():
    d=doc('Original styled content\n')
    requests=update_requests(d,wrap('New release'))
    assert len(requests)==1 and 'insertText' in requests[0]
    assert requests[0]['insertText']['location']['index']==1

def test_update_only_deletes_managed_prefix_utf16():
    prefix=wrap('新版 🏀')
    d=doc(prefix+'Original\n')
    r=update_requests(d,wrap('Next'))
    assert r[0]['deleteContentRange']['range']['endIndex']==1+units(prefix)
    assert current_prefix(d)[1]==prefix

def test_rollback_only_deletes_prefix():
    r=update_requests(doc(wrap('New')+'Original\n'),'')
    assert len(r)==1 and 'deleteContentRange' in r[0]

def test_reject_multitab_and_truncated_prefix():
    d=doc('old\n');d['tabs']*=2
    with pytest.raises(ValueError):update_requests(d,wrap('new'))
    with pytest.raises(ValueError):current_prefix(doc(BEGIN+'unfinished\n'))

def test_original_table_not_modified():
    d=doc('old\n',[{'table':{'rows':2},'startIndex':5}])
    assert len(update_requests(d,wrap('new')))==1
