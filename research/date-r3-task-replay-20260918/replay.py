#!/usr/bin/env python3
"""R3 task/input replay: original tracked Git evidence only, NOT historical proof execution."""
import hashlib,json,pathlib,subprocess,sys,re
R1_HASH='2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161'
R2_HASH='00cc74ac08a15a785eaf7caa601181c16d8a2ee152b735b3e87ae9a3bb981af1'
CASE={
'1958cdb56f189fd5e38307c7066eb69c5f15714e':'IGNORE_ZIP',
'9f750c4364872d1494a1bc3beb1cf67dcb26e4ae':'IGNORE_RISCV',
'e860404b043380e6bf0c937fcb8427dc265ea91f':'COMMENT_FDEBUG',
'097a92bb1d2eab235e7c32ae3af839f090dedc51':'ENGINE_AXIICACHE',
'12845b21f075fc8ee6f2aa02926cc1b24a7b6b3f':'SERV_MULTI',
'c115288383a3ec27b477daa3b6f0447b6f707195':'PICORV_TASK_ADD',
'1b878a932127a18698eca2bb74fc4230009a80d5':'ROCKET_GENERATED',
'c20ffe060413d056fdb5973a92a63efe4e58ca7b':'MACRO_REWRITE',
'6b5ce16b24c5a46dff375e6bfeedd4631de9c6db':'RTL_NO_TASK'}
CONFIG={'ENGINE_AXIICACHE':'bench/formal/axiicache.sby','SERV_MULTI':'cores/serv/cover.sby','PICORV_TASK_ADD':'cores/picorv32/complete.sby'}
REPOS={'ZipCPU/zipcpu':pathlib.Path('zipcpu'),'SymbioticEDA/riscv-formal':pathlib.Path('riscv-formal')}
def h(b):return hashlib.sha256(b).hexdigest()
def git(r,*cmd):
 p=subprocess.run(['git','-C',str(r),*cmd],capture_output=True)
 if p.returncode:raise RuntimeError('git '+repr(cmd)+' '+p.stderr.decode(errors='replace')[-1000:])
 return p.stdout
def blob(r,rev,path):return git(r,'show',rev+':'+path)
def tree(r,rev):
 d={}
 for e in git(r,'ls-tree','-r','-z',rev).split(b'\0'):
  if not e:continue
  typ,obj=e.split(b' ',1);kind,idpath=obj.split(b' ',1);oid,path=idpath.split(b'\t',1)
  d[path.decode()]={'mode':typ.decode(),'type':kind.decode(),'oid':oid.decode()}
 return d
def uncomment(src):
 s=src.decode('utf8');out=[];i=0;state='code'
 while i<len(s):
  c=s[i];n=s[i+1] if i+1<len(s) else ''
  if state=='code':
   if c=='"':out.append(c);state='string';i+=1;continue
   if c=='/' and n=='/':state='line';i+=2;continue
   if c=='/' and n=='*':state='block';i+=2;continue
   out.append(c);i+=1;continue
  if state=='string':
   out.append(c)
   if c=='\\' and n:out.append(n);i+=2;continue
   if c=='"':state='code'
   i+=1;continue
  if state=='line':
   if c=='\n':out.append(c);state='code'
   i+=1;continue
  if state=='block':
   if c=='*' and n=='/':state='code';i+=2;continue
   if c=='\n':out.append(c)
   i+=1;continue
 assert state in ('code','line'),'unterminated comment/string'
 return ''.join(out).encode()
def sby(b):
 d={};section=''
 for line in b.decode().splitlines():
  line=line.strip();m=re.fullmatch(r'\[([^]]+)\]',line)
  if m:section=m.group(1).lower();d.setdefault(section,[])
  elif line and not line.startswith('#'):d.setdefault(section,[]).append(line)
 return {'sha256':h(b),'tasks':d.get('tasks',[]),'options':d.get('options',[]),'engines':d.get('engines',[]),'files':d.get('files',[]),'script':d.get('script',[])}
def main():
 root=pathlib.Path('evidence');root.mkdir(exist_ok=True)
 r1p=pathlib.Path('g6-r1/g6-r1-inventory.json');r2p=pathlib.Path('g6-r2/g6-r2-results.json')
 assert h(r1p.read_bytes())==R1_HASH and h(r2p.read_bytes())==R2_HASH,'original G6 authority hashes differ'
 r1=json.loads(r1p.read_text());r2=json.loads(r2p.read_text());assert r1['pair_count']==144 and r2['blind_non_sentinel_total']==143
 indexed={z['child']:z for z in r1['rows']};old_r2={z['child']:z for z in r2['rows']};results=[]
 for cid,tag in CASE.items():
  row=indexed[cid];assert cid in old_r2 and not row['pre_registered_sentinel']
  repo=REPOS[row['repository_full_name']];par=row['parent'];assert git(repo,'rev-parse',cid+'^').decode().strip()==par
  old=tree(repo,par);new=tree(repo,cid)
  changed=sorted(p for p in old.keys()|new.keys() if old.get(p)!=new.get(p))
  assert changed==sorted(x['path'] for x in row['changed_name_status'])
  assert changed==sorted(z['path'] for z in row['formal_records'] if z['changed_path_present'])
  for item in row['formal_records']:
   for side,rev in [('old',par),('new',cid)]:
    meta=item[side];src=blob(repo,rev,item['path']) if meta['exists'] else b''
    if meta['exists']:assert h(src)==meta['sha256'] and len(src)==meta['size']
    else:assert item['path'] not in (old if side=='old' else new)
  oldtasks={p:old[p]['oid'] for p in old if p.endswith('.sby')}
  newtasks={p:new[p]['oid'] for p in new if p.endswith('.sby')}
  record={'child':cid,'parent':par,'repository':row['repository_full_name'],'stratum':tag,'complete_changed_paths':changed,'frozen_changed_source_sha256_verified':True,'available_sby_counts':[len(oldtasks),len(newtasks)],'available_sby_catalog_equal':oldtasks==newtasks,'historical_selected_task':'UNKNOWN','historical_executed_task':'UNKNOWN','historical_native_PASS':'UNKNOWN','H143_new_full_resolution':False}
  if tag.startswith('IGNORE_'):
   assert all(p.endswith('/.gitignore') for p in changed)
   assert {p:z for p,z in old.items() if p not in changed}=={p:z for p,z in new.items() if p not in changed}
   assert oldtasks==newtasks and len(oldtasks)>0
   record['tracked_nonignore_inputs_identical']=True
   record['tracked_nonignore_tree_entries']=len(old)-len(changed)
  if tag=='COMMENT_FDEBUG':
   assert changed==['bench/formal/fdebug.v']
   a=blob(repo,par,changed[0]);b=blob(repo,cid,changed[0]);assert a!=b and uncomment(a)==uncomment(b)
   assert all(old[p]==new[p] for p in old if p!=changed[0])
   record['verilog_without_comments_identical']=True
   record['verilog_without_comments_sha256']=h(uncomment(a))
  if tag in CONFIG:
   path=CONFIG[tag];record['candidate_sby_path']=path;record['candidate_sby_not_historical_selection']=True
   record['available_sby_config']={side:(sby(blob(repo,rev,path)) if path in t else None) for side,rev,t in [('parent',par,old),('child',cid,new)]}
   if tag=='ENGINE_AXIICACHE':assert record['available_sby_config']['parent']['engines']!=record['available_sby_config']['child']['engines']
   if tag=='PICORV_TASK_ADD':assert record['available_sby_config']['parent'] is None and record['available_sby_config']['child'] is not None
  results.append(record);print(tag,'tracked_changes',len(changed),'SBY',len(oldtasks),'->',len(newtasks),flush=True)
 assert len(results)==9
 summary={'original_143':143,'original_9_adverse':9,'original_4_resolved':4,'original_130_unresolved':130,'full_original_H143_upgrades':0,'original_historical_run_logs_recovered':0,'new_native_engine_proofs':0,'tracked_no_change_pairs':2,'Verilog_comment_semantics_pair':1,'cases':results,'limits':['Candidate SBY file != historically selected/executed task','Untracked/generated inputs and external tools not yet captured','Git ignore rules can affect source discovery or cleanup','No native JasperGold run; OpenTitan remains unresolved']}
 (root/'RESULTS.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
 print('PASSED 9 source-tree audits; 0 historical task-execution claims; 0 full H143 upgrades')
if __name__=='__main__':main()
