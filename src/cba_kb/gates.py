"""Explicit target allowlist; production is never enabled by changing sandbox IDs."""
from .common import read


def authorize_plan(drive, root, plan, environment):
    if environment=='sandbox':
        folder=read(root/'config/runtime.json').get('sandbox_folder_id')
        for fid in [e['id'] for e in plan['entries']]+[plan['status_id'],plan['archive_id']]:
            if not folder or folder not in drive.meta(fid).get('parents',[]):
                raise RuntimeError('Sandbox targets required')
        return
    if environment!='production':raise ValueError('Unknown release environment')
    policy=read(root/'config/production.json')
    if policy.get('enabled') is not True:raise RuntimeError('Production policy disabled')
    if plan['status_id']!=policy['status_id'] or plan['archive_id']!=policy['archive_id']:
        raise RuntimeError('Unapproved production control object')
    if not plan.get('dependencies'):raise RuntimeError('Production input dependencies required')
    allowed=policy['targets']
    if {e['id'] for e in plan['entries']}!=set(allowed):
        raise RuntimeError('Production requires the complete approved target set')
    for e in plan['entries']:
        item=allowed[e['id']]
        if e['mime']!=item['mime'] or e.get('mode','binary')!=item.get('mode','binary'):
            raise RuntimeError('Production target type changed')
        if e.get('publish_parent')!=item.get('publish_parent') or e.get('staging_parent')!=item.get('staging_parent'):
            raise RuntimeError('Production move not approved')
        parents=drive.meta(e['id']).get('parents',[])
        if not set(parents)&set(item['allowed_parents']):raise RuntimeError('Production target parent changed')
    if {x['id'] for x in plan['dependencies']}!=set(policy['dependency_ids']):
        raise RuntimeError('Production dependency set changed')
