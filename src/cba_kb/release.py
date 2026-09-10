"""Frozen binary release plans, optimistic checks, journaling and rollback.

Cross-client exclusion still requires a human-maintained single-writer window.
No native document is accepted by this binary publisher.
"""
from pathlib import Path
from .common import atomic, child, digest, lock, read, save

FOLDER = 'application/vnd.google-apps.folder'


def fingerprint(meta):
    return {k:meta.get(k) for k in ('id','version','modifiedTime','mimeType','parents')}


def snapshot(drive, file_id):
    before=drive.meta(file_id)
    data=drive.get(file_id)
    after=drive.meta(file_id)
    if fingerprint(before)!=fingerprint(after):
        raise RuntimeError('Remote changed during download')
    return data, after


def prepare(drive, root, release_id, entries, archive_id, status_id):
    root=Path(root)
    if root.exists(): raise ValueError('Release already exists; resume it instead')
    if not entries: raise ValueError('Empty release')
    ids=[e['id'] for e in entries]
    if len(ids)!=len(set(ids)) or status_id in ids:
        raise ValueError('Duplicate target or status overlaps payload')
    plan={'release_id':release_id,'archive_id':archive_id,'status_id':status_id,'entries':[]}
    root.mkdir(parents=True)
    try:
        status_data,status_meta=snapshot(drive,status_id)
        import json
        status=json.loads(status_data)
        if status.get('state') not in ('COMPLETE','ROLLED_BACK','INITIAL'):
            raise RuntimeError('Another incomplete release needs recovery')
        atomic(root/'status.before.json',status_data)
        plan['status_before_hash']=digest(status_data)
        plan['status_before_meta']=fingerprint(status_meta)
        plan['previous_release_id']=status.get('current_release_id')
        for i, entry in enumerate(entries):
            name=entry['name']
            if Path(name).name!=name: raise ValueError('Unsafe artifact name')
            new=Path(entry['path']).read_bytes()
            old,meta=snapshot(drive,entry['id'])
            if meta['mimeType']!=entry['mime'] or meta['mimeType'].startswith('application/vnd.google-apps.'):
                raise ValueError('MIME mismatch or native object')
            atomic(root/f'before/{i}',old); atomic(root/f'candidate/{i}',new)
            plan['entries'].append({'id':entry['id'],'name':name,'mime':entry['mime'],
                'before':f'before/{i}','candidate':f'candidate/{i}',
                'before_hash':digest(old),'after_hash':digest(new),'meta':fingerprint(meta)})
        save(root/'plan.json',plan)
        save(root/'journal.json',{'state':'PREPARED','uploaded':{},'inflight':None})
    except BaseException:
        save(root/'prepare_failed.json',{'state':'PREPARE_FAILED'})
        raise
    return plan


def verified_local(root, entry, key):
    data=child(root,entry[key]).read_bytes()
    expected=entry['before_hash' if key=='before' else 'after_hash']
    if digest(data)!=expected: raise RuntimeError('Frozen local artifact was altered')
    return data


def set_status(drive, plan, state, previous_snapshot):
    import json
    status={'state':state,'current_release_id':plan['release_id'] if state=='COMPLETE' else plan['previous_release_id'],
            'pending_release_id':plan['release_id'] if state not in ('COMPLETE','ROLLED_BACK') else None,
            'previous_release_id':plan['previous_release_id'],
            'previous_snapshot':previous_snapshot,
            'artifacts':[{'id':e['id'],'name':e['name'],'sha256':e['after_hash']} for e in plan['entries']]}
    content=json.dumps(status,ensure_ascii=False,sort_keys=True).encode()
    drive.put(plan['status_id'],content,'application/json')
    if drive.get(plan['status_id'])!=content: raise RuntimeError('Status readback failed')


def publish(drive, root, single_writer=False):
    root=Path(root)
    if not single_writer: raise RuntimeError('Single-writer maintenance window must be acknowledged')
    with lock(root.parent/'publish.lock'):
        plan,journal=read(root/'plan.json'),read(root/'journal.json')
        if journal['state']=='ROLLED_BACK': raise RuntimeError('Create a new plan after rollback')
        for e in plan['entries']:
            verified_local(root,e,'before'); verified_local(root,e,'candidate')
        if journal['state']=='COMPLETE':
            import json
            current=json.loads(drive.get(plan['status_id']))
            if current.get('state')!='COMPLETE' or current.get('current_release_id')!=plan['release_id']:
                raise RuntimeError('Publication status no longer refers to this complete release')
            verify(drive,root); return journal
        # Preflight every object before any production mutation.
        for e in plan['entries']:
            data,meta=snapshot(drive,e['id']); sha=digest(data)
            if e['id'] in journal['uploaded'] or journal['inflight']==e['id']:
                if sha==e['after_hash']: continue
                if sha!=e['before_hash']: raise RuntimeError('Conflict while resuming')
            if fingerprint(meta)!=e['meta'] or sha!=e['before_hash']:
                raise RuntimeError('Remote conflict: '+e['name'])
        if journal['state']=='PREPARED' and fingerprint(drive.meta(plan['status_id']))!=plan['status_before_meta']:
            raise RuntimeError('Publication status changed')
        import json
        if journal['state']!='PREPARED':
            status=json.loads(drive.get(plan['status_id']))
            untouched_status=digest(drive.get(plan['status_id']))==plan['status_before_hash']
            if not untouched_status and status.get('pending_release_id')!=plan['release_id'] and status.get('current_release_id')!=plan['release_id']:
                raise RuntimeError('Another publisher changed the release status')
        folder=drive.ensure(plan['archive_id'],plan['release_id'],plan['release_id'],FOLDER)
        previous=[]
        for i,e in enumerate(plan['entries']):
            for kind in ('before','candidate'):
                data=verified_local(root,e,kind)
                fid=drive.ensure(folder,f'{kind}-{i}',f'{kind}_{e["name"]}',e['mime'],data)
                if kind=='before': previous.append({'original_id':e['id'],'snapshot_id':fid,'name':e['name'],'sha256':e['before_hash']})
        drive.ensure(folder,'plan','plan.json','application/json',(root/'plan.json').read_bytes())
        journal['previous_snapshot']=previous
        journal['state']='PUBLISHING'; save(root/'journal.json',journal)
        try:
            set_status(drive,plan,'PUBLISHING',previous)
            for e in plan['entries']:
                data=drive.get(e['id'])
                if digest(data)==e['after_hash']:
                    journal['uploaded'][e['id']]=True
                    save(root/'journal.json',journal); continue
                if digest(data)!=e['before_hash']: raise RuntimeError('Remote changed during publication')
                journal['inflight']=e['id']; save(root/'journal.json',journal)
                drive.put(e['id'],verified_local(root,e,'candidate'),e['mime'])
                if digest(drive.get(e['id']))!=e['after_hash']: raise RuntimeError('Readback mismatch')
                journal['uploaded'][e['id']]=True; journal['inflight']=None
                save(root/'journal.json',journal)
            verify(drive,root)
            set_status(drive,plan,'COMPLETE',previous)
            journal['state']='COMPLETE'; save(root/'journal.json',journal)
        except BaseException:
            journal['state']='FAILED'; save(root/'journal.json',journal)
            try: set_status(drive,plan,'FAILED',previous)
            except Exception: pass  # Local journal/outbox survives network loss.
            raise
        return journal


def verify(drive, root):
    plan=read(Path(root)/'plan.json')
    for e in plan['entries']:
        if drive.meta(e['id'])['mimeType']!=e['mime'] or digest(drive.get(e['id']))!=e['after_hash']:
            raise RuntimeError('Remote verification failed: '+e['name'])
    return {'verified':len(plan['entries'])}


def restore(drive, root, single_writer=False):
    root=Path(root)
    if not single_writer: raise RuntimeError('Single-writer maintenance window required')
    with lock(root.parent/'publish.lock'):
        plan,journal=read(root/'plan.json'),read(root/'journal.json')
        if digest((root/'status.before.json').read_bytes())!=plan['status_before_hash']:
            raise RuntimeError('Frozen previous status was altered')
        import json
        status=json.loads(drive.get(plan['status_id']))
        if status.get('pending_release_id')!=plan['release_id'] and status.get('current_release_id')!=plan['release_id']:
            raise RuntimeError('Cannot roll back another release')
        for e in plan['entries']:
            verified_local(root,e,'before')
            if digest(drive.get(e['id'])) not in (e['before_hash'],e['after_hash']):
                raise RuntimeError('Rollback would overwrite an external edit')
        for e in reversed(plan['entries']):
            old=verified_local(root,e,'before')
            if digest(drive.get(e['id']))!=e['before_hash']: drive.put(e['id'],old,e['mime'])
            if digest(drive.get(e['id']))!=e['before_hash']: raise RuntimeError('Rollback readback failed')
        previous_status=read(root/'status.before.json')
        previous_status['state']='ROLLED_BACK'
        previous_status['rolled_back_release_id']=plan['release_id']
        data=json.dumps(previous_status,ensure_ascii=False,sort_keys=True).encode()
        drive.put(plan['status_id'],data,'application/json')
        if drive.get(plan['status_id'])!=data: raise RuntimeError('Rollback status verification failed')
        journal['state']='ROLLED_BACK'; save(root/'journal.json',journal)
        return journal
