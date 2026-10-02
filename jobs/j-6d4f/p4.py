#!/usr/bin/env python3
import ast,csv,hashlib,json,math,pathlib,statistics,sys
from collections import defaultdict,Counter
OUT=pathlib.Path(sys.argv[1]); ROOT=pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True,exist_ok=True)
# Verified pure readers only; output hashes names to keep public transport opaque.
PURE={"get_reservation_details":"reservation_id","get_order_details":"order_id","get_product_details":"product_id"}
MUT_PREFIX=("book_","cancel_","update_","modify_","change_","add_","remove_","delete_","create_","transfer_","send_","exchange_","return_")
def H(x): return hashlib.sha256(str(x).encode()).hexdigest()[:12]
def parse_args(v):
 if isinstance(v,dict): return v
 if not isinstance(v,str): return {"_":v}
 for f in (json.loads,ast.literal_eval):
  try:
   z=f(v)
   if isinstance(z,dict): return z
  except Exception: pass
 return {"_raw":v}
def text(m):
 z=m.get('content','')
 return json.dumps(z,sort_keys=True,ensure_ascii=False) if isinstance(z,(dict,list)) else str(z or '')
def wilson(x,n,z=1.959963984540054):
 if n==0:return [0,0]
 p=x/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
 return [max(0,c-h),min(1,c+h)]
def q(v,p):
 if not v:return 0
 a=sorted(v);r=(len(a)-1)*p;i=math.floor(r);j=math.ceil(r)
 return a[i] if i==j else a[i]*(j-r)+a[j]*(r-i)
def msgs(o):
 # materialize calls/results and identify state barriers
 out=[]; byid={}; state=0; user=0; seq=0
 for mi,m in enumerate(o.get('traj',[])):
  role=m.get('role','')
  if role=='user':user+=1; out.append({'mi':mi,'role':'user','user':user,'state':state,'text':text(m)});continue
  if role=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{});name=fn.get('name','');a=parse_args(fn.get('arguments',{}));seq+=1
    c={'mi':mi,'role':'call','seq':seq,'user':user,'state':state,'name':name,'args':a,'tcid':tc.get('id')}
    out.append(c);byid[tc.get('id')]=c
   continue
  if role=='tool':
   c=byid.get(m.get('tool_call_id')); name=c['name'] if c else ''
   r={'mi':mi,'role':'result','user':user,'state':state,'name':name,'text':text(m),'tcid':m.get('tool_call_id')}
   out.append(r)
   if name.lower().startswith(MUT_PREFIX):state+=1
   continue
  out.append({'mi':mi,'role':role,'user':user,'state':state,'text':text(m)})
 return out
def groups(o,src,idx,domain):
 ev=msgs(o); calls=[e for e in ev if e['role']=='call' and e['name'] in PURE and PURE[e['name']] in e['args']]
 # enumerate same-function calls within same user/state epoch
 buck=defaultdict(list)
 for c in calls:
  buck[(c['name'],c['user'],c['state'])].append(c)
 ans=[]
 for (name,ue,se),cs in buck.items():
  # exact entity dedup
  fld=PURE[name]; seen=set(); zz=[]
  for c in cs:
   v=str(c['args'][fld])
   if v not in seen:seen.add(v);c=dict(c);c['ent']=v;zz.append(c)
  cs=zz
  if len(cs)<2:continue
  first=min(c['mi'] for c in cs); last=max(c['mi'] for c in cs)
  # Candidate parent must be ONE earlier tool output containing all IDs.
  parents=[]
  for e in ev:
   if e['role']!='result' or e['mi']>=first:continue
   if all(c['ent'] in e['text'] for c in cs):parents.append(e)
  if not parents:continue
  parent=max(parents,key=lambda e:e['mi'])
  # Ultra-strict: no user turn and no mutating state change after parent through last child call.
  bad=False
  for e in ev:
   if parent['mi']<e['mi']<=last:
    if e['role']=='user' or e.get('state',se)!=parent.get('state',se):bad=True;break
  if bad:continue
  # Parent itself must be prior to every call and all children remain same epoch.
  if any(c['mi']<=parent['mi'] for c in cs):continue
  ans.append({'src':src,'domain':domain,'idx':idx,'task_id':o.get('task_id'),'trial':o.get('trial'),'reward':o.get('reward'),'n':len(cs),'child':H(name),'parent':H(parent.get('name','')),'parent_gap':first-parent['mi'],'span':last-first+1})
 return ans
def classify_reward(r):
 try:return 'success' if float(r)>=0.999 else 'failure'
 except:return 'unknown'
allg=[]; trace_rows=[]; task=defaultdict(lambda:{'n':0,'h':0,'succ_n':0,'succ_h':0,'fail_n':0,'fail_h':0}); files={}
for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
 domain='airline' if 'airline' in p.name else 'retail'; arr=json.loads(p.read_text()); fh=0;fs=Counter(); groups_file=[]
 for idx,o in enumerate(arr):
  gs=groups(o,p.name,idx,domain);hit=bool(gs);fh+=hit;groups_file+=gs;cls=classify_reward(o.get('reward'));fs[(cls,'n')]+=1;fs[(cls,'h')]+=hit
  k=(domain,str(o.get('task_id')));task[k]['n']+=1;task[k]['h']+=hit
  if cls=='success':task[k]['succ_n']+=1;task[k]['succ_h']+=hit
  elif cls=='failure':task[k]['fail_n']+=1;task[k]['fail_h']+=hit
  trace_rows.append({'src':p.name,'domain':domain,'idx':idx,'task_id':o.get('task_id'),'reward':o.get('reward'),'hit':int(hit),'groups':len(gs),'max_n':max([g['n'] for g in gs],default=0)})
 allg+=groups_file
 sizes=[g['n'] for g in groups_file]
 files[p.name]={'traces':len(arr),'hits':fh,'ci':wilson(fh,len(arr)),'groups':len(groups_file),'calls':sum(sizes),'median_n':statistics.median(sizes) if sizes else 0,'max_n':max(sizes) if sizes else 0,'success_n':fs[('success','n')],'success_h':fs[('success','h')],'failure_n':fs[('failure','n')],'failure_h':fs[('failure','h')]}
# domain/task aggregation across models and repeats
task_rows=[]
for (d,t),v in sorted(task.items()):
 r={'domain':d,'task_id':t,**v,'frac':v['h']/v['n'] if v['n'] else 0,'succ_frac':v['succ_h']/v['succ_n'] if v['succ_n'] else None,'fail_frac':v['fail_h']/v['fail_n'] if v['fail_n'] else None};task_rows.append(r)
summary={}
for d in ('airline','retail','all'):
 trs=[r for r in trace_rows if d=='all' or r['domain']==d]; ts=[r for r in task_rows if d=='all' or r['domain']==d]
 n=len(trs);h=sum(r['hit'] for r in trs);sn=sum(classify_reward(r['reward'])=='success' for r in trs);sh=sum(r['hit'] for r in trs if classify_reward(r['reward'])=='success');fn=sum(classify_reward(r['reward'])=='failure' for r in trs);fh=sum(r['hit'] for r in trs if classify_reward(r['reward'])=='failure')
 summary[d]={'traces':n,'trace_hits':h,'trace_frac':h/n if n else 0,'trace_ci95':wilson(h,n),'success_n':sn,'success_h':sh,'success_frac':sh/sn if sn else None,'success_ci95':wilson(sh,sn) if sn else None,'failure_n':fn,'failure_h':fh,'failure_frac':fh/fn if fn else None,'failure_ci95':wilson(fh,fn) if fn else None,'tasks':len(ts),'tasks_any':sum(r['h']>0 for r in ts),'tasks_any_frac':sum(r['h']>0 for r in ts)/len(ts) if ts else 0,'task_trial_frac_median':statistics.median([r['frac'] for r in ts]) if ts else 0,'tasks_majority':sum(r['frac']>=0.5 for r in ts),'tasks_majority_frac':sum(r['frac']>=0.5 for r in ts)/len(ts) if ts else 0}
sizes=[g['n'] for g in allg]
def lf(k):return sum(sizes)/sum(math.ceil(x/k) for x in sizes) if sizes else 1
res={'schema':'j6d4f-p4','groups':len(allg),'calls':sum(sizes),'median_n':statistics.median(sizes) if sizes else 0,'mean_n':statistics.mean(sizes) if sizes else 0,'p90_n':q(sizes,.9),'max_n':max(sizes) if sizes else 0,'local_k2':lf(2),'local_k4':lf(4),'local_kinf':statistics.mean(sizes) if sizes else 1,'summary':summary,'files':files,'pair_counts':dict(Counter((g['parent'],g['child']) for g in allg))}
(OUT/'r.json').write_text(json.dumps(res,indent=2,sort_keys=True))
def wc(path,rows):
 if not rows:path.write_text('');return
 cols=sorted({k for r in rows for k in r});
 with path.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
wc(OUT/'g.csv',allg);wc(OUT/'t.csv',task_rows);wc(OUT/'z.csv',trace_rows)
# deterministic sample: 5 largest per domain/source, opaque names only
samp=[]
for src in sorted(files):samp+=sorted([g for g in allg if g['src']==src],key=lambda g:(-g['n'],g['idx']))[:5]
(OUT/'s.json').write_text(json.dumps(samp,indent=2,sort_keys=True));print(json.dumps(res,indent=2,sort_keys=True))