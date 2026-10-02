#!/usr/bin/env python3
import json,re,csv,hashlib,math,statistics,pathlib,sys
from collections import defaultdict,Counter
OUT=pathlib.Path(sys.argv[1]);ROOT=pathlib.Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
READ_PREFIX=("get_","search_","find_","list_","lookup_","calculate","check_")
WRITE_PREFIX=("book_","cancel_","update_","modify_","change_","add_","remove_","delete_","create_","transfer_","send_")
ENTITY_FIELDS=("reservation_id","order_id","product_id","item_id","user_id")
ALL_KEYS=ENTITY_FIELDS+("flight_number","email")
MUT=re.compile(r'(^|[\s;&|])(rm|mv|cp|touch|mkdir|rmdir|chmod|chown|truncate|dd|tee)\b|>>|(^|[^<>])>(?!=)|sed\s+-i',re.I)
PATH=re.compile(r'(?:(?:/testdata/[A-Za-z0-9_./-]+)|(?:\./)?[A-Za-z0-9_.-]+\.(?:txt|log|json|csv|py|c|cpp|php|md|yaml|yml|xml))')
def H(x):return hashlib.sha256(str(x).encode()).hexdigest()[:12]
def scalars(x):
 r=[]
 if isinstance(x,dict):
  for v in x.values():r+=scalars(v)
 elif isinstance(x,list):
  for v in x:r+=scalars(v)
 elif isinstance(x,(str,int,float)) and len(str(x))>=2:r.append(str(x))
 return r
def isread(n):
 n=(n or '').lower();return n.startswith(READ_PREFIX) and not n.startswith(WRITE_PREFIX)
def ident(n,a):
 if isinstance(a,dict):
  for k in ALL_KEYS:
   if k in a:return (k,str(a[k]))
  if all(k in a for k in ('origin','destination')):return ('route',str(a.get('origin')),str(a.get('destination')),str(a.get('date','')))
 return ('opaque',H(json.dumps(a,sort_keys=True,default=str)))
def latest_sources(vs,texts):
 if not vs:return (-1,'none')
 pos=[]
 for v in vs:
  hit=None
  for j in range(len(texts)-1,-1,-1):
   if v in texts[j][1]:hit=j;break
  if hit is None:return (None,'missing')
  pos.append(hit)
 a=max(pos);return a,texts[a][0]
def tau_parse(o):
 texts=[];byid={};user_epoch=0;state_epoch=0;calls=[];call_seq=0
 for mi,m in enumerate(o.get('traj',[])):
  role=m.get('role','')
  if role=='user':user_epoch+=1
  if role=='tool':
   cid=m.get('tool_call_id'); prior=byid.get(cid)
   if prior and not prior['ro']:state_epoch+=1
   z=m.get('content');texts.append((role,json.dumps(z,sort_keys=True) if isinstance(z,(dict,list)) else str(z or '')));continue
  if role=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{});name=fn.get('name','')
    try:a=json.loads(fn.get('arguments','{}'))
    except:a={'_raw':fn.get('arguments','')}
    an,ar=latest_sources(scalars(a),texts);fieldval=ident(name,a);call_seq+=1
    c={'msg':mi,'seq':call_seq,'name':name,'args':a,'ro':isread(name),'idkey':fieldval,'anchor':an,'anchor_role':ar,'ue':user_epoch,'se':state_epoch,'tcid':tc.get('id')}
    calls.append(c);byid[tc.get('id')]=c
   texts.append((role,''))
  else:
   z=m.get('content');texts.append((role,json.dumps(z,sort_keys=True) if isinstance(z,(dict,list)) else str(z or '')))
 return calls
def tau_groups(o,src,idx):
 cs=tau_parse(o);d=defaultdict(list)
 for c in cs:
  if c['ro'] and c['anchor'] is not None:d[(c['ue'],c['se'],c['anchor'],c['anchor_role'])].append(c)
 out=[]
 for gk,x in d.items():
  # de-dup exact same function/resource identity while preserving first occurrence
  seen=set();u=[]
  for c in x:
   z=(c['name'],c['idkey'])
   if z not in seen:seen.add(z);u.append(c)
  x=u
  if len(x)<2:continue
  if any(c['msg']<=gk[2] for c in x):continue
  names={c['name'] for c in x};fields=[c['idkey'][0] for c in x];vals=[c['idkey'][1:] for c in x]
  strict=(len(names)==1 and len(set(fields))==1 and fields[0] in ENTITY_FIELDS and len(set(vals))==len(vals))
  route=(len(names)==1 and len(set(fields))==1 and fields[0]=='route' and len(set(vals))==len(vals))
  typ='S' if strict else ('R' if route else 'M')
  rounds=len({c['seq'] for c in x})
  out.append({'src':src,'idx':idx,'task_id':o.get('task_id'),'trial':o.get('trial'),'reward':o.get('reward'),'n':len(x),'type':typ,'anchor_role':gk[3],'seq_rounds':rounds,'span_seq':max(c['seq'] for c in x)-min(c['seq'] for c in x)+1,'fn':H('|'.join(sorted(names))),'field':fields[0] if len(set(fields))==1 else 'mixed'})
 return cs,out
def q(v,p):
 if not v:return 0
 v=sorted(v);x=(len(v)-1)*p;a=math.floor(x);b=math.ceil(x)
 return v[a] if a==b else v[a]*(b-x)+v[b]*(x-a)
def tau_audit():
 allg=[];files={};samples=[]
 for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
  arr=json.loads(p.read_text());tr=[];task_any=defaultdict(bool);task_strict=defaultdict(bool);succ_g=[];succ_n=[]
  for idx,o in enumerate(arr):
   cs,gs=tau_groups(o,p.name,idx);tr.append((o,cs,gs));tid=str(o.get('task_id'));task_any[tid]=task_any[tid] or bool(gs);task_strict[tid]=task_strict[tid] or any(g['type']=='S' for g in gs)
   rew=o.get('reward');(succ_g if gs else succ_n).append(float(rew or 0))
   allg+=gs
  gs=[g for _,_,z in tr for g in z];strict=[g for g in gs if g['type']=='S'];routes=[g for g in gs if g['type']=='R'];mixed=[g for g in gs if g['type']=='M']
  files[p.name]={'traces':len(arr),'unique_tasks':len(task_any),'traces_any':sum(bool(z) for _,_,z in tr),'traces_strict':sum(any(g['type']=='S' for g in z) for _,_,z in tr),'tasks_any':sum(task_any.values()),'tasks_strict':sum(task_strict.values()),'groups':len(gs),'strict_groups':len(strict),'route_groups':len(routes),'mixed_groups':len(mixed),'calls_in_strict':sum(g['n'] for g in strict),'success_mean_with_group':statistics.mean(succ_g) if succ_g else None,'success_mean_without_group':statistics.mean(succ_n) if succ_n else None}
  # deterministic audit sample: largest strict + first smaller strict/route/mixed
  for typ in ('S','R','M'):
   z=sorted([g for g in gs if g['type']==typ],key=lambda g:(-g['n'],g['idx']))[:5]
   samples+=z
 sizes=[g['n'] for g in allg];strict=[g for g in allg if g['type']=='S'];ss=[g['n'] for g in strict]
 def rf(groups,k):
  if not groups:return 1
  num=sum(g['seq_rounds'] for g in groups);den=sum(math.ceil(g['n']/k) for g in groups);return num/den if den else 1
 summary={'traces':sum(x['traces'] for x in files.values()),'unique_task_slots':sum(x['unique_tasks'] for x in files.values()),'groups':len(allg),'strict_groups':len(strict),'route_groups':sum(g['type']=='R' for g in allg),'mixed_groups':sum(g['type']=='M' for g in allg),'strict_calls':sum(ss),'strict_size_median':statistics.median(ss) if ss else 0,'strict_size_mean':statistics.mean(ss) if ss else 0,'strict_size_p90':q(ss,.9),'strict_size_max':max(ss) if ss else 0,'strict_anchor_roles':dict(Counter(g['anchor_role'] for g in strict)),'strict_round_ratio_k2':rf(strict,2),'strict_round_ratio_k4':rf(strict,4),'strict_round_ratio_inf':(sum(g['seq_rounds'] for g in strict)/len(strict) if strict else 1),'by_file':files}
 return summary,allg,samples
# AgentTrace conservative file-read audit
def cmd(inp):
 m=re.search(r"(?:command['\"]?\s*:\s*|command=)(['\"])(.*?)\1",inp or '',re.S);return m.group(2) if m else (inp or '')
def atkey(s,prompt):
 if str(s.get('tool_name','')).lower()!='bash':return None
 c=cmd(str(s.get('tool_input','')))
 if MUT.search(c):return None
 ps=sorted(set(PATH.findall(c)))
 if ps and any(x not in prompt for x in ps):return None
 if not ps and not re.search(r'\b(grep|find|cat|ls|head|tail|wc|stat|sort|uniq|cut|awk)\b',c):return None
 return tuple(ps) if ps else (re.sub(r'\s+',' ',c)[:120],)
def lpt(ds,k):
 if not ds:return 0
 if k>=len(ds):return max(ds)
 bins=[0.0]*k
 for d in sorted(ds,reverse=True):
  j=min(range(k),key=lambda i:bins[i]);bins[j]+=d
 return max(bins)
def at_audit():
 rows=[];tot_trace=0;tot_calls=0;byfile={};deltas=[0,10,100,500,1000]
 for p in sorted((ROOT/'at'/'datasets').glob('*.jsonl')):
  F={'traces':0,'multi':0,'groups':0,'tool_calls':0};
  for idx,line in enumerate(p.open(errors='replace')):
   if not line.strip():continue
   o=json.loads(line);prompt=str(o.get('prompt',''));sp=[s for s in o.get('spans',[]) if s.get('tool_name')!='final_answer'];F['traces']+=1;F['tool_calls']+=len(sp);F['multi']+=len(sp)>1;tot_trace+=float(o.get('total_duration_ms') or 0);tot_calls+=len(sp)
   cand=[];seen=set()
   for s in sp:
    k=atkey(s,prompt)
    if k is None or k in seen:continue
    seen.add(k);cand.append(float(s.get('duration_ms') or 0))
   if len(cand)>=2:
    F['groups']+=1;ser=sum(cand);mi=max(cand);t=float(o.get('total_duration_ms') or 0);r={'src':p.name,'idx':idx,'trace':H(o.get('trace_id')),'n':len(cand),'serial_ms':ser,'minf_ms':mi,'local_factor':ser/mi if mi else None,'e2e_factor':t/(t-(ser-mi)) if t>(ser-mi) else None}
    for d in deltas:
     sd=[x+d for x in cand];save=sum(sd)-max(sd);base=t+d*len(sp);r['e2e_d'+str(d)]=base/(base-save) if base>save else None
    rows.append(r)
  byfile[p.name]=F
 loc=[r['local_factor'] for r in rows if r['local_factor']];e2=[r['e2e_factor'] for r in rows if r['e2e_factor']]
 sens={}
 for d in deltas:
  adjusted=tot_trace+d*tot_calls;save=sum(sum([0]) + ((r['serial_ms']+d*r['n'])-(r['minf_ms']+d)) for r in rows);sens[str(d)]={'global_factor':adjusted/(adjusted-save) if adjusted>save else None,'saved_ms':save}
 return {'traces':sum(f['traces'] for f in byfile.values()),'tool_calls':tot_calls,'candidate_traces':len(rows),'candidate_fraction':len(rows)/sum(f['traces'] for f in byfile.values()),'conditional_local_factor_median':statistics.median(loc) if loc else 1,'conditional_local_factor_mean':statistics.mean(loc) if loc else 1,'conditional_local_factor_p90':q(loc,.9),'conditional_e2e_factor_median':statistics.median(e2) if e2 else 1,'sensitivity_added_ms_per_tool':sens,'by_file':byfile},rows
def wc(p,rows):
 if not rows:p.write_text('');return
 cols=sorted({k for r in rows for k in r});
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
a,ag,samp=tau_audit();b,bg=at_audit();res={'schema':'j6d4f-v2','a':a,'b':b};(OUT/'audit.json').write_text(json.dumps(res,indent=2,sort_keys=True));wc(OUT/'x.csv',ag);wc(OUT/'y.csv',bg);(OUT/'sample.json').write_text(json.dumps(samp,indent=2,sort_keys=True));print(json.dumps(res,indent=2,sort_keys=True))