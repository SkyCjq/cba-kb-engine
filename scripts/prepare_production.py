"""Reserve stable IDs, then freeze a complete v1.5 release from a clean commit.

Run reserve, commit policy/code, then freeze. Publication remains the CLI's job.
"""
import argparse,csv,io,json,subprocess
from pathlib import Path
from cba_kb.drive import Drive
from cba_kb.common import read,save,digest,atomic
from cba_kb.native import wrap
from cba_kb.master import build
from cba_kb.release import fingerprint,snapshot

ROOT='1bQybVHV_RRtZvvFuFhLpM-rXbVsNT2Dq'
AI='1y-JlN327UARvZ4A8QN0EDDxg_0oggiUp'
SCRIPTS='1oaJW9FDUjmIbFDafpt6aqtlvvzqQYhdC'
CONFIG='1ccYmxy8NKt79pgyhDnyHUE14C6_wsn83'
ARCHIVE='1GjDMkAYs7JpIrI9_kQduxlqygZmxeb9M'
DOC='application/vnd.google-apps.document';FOLDER='application/vnd.google-apps.folder'
from cba_kb.catalog import EXISTING_DOCS


def main():
 p=argparse.ArgumentParser();p.add_argument('step',choices=['reserve','freeze']);p.add_argument('--release',default='v1.5.0');a=p.parse_args()
 root=Path.cwd();d=Drive(root);area=root/'workspace/production'/a.release;area.mkdir(parents=True,exist_ok=True)
 allocation=area/'allocation.json'
 if a.step=='reserve':
  if allocation.exists():raise RuntimeError('Allocation exists; inspect/resume rather than reserve again')
  staging=d.ensure(ARCHIVE,a.release+'-staging',a.release+'_staging',FOLDER)
  status_id=d.ensure(CONFIG,'release-status-v1.5','release_status.json','application/json',json.dumps({'state':'INITIAL','current_release_id':'v1.1','artifacts':[]}).encode())
  definitions=[]
  def add(key,name,parent,mime='text/markdown',fid=None,mode='binary',**extra):
   new=fid is None
   if new:fid=d.ensure(staging,'reserve:'+key,name,mime,b'')
   m=d.meta(fid)
   if m['mimeType']!=mime:raise RuntimeError('Unexpected MIME '+name)
   item=dict(logical_key=key,id=fid,name=name,mime=mime,mode=mode,allowed_parents=[parent,staging] if new else [parent],**extra)
   if new:item.update(staging_parent=staging,publish_parent=parent)
   definitions.append(item);save(area/'allocation-progress.json',definitions)
  for name in ['INDEX.md']+[f'CBA_注册_{y}-{y+1}.md' for y in range(2017,2027)]+['MASTER.csv','MASTER.jsonl','provenance.json','validation.json']:
   fid=EXISTING_DOCS.get(name);mime=DOC if fid else ('text/csv' if name.endswith('.csv') else 'application/json' if name.endswith('.json') else 'application/x-ndjson' if name.endswith('.jsonl') else 'text/markdown')
   add('derived/'+name,name.removesuffix('.md') if fid else name,CONFIG if name=='provenance.json' else AI,mime,fid,'managed_doc' if fid else 'binary',candidate_name=name)
  # Existing controls are included even when byte-identical, preventing partial release sets.
  for name,item in read(root/'config/runtime.json')['inputs'].items():
   add('input/'+name,name,'1nL4xomHNQInbskeYdqa45hHfleHhExW-' if name=='MASTER.xlsx' else CONFIG,item['mime'],item['id'])
  add('control/drive_map.yaml','drive_map.yaml',CONFIG,'text/yaml','1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf')
  add('entry/README','00_README_先读这个',ROOT,DOC,'1AqCZwlSKlZdfR9F0ftG_Dc36gffmz_NLs8v1FS99uu0','managed_doc')
  add('entry/context','02_AI_PROJECT_CONTEXT_技术手册.md',ROOT,'text/markdown','1Ckg-y3Ke4tHxGHPp1dY2A_cO39xzF9u4')
  add('entry/crossseason','CBA_跨赛季变动',AI,DOC,'1Vng3JOfE4Zi9zWbSUj8qhBJeuCU1jXS_LtGV6W_ozS0','managed_doc')
  add('entry/code','CODE_MANIFEST.md',SCRIPTS)
  inventory={x['name']:x for x in read(root/'docs/import_inventory.json')}
  tracked=subprocess.check_output(['git','ls-files','-z'],text=True).split('\0')
  # production.json is created by this command and committed before freeze.
  tracked=sorted(set(filter(None,tracked))|{'config/production.json'})
  for relative in tracked:
   item=inventory.get(Path(relative).name) if relative.startswith('legacy/') and Path(relative).name!='Makefile' else inventory.get('Makefile') if relative=='Makefile' else None
   add('code/'+relative,item['name'] if item else relative.replace('/','__'),SCRIPTS,item['mime'] if item else 'text/plain',item['id'] if item else None,source=relative)
  save(allocation,{'status_id':status_id,'archive_id':ARCHIVE,'staging_id':staging,'definitions':definitions})
  policy={'enabled':True,'status_id':status_id,'archive_id':ARCHIVE,'dependency_ids':[read(root/'config/runtime.json')['inputs']['MASTER.xlsx']['id'],read(root/'config/runtime.json')['inputs']['source_registry.csv']['id']], 'targets':{e['id']:{k:v for k,v in e.items() if k in ('mime','mode','allowed_parents','staging_parent','publish_parent')} for e in definitions}}
  save(root/'config/production.json',policy);print('Reserved',len(definitions),'targets; commit policy and code before freeze',flush=True);return
 if subprocess.check_output(['git','status','--porcelain'],text=True).strip():raise RuntimeError('Clean committed tree required')
 alloc=read(allocation);commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();defs=alloc['definitions']
 # Snapshot current raw controls; pin input dependencies at build time.
 inputs=area/'inputs';inputs.mkdir(exist_ok=False);dependencies=[];raw={}
 for name,item in read(root/'config/runtime.json')['inputs'].items():
  content,meta=snapshot(d,item['id']);atomic(inputs/name,content);raw[name]=content
  if name in ('MASTER.xlsx','source_registry.csv'):dependencies.append(dict(id=item['id'],sha256=digest(content),meta=fingerprint(meta)))
 save(area/'dependencies.json',dependencies)
 from datetime import datetime,timezone
 candidate=area/'candidate';build(inputs/'MASTER.xlsx',candidate,a.release,commit,datetime.now(timezone.utc).isoformat())
 urls={e['logical_key']:d.meta(e['id'])['webViewLink'] for e in defs};status_url=d.meta(alloc['status_id'])['webViewLink']
 nav=f'''当前发布：{a.release}。代码提交：{commit}。\n首先读取发布状态：{status_url}\n仅 COMPLETE 使用当前对象；PUBLISHING/FAILED/ROLLING_BACK 使用状态中的 previous_snapshot；ROLLED_BACK 使用已恢复上一发布。读取前后状态变化则重试。\n本地执行，GitHub 私有仓库 https://github.com/SkyCjq/cba-kb-engine 管理代码，Drive 为已发布事实入口。\nGemini 请直接提供所需文件链接；入口递归读取与自动刷新不保证。auto_validated 不是人工核验，空值不是零，记录数不是独立球员数。\n'''
 links='\n'.join(f'- {k}: {v}' for k,v in urls.items() if k.startswith(('derived/','input/')))
 index=(candidate/'INDEX.md').read_text()+'\n\n'+nav+'\n'+links+'\n代码镜像：'+urls['entry/code']+'\n'
 code=['# 当前代码镜像',f'GitHub: https://github.com/SkyCjq/cba-kb-engine/tree/{commit}',f'commit: {commit}', 'legacy/ 是历史脚本，禁止用旧 uploader/wechat/sync 向生产写入。当前命令入口是 Makefile 和 src/cba_kb/。']
 for e in defs:
  if e['logical_key'].startswith('code/'):
   data=(root/e['source']).read_bytes();code.append(f"- {e['source']} | SHA256 {digest(data)} | {urls[e['logical_key']]}")
 manifest=list(csv.DictReader(io.StringIO(raw['manifest.csv'].decode('utf-8-sig'))));columns=list(manifest[0])
 # Keep the legacy manifest schema and entries; complete release map is additive.
 map_before=d.get('1wi_Oyp5DhJBEDiJoWdbMaX9ABoysdzRf')
 import yaml
 mapping=yaml.safe_load(map_before);mapping['v1_5_release']={'release_id':a.release,'status_id':alloc['status_id'],'code_commit':commit,'targets':{e['logical_key']:e['id'] for e in defs}}
 frozen=area/'frozen';frozen.mkdir(exist_ok=False);entries=[]
 for i,e in enumerate(defs):
  key=e['logical_key']
  if key=='derived/INDEX.md':data=index.encode()
  elif key.startswith('derived/'):data=(candidate/e['candidate_name']).read_bytes()
  elif key=='input/manifest.csv':data=raw['manifest.csv'] # authoritative complete map is drive_map and release_status
  elif key.startswith('input/'):data=raw[key[6:]]
  elif key=='control/drive_map.yaml':data=yaml.safe_dump(mapping,allow_unicode=True,sort_keys=False).encode()
  elif key=='entry/code':data=('\n'.join(code)+'\n').encode()
  elif key=='entry/README':data=(nav+'\n'+links+'\n代码镜像：'+urls['entry/code']+'\nv1.5 基础设施已发布，最终 Gemini 生产复验与封板以实施报告为准。v1.5.1 尚未实施。').encode()
  elif key=='entry/context':data=(nav+'\n'+links+'\n'+(root/'docs/OPERATIONS_V1_5.md').read_text()+'\n'+(root/'docs/MASTER_MIGRATION_NOTE.md').read_text()).encode()
  elif key=='entry/crossseason':data=(nav+'\n以下历史跨赛季内容不作为 v1.5 当前事实。请改用当前 MASTER/赛季阅读版：\n'+links).encode()
  else:data=(root/e['source']).read_bytes()
  if e['mode']=='managed_doc':data=wrap(data.decode()).encode()
  path=frozen/str(i);atomic(path,data);entry={k:v for k,v in e.items() if k not in ('source','candidate_name','allowed_parents')};entry['path']=str(path);entries.append(entry)
 # Manifest publishes the complete set; omit its own content hash to avoid recursion.
 by_id={row.get('drive_file_id'):row for row in manifest}
 for entry in entries:
  row=by_id.get(entry['id'])
  if row is None:
   row={c:'' for c in columns};manifest.append(row)
  row.update(uid=entry['logical_key'],asset_type='release_artifact',source_type='generated',local_path=entry['logical_key'],drive_file_id=entry['id'],status='published',synced_at=datetime.now(timezone.utc).isoformat(),content_hash='' if entry['logical_key']=='input/manifest.csv' else digest(Path(entry['path']).read_bytes()))
  row['published_release']=a.release;row['hash_scope']='managed_prefix' if entry['mode']=='managed_doc' else 'bytes'
 stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns+['published_release','hash_scope'],lineterminator='\n');writer.writeheader();writer.writerows(manifest)
 target=next(e for e in entries if e['logical_key']=='input/manifest.csv');atomic(target['path'],stream.getvalue().encode('utf-8-sig'))
 save(area/'entries.json',entries);save(area/'release-links.json',{'release_id':a.release,'commit':commit,'status':status_url,'urls':urls});print('Frozen',len(entries),'targets for',commit)

if __name__=='__main__':main()
