#!/usr/bin/env python3
import json,re,csv,hashlib,math,statistics,pathlib,sys
from collections import defaultdict
OUT=pathlib.Path(sys.argv[1]); ROOT=pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True,exist_ok=True)
RP=("get_","search_","find_","list_","lookup_","calculate","check_")
WP=("book_","cancel_","update_","modify_","change_","add_","remove_","delete_","create_","transfer_")
IDS=["reservation_id","order_id","product_id","item_id","flight_number","user_id","email"]
MUT=re.compile(r'(^|[\s;&|])(rm|mv|cp|touch|mkdir|rmdir|chmod|chown|truncate|dd|tee)\b|>>|(^|[^<>])>(?!=)|sed\s+-i',re.I)
PATH=re.compile(r'(?:(?:/testdata/[A-Za-z0-9_./-]+)|(?:\./)?[A-Za-z0-9_.-]+\.(?:txt|log|json|csv|py|c|cpp|php|md|yaml|yml|xml))')
def H(x): return hashlib.sha256(str(x).encode()).hexdigest()[:12]
def vals(x):
 r=[]
 if isinstance(x,dict):
  for v in x.values(): r+=vals(v)
 elif isinstance(x,list):
  for v in x:r+=vals(v)
 elif isinstance(x,(str,int,float)) and len(str(x))>=2:r.append(str(x))
 return r
def isread(n):
 n=(n or '').lower(); return (not n.startswith(WP)) and n.startswith(RP)
def rkey(n,a):
 if isinstance(a,dict):
  for k in IDS:
   if k in a:return (n,k,str(a[k]))
  if all(k in a for k in ('origin','destination')):return (n,'route',str(a.get('origin')),str(a.get('destination')),str(a.get('date','')))
 return (n,H(json.dumps(a,sort_keys=True,default=str)))
def anchor(vs,texts):
 if not vs:return -1
 aa=[]
 for v in vs:
  q=-1
  for i,t in enumerate(texts):
   if v in t:q=i;break
  if q<0:return None
  aa.append(q)
 return max(aa)
def tau_calls(obj):
 texts=[]; byid={}; us=0; st=0; calls=[]
 for i,m in enumerate(obj.get('traj',[])):
  role=m.get('role')
  if role=='user':us+=1
  if role=='tool':
   txt=str(m.get('content',''));texts.append(txt);cid=m.get('tool_call_id')
   if cid in byid and not byid[cid]['ro']:st+=1
   continue
  if role=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{}); n=fn.get('name','')
    try:a=json.loads(fn.get('arguments','{}'))
    except:a={'_raw':fn.get('arguments','')}
    c={'i':i,'n':n,'a':a,'ro':isread(n),'k':rkey(n,a),'an':anchor(vals(a),texts),'us':us,'st':st,'id':tc.get('id')}
    calls.append(c);byid[tc.get('id')]=c
   texts.append('')
  else:
   z=m.get('content');texts.append(json.dumps(z,sort_keys=True) if isinstance(z,(dict,list)) else str(z or ''))
 return calls
def tau_groups(obj,src):
 cs=tau_calls(obj); d=defaultdict(list)
 for c in cs:
  if c['ro'] and c['an'] is not None:d[(c['us'],c['st'],c['an'])].append(c)
 out=[]
 for gk,x in d.items():
  u={c['k']:c for c in x};x=list(u.values())
  if len(x)<2 or any(c['i']<=gk[2] for c in x):continue
  out.append({'src':src,'task_id':obj.get('task_id'),'n':len(x),'span':max(c['i'] for c in x)-min(c['i'] for c in x),'anchor':gk[2],'names':';'.join(H(c['n']) for c in x),'keys':';'.join(H(c['k']) for c in x)})
 return cs,out
def load_tau():
 groups=[]; bf=defaultdict(lambda:{'traces':0,'tool_calls':0,'group_traces':0,'groups':0,'group_calls':0})
 for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
  for o in json.loads(p.read_text()):
   cs,gs=tau_groups(o,p.name);b=bf[p.name];b['traces']+=1;b['tool_calls']+=len(cs);b['group_traces']+=bool(gs);b['groups']+=len(gs);b['group_calls']+=sum(g['n'] for g in gs);groups+=gs
 s=[g['n'] for g in groups]
 return {'traces':sum(v['traces'] for v in bf.values()),'tool_calls':sum(v['tool_calls'] for v in bf.values()),'traces_with_groups':sum(v['group_traces'] for v in bf.values()),'groups':len(groups),'group_calls':sum(s),'group_size_median':statistics.median(s) if s else 0,'group_size_mean':statistics.mean(s) if s else 0,'group_size_max':max(s) if s else 0,'k2_local_factor':sum(s)/sum(math.ceil(x/2) for x in s) if s else 1,'k4_local_factor':sum(s)/sum(math.ceil(x/4) for x in s) if s else 1,'kinf_local_factor':sum(s)/len(s) if s else 1,'by_file':dict(bf)},groups
def cmd(inp):
 m=re.search(r"(?:command['\"]?\s*:\s*|command=)(['\"])(.*?)\1",inp or '',re.S);return m.group(2) if m else (inp or '')
def atkey(s,prompt):
 if s.get('tool_name')=='final_answer' or str(s.get('tool_name','')).lower()!='bash':return None
 c=cmd(str(s.get('tool_input','')))
 if MUT.search(c):return None
 ps=sorted(set(PATH.findall(c)))
 if ps and any(x not in prompt for x in ps):return None
 if not ps and not re.search(r'\b(grep|find|cat|ls|head|tail|wc|stat|sort|uniq|cut|awk)\b',c):return None
 return tuple(ps) if ps else (re.sub(r'\s+',' ',c)[:120],)
def lpt(ds,k):
 if not ds:return 0.0
 if k>=len(ds):return max(ds)
 b=[0.0]*k
 for d in sorted(ds,reverse=True):
  j=min(range(k),key=lambda q:b[q]);b[j]+=d
 return max(b)
def load_at():
 A=defaultdict(lambda:{'traces':0,'tool_calls':0,'multi_tool':0,'group_traces':0,'groups':0,'serial_ms':0.0,'saved_ms_k2':0.0,'saved_ms_k4':0.0,'saved_ms_inf':0.0,'total_trace_ms':0.0});rows=[]
 for p in sorted((ROOT/'at'/'datasets').glob('*.jsonl')):
  with p.open(errors='replace') as f:
   for line in f:
    if not line.strip():continue
    o=json.loads(line);prompt=str(o.get('prompt',''));sp=[s for s in o.get('spans',[]) if s.get('tool_name')!='final_answer'];a=A[p.name];a['traces']+=1;a['tool_calls']+=len(sp);a['multi_tool']+=len(sp)>1;a['total_trace_ms']+=float(o.get('total_duration_ms') or 0)
    cand=[];seen=set()
    for s in sp:
     k=atkey(s,prompt)
     if k is None or k in seen:continue
     seen.add(k);cand.append((k,float(s.get('duration_ms') or 0)))
    if len(cand)>=2:
     ds=[d for _,d in cand];a['group_traces']+=1;a['groups']+=1;ser=sum(ds);m2=lpt(ds,2);m4=lpt(ds,4);mi=max(ds);a['serial_ms']+=ser;a['saved_ms_k2']+=ser-m2;a['saved_ms_k4']+=ser-m4;a['saved_ms_inf']+=ser-mi;rows.append({'src':p.name,'trace':H(o.get('trace_id')),'n':len(ds),'serial_ms':ser,'m2_ms':m2,'m4_ms':m4,'minf_ms':mi})
 T={k:sum(v[k] for v in A.values()) for k in ['traces','tool_calls','multi_tool','group_traces','groups','serial_ms','saved_ms_k2','saved_ms_k4','saved_ms_inf','total_trace_ms']}
 for q in ['k2','k4','inf']:
  sv=T['saved_ms_'+q];T['counterfactual_e2e_factor_'+q]=T['total_trace_ms']/(T['total_trace_ms']-sv) if T['total_trace_ms']>sv else None
 T['by_file']=dict(A);return T,rows
def wfrow(p):
 o=json.loads(p.read_text());w=o.get('workflow',o);ts=w.get('specification',{}).get('tasks',[])
 if not ts:return None
 par={str(t.get('id')):set(map(str,t.get('parents',[]))) for t in ts};rem=set(par);done=set();lv=[]
 while rem:
  rd=[x for x in rem if par[x]<=done]
  if not rd:return None
  lv.append(len(rd));done.update(rd);rem.difference_update(rd)
 return {'tasks':len(ts),'levels':len(lv),'max_width':max(lv),'mean_width':statistics.mean(lv)}
def load_wf():
 rows=[]
 for base in [ROOT/'wf'/'pegasus'/'montage',ROOT/'wf'/'pegasus'/'cycles',ROOT/'wf'/'pegasus'/'epigenomics']:
  if not base.exists():continue
  for p in sorted(base.glob('*.json'))[:50]:
   x=wfrow(p)
   if x:x['src']=base.name;rows.append(x)
 apps=sorted(set(x['src'] for x in rows))
 return {'instances':len(rows),'tasks_median':statistics.median([x['tasks'] for x in rows]) if rows else 0,'max_width_median':statistics.median([x['max_width'] for x in rows]) if rows else 0,'max_width_mean':statistics.mean([x['max_width'] for x in rows]) if rows else 0,'levels_median':statistics.median([x['levels'] for x in rows]) if rows else 0,'by_app':{a:{'n':sum(x['src']==a for x in rows),'max_width_median':statistics.median([x['max_width'] for x in rows if x['src']==a])} for a in apps}},rows
def wc(p,rows):
 if not rows:p.write_text('');return
 cols=sorted({k for r in rows for k in r})
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
ts,tg=load_tau();ats,ag=load_at();ws,wr=load_wf();res={'schema':'j6d4f-v1','a':ts,'b':ats,'c':ws};(OUT/'result.json').write_text(json.dumps(res,indent=2,sort_keys=True));wc(OUT/'a.csv',tg);wc(OUT/'b.csv',ag);wc(OUT/'c.csv',wr);print(json.dumps(res,indent=2,sort_keys=True))