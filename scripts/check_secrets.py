"""Reject staged credentials and private keys before committing."""
import pathlib
import re
import subprocess
import sys

paths=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z']).decode().split('\0')
bad=[]
for path in filter(None,paths):
    name=pathlib.PurePosixPath(path).name.lower()
    if '.credentials' in pathlib.PurePosixPath(path).parts or name in ('token.json','credentials.json','.env') or name.startswith('client_secret'):
        bad.append(path);continue
    data=subprocess.check_output(['git','show',':'+path])
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"refresh_token"\s*:\s*"[^"\s]+"|gh[pousr]_[A-Za-z0-9]{30,}',data):
        bad.append(path)
if bad:
    print('Blocked credential files: '+', '.join(bad),file=sys.stderr);sys.exit(1)
print('Staged credential scan passed')
