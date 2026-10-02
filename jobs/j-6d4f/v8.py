#!/usr/bin/env python3
import ast,hashlib,json,pathlib,sys
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor
ROOT=pathlib.Path(sys.argv[1]);OUT=pathlib.Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
PURE={'get_reservation_details':('reservation_id','reservations'),'get_order_details':('order_id','orders'),'get_product_details':('product_id','products')}
MUT=('book_','cancel_','update_','modify_','change_','add_','remove_','delete_','create_','transfer_','send_','exchange_','return_')
def pa(v):
 if isinstance(v,dict):return v
 if isinstance(v,str):
  for f in (json.loads,ast.literal_eval):
   try:
    z=f(v)
    if isinstance(z,dict):return z
   except Exception:pass
 return {'_raw':str(v)}
def text(m):
 z=m.get('content','');return json.dumps(z,sort_keys=True,ensure_ascii=False) if isinstance(z,(dict,list)) else str(z or '')
def groups(o):
 ev=[];by={};ue=0;se=0
 for mi,m in enumerate(o.get('traj',[])):
  r=m.get('role','')
  if r=='user':ue+=1;ev.append((mi,'u',ue,se,'',{},text(m)));continue
  if r=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{});n=fn.get('name','');a=pa(fn.get('arguments',{}));c=(mi,'c',ue,se,n,a,'');ev.append(c);by[tc.get('id')]=c
   continue
  if r=='tool':
   c=by.get(m.get('tool_call_id'));n=c[4] if c else '';ev.append((mi,'r',ue,se,n,{},text(m)))
   if n.lower().startswith(MUT):se+=1
  else:ev.append((mi,r,ue,se,'',{},text(m)))
 calls=[e for e in ev if e[1]=='c' and e[4] in PURE and PURE[e[4]][0] in e[5]];b=defaultdict(list)
 for c in calls:b[(c[4],c[2],c[3])].append(c)
 out=[]
 for (n,u,s),cs in b.items():
  fld=PURE[n][0];seen=set();z=[]
  for c in cs:
   v=str(c[5][fld])
   if v not in seen:seen.add(v);z.append((c,v))
  if len(z)<2:continue
  first=min(c[0] for c,_ in z);last=max(c[0] for c,_ in z)
  ps=[e for e in ev if e[1]=='r' and e[0]<first and all(v in e[6] for _,v in z)]
  if not ps:continue
  p=max(ps,key=lambda e:e[0])
  if any((e[1]=='u' or e[3]!=p[3]) for e in ev if p[0]<e[0]<=last):continue
  out.append((n,[v for _,v in z]))
 return out
def load_data(domain):
 base=ROOT/'tau'/'tau_bench'/'envs'/domain/'data'
 if domain=='airline':return {'reservations':json.loads((base/'reservations.json').read_text())}
 return {'orders':json.loads((base/'orders.json').read_text()),'products':json.loads((base/'products.json').read_text())}
def invoke(data,name,x):
 store=PURE[name][1];d=data[store]
 return json.dumps(d[x]) if x in d else ('Error: reservation not found' if store=='reservations' else ('Error: order not found' if store=='orders' else 'Error: product not found'))
D={'airline':load_data('airline'),'retail':load_data('retail')};c=Counter();bad=[];dig=hashlib.sha256()
for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
 dom='airline' if 'airline' in p.name else 'retail';data=D[dom]
 for idx,o in enumerate(json.loads(p.read_text())):
  for name,ids in groups(o):
   c['groups']+=1;c['calls']+=len(ids)
   try:
    ser={x:invoke(data,name,x) for x in ids}
    ok=True
    for _ in range(3):
     with ThreadPoolExecutor(max_workers=min(8,len(ids))) as ex:par=dict(zip(ids,ex.map(lambda x:invoke(data,name,x),ids)))
     ok=ok and (ser==par)
    if not ok:raise AssertionError('mismatch')
    c['pass']+=1;c[name]+=1
    for x in sorted(ser):dig.update(x.encode());dig.update(ser[x].encode())
   except Exception as e:
    c['fail']+=1
    if len(bad)<20:bad.append({'src':p.name,'idx':idx,'n':len(ids),'kind':hashlib.sha256(name.encode()).hexdigest()[:12],'err':type(e).__name__})
res={'schema':'j6d4f-v8','groups':c['groups'],'calls':c['calls'],'passes':c['pass'],'fails':c['fail'],'repetitions':3,'kinds':{hashlib.sha256(k.encode()).hexdigest()[:12]:c[k] for k in PURE},'digest':dig.hexdigest(),'bad':bad}
(OUT/'v8.json').write_text(json.dumps(res,indent=2,sort_keys=True));print(json.dumps(res,indent=2,sort_keys=True))