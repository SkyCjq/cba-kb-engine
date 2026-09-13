import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from cba_kb.current_state import clean, security

ROOT = Path(__file__).resolve().parents[1]


def fake_value():
    return '_'.join(['FAKE', 'CREDENTIAL', 'FOR', 'TEST', 'ONLY', '12345'])


@pytest.mark.parametrize('key', [
    'api_key', 'API_KEY', 'apiKey', 'client_secret', 'refresh_token', 'access_token',
    'id_token', 'private_key', 'password', 'session_token', 'session_id', 'cookie',
])
@pytest.mark.parametrize('format', ['json', 'yaml', 'env'])
def test_fake_secret_corpus_is_blocked_and_redacted(key, format):
    value = fake_value()
    payload = (json.dumps(dict(zip([key], [value]))) if format == 'json'
               else key + (': ' if format == 'yaml' else '=') + value)
    findings = security().scan_bytes(payload.encode(), 'candidate.txt')
    assert findings
    assert value not in json.dumps(findings)
    with pytest.raises(RuntimeError) as error:
        clean(payload.encode(), 'candidate.txt')
    assert value not in str(error.value)
    assert all(set(item) == {'path', 'rule_id', 'line', 'fingerprint'} for item in findings)


def test_private_keys_bearer_and_provider_tokens():
    values = [
        '-----BEGIN ' + 'PRIVATE KEY-----',
        'Authorization: ' + 'Bearer ' + fake_value(),
        'ghp_' + 'A' * 36,
        'sk-' + 'A' * 30,
        'https://user:' + fake_value() + '@example.test/',
    ]
    for value in values:
        findings = security().scan_bytes(value.encode(), 'report.md')
        assert findings
        assert value not in json.dumps(findings)


def test_plain_identifiers_and_environment_references_are_not_secrets():
    content = '\n'.join([
        'Drive ID: 1E-keiN92cubFQNcuhO5jhPEosp9ypPfK',
        'Git SHA: ' + 'f' * 40,
        'SHA-256: ' + 'a' * 64,
        'https://drive.google.com/file/d/public-identifier/view?usp=drivesdk',
        'Environment names: OPENAI_API_KEY GOOGLE_APPLICATION_CREDENTIALS',
        'api_' + 'key=' + '$' + '{OPENAI_API_KEY}',
        'client_' + 'secret: ' + '<CLIENT_SECRET>',
    ])
    assert security().scan_bytes(content.encode(), 'config.example') == []
    assert security().scan_bytes(content.encode(), '.env.example') == []


@pytest.mark.parametrize('name', [
    '.env', '.env.production', 'credentials.json', 'token.json',
    'client_secret_desktop.json', 'service-account.json', 'ssh_private_key',
])
def test_high_risk_filenames_cannot_be_archived_even_when_empty(name):
    assert security().scan_bytes(b'', 'before/' + name)


def test_nested_office_xml_and_split_text_runs_are_scanned():
    secret = fake_value()
    xml = '<document><t>access_</t><t>token=</t><t>' + secret + '</t></document>'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('word/document.xml', xml)
    findings = security().scan_bytes(archive.getvalue(), 'report.docx')
    assert findings and secret not in json.dumps(findings)


def test_archive_member_names_and_malformed_zip_fail_closed():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('credentials.json', '{}')
    assert security().scan_bytes(archive.getvalue(), 'backup.zip')
    assert security().scan_bytes(b'PKbroken', 'archive.zip')
    assert security().scan_bytes(b'not an office file', 'report.xlsx')


def test_archive_limits_and_recursion_fail_closed(monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('large.txt', b'a' * 101)
    monkeypatch.setattr(security(), 'MAX_ARCHIVE_BYTES', 100)
    findings = security().scan_bytes(archive.getvalue(), 'archive.zip')
    assert any(item['rule_id'] == 'S_ARCHIVE_LIMIT' for item in findings)


def test_multiline_and_json_unicode_escaping_do_not_hide_values():
    value = fake_value()
    raw = '{\n"access_' + 'token":\n"' + value + '"\n}'
    assert security().scan_bytes(raw.encode(), 'report.json')
    encoded = json.dumps(dict(zip(['access_token'], [value]))).replace('access', r'\u0061ccess')
    assert security().scan_bytes(encoded.encode(), 'report.json')


def test_staged_blob_scan_cannot_be_bypassed_by_clean_worktree(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path / 'report.md'
    value = fake_value()
    path.write_text('refresh_' + 'token=' + value)
    subprocess.run(['git', 'add', 'report.md'], cwd=tmp_path, check=True)
    path.write_text('Clean working tree')
    findings = security().scan_git(tmp_path)
    assert findings and value not in json.dumps(findings)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_secrets.py')],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 1
    assert value not in result.stderr + result.stdout


def test_git_tracked_scan_and_credential_free_ci_contract():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    assert all('.credentials' not in Path(path).parts for path in tracked if path)
    assert subprocess.run(
        ['git', 'check-ignore', '-q', '.credentials'], cwd=ROOT,
    ).returncode == 0
    workflow = (ROOT / '.github/workflows/offline-tests.yml').read_text()
    import re
    assert not re.search(r'\$\{\{[^}]*\bsecrets(?:\.|\[)', workflow)
    assert 'id-' + 'token: write' not in workflow
    import yaml
    steps = yaml.safe_load(workflow)['jobs']['tests']['steps']
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['fetch-depth'] == 0
    scanner = next(i for i, step in enumerate(steps)
                   if step.get('run') == 'python scripts/check_secrets.py --tracked')
    regression = next(i for i, step in enumerate(steps) if step.get('name') == 'Offline regression suite')
    assert scanner < regression


def test_document_lane_has_no_remote_scraping_or_private_paths():
    paths = [
        ROOT/'src/cba_kb/document_sources.py',
        ROOT/'src/cba_kb/document_lane.py',
    ]
    text = '\n'.join(path.read_text() for path in paths)
    assert 'requests' not in text
    assert 'urlopen' not in text
    assert 'mp.weixin.qq.com' not in text
    assert '/Users/' not in text
    sources = paths[0].read_text()
    assert 'drive.docs.document(file_id)' in sources
    lane = paths[1].read_text()
    assert '"canonical_write_allowed": False' in lane
