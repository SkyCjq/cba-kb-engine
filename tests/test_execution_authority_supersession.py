"""Official fail-closed execution-authority supersession for in-flight releases."""
import json

import pytest

from cba_kb import release as release_module
from cba_kb.common import digest, read, save

OLD_EXECUTION = 'b' * 40
NEW_EXECUTION = 'c' * 40
NEXT_EXECUTION = 'd' * 40
ADOPTED_AT = '2026-09-26T14:00:00Z'


def _moment():
    import datetime
    return datetime.datetime(2026, 9, 26, 14, 0, 0, tzinfo=datetime.timezone.utc)


def _write_plan(root, release_id='v1.8.1-1'):
    entries = []
    for i in range(3):
        before = root / 'before' / str(i)
        candidate = root / 'candidate' / str(i)
        before.parent.mkdir(parents=True, exist_ok=True)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        before.write_bytes(b'old-%d' % i)
        candidate.write_bytes(b'new-%d' % i)
        entries.append({
            'id': 'target-%d' % i,
            'name': 'target-%d' % i,
            'logical_key': 'logical-%d' % i,
            'mime': 'text/plain',
            'before': 'before/%d' % i,
            'candidate': 'candidate/%d' % i,
            'before_hash': digest(b'old-%d' % i),
            'after_hash': digest(b'new-%d' % i),
        })
    return {
        'release_id': release_id,
        'environment': 'production',
        'previous_release_id': 'v1.8.0-1',
        'status_before_hash': 'e' * 64,
        'closure': {'code_commit': 'a' * 40, 'previous_code_commit': '9' * 40},
        'entries': entries,
    }


def _journal(state='ARCHIVING', execution=OLD_EXECUTION, **overrides):
    journal = {
        'state': state,
        'uploaded': {},
        'inflight': None,
        'execution_authority': {
            'file_id': 'authority-old',
            'sha256': '1' * 64,
            'release_execution_sha': execution,
        },
    }
    journal.update(overrides)
    return journal


def _plan(tmp_path, release_id='v1.8.1-1'):
    root = tmp_path / 'release'
    root.mkdir(parents=True, exist_ok=True)
    return _write_plan(root, release_id)


def _root(tmp_path, plan, journal):
    root = tmp_path / 'release'
    root.mkdir(parents=True, exist_ok=True)
    save(root / 'plan.json', plan)
    save(root / 'journal.json', journal)
    return root


def _authority_value(plan, root, execution=NEW_EXECUTION, previous=OLD_EXECUTION, **overrides):
    value = {
        'schema_version': 'cba-kb.release-execution-rebind.v1',
        'classification': 'V181_PRODUCTION_GO_EXECUTION_REBIND',
        'status': 'APPROVED',
        'approved_by': 'HUMAN_WEB',
        'release_id': plan['release_id'],
        'product_candidate_sha': plan['closure']['code_commit'],
        'candidate_entry_count': len(plan['entries']),
        'candidate_hash_set_sha256': release_module._candidate_hash_set(plan),
        'target_id_count': len(plan['entries']),
        'plan_sha256': digest((root / 'plan.json').read_bytes()),
        'journal_sha256': 'f' * 64,
        'journal_state': 'PREPARED',
        'release_readiness_file_id': release_module.V181_READINESS_ID,
        'release_readiness_sha256': release_module.V181_READINESS_SHA256,
        'original_production_go_file_id': release_module.V181_PRODUCTION_GO_ID,
        'original_production_go_authority_payload_sha256':
            release_module.V181_PRODUCTION_GO_PAYLOAD_SHA256,
        'previous_release_execution_sha': previous,
        'release_execution_sha': execution,
        'release_merge_sha': execution,
        'production_execution_sha_expected': execution,
        'release_ci_workflow': 'Offline tests',
        'release_ci_event': 'push',
        'release_ci_run_id': 12345,
        'release_ci_head_sha': execution,
        'release_ci_conclusion': 'success',
        'production_baseline_state': 'COMPLETE',
        'production_baseline_release_id': plan['previous_release_id'],
        'production_baseline_release_status_sha256': plan['status_before_hash'],
        'publish_authorized': True,
        'pre_publish_canary_required': True,
        'fail_closed_on_binding_drift': True,
        'restore_authorized': False,
        'official_controlled_publish_only': True,
        'product_candidate_content_must_remain_unchanged': True,
    }
    value.update(overrides)
    return value


def _authority(plan, root, file_id='authority-new', sha256='a' * 64, **overrides):
    return {
        'file_id': file_id,
        'sha256': sha256,
        'value': _authority_value(plan, root, **overrides),
    }


def _entry(sequence, predecessor=OLD_EXECUTION, successor=NEW_EXECUTION, **overrides):
    entry = {
        'sequence': sequence,
        'classification': release_module.SUPERSESSION_CLASSIFICATION,
        'reason': release_module.SUPERSESSION_REASON,
        'state_at_adoption': 'ARCHIVING',
        'pre_supersession_journal_sha256': '2' * 64,
        'from': {'file_id': 'authority-old', 'sha256': '1' * 64,
                 'release_execution_sha': predecessor},
        'to': {'file_id': 'authority-new', 'sha256': 'a' * 64,
               'release_execution_sha': successor},
        'release_id': 'v1.8.1-1',
        'plan_sha256': '3' * 64,
        'adopted_at': ADOPTED_AT,
    }
    entry.update(overrides)
    return entry


def _assert_rejected(root, plan, authority, match):
    before = (root / 'journal.json').read_bytes()
    with pytest.raises(ValueError, match=match):
        release_module.adopt_superseding_authority(root, plan, authority)
    assert (root / 'journal.json').read_bytes() == before


def _bound_history(root, *entries):
    plan_sha256 = digest((root / 'plan.json').read_bytes())
    return [{**entry, 'plan_sha256': plan_sha256} for entry in entries]


# ------------------------------------------------------------------ positive


def test_supersession_adopts_valid_superseding_authority(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    authority = _authority(plan, root)
    result = release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    assert result['status'] == 'SUPERSEDED'
    journal = read(root / 'journal.json')
    assert journal['execution_authority'] == {
        'file_id': 'authority-new', 'sha256': 'a' * 64,
        'release_execution_sha': NEW_EXECUTION,
    }
    assert journal['state'] == 'ARCHIVING'
    assert journal['uploaded'] == {} and journal['inflight'] is None
    assert result['post_journal_sha256'] == digest((root / 'journal.json').read_bytes())
    assert result['pre_journal_sha256'] != result['post_journal_sha256']


def test_supersession_audit_chain_preserves_old_and_new_authority(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    before = (root / 'journal.json').read_bytes()
    release_module.adopt_superseding_authority(
        root, plan, _authority(plan, root), now=_moment(),
    )
    journal = read(root / 'journal.json')
    history = journal['authority_supersessions']
    assert len(history) == 1
    entry = history[0]
    assert entry['classification'] == release_module.SUPERSESSION_CLASSIFICATION
    assert entry['reason'] == release_module.SUPERSESSION_REASON
    assert entry['sequence'] == 1
    assert entry['state_at_adoption'] == 'ARCHIVING'
    assert entry['pre_supersession_journal_sha256'] == digest(before)
    assert entry['from']['release_execution_sha'] == OLD_EXECUTION
    assert entry['to']['release_execution_sha'] == NEW_EXECUTION
    assert entry['adopted_at'] == ADOPTED_AT
    assert entry['plan_sha256'] == digest((root / 'plan.json').read_bytes())
    # transaction identity, candidate and archive checkpoint are untouched
    assert journal['state'] == 'ARCHIVING'
    assert set(journal) == {'state', 'uploaded', 'inflight', 'execution_authority',
                            'authority_supersessions'}


def test_supersession_replay_is_idempotent(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    authority = _authority(plan, root)
    first = release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    adopted = (root / 'journal.json').read_bytes()
    second = release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    assert first['status'] == 'SUPERSEDED'
    assert second['status'] == 'ALREADY_ADOPTED'
    assert second['sequence'] == 1
    assert (root / 'journal.json').read_bytes() == adopted


def test_security_preflight_passes_under_adopted_executor(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    authority = _authority(plan, root)
    release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    observed = {}

    def verify(repo, product_candidate_sha, release_execution_sha=None):
        observed.update(product=product_candidate_sha, execution=release_execution_sha)
        return {'status': 'PASS'}

    monkeypatch.setattr(release_module, 'verify_code_provenance', verify)
    result = release_module.security_preflight(root, plan, authority)
    assert result['status'] == 'PASS'
    assert observed == {'product': plan['closure']['code_commit'],
                        'execution': NEW_EXECUTION}
    assert result['release_execution_sha'] == NEW_EXECUTION


def test_publish_continues_from_superseded_checkpoint(tmp_path, monkeypatch):
    from test_release import _add_v181_post_freeze_evidence, closure_setup
    from cba_kb.release import publish

    d, root = closure_setup(tmp_path, 'v1.8.1-1')
    plan = read(root / 'plan.json')
    plan['environment'] = 'production'
    save(root / 'plan.json', plan)
    plan = read(root / 'plan.json')
    _add_v181_post_freeze_evidence(d, root)
    prepared_journal_sha = digest((root / 'journal.json').read_bytes())
    save(root / 'journal.json', _journal())
    value = _authority_value(plan, root, journal_sha256=prepared_journal_sha,
                             journal_state='PREPARED')
    raw = json.dumps(value, sort_keys=True).encode()
    d.add('authority-new', raw, 'application/json')
    binding = {'file_id': 'authority-new', 'sha256': digest(raw)}
    authority = release_module.read_execution_authority(d, binding)
    adopted = release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    assert adopted['status'] == 'SUPERSEDED'
    monkeypatch.setattr(
        release_module, 'verify_code_provenance',
        lambda repo, product, execution=None: {'status': 'PASS'},
    )
    journal = publish(d, root, True, binding)
    assert journal['state'] == 'COMPLETE'
    assert journal['execution_authority']['release_execution_sha'] == NEW_EXECUTION
    assert journal['authority_supersessions'][-1]['to']['release_execution_sha'] == NEW_EXECUTION
    status = json.loads(d.get('status'))
    assert status['state'] == 'COMPLETE'
    assert status['release_execution_sha'] == NEW_EXECUTION
    assert status['product_candidate_sha'] == plan['closure']['code_commit']


def test_supersession_chain_supports_sequential_repairs(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    release_module.adopt_superseding_authority(root, plan, _authority(plan, root), now=_moment())
    second = _authority(plan, root, file_id='authority-next', sha256='9' * 64,
                        execution=NEXT_EXECUTION, previous=NEW_EXECUTION)
    result = release_module.adopt_superseding_authority(root, plan, second, now=_moment())
    assert result['status'] == 'SUPERSEDED'
    journal = read(root / 'journal.json')
    assert [entry['to']['release_execution_sha']
            for entry in journal['authority_supersessions']] == [NEW_EXECUTION, NEXT_EXECUTION]
    assert journal['execution_authority']['release_execution_sha'] == NEXT_EXECUTION
    assert journal['state'] == 'ARCHIVING'


# ------------------------------------------------------------------ negative


@pytest.mark.parametrize(('name', 'overrides', 'error'), [
    ('previous-executor', {'previous_release_execution_sha': NEXT_EXECUTION},
     'AUTHORITY_SUPERSESSION_PREDECESSOR_MISMATCH'),
    ('release-id', {'release_id': 'v1.8.0-1'}, 'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('product-candidate', {'product_candidate_sha': '0' * 40},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('candidate-hash-set', {'candidate_hash_set_sha256': '0' * 64},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('candidate-count', {'candidate_entry_count': 4},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('plan-identity', {'plan_sha256': '0' * 64},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('target-ids', {'target_id_count': 4},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('readiness', {'release_readiness_sha256': '0' * 64},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('production-go', {'original_production_go_file_id': 'wrong'},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('baseline-release', {'production_baseline_release_id': 'v1.8.0-2'},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('baseline-hash', {'production_baseline_release_status_sha256': '0' * 64},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('baseline-state', {'production_baseline_state': 'PARTIAL'},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('publish-not-authorized', {'publish_authorized': False},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('restore-implied', {'restore_authorized': True},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('approval-status', {'status': 'PENDING'},
     'AUTHORITY_SUPERSESSION_BINDING_INVALID'),
    ('executor-merge-sha', {'release_merge_sha': NEXT_EXECUTION},
     'AUTHORITY_SUPERSESSION_EXECUTOR_INVALID'),
    ('executor-expected-sha', {'production_execution_sha_expected': NEXT_EXECUTION},
     'AUTHORITY_SUPERSESSION_EXECUTOR_INVALID'),
])
def test_supersession_rejects_any_binding_drift(tmp_path, name, overrides, error):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    authority = _authority(plan, root, **overrides)
    _assert_rejected(root, plan, authority, error)


@pytest.mark.parametrize('state', ['PREPARED', 'PUBLISHING', 'VERIFYING', 'COMPLETE',
                                   'FAILED', 'ROLLED_BACK'])
def test_supersession_rejects_disallowed_journal_states(tmp_path, state):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal(state=state))
    _assert_rejected(root, plan, _authority(plan, root),
                     'AUTHORITY_SUPERSESSION_STATE_INVALID')


@pytest.mark.parametrize('journals', [
    {'uploaded': {'target-0': True}},
    {'inflight': 'target-0'},
])
def test_supersession_rejects_conflicting_checkpoint_state(tmp_path, journals):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal(**journals))
    _assert_rejected(root, plan, _authority(plan, root),
                     'AUTHORITY_SUPERSESSION_STATE_CONFLICT')


def test_supersession_requires_recorded_predecessor(tmp_path):
    plan = _plan(tmp_path)
    journal = _journal()
    del journal['execution_authority']
    root = _root(tmp_path, plan, journal)
    _assert_rejected(root, plan, _authority(plan, root),
                     'EXECUTION_AUTHORITY_JOURNAL_BINDING_INVALID')


def test_supersession_rejects_history_loss(tmp_path):
    plan = _plan(tmp_path)
    journal = _journal(execution=NEW_EXECUTION)
    journal['execution_authority'] = {'file_id': 'authority-new', 'sha256': 'a' * 64,
                                      'release_execution_sha': NEW_EXECUTION}
    root = _root(tmp_path, plan, journal)
    _assert_rejected(root, plan, _authority(plan, root),
                     'AUTHORITY_SUPERSESSION_HISTORY_INVALID')


def test_supersession_rejects_incompatible_recorded_successor(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    history = _bound_history(root, _entry(1, successor=NEXT_EXECUTION))
    journal = _journal(execution=OLD_EXECUTION, authority_supersessions=history)
    save(root / 'journal.json', journal)
    _assert_rejected(root, plan, _authority(plan, root),
                     'AUTHORITY_SUPERSESSION_HISTORY_INVALID')


def test_supersession_rejects_conflicting_successor_for_same_predecessor(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    history = _bound_history(
        root,
        _entry(1, predecessor=OLD_EXECUTION, successor=NEW_EXECUTION),
        _entry(2, predecessor=NEW_EXECUTION, successor=OLD_EXECUTION,
               to={'file_id': 'authority-new', 'sha256': 'a' * 64,
                   'release_execution_sha': OLD_EXECUTION}),
    )
    journal = _journal(execution=OLD_EXECUTION, authority_supersessions=history)
    journal['execution_authority'] = {'file_id': 'authority-new', 'sha256': 'a' * 64,
                                      'release_execution_sha': OLD_EXECUTION}
    save(root / 'journal.json', journal)
    authority = _authority(plan, root, execution=NEXT_EXECUTION)
    _assert_rejected(root, plan, authority, 'AUTHORITY_SUPERSESSION_CONFLICT')


@pytest.mark.parametrize('successor', [OLD_EXECUTION, NEW_EXECUTION])
def test_supersession_rejects_cycles_and_replays(tmp_path, successor):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    history = _bound_history(root, _entry(1, successor=NEW_EXECUTION))
    journal = _journal(execution=NEW_EXECUTION, authority_supersessions=history)
    journal['execution_authority'] = {'file_id': 'authority-new', 'sha256': 'a' * 64,
                                      'release_execution_sha': NEW_EXECUTION}
    save(root / 'journal.json', journal)
    authority = _authority(plan, root, file_id='authority-third', sha256='8' * 64,
                           execution=successor, previous=NEW_EXECUTION)
    _assert_rejected(root, plan, authority, 'AUTHORITY_SUPERSESSION_CYCLE')


def test_supersession_rejects_malformed_history_entry(tmp_path):
    plan = _plan(tmp_path)
    broken = _entry(1)
    del broken['adopted_at']
    journal = _journal(authority_supersessions=[broken])
    root = _root(tmp_path, plan, journal)
    _assert_rejected(root, plan, _authority(plan, root),
                     'AUTHORITY_SUPERSESSION_HISTORY_INVALID')


def test_security_preflight_rejects_inconsistent_supersession_history(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    authority = _authority(plan, root)
    release_module.adopt_superseding_authority(root, plan, authority, now=_moment())
    journal = read(root / 'journal.json')
    journal['authority_supersessions'][0]['to']['release_execution_sha'] = NEXT_EXECUTION
    save(root / 'journal.json', journal)
    monkeypatch.setattr(release_module, 'verify_code_provenance',
                        lambda *args, **kwargs: {'status': 'PASS'})
    with pytest.raises(ValueError, match='EXECUTION_AUTHORITY_JOURNAL_BINDING_INVALID'):
        release_module.security_preflight(root, plan, authority)


def test_supersession_rejects_tampered_candidate_bytes(tmp_path):
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    (root / 'candidate' / '0').write_bytes(b'tampered')
    _assert_rejected(root, plan, _authority(plan, root),
                     'EXECUTION_AUTHORITY_CANDIDATE_INVALID')


def test_supersession_rejects_invalid_authority_readback(tmp_path):
    from test_release import FakeDrive
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    raw = json.dumps(_authority_value(plan, root), sort_keys=True).encode()
    d = FakeDrive()
    d.add('authority-new', raw, 'application/json')
    with pytest.raises(ValueError, match='EXECUTION_AUTHORITY_READBACK_MISMATCH'):
        release_module.read_execution_authority(
            d, {'file_id': 'authority-new', 'sha256': '0' * 64},
        )


def test_supersession_requires_single_writer(tmp_path):
    from test_release import FakeDrive
    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    before = (root / 'journal.json').read_bytes()
    with pytest.raises(RuntimeError, match='Single-writer'):
        release_module.adopt_execution_authority(
            FakeDrive(), root,
            {'file_id': 'authority-new', 'sha256': 'a' * 64},
        )
    assert (root / 'journal.json').read_bytes() == before


def test_cli_adopt_authority_uses_official_path(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from test_release import FakeDrive
    from cba_kb import cli
    import cba_kb.gates

    plan = _plan(tmp_path)
    root = _root(tmp_path, plan, _journal())
    raw = json.dumps(_authority_value(plan, root), sort_keys=True).encode()
    drive = FakeDrive()
    drive.add('authority-new', raw, 'application/json')
    monkeypatch.setattr(cli, 'Drive', lambda *args, **kwargs: drive)
    monkeypatch.setattr(cli, 'load_instance',
                        lambda *args, **kwargs: SimpleNamespace(credentials_store='unused'))
    monkeypatch.setattr(cba_kb.gates, 'authorize_plan', lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, 'argv', [
        'cba-kb', '--root', str(root), '--instance-root', str(tmp_path / 'instance'),
        'adopt-authority', '--release', str(root),
        '--execution-authority-id', 'authority-new',
        '--execution-authority-sha256', digest(raw),
        '--single-writer',
    ])
    cli.main()
    journal = read(root / 'journal.json')
    assert journal['execution_authority'] == {
        'file_id': 'authority-new', 'sha256': digest(raw),
        'release_execution_sha': NEW_EXECUTION,
    }
    assert journal['state'] == 'ARCHIVING'
    assert journal['authority_supersessions'][0]['to']['release_execution_sha'] == NEW_EXECUTION
