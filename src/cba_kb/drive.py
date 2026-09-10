"""Authenticated Drive transport. Credentials stay on this machine."""
import io
import json
import os
from pathlib import Path
from .common import atomic, digest

SCOPES = ['https://www.googleapis.com/auth/drive']
FIELDS = 'id,name,mimeType,parents,version,modifiedTime,headRevisionId,md5Checksum,size,webViewLink'


def credentials(root, interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    directory = Path(root)/'.credentials'
    client, token = directory/'credentials.json', directory/'token.json'
    creds = Credentials.from_authorized_user_file(str(token)) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid or not creds.has_scopes(SCOPES):
        if not interactive:
            raise RuntimeError('Drive authorization missing; run make auth after adding .credentials/credentials.json')
        if not client.exists():
            raise RuntimeError('Desktop OAuth client JSON missing: .credentials/credentials.json')
        if 'installed' not in json.loads(client.read_text()):
            raise ValueError('Expected Desktop OAuth client (installed)')
        creds = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES).run_local_server(port=0)
    directory.mkdir(parents=True,exist_ok=True)
    os.chmod(directory,0o700)
    atomic(token,creds.to_json().encode()); os.chmod(token,0o600)
    return creds


class Drive:
    def __init__(self, root):
        from googleapiclient.discovery import build
        creds=credentials(root)
        self.api = build('drive','v3',credentials=creds,cache_discovery=False)
        from .native import NativeDocs
        self.docs=NativeDocs(build('docs','v1',credentials=creds,cache_discovery=False))

    def get_managed_doc(self,file_id):
        if self.meta(file_id)['mimeType']!='application/vnd.google-apps.document':
            raise ValueError('Not a native Doc')
        return self.docs.get(file_id)

    def put_managed_doc(self,file_id,content):
        if self.meta(file_id)['mimeType']!='application/vnd.google-apps.document':
            raise ValueError('Not a native Doc')
        return self.docs.put(file_id,content)

    def ensure_copy(self,parent,key,file_id,name):
        matches=[f for f in self.list(parent) if f.get('appProperties',{}).get('cba_key')==key]
        if len(matches)>1:raise RuntimeError('Duplicate backup copy')
        if matches:return matches[0]['id']
        return self.api.files().copy(fileId=file_id,body={'name':name,'parents':[parent],
            'appProperties':{'cba_key':key}},fields='id',supportsAllDrives=True).execute(num_retries=0)['id']

    def meta(self, file_id):
        return self.api.files().get(fileId=file_id,fields=FIELDS,supportsAllDrives=True).execute()

    def get(self, file_id):
        from googleapiclient.http import MediaIoBaseDownload
        meta = self.meta(file_id)
        if meta['mimeType'].startswith('application/vnd.google-apps.'):
            raise ValueError('Native object requires native adapter; raw download rejected')
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf,self.api.files().get_media(fileId=file_id,supportsAllDrives=True))
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=3)
        return buf.getvalue()

    def put(self, file_id, content, mime):
        from googleapiclient.http import MediaIoBaseUpload
        actual = self.meta(file_id)['mimeType']
        if actual != mime or actual.startswith('application/vnd.google-apps.'):
            raise ValueError('MIME mismatch/native byte update rejected')
        return self.api.files().update(fileId=file_id,media_body=MediaIoBaseUpload(io.BytesIO(content),mimetype=mime),
                                      fields=FIELDS,supportsAllDrives=True).execute(num_retries=0)

    def list(self, parent):
        items, page = [], None
        while True:
            result = self.api.files().list(q=f"'{parent}' in parents and trashed=false",pageToken=page,
                pageSize=1000,fields=f'nextPageToken,files({FIELDS},appProperties)',supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute(num_retries=3)
            items.extend(result.get('files',[])); page=result.get('nextPageToken')
            if not page: return items

    def ensure(self, parent, key, name, mime, content=None):
        # Recovery after a successful create with a lost response: same key, no blind duplicate.
        matches=[f for f in self.list(parent) if f.get('appProperties',{}).get('cba_key')==key]
        if len(matches)>1: raise RuntimeError('Duplicate release object key')
        if matches:
            result=matches[0]
            if result['mimeType']!=mime: raise ValueError('Existing object MIME mismatch')
            if content is not None and digest(self.get(result['id']))!=digest(content):
                raise RuntimeError('Immutable release object differs')
            return result['id']
        from googleapiclient.http import MediaIoBaseUpload
        body={'name':name,'parents':[parent],'mimeType':mime,'appProperties':{'cba_key':key}}
        kwargs={'body':body,'fields':'id','supportsAllDrives':True}
        if content is not None: kwargs['media_body']=MediaIoBaseUpload(io.BytesIO(content),mimetype=mime)
        return self.api.files().create(**kwargs).execute(num_retries=0)['id']
