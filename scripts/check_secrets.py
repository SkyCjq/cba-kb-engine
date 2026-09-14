"""One scanner for Git blobs, publish preflight and readback.

Findings never contain matched content. Office archives are inspected in memory
with bounds; an unreadable, encrypted or oversized archive fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile
from xml.etree import ElementTree

KEY = r'(?:api[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token|id[_-]?token|private[_-]?key|password|passwd|session(?:[_-]?(?:token|id|cookie))?|cookie)'
ASSIGNMENT = re.compile(
    r'(?i)(?<![\w])([\w.-]*' + KEY + r')[\"\']?[ \t]*[:=]'
    r'(?:\s*\"([^\"\r\n]*)\"|\s*\'([^\'\r\n]*)\'|[ \t]*(\$\{[A-Z_][A-Z0-9_]*\}|[^\s,;\}\]\r\n]+))'
)
PATTERNS = {
    'S_PRIVATE_KEY': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'S_BEARER': re.compile(r'(?i)\bBearer\s+([A-Za-z0-9_./+~=-]+)'),
    'S_PROVIDER_TOKEN': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b'),
    'S_URL_CREDENTIAL': re.compile(r'https?://[^\s/@:]+:[^\s/@]+@'),
}
REFERENCE = re.compile(r'(?:\$\{[A-Z_][A-Z0-9_]*\}|\$[A-Z_][A-Z0-9_]*|<[A-Z_][A-Z0-9_]*>|\{\{[^}]+\}\})\Z')
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 2000


def _placeholder(value):
    return (not value or value in {'null', 'None', 'true', 'false', '[REDACTED]'}
            or bool(REFERENCE.fullmatch(value))
            or value.startswith(('os.environ[', 'os.getenv(', 'environ.get(', 'getenv(')))


def _finding(path, rule, value=b'', line=0):
    if isinstance(value, str):
        value = value.encode('utf-8')
    if any(pattern.search(str(path)) for pattern in PATTERNS.values()) or ASSIGNMENT.search(str(path)):
        path = '[path-sha256:' + hashlib.sha256(str(path).encode()).hexdigest()[:12] + ']'
    return {'path': str(path), 'rule_id': rule, 'line': line,
            'fingerprint': 'sha256:' + hashlib.sha256(value).hexdigest()[:12]}


def _scan_text(text, path):
    findings = []
    for match in ASSIGNMENT.finditer(text):
        value = next(group for group in match.groups()[1:] if group is not None)
        if (str(path).endswith('.py') and match.group(4) is not None
                and re.match(r'[A-Za-z_]\w*(?:\.\w+)*(?:\(|\[)', value)):
            continue  # Python computation/reference, not a literal credential value.
        if not _placeholder(value):
            findings.append(_finding(path, 'S_SECRET_VALUE', value, text.count('\n', 0, match.start()) + 1))
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            if rule == 'S_BEARER' and _placeholder(match.group(1)):
                continue
            findings.append(_finding(path, rule, match.group(), text.count('\n', 0, match.start()) + 1))
    return findings


def scan_bytes(data, path='<payload>', *, _depth=0, _budget=None):
    """Return redacted findings for exact bytes, including zipped Office XML."""
    if not isinstance(data, bytes):
        raise TypeError('Secret scanner requires bytes')
    budget = _budget if _budget is not None else [MAX_ARCHIVE_BYTES, MAX_MEMBERS]
    member_name = str(path).rsplit('!', 1)[-1].replace('\\', '/')
    name = PurePosixPath(member_name).name.lower()
    parts = PurePosixPath(member_name).parts
    findings = []
    if ('.credentials' in parts or name in {'credentials.json', 'token.json', '.env'}
            or name.startswith(('client_secret', 'service-account', 'service_account'))
            or '_private_key' in name
            or (name.startswith('.env.') and name not in {'.env.example', '.env.template'})):
        findings.append(_finding(path, 'S_CREDENTIAL_FILE', data))
    is_zip = data.startswith(b'PK') or name.endswith(('.zip', '.xlsx', '.docx', '.pptx', '.jar', '.odt', '.ods'))
    if is_zip:
        if _depth >= 3:
            return findings + [_finding(path, 'S_ARCHIVE_LIMIT', data)]
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > budget[1] or sum(m.file_size for m in members) > budget[0]:
                    return findings + [_finding(path, 'S_ARCHIVE_LIMIT', data)]
                budget[1] -= len(members)
                for member in members:
                    if member.is_dir():
                        continue
                    if member.flag_bits & 1:
                        return findings + [_finding(path, 'S_ARCHIVE_UNREADABLE', data)]
                    budget[0] -= member.file_size
                    content = archive.read(member)
                    member_path = str(path) + '!' + member.filename
                    findings.extend(scan_bytes(content, member_path, _depth=_depth + 1, _budget=budget))
                    if member.filename.endswith('.xml'):
                        try:
                            fragments = list(ElementTree.fromstring(content).itertext())
                            for separator in ('', ' '):
                                findings.extend(_scan_text(separator.join(fragments), member_path))
                        except ElementTree.ParseError:
                            findings.append(_finding(member_path, 'S_ARCHIVE_UNREADABLE', content))
        except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, NotImplementedError):
            findings.append(_finding(path, 'S_ARCHIVE_UNREADABLE', data))
        return findings
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    text = data.decode(encoding, errors='replace')
    findings.extend(_scan_text(text, path))
    if name.endswith('.json') or text.lstrip().startswith(('{', '[')):
        try:
            decoded = json.loads(text)
            normalized = json.dumps(decoded, ensure_ascii=False)
            if normalized != text:
                findings.extend(_scan_text(normalized, path))
        except (ValueError, RecursionError):
            pass  # Free text is still scanned above.
    return findings


def require_clean(data, path='<payload>'):
    findings = scan_bytes(data, path)
    if findings:
        raise RuntimeError('SECRET_GUARD: ' + json.dumps(findings, ensure_ascii=True, sort_keys=True))


def scan_git(root='.', *, staged=True):
    """Scan index blobs, not working-tree substitutes; tracked mode scans HEAD."""
    command = (['diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'] if staged
               else ['ls-tree', '-r', '--name-only', '-z', 'HEAD'])
    paths = subprocess.check_output(['git', *command], cwd=root).decode().split('\0')
    findings = []
    for path in filter(None, paths):
        ref = ':' + path if staged else 'HEAD:' + path
        data = subprocess.check_output(['git', 'show', ref], cwd=root)
        findings.extend(scan_bytes(data, path))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracked', action='store_true', help='Scan every HEAD blob (CI)')
    parser.add_argument('paths', nargs='*', type=Path, help='Explicit candidate/readback files')
    args = parser.parse_args(argv)
    findings = []
    if args.paths:
        for path in args.paths:
            findings.extend(scan_bytes(path.read_bytes(), str(path)))
    else:
        findings = scan_git(staged=not args.tracked)
    if findings:
        print(json.dumps({'status': 'FAIL', 'findings': findings}, sort_keys=True), file=sys.stderr)
        return 1
    print('Secret scan passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
