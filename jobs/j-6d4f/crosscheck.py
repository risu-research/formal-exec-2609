#!/usr/bin/env python3
import ast,csv,json,math,pathlib,statistics,sys
from collections import defaultdict,Counter
OUT=pathlib.Path(sys.argv[1]); ROOT=pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True,exist_ok=True)
IDFIELDS=("reservation_id","order_id","product_id","item_id","user_id")
READ=("get_","search_","find_","list_","lookup_","calculate","check_")
WRITE=("book_","cancel_","update_","modify_","change_","add_","remove_","delete_","create_","transfer_","send_")
def args(v):
 if isinstance(v,dict): return v
 if not isinstance(v,str): return {"_":v}
 for f in (json.loads,ast.literal_eval):
  try:
   z=f(v)
   if isinstance(z,dict): return z
  except Exception: pass
 return {"_raw":v}
def rd(n):
 n=(n or '').lower(); return n.startswith(READ) and not n.startswith(WRITE)
def vals(x):
 r=[]
 if isinstance(x,dict):
  for v in x.values(): r+=vals(v)
 elif isinstance(x,list):
  for v in x:r+=vals(v)
 elif isinstance(x,(str,int,float,bool)):
  s=str(x)
  if len(s)>=2:r.append(s)
 return r
def txt(m):
 z=m.get('content','')
 if isinstance(z,(dict,list)):return json.dumps(z,sort_keys=True,ensure_ascii=False)
 return str(z or '')
def parse(o):
 calls=[]; prefix=[]; ue=0; se=0; byid={}; seq=0
 for mi,m in enumerate(o.get('traj',[])):
  role=m.get('role','')
  if role=='user': ue+=1
  if role=='tool':
   old=byid.get(m.get('tool_call_id'))
   if old and not old['read']:se+=1
   prefix.append(txt(m));continue
  if role=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{}); name=fn.get('name',''); a=args(fn.get('arguments',{})); seq+=1
    field=None
    for k in IDFIELDS:
     if k in a:field=k;break
    calls.append({'mi':mi,'seq':seq,'ue':ue,'se':se,'name':name,'args':a,'read':rd(name),'field':field,'id':str(a.get(field)) if field else None,'tcid':tc.get('id'),'prefix':'\n'.join(prefix)})
    byid[tc.get('id')]=calls[-1]
   prefix.append('')
  else: prefix.append(txt(m))
 return calls
def groups(o,src,idx):
 cs=parse(o); d=defaultdict(list)
 for c in cs:
  if c['read'] and c['field'] and c['id'] is not None:d[(c['ue'],c['se'],c['name'],c['field'])].append(c)
 out=[]
 for key,x in d.items():
  # exact resource de-dup
  seen=set(); y=[]
  for c in x:
   if c['id'] not in seen:seen.add(c['id']);y.append(c)
  x=y
  if len(x)<2:continue
  first=min(x,key=lambda c:c['seq']); pre=first['prefix']
  # Strong test: every scalar argument of every call was already present before first call.
  known=True
  for c in x:
   for v in vals(c['args']):
    if v not in pre:
     known=False;break
   if not known:break
  if not known:continue
  out.append({'src':src,'idx':idx,'task_id':o.get('task_id'),'trial':o.get('trial'),'reward':o.get('reward'),'n':len(x),'name':key[2],'field':key[3],'first_seq':min(c['seq'] for c in x),'last_seq':max(c['seq'] for c in x),'rounds':len(x)})
 return out
def q(v,p):
 if not v:return 0
 v=sorted(v);z=(len(v)-1)*p;a=int(math.floor(z));b=int(math.ceil(z));return v[a] if a==b else v[a]*(b-z)+v[b]*(z-a)
allg=[];files={}
for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
 arr=json.loads(p.read_text()); gs=[]; tracehit=0; tasks=defaultdict(bool); rewards_hit=[];rewards_no=[]
 for i,o in enumerate(arr):
  z=groups(o,p.name,i);gs+=z;tracehit+=bool(z);tasks[str(o.get('task_id'))]=tasks[str(o.get('task_id'))] or bool(z)
  r=o.get('reward');rv=float(r) if isinstance(r,(int,float)) else None
  if rv is not None:(rewards_hit if z else rewards_no).append(rv)
 allg+=gs;sizes=[g['n'] for g in gs]
 files[p.name]={'traces':len(arr),'groups':len(gs),'traces_hit':tracehit,'task_slots':len(tasks),'tasks_hit':sum(tasks.values()),'calls':sum(sizes),'median_n':statistics.median(sizes) if sizes else 0,'max_n':max(sizes) if sizes else 0,'reward_hit':statistics.mean(rewards_hit) if rewards_hit else None,'reward_no':statistics.mean(rewards_no) if rewards_no else None,'names':dict(Counter(g['name'] for g in gs))}
s=[g['n'] for g in allg]
# aggregate unique task slots by file is deliberate; task ids are environment-local
def rat(k):return sum(s)/sum(math.ceil(x/k) for x in s) if s else 1
res={'schema':'j6d4f-v3-independent','traces':sum(x['traces'] for x in files.values()),'groups':len(allg),'traces_hit':sum(x['traces_hit'] for x in files.values()),'task_slots':sum(x['task_slots'] for x in files.values()),'tasks_hit':sum(x['tasks_hit'] for x in files.values()),'calls':sum(s),'median_n':statistics.median(s) if s else 0,'mean_n':statistics.mean(s) if s else 0,'p90_n':q(s,.9),'max_n':max(s) if s else 0,'local_k2':rat(2),'local_k4':rat(4),'local_kinf':statistics.mean(s) if s else 1,'by_file':files}
(OUT/'cross.json').write_text(json.dumps(res,indent=2,sort_keys=True));
cols=['src','idx','task_id','trial','reward','n','name','field','first_seq','last_seq','rounds']
with (OUT/'groups.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(allg)
# Small deterministic audit sample with raw source indices but no prompts/content
sample=[]
for src in sorted(files):
 z=sorted([g for g in allg if g['src']==src],key=lambda g:(-g['n'],g['idx']))[:10];sample+=z
(OUT/'sample.json').write_text(json.dumps(sample,indent=2,sort_keys=True));print(json.dumps(res,indent=2,sort_keys=True))