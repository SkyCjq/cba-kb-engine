"""Authenticated Drive transport. Credentials stay on this machine."""
import io
import json
import os
from pathlib import Path
from .common import atomic, digest
from .transport import retry_read

SCOPES = ['https://www.googleapis.com/auth/drive']
FIELDS = 'id,name,mimeType,parents,version,modifiedTime,headRevisionId,md5Checksum,size,webViewLink'


def credentials(root, interactive=False, credentials_store=None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from .oauth import authorize
    if credentials_store is None:
        from .instance import load_instance
        credentials_store = load_instance(root).credentials_store
    directory = Path(credentials_store).resolve()
    client, token = directory/'credentials.json', directory/'token.json'
    creds = Credentials.from_authorized_user_file(str(token)) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid or not creds.has_scopes(SCOPES):
        if not interactive:
            raise RuntimeError('Drive authorization missing from Private Instance; run make auth')
        if not client.exists():
            raise RuntimeError('Desktop OAuth client JSON missing from Private Instance credentials store')
        if 'installed' not in json.loads(client.read_text()):
            raise ValueError('Expected Desktop OAuth client (installed)')
        creds = authorize(client, SCOPES)
    directory.mkdir(parents=True,exist_ok=True)
    os.chmod(directory,0o700)
    atomic(token,creds.to_json().encode()); os.chmod(token,0o600)
    return creds


class Drive:
    def __init__(self, root, instance=None):
        from googleapiclient.discovery import build
        creds=credentials(root, credentials_store=instance.credentials_store if instance else None)
        self.api = build('drive','v3',credentials=creds,cache_discovery=False)
        from .native import NativeDocs
        docs_api = build('docs','v1',credentials=creds,cache_discovery=False)
        self.docs=NativeDocs(docs_api)
        self._read_clients = (self.api, docs_api)

    def _reset_read_connections(self):
        """Discard broken pooled sockets before retrying a read, never a write."""
        for api in getattr(self, '_read_clients', (self.api,)):
            http = getattr(api, '_http', None)
            http = getattr(http, 'http', http)
            if http is not None:
                http.close()

    def get_managed_doc(self,file_id):
        if self.meta(file_id)['mimeType']!='application/vnd.google-apps.document':
            raise ValueError('Not a native Doc')
        return retry_read(lambda: self.docs.get(file_id), label='native doc',
                          reset=self._reset_read_connections)

    def put_managed_doc(self,file_id,content):
        if self.meta(file_id)['mimeType']!='application/vnd.google-apps.document':
            raise ValueError('Not a native Doc')
        return self.docs.put(file_id,content)

    def index_cba_keys(self, parent):
        """List all pages once; reject ambiguous identities before any writes."""
        index = {}
        for item in self.list(parent):
            key = item.get('appProperties', {}).get('cba_key')
            if key is None:
                continue
            if key in index:
                raise RuntimeError('Duplicate release object key')
            index[key] = item
        return index

    def ensure_copy(self,parent,key,file_id,name,*,index=None):
        index = self.index_cba_keys(parent) if index is None else index
        source_mime = self.meta(file_id)['mimeType']
        if key in index:
            existing = index[key]
            if existing['mimeType'] != source_mime or parent not in existing.get('parents', []):
                raise RuntimeError('Existing backup copy MIME/parent mismatch')
            return existing['id']
        result = self.api.files().copy(fileId=file_id,body={'name':name,'parents':[parent],
            'appProperties':{'cba_key':key}},fields='id',supportsAllDrives=True).execute(num_retries=0)['id']
        index[key] = {'id': result, 'mimeType': source_mime, 'parents': [parent],
                      'appProperties': {'cba_key': key}}
        return result

    def meta(self, file_id):
        return retry_read(lambda: self.api.files().get(fileId=file_id,fields=FIELDS,
            supportsAllDrives=True).execute(num_retries=0), label='meta',
            reset=self._reset_read_connections)

    def get(self, file_id):
        meta = self.meta(file_id)
        if meta['mimeType'].startswith('application/vnd.google-apps.'):
            raise ValueError('Native object requires native adapter; raw download rejected')
        return self._download(file_id)

    def _download(self, file_id):
        """Raw content read after MIME has been checked by get or the live index."""
        from googleapiclient.http import MediaIoBaseDownload
        def download():
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf,self.api.files().get_media(fileId=file_id,supportsAllDrives=True))
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=0)
            return buf.getvalue()
        return retry_read(download, label='download', reset=self._reset_read_connections)

    def put(self, file_id, content, mime):
        from googleapiclient.http import MediaIoBaseUpload
        actual = self.meta(file_id)['mimeType']
        if actual != mime or actual.startswith('application/vnd.google-apps.'):
            raise ValueError('MIME mismatch/native byte update rejected')
        return self.api.files().update(fileId=file_id,media_body=MediaIoBaseUpload(io.BytesIO(content),mimetype=mime),
                                      fields=FIELDS,supportsAllDrives=True).execute(num_retries=0)

    def move(self,file_id,destination,previous):
        return self.api.files().update(fileId=file_id,addParents=destination,removeParents=previous,
            fields=FIELDS,supportsAllDrives=True).execute(num_retries=0)

    def list(self, parent):
        items, page = [], None
        while True:
            result = retry_read(lambda: self.api.files().list(
                q=f"'{parent}' in parents and trashed=false",pageToken=page,
                pageSize=1000,fields=f'nextPageToken,files({FIELDS},appProperties)',supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute(num_retries=0), label='list',
                reset=self._reset_read_connections)
            items.extend(result.get('files',[])); page=result.get('nextPageToken')
            if not page: return items

    def ensure(self, parent, key, name, mime, content=None, *, index=None):
        # Recovery after a successful create with a lost response: same key, no blind duplicate.
        index = self.index_cba_keys(parent) if index is None else index
        if key in index:
            result=index[key]
            if result['mimeType']!=mime or parent not in result.get('parents', []):
                raise ValueError('Existing object MIME/parent mismatch')
            if content is not None:
                if mime.startswith('application/vnd.google-apps.'):
                    raise ValueError('Native object requires native adapter; raw download rejected')
                if digest(self._download(result['id']))!=digest(content):
                    raise RuntimeError('Immutable release object differs')
            return result['id']
        from googleapiclient.http import MediaIoBaseUpload
        body={'name':name,'parents':[parent],'mimeType':mime,'appProperties':{'cba_key':key}}
        kwargs={'body':body,'fields':'id','supportsAllDrives':True}
        if content is not None: kwargs['media_body']=MediaIoBaseUpload(io.BytesIO(content),mimetype=mime)
        result = self.api.files().create(**kwargs).execute(num_retries=0)['id']
        index[key] = {'id': result, 'mimeType': mime, 'parents': [parent],
                      'appProperties': {'cba_key': key}}
        return result
