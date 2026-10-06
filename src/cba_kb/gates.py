"""Explicit target allowlist; production is never enabled by changing sandbox IDs."""
import json
from .common import read


def authorize_plan(drive, root, plan, environment, instance=None):
    if instance is None:
        from .instance import load_instance
        instance=load_instance(root)
    if environment=='sandbox':
        folder=instance.read_json('runtime.json').get('sandbox_folder_id')
        for fid in [e['id'] for e in plan['entries']]+[plan['status_id'],plan['archive_id']]:
            if not folder or folder not in drive.meta(fid).get('parents',[]):
                raise RuntimeError('Sandbox targets required')
        return
    if environment!='production':raise ValueError('Unknown release environment')
    policy=instance.read_json('production.json')
    if policy.get('enabled') is not True:raise RuntimeError('Production policy disabled')
    if plan['status_id']!=policy['status_id'] or plan['archive_id']!=policy['archive_id']:
        raise RuntimeError('Unapproved production control object')
    if not plan.get('dependencies'):raise RuntimeError('Production input dependencies required')
    allowed=policy['targets']
    release_id=plan.get('release_id')
    if release_id:
        # 与 project_targets() 同语义：仅当 marker==本轮release（本轮退休），
        # 或 marker为他轮且不在上轮现行集合（历史已退休）时，才从期望集合排除。
        # 未来标记的现行 target 仍是 approved set 成员，缺失必须拒绝（防 fail-open）。
        # malformed marker（非字符串/空）一律视为未退休 → 仍要求（fail-closed）。
        status=json.loads(drive.get(plan['status_id']))
        active_ids={a.get('id') for a in status.get('artifacts',[])}
        def _retired(fid,spec):
            marker=spec.get('retire_in_release') if isinstance(spec,dict) else None
            if not isinstance(marker,str) or not marker:return False
            if marker==release_id:
                if fid not in active_ids:
                    raise RuntimeError('Retirement target not in previous active set')
                return True
            return fid not in active_ids
        publishable={fid for fid,spec in allowed.items() if not _retired(fid,spec)}
    else:
        # legacy pre-prepare request 路径无 release 上下文，保留严格全等行为；
        # 退休解析在 prepare 阶段完成。
        publishable=set(allowed)
    if {e['id'] for e in plan['entries']}!=publishable:
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
