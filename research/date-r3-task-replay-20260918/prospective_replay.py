#!/usr/bin/env python3
"""Prospectively replay two preregistered historical SBY tasks. NEVER infer a historical PASS."""
import argparse,hashlib,json,os,pathlib,re,subprocess,tarfile,io,time,shutil
ROOT=pathlib.Path(__file__).resolve().parent
SELECTION=ROOT/'PROSPECTIVE_TASK_SELECTION.json'
R1='2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161'
R2='00cc74ac08a15a785eaf7caa601181c16d8a2ee152b735b3e87ae9a3bb981af1'
def sha(b):return hashlib.sha256(b).hexdigest()
def run(argv,cwd=None,timeout=240,env=None):
 try:
  p=subprocess.run(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
  return p.returncode,p.stdout.decode(errors='replace')
 except subprocess.TimeoutExpired as e:return 124,(e.stdout or b'').decode(errors='replace')+'\nTIMEOUT\n'
def git(repo,*args):
 code,out=run(['git','-C',str(repo),*args],timeout=180)
 if code:raise RuntimeError('git failure '+str(args)+' '+out[-1000:])
 return out
def fresh_tree(repo,rev,dest):
 # --work-tree checkout is isolated, with no inherited ignored or generated sources.
 dest.mkdir(parents=True)
 code,out=run(['git','-C',str(repo),'--work-tree='+str(dest.resolve()),'checkout','--force',rev,'--','.'],timeout=180)
 if code:raise RuntimeError('isolated worktree checkout failed: '+out[-1200:])
 # Checkout writes the tracked revision; there is no historical-run inference.
def read_sby(b):
 section=''; data={}
 for line in b.decode().splitlines():
  x=line.strip();m=re.fullmatch(r'\[([^]]+)\]',x)
  if m:section=m[1].lower();data.setdefault(section,[])
  elif x and not x.startswith('#'):data.setdefault(section,[]).append(x)
 return data
def direct_inputs(d,config):
 files=[]
 for line in d.get('files',[]):
  tokens=line.split()
  if len(tokens)!=1 or tokens[0].startswith('--'):raise RuntimeError('unsupported [files] syntax: '+line)
  relative=(config.parent/tokens[0]).resolve()
  assert relative.is_relative_to(config.parents[2]),'path escapes checkout'
  files.append(relative.relative_to(config.parents[2]).as_posix())
 return files
def no_comments(b):
 s=b.decode();res=[];state='code';i=0
 while i<len(s):
  c=s[i];n=s[i+1] if i+1<len(s) else ''
  if state=='code':
   if c=='"':state='string';res.append(c);i+=1;continue
   if c=='/' and n=='/':state='line';i+=2;continue
   if c=='/' and n=='*':state='block';i+=2;continue
   res.append(c);i+=1;continue
  if state=='string':
   res.append(c)
   if c=='\\' and n:res.append(n);i+=2;continue
   if c=='"':state='code'
   i+=1;continue
  if state=='line':
   if c=='\n':res.append(c);state='code'
   i+=1;continue
  if state=='block':
   if c=='*' and n=='/':state='code';i+=2;continue
   if c=='\n':res.append(c)
   i+=1;continue
 assert state=='code','unterminated Verilog token'
 return ''.join(res).encode()
def main():
 p=argparse.ArgumentParser();p.add_argument('--zipcpu',required=True);p.add_argument('--r1',required=True);p.add_argument('--r2',required=True);p.add_argument('--out',required=True);p.add_argument('--sby',default='sby');args=p.parse_args()
 out=pathlib.Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
 assert sha(pathlib.Path(args.r1).read_bytes())==R1
 assert sha(pathlib.Path(args.r2).read_bytes())==R2
 r1=json.loads(pathlib.Path(args.r1).read_text());r2=json.loads(pathlib.Path(args.r2).read_text());assert r1['pair_count']==144 and r2['blind_non_sentinel_total']==143
 rows={r['child']:r for r in r1['rows']};classifications={r['child']:r for r in r2['rows']}
 selection=json.loads(SELECTION.read_text());assert len(selection['selected'])==2 and selection['fixed_h143_accounting']=={'total':143,'adverse':9,'resolved_nonregressive':4,'unresolved':130}
 selection_sha=sha(SELECTION.read_bytes());repo=pathlib.Path(args.zipcpu).resolve();assert repo.is_dir()
 versions={}
 for tool in ('yosys','sby','yosys-smtbmc','z3','boolector'):
  code,text=run([tool,'--version'],timeout=10);versions[tool]={'exit':code,'text':text[:400]}
 results=[]
 for case in selection['selected']:
  cid=case['child'];rr=rows[cid];assert rr['parent']==case['parent'] and rr['repository_full_name']==case['repository'] and not rr['pre_registered_sentinel']
  assert classifications[cid]['overall']=='UNRESOLVED_NO_CONFIRMED_ADVERSE'
  assert git(repo,'rev-parse',cid+'^').strip()==rr['parent']
  case_out=out/case['id'];case_out.mkdir()
  statuses={};closures={};dirs={}
  for side,commit in (('parent',rr['parent']),('child',cid)):
   dst=case_out/side;fresh_tree(repo,commit,dst);dirs[side]=dst
   config=dst/case['formal_target'];assert config.is_file()
   expected=git(repo,'show',commit+':'+case['formal_target']).encode()
   assert config.read_bytes()==expected
   sby=read_sby(expected);assert any(line.split()[0]==case['task'] for line in sby.get('tasks',[]))
   dep_paths=direct_inputs(sby,config)
   assert dep_paths and len(set(dep_paths))==len(dep_paths)
   entries={case['formal_target']:sha(expected)}
   for path in dep_paths:
    b=(dst/path).read_bytes();assert b==git(repo,'show',commit+':'+path).encode()
    entries[path]=sha(b)
   # Explicit declared [files] only, not an exhaustive expansion of $readmem,
   # macros, generator scripts, extra include paths, or external files.
   closures[side]=entries
   invocation={'git_revision':commit,'sby_sha256':sha(expected),'task_name':case['task'],'declared_inputs_sha256':entries,'sby_engines':sby.get('engines',[]),'sby_script':sby.get('script',[]),'historical_selection':'UNKNOWN','historical_execution':'UNKNOWN','historical_tool_version':'UNKNOWN'}
   (case_out/(side+'-invocation.json')).write_text(json.dumps(invocation,indent=2,sort_keys=True)+'\n')
   command=[args.sby,'-f','-d',str(case_out/(side+'-workdir')),str(config),case['task']]
   start=time.monotonic();code,log=run(command,cwd=config.parent,timeout=360)
   (case_out/(side+'-console.log')).write_text(log)
   workdir=case_out/(side+'-workdir');statusfile=workdir/'status'
   status=statusfile.read_text().strip() if statusfile.exists() else 'NO_STATUS'
   statuses[side]={'exit_code':code,'sby_status':status,'elapsed_monotonic_seconds':round(time.monotonic()-start,3),'command':command,'workdir_exists':workdir.exists()}
   if workdir.exists():
    generated={}
    for pth in sorted(workdir.rglob('*')):
     if pth.is_file():
      b=pth.read_bytes();generated[pth.relative_to(workdir).as_posix()]={'sha256':sha(b),'size':len(b)}
    (case_out/(side+'-generated-manifest.json')).write_text(json.dumps(generated,sort_keys=True,indent=2)+'\n')
   print(case['id'],side,'sby',status,'exit',code,flush=True)
  assert closures['parent'].keys()==closures['child'].keys()
  diff=[p for p in sorted(closures['parent']) if closures['parent'][p]!=closures['child'][p]]
  if case['id']=='H143-ZIP-IGNORE-PIPMEM':assert not diff, 'ignore-only case changed tracked task input'
  if case['id']=='H143-ZIP-COMMENT-ZIPCORE':
   assert diff==['bench/formal/fdebug.v']
   assert no_comments((dirs['parent']/diff[0]).read_bytes())==no_comments((dirs['child']/diff[0]).read_bytes())
  results.append({'case_id':case['id'],'parent':rr['parent'],'child':cid,'original_H143_overall':classifications[cid]['overall'],'parent_and_child_declared_input_changes':diff,'preregistered_task':case['task'],'candidate_sby':case['formal_target'],'prospective_run':statuses,'historical_selected_task':'UNKNOWN','historical_completed_run':'UNKNOWN','original_H143_upgraded':False,'no_generated_input_completeness_claim':True})
 summary={'schema':'date-r3-prospective-actual-sby-v1','selection_sha256':selection_sha,'original_G6_R1_sha256':R1,'original_G6_R2_sha256':R2,'tool_versions':versions,'cases':results,'original_H143':{'adverse':9,'resolved_nonregressive':4,'unresolved':130,'total':143},'new_original_H143_fully_resolved':0,'limitations':['These tasks were chosen prospectively from historically available SBY files; no original workflow invocation or original-run PASS recovered.','A current native SBY PASS is prospective, not a historical proof.','Declared input closure does not necessarily include externally generated or implicit readmem/include inputs.','Original historical toolchain version unavailable; tool-version equivalence not established.']}
 (out/'RESULTS.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
 print('REPLAY_DONE cases',len(results),'full_original_H143_upgrades',0)
if __name__=='__main__':main()
