"""List the Drive inbox and download matching source files (read-only)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from cba_kb.common import atomic, digest, save
from cba_kb.drive import Drive

INBOX = '1wKERXv7u_BcxSAdPXZpKL_zrv7IcVpPf'
WANTED = ('八一球员_2021赛季中期转会核验', '外籍球员注册信息')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True,
                        help='deployment root that holds .credentials')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--folder', default=INBOX)
    parser.add_argument('--all', action='store_true', help='download every non-native file')
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    drive = Drive(arguments.root)
    files = drive.list(arguments.folder)
    save(arguments.output/'inbox-listing.json',
         [{'id': f['id'], 'name': f['name'], 'mimeType': f['mimeType'],
           'size': f.get('size'), 'modifiedTime': f.get('modifiedTime')} for f in files])
    saved = []
    for item in files:
        name = item['name']
        if not arguments.all and not any(key in name for key in WANTED):
            continue
        record = {'id': item['id'], 'name': name, 'mime': item['mimeType'],
                  'modifiedTime': item.get('modifiedTime')}
        if item['mimeType'].startswith('application/vnd.google-apps.'):
            record['skipped'] = 'native object requires the Docs adapter'
        else:
            data = drive.get(item['id'])
            path = arguments.output/name
            atomic(path, data)
            record.update({'size': len(data), 'sha256': digest(data), 'path': str(path)})
        saved.append(record)
    save(arguments.output/'sources.json', saved)
    print(json.dumps(saved, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
