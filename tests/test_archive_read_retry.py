"""Archive checkpoint-resume remote READ recovery.

A retryable remote read failure must never be mistaken for a missing object:
existing immutable objects are re-verified through the bounded read-retry path
and only deterministically missing objects may reach the create path.
"""
import json
import re
from types import SimpleNamespace

import pytest

from cba_kb import release as release_module
from cba_kb import transport
from cba_kb.common import digest
from cba_kb.drive import Drive


class AuthorizationError(Exception):
    """Transport-shaped 403 that must stay deterministic and non-retryable."""

    status_code = 403


class ArchiveDrive(Drive):
    """In-memory Drive exercising the real list/ensure/download read paths."""

    def __init__(self):
        self.files = {}
        self.created = []
        self.calls = []
        self.reads = []
        self.read_transports = []
        self.faults = []
        self.list_faults = []
        self.faults_raised = 0
        self.transport_builds = 0
        self._credentials = SimpleNamespace(name='unchanged-credentials')
        self.docs = None
        self.api = self._make_api()
        self._read_clients = (self.api,)

    def _make_api(self):
        files = SimpleNamespace(
            get_media=self._get_media,
            create=self._create,
            update=self._update,
            list=self._list,
        )
        return SimpleNamespace(files=lambda: files)

    def _build_clients(self):
        self.transport_builds += 1
        self.api = self._make_api()
        self._read_clients = (self.api,)

    def _get_media(self, fileId=None, supportsAllDrives=None):
        return SimpleNamespace(execute=lambda: self._read(fileId))

    def _read(self, file_id):
        self.reads.append(file_id)
        self.read_transports.append(id(self.api))
        if self.faults:
            fault = self.faults.pop(0)
            if fault is not None:
                self.faults_raised += 1
                raise fault
        return self.files[file_id]['data']

    def _list(self, **kwargs):
        assert 'trashed=false' in kwargs['q']

        def execute(num_retries):
            assert num_retries == 0
            if self.list_faults:
                raise self.list_faults.pop(0)
            parent = re.search(r"'([^']+)' in parents", kwargs['q']).group(1)
            return {'files': [self.meta(fid) for fid, item in self.files.items()
                              if parent in item['parents']]}

        return SimpleNamespace(execute=execute)

    def _create(self, **kwargs):
        def execute(num_retries):
            assert num_retries == 0  # writes stay single-attempt
            body = kwargs['body']
            media = kwargs.get('media_body')
            data = media.getbytes(0, media.size()) if media else b''
            file_id = 'created-%d' % len(self.created)
            self.add(file_id, data, body['mimeType'])
            self.files[file_id].update({
                'name': body['name'],
                'parents': list(body['parents']),
                'appProperties': dict(body['appProperties']),
            })
            self.created.append((file_id, body['appProperties']['cba_key']))
            return {'id': file_id}

        return SimpleNamespace(execute=execute)

    def _update(self, **kwargs):
        def execute(num_retries):
            raise AssertionError('archive verification must not mutate objects')

        return SimpleNamespace(execute=execute)

    def add(self, file_id, data, mime='text/plain', parent='folder'):
        self.files[file_id] = {
            'id': file_id, 'name': file_id, 'version': '1', 'mimeType': mime,
            'parents': [parent], 'data': data,
        }

    def add_archive_object(self, parent, key, content, mime='text/plain'):
        file_id = '%s/%s' % (parent, key)
        self.add(file_id, content, mime, parent=parent)
        self.files[file_id]['appProperties'] = {'cba_key': key}
        return file_id

    def meta(self, file_id):
        return {k: v for k, v in self.files[file_id].items() if k != 'data'}

    def put(self, file_id, content, mime):
        raise AssertionError('archive verification must not write')


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(transport.time, 'sleep', lambda delay: None)


@pytest.fixture(autouse=True)
def patch_downloader(monkeypatch):
    import googleapiclient.http

    class Downloader:
        def __init__(self, stream, request):
            self.stream = stream
            self.request = request

        def next_chunk(self, num_retries):
            assert num_retries == 0
            self.stream.write(self.request.execute())
            return None, True

    monkeypatch.setattr(googleapiclient.http, 'MediaIoBaseDownload', Downloader)


# ---------------------------------------------------------------- transport


def test_transient_read_timeout_is_retried_and_recovers():
    attempts, resets = [], []

    def read():
        attempts.append(True)
        if len(attempts) == 1:
            raise TimeoutError('read timeout')
        return 'payload'

    assert transport.retry_read(read, reset=lambda: resets.append(True)) == 'payload'
    assert len(attempts) == 2 and len(resets) == 1


def test_transient_broken_pipe_uses_a_fresh_transport():
    attempts, transports = [], []

    def read():
        attempts.append(True)
        if len(attempts) == 1:
            raise BrokenPipeError('stale socket')
        return 'payload'

    assert transport.retry_read(
        read, label='download', reset=lambda: transports.append('fresh'),
    ) == 'payload'
    assert attempts == [True, True] and transports == ['fresh']


def test_multiple_transient_failures_within_budget_still_pass():
    attempts = []

    def read():
        attempts.append(True)
        if len(attempts) < 4:
            raise TimeoutError('flaky read')
        return 'payload'

    assert transport.retry_read(read) == 'payload'
    assert len(attempts) == 4


def test_transient_read_failures_exceeding_budget_fail_closed():
    attempts, resets = [], []

    def read():
        attempts.append(True)
        raise BrokenPipeError('persistent transport fault')

    with pytest.raises(BrokenPipeError):
        transport.retry_read(read, reset=lambda: resets.append(True))
    assert len(attempts) == transport.RETRY_ATTEMPTS
    assert len(resets) == transport.RETRY_ATTEMPTS - 1


@pytest.mark.parametrize('error', [AuthorizationError('permission denied'),
                                   ValueError('wrong object identity'),
                                   RuntimeError('malformed response')])
def test_non_transient_read_failures_fail_immediately(error):
    attempts = []

    def read():
        attempts.append(True)
        raise error

    with pytest.raises(type(error)):
        transport.retry_read(read, reset=lambda: pytest.fail('non-transient reset'))
    assert len(attempts) == 1


def test_retry_counters_record_retries_and_fresh_transports():
    transport.reset_read_retry_stats()
    attempts = []

    def read():
        attempts.append(True)
        if len(attempts) == 1:
            raise TimeoutError('once')
        return 'ok'

    transport.retry_read(read, reset=lambda: None)
    assert transport.read_retry_stats() == {
        'attempts': 2, 'retries': 1, 'resets': 1, 'failures': 0,
    }
    transport.reset_read_retry_stats()
    assert transport.read_retry_stats() == {
        'attempts': 0, 'retries': 0, 'resets': 0, 'failures': 0,
    }


# --------------------------------------------------------- archive readback


def test_existing_object_timeout_then_valid_read_is_reused():
    d = ArchiveDrive()
    existing = d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [TimeoutError('read timeout')]
    result = d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert result == existing
    assert d.created == [] and d.calls == []
    assert d.reads == [existing, existing]
    assert d.transport_builds == 1


def test_existing_object_broken_pipe_recreates_transport_then_verifies():
    d = ArchiveDrive()
    existing = d.add_archive_object('folder', 'k1', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [BrokenPipeError('broken pipe')]
    result = d.ensure('folder', 'k1', 'candidate_1', 'text/plain', b'payload', index=index)
    assert result == existing
    assert d.transport_builds == 1
    assert len(set(d.read_transports)) == 2  # stale transport is never reused
    assert d.created == []


def test_mixed_inventory_reuses_verified_and_only_creates_missing():
    d = ArchiveDrive()
    existing_keys = ['k%d' % i for i in range(12)]
    for key in existing_keys:
        d.add_archive_object('folder', key, b'payload-' + key.encode())
    index = d.index_cba_keys('folder')
    d.faults = [TimeoutError('t') if i % 5 == 0 else None
                for i in range(len(existing_keys))]
    for i, key in enumerate(existing_keys):
        d.ensure('folder', key, 'candidate_%d' % i, 'text/plain',
                 b'payload-' + key.encode(), index=index)
    missing_keys = ['m%d' % i for i in range(4)]
    for i, key in enumerate(missing_keys):
        d.ensure('folder', key, 'protected_%d' % i, 'text/plain',
                 b'missing-' + key.encode(), index=index)
    assert [key for _, key in d.created] == missing_keys
    assert d.faults_raised == len(d.reads) - len(existing_keys)
    assert d.transport_builds == d.faults_raised
    assert all('k' in key or 'm' in key for _, key in d.created)


def test_archive_resume_with_transient_faults_creates_no_duplicates():
    d = ArchiveDrive()
    keys = ['k%d' % i for i in range(40)]
    for key in keys:
        d.add_archive_object('folder', key, b'payload-' + key.encode())
    index = d.index_cba_keys('folder')
    faults = []
    for i in range(len(keys)):
        if i % 7 == 0:
            faults.append(BrokenPipeError('fault %d' % i))
        faults.append(None)
    d.faults = faults
    for i, key in enumerate(keys):
        d.ensure('folder', key, 'candidate_%d' % i, 'text/plain',
                 b'payload-' + key.encode(), index=index)
    assert d.created == [] and d.calls == []
    assert d.faults_raised >= 4  # transient faults were exercised during resume
    assert d.transport_builds == d.faults_raised
    assert len(set(d.read_transports)) == d.faults_raised + 1


def test_transient_read_failure_never_increments_archive_object_count():
    d = ArchiveDrive()
    d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [TimeoutError('t')] * transport.RETRY_ATTEMPTS
    before = len(d.files)
    with pytest.raises(TimeoutError):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert len(d.files) == before
    assert d.created == [] and d.calls == []


def test_broken_transport_is_not_reused_forever():
    d = ArchiveDrive()
    d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [BrokenPipeError('p')] * (transport.RETRY_ATTEMPTS + 5)
    with pytest.raises(BrokenPipeError):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert len(d.reads) == transport.RETRY_ATTEMPTS
    assert len(set(d.read_transports)) == transport.RETRY_ATTEMPTS
    assert d.created == []


def test_missing_listing_failure_never_becomes_object_creation():
    d = ArchiveDrive()
    d.add_archive_object('folder', 'k0', b'payload')
    d.list_faults = [TimeoutError('listing timeout')] * transport.RETRY_ATTEMPTS
    with pytest.raises(TimeoutError):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload')
    assert d.transport_builds == transport.RETRY_ATTEMPTS - 1
    assert d.created == [] and d.calls == []


def test_duplicate_archive_key_is_rejected_before_any_write():
    d = ArchiveDrive()
    for file_id, content in (('folder/dup-a', b'one'), ('folder/dup-b', b'two')):
        d.add(file_id, content)
        d.files[file_id]['appProperties'] = {'cba_key': 'dup'}
    with pytest.raises(RuntimeError, match='Duplicate release object key'):
        d.index_cba_keys('folder')
    assert d.created == [] and d.calls == []


def test_hash_mismatch_fails_deterministically_without_retry_or_create():
    d = ArchiveDrive()
    existing = d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.files[existing]['data'] = b'tampered'
    with pytest.raises(RuntimeError, match='Immutable release object differs'):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert d.reads == [existing]
    assert d.transport_builds == 0
    assert d.created == [] and d.calls == []


def test_identity_and_binding_mismatch_fail_closed():
    d = ArchiveDrive()
    d.add_archive_object('folder', 'k0', b'payload', mime='text/plain')
    index = d.index_cba_keys('folder')
    with pytest.raises(ValueError, match='MIME/parent mismatch'):
        d.ensure('folder', 'k0', 'candidate_0', 'application/json', b'payload', index=index)
    stale_index = {'k0': {**d.meta('folder/k0'), 'parents': ['other-folder']}}
    with pytest.raises(ValueError, match='MIME/parent mismatch'):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=stale_index)
    assert d.created == [] and d.calls == []


def test_permission_error_is_not_retried_and_creates_nothing():
    d = ArchiveDrive()
    existing = d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [AuthorizationError('permission denied')]
    with pytest.raises(AuthorizationError):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert d.reads == [existing]
    assert d.transport_builds == 0
    assert d.created == [] and d.calls == []


def test_failed_read_recovery_keeps_journal_archiving_checkpoint(tmp_path):
    journal = tmp_path / 'journal.json'
    payload = {'state': 'ARCHIVING', 'uploaded': {}, 'inflight': None,
               'execution_authority': {'file_id': 'authority', 'sha256': 'a' * 64,
                                       'release_execution_sha': 'e' * 40}}
    original = json.dumps(payload, sort_keys=True).encode()
    journal.write_bytes(original)
    d = ArchiveDrive()
    d.add_archive_object('folder', 'k0', b'payload')
    index = d.index_cba_keys('folder')
    d.faults = [TimeoutError('t')] * transport.RETRY_ATTEMPTS
    with pytest.raises(TimeoutError):
        d.ensure('folder', 'k0', 'candidate_0', 'text/plain', b'payload', index=index)
    assert journal.read_bytes() == original
    resumed = json.loads(journal.read_bytes())
    assert resumed['state'] == 'ARCHIVING'
    assert resumed['uploaded'] == {} and resumed['inflight'] is None
    assert resumed['execution_authority']['release_execution_sha'] == 'e' * 40


def test_verified_existing_object_keeps_exact_hash_evidence():
    d = ArchiveDrive()
    keys = ['k%d' % i for i in range(6)]
    for key in keys:
        d.add_archive_object('folder', key, b'payload-' + key.encode())
    index = d.index_cba_keys('folder')
    d.faults = [TimeoutError('t'), None]
    verified = {}
    for key in keys:
        file_id = index[key]['id']
        verified[key] = digest(d._download(file_id))
    assert verified == {key: digest(b'payload-' + key.encode()) for key in keys}
    assert d.created == [] and d.calls == []


class NativeArchiveDrive(ArchiveDrive):
    """Native Docs readback uses the retrying accessor, never a raw API call."""

    def __init__(self):
        super().__init__()
        self.document_reads = []
        self.document_faults = []

        class Docs:
            def __init__(self, owner):
                self.owner = owner

            def document(self, file_id):
                self.owner.document_reads.append(file_id)
                if self.owner.document_faults:
                    raise self.owner.document_faults.pop(0)
                return json.loads(self.owner.files[file_id]['data'].decode())

        self.docs = Docs(self)


def test_native_document_readback_retries_transient_transport_faults():
    d = NativeArchiveDrive()
    d.add('doc-0', json.dumps({'body': 'managed'}).encode(), release_module.DOC)
    d.document_faults = [BrokenPipeError('native doc read')]
    assert d.document_json('doc-0') == {'body': 'managed'}
    assert d.document_reads == ['doc-0', 'doc-0']
    assert d.transport_builds == 1


def test_native_document_readback_fails_closed_after_budget():
    d = NativeArchiveDrive()
    d.add('doc-0', json.dumps({'body': 'managed'}).encode(), release_module.DOC)
    d.document_faults = [TimeoutError('t')] * transport.RETRY_ATTEMPTS
    with pytest.raises(TimeoutError):
        d.document_json('doc-0')
    assert len(d.document_reads) == transport.RETRY_ATTEMPTS
