#!/usr/bin/env python3
import ast,hashlib,json,pathlib,sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
ROOT=pathlib.Path(sys.argv[1]);OUT=pathlib.Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'tau'))
from tau_bench.envs.airline.data import load_data as load_a
from tau_bench.envs.retail.data import load_data as load_r
from tau_bench.envs.airline.tools.get_reservation_details import GetReservationDetails
from tau_bench.envs.retail.tools.get_order_details import GetOrderDetails
from tau_bench.envs.retail.tools.get_product_details import GetProductDetails
PURE={'get_reservation_details':('reservation_id',GetReservationDetails.invoke),'get_order_details':('order_id',GetOrderDetails.invoke),'get_product_details':('product_id',GetProductDetails.invoke)}
MUT=('book_','cancel_','update_','modify_','change_','add_','remove_','delete_','create_','transfer_','send_','exchange_','return_')
def pa(v):
 if isinstance(v,dict):return v
 if isinstance(v,str):
  for f in (json.loads,ast.literal_eval):
   try:
    z=f(v)
    if isinstance(z,dict):return z
   except:pass
 return {'_raw':str(v)}
def txt(m):
 z=m.get('content','');return json.dumps(z,sort_keys=True,ensure_ascii=False) if isinstance(z,(dict,list)) else str(z or '')
def groups(o):
 ev=[];by={};ue=0;se=0
 for mi,m in enumerate(o.get('traj',[])):
  r=m.get('role','')
  if r=='user':ue+=1;ev.append((mi,'u',ue,se,'',{},txt(m)));continue
  if r=='assistant' and m.get('tool_calls'):
   for tc in m['tool_calls']:
    fn=tc.get('function',{});n=fn.get('name','');a=pa(fn.get('arguments',{}));c=(mi,'c',ue,se,n,a,'');ev.append(c);by[tc.get('id')]=c
   continue
  if r=='tool':
   c=by.get(m.get('tool_call_id'));n=c[4] if c else '';ev.append((mi,'r',ue,se,n,{},txt(m)))
   if n.lower().startswith(MUT):se+=1
  else:ev.append((mi,r,ue,se,'',{},txt(m)))
 calls=[e for e in ev if e[1]=='c' and e[4] in PURE and PURE[e[4]][0] in e[5]];b=defaultdict(list)
 for c in calls:b[(c[4],c[2],c[3])].append(c)
 out=[]
 for (n,u,s),cs in b.items():
  fld=PURE[n][0]; seen=set();z=[]
  for c in cs:
   v=str(c[5][fld])
   if v not in seen:seen.add(v);z.append((c,v))
  if len(z)<2:continue
  first=min(c[0] for c,_ in z);last=max(c[0] for c,_ in z)
  parents=[e for e in ev if e[1]=='r' and e[0]<first and all(v in e[6] for _,v in z)]
  if not parents:continue
  p=max(parents,key=lambda e:e[0]);bad=False
  for e in ev:
   if p[0]<e[0]<=last and (e[1]=='u' or e[3]!=p[3]):bad=True;break
  if bad:continue
  out.append((n,[v for _,v in z]))
 return out
A=load_a();R=load_r();counts=defaultdict(int);fail=[];digest=hashlib.sha256()
for p in sorted((ROOT/'tau'/'historical_trajectories').glob('*.json')):
 data=A if 'airline' in p.name else R
 for idx,o in enumerate(json.loads(p.read_text())):
  for name,ids in groups(o):
   fn=PURE[name][1]
   try:
    serial={i:fn(data,i) for i in ids}
    for rep in range(3):
     with ThreadPoolExecutor(max_workers=min(8,len(ids))) as ex: vals=list(ex.map(lambda i:fn(data,i),ids))
     parallel=dict(zip(ids,vals))
     if serial!=parallel:raise AssertionError('mismatch')
    for i in sorted(serial):digest.update(i.encode());digest.update(str(serial[i]).encode())
    counts['groups']+=1;counts['calls']+=len(ids);counts['passes']+=1;counts[name]+=1
   except Exception as e:
    counts['groups']+=1;counts['fails']+=1
    if len(fail)<20:fail.append({'src':p.name,'idx':idx,'name':hashlib.sha256(name.encode()).hexdigest()[:12],'n':len(ids),'err':type(e).__name__})
res={'schema':'j6d4f-v7','groups':counts['groups'],'passes':counts['passes'],'fails':counts['fails'],'calls':counts['calls'],'kinds':{hashlib.sha256(k.encode()).hexdigest()[:12]:v for k,v in counts.items() if k in PURE},'repetitions':3,'output_digest':digest.hexdigest(),'failure_sample':fail}
(OUT/'v7.json').write_text(json.dumps(res,indent=2,sort_keys=True));print(json.dumps(res,indent=2,sort_keys=True))