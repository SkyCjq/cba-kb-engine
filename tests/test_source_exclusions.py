"""Current processing excludes user-selected IDs, even with stale inputs."""
import copy
import csv
import io
import json
import sys

import pytest

import process_inbox
from scripts import fetch_inbox_sources, prepare_v1_5_1, prepare_v1_5_2
from scripts import propose_source_registry


EXCLUDED = (
    '1D72_i0dzR9TmeV_XH8KeBr_BTxxwFPIM',
    '1WYVqi94bqVGOLsD7t1nmMXyTMTU4TOlr',
)


def item(file_id, name='renamed.png'):
    return {'id': file_id, 'name': name, 'mimeType': 'image/png'}


def registry_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=propose_source_registry.COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


@pytest.mark.parametrize('file_id', EXCLUDED)
def test_direct_registration_rejects_excluded_id_without_mutation(file_id):
    rows = [{'source_id': 'drive:kept', 'drive_file_id': 'kept'}]
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError, match='excluded'):
        process_inbox.register_source(rows, item(file_id), relative_path='renamed.png')
    assert rows == before


def test_recursive_current_inbox_excludes_ids_and_keeps_other_images(monkeypatch):
    folders = {
        'inbox': [item(EXCLUDED[0]), {'id': 'nested', 'name': 'nested',
                                    'mimeType': process_inbox.GFOLDER}],
        'nested': [item(EXCLUDED[1]), item('other-image')],
    }
    monkeypatch.setattr(process_inbox, 'list_children', lambda _, folder: folders[folder])
    assert [row['id'] for row in process_inbox.all_inbox_files(None, 'inbox')] == ['other-image']


@pytest.mark.parametrize('all_files', [False, True])
def test_fetch_never_downloads_or_queues_excluded_sources(tmp_path, monkeypatch, all_files):
    files = [item(value, '外籍球员注册信息.png') for value in (*EXCLUDED, 'kept')]
    downloaded = []

    class Drive:
        def __init__(self, root):
            pass

        def list(self, folder):
            return files

        def get(self, file_id):
            downloaded.append(file_id)
            return b'ordinary image'

    monkeypatch.setattr(fetch_inbox_sources, 'Drive', Drive)
    monkeypatch.setattr(sys, 'argv', ['fetch', '--root', str(tmp_path),
                                   '--output', str(tmp_path), *(['--all'] if all_files else [])])
    fetch_inbox_sources.main()
    assert downloaded == ['kept']
    assert [row['id'] for row in json.loads((tmp_path/'sources.json').read_text())] == ['kept']
    # A raw discovery listing remains usable as audit evidence.
    assert len(json.loads((tmp_path/'inbox-listing.json').read_text())) == 3


def test_proposal_drops_excluded_ids_before_filename_planning(tmp_path, monkeypatch):
    sources = [dict(id=value, name='renamed.png', mime='image/png') for value in EXCLUDED]
    sources.append(dict(id='kept', name='2024-2025.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    path = tmp_path/'sources.json'
    path.write_text(json.dumps(sources))
    monkeypatch.setattr(propose_source_registry, 'collect', lambda paths: {'runs': []})
    monkeypatch.setattr(sys, 'argv', ['propose', '--sources', str(path),
                                   '--staging', str(tmp_path), '--output', str(tmp_path)])
    propose_source_registry.main()
    rows = json.loads((tmp_path/'registry-proposal.json').read_text())
    assert [row['source_id'] for row in rows] == ['drive:kept']
    assert len(list(csv.DictReader(io.StringIO((tmp_path/'registry-proposal.csv').read_text())))) == 1


@pytest.mark.parametrize('module,proposal_path', [
    (prepare_v1_5_1, 'workspace/staging/registry-proposal/registry-proposal.json'),
    (prepare_v1_5_2, 'config/v1.5.2_registry_proposal.json'),
])
@pytest.mark.parametrize('identity', ['source_id', 'drive_file_id'])
@pytest.mark.parametrize('only_excluded', [False, True])
def test_merge_filters_stale_current_rows_and_proposals_without_rewriting_history(
        tmp_path, module, proposal_path, identity, only_excluded):
    excluded = [{identity: ('drive:' if identity == 'source_id' else '') + value,
                 'extraction_status': 'ocr_deferred'} for value in EXCLUDED]
    kept = {'source_id': 'drive:kept', 'drive_file_id': 'kept', 'notes': 'retain verbatim'}
    raw = registry_bytes(excluded + ([] if only_excluded else [kept]))
    proposal = tmp_path/proposal_path
    proposal.parent.mkdir(parents=True)
    proposal.write_text(json.dumps(excluded + [dict(source_id='drive:new', drive_file_id='new')]))
    before = proposal.read_bytes()
    result, added = module.merged_registry(tmp_path, raw)
    rows = list(csv.DictReader(io.StringIO(result.decode('utf-8-sig'))))
    assert [row['source_id'] for row in rows] == (['drive:new'] if only_excluded else ['drive:kept', 'drive:new'])
    assert added == ['drive:new']
    if not only_excluded:
        assert rows[0]['notes'] == 'retain verbatim'
    assert proposal.read_bytes() == before
    assert b'ocr_deferred' in raw
    again, added_again = module.merged_registry(tmp_path, result)
    assert again == result
    assert added_again == []


def test_historical_registry_loader_preserves_excluded_records(tmp_path):
    raw = registry_bytes([dict(source_id='drive:'+value, drive_file_id=value,
                               extraction_status='pending_ocr') for value in EXCLUDED])
    archive = tmp_path/'historical.csv'
    archive.write_bytes(raw)
    assert len(process_inbox.load_source_registry(archive)) == 2
    assert archive.read_bytes() == raw
