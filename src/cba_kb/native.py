"""Managed current-release prefix for existing Google Docs.

Only our prefix is rewritten. Original paragraphs, styles, tables and controls
remain below an explicit historical-content divider, and rollback removes the
prefix. Multi-tab documents require explicit selection and are rejected here.
"""
BEGIN='[CBA-KB CURRENT RELEASE BEGIN]\n'
END='[CBA-KB CURRENT RELEASE END]\n'
HISTORY='以下为迁移前的历史阅读内容；当前回答请以上方发布内容及 MASTER 为准。\n'


def units(text):
    return len(text.encode('utf-16-le'))//2


def body(document):
    tabs=document.get('tabs',[])
    if len(tabs)!=1 or tabs[0].get('childTabs'):
        raise ValueError('Exactly one top-level native Doc tab is required')
    tab=tabs[0]
    return tab['tabProperties']['tabId'],tab['documentTab']['body']['content']


def current_prefix(document):
    tab_id,elements=body(document)
    # Read only the consecutive text paragraphs from the document start.
    text=''
    for element in elements:
        if element.get('startIndex',0)==0: continue  # section break
        paragraph=element.get('paragraph')
        if not paragraph: break
        parts=paragraph.get('elements',[])
        if any('textRun' not in p for p in parts): break
        text+=''.join(p['textRun']['content'] for p in parts)
        if END in text:break
    if not text.startswith(BEGIN):
        if BEGIN in text or END in text:raise ValueError('Malformed managed prefix')
        return tab_id,''
    if text.count(BEGIN)!=1 or text.count(END)!=1:
        raise ValueError('Malformed managed prefix')
    prefix=text[:text.index(END)+len(END)]
    return tab_id,prefix


def wrap(text):
    if BEGIN in text or END in text:raise ValueError('Reserved native prefix marker')
    return BEGIN+text.rstrip()+'\n'+HISTORY+END


def update_requests(document,new_prefix):
    tab_id,old=current_prefix(document)
    if new_prefix and (not new_prefix.startswith(BEGIN) or not new_prefix.endswith(END)):
        raise ValueError('Only a complete managed prefix is writable')
    requests=[]
    if old:
        requests.append({'deleteContentRange':{'range':{'tabId':tab_id,'startIndex':1,'endIndex':1+units(old)}}})
    if new_prefix:
        requests.append({'insertText':{'location':{'tabId':tab_id,'index':1},'text':new_prefix}})
        area={'tabId':tab_id,'startIndex':1,'endIndex':1+units(new_prefix)}
        requests.append({'updateParagraphStyle':{'range':area,'paragraphStyle':{'namedStyleType':'NORMAL_TEXT'},'fields':'namedStyleType'}})
        requests.append({'updateTextStyle':{'range':area,'textStyle':{'fontSize':{'magnitude':10,'unit':'PT'},'bold':False},'fields':'fontSize,bold'}})
    return requests


class NativeDocs:
    def __init__(self,api):self.api=api
    def document(self,file_id):
        return self.api.documents().get(documentId=file_id,includeTabsContent=True).execute()
    def get(self,file_id):
        _,text=current_prefix(self.document(file_id));return text.encode()
    def put(self,file_id,data):
        doc=self.document(file_id)
        requests=update_requests(doc,data.decode('utf-8'))
        if not requests:return
        revision=doc.get('revisionId')
        if not revision:raise RuntimeError('Native Doc revision unavailable')
        self.api.documents().batchUpdate(documentId=file_id,body={
            'writeControl':{'requiredRevisionId':revision},'requests':requests}).execute(num_retries=0)
        if self.get(file_id)!=data:raise RuntimeError('Native prefix readback failed')
