#!/usr/bin/env python3
import json, pathlib, statistics, math, csv, sys
from collections import defaultdict, deque
ROOT=pathlib.Path(sys.argv[1]); OUT=pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True,exist_ok=True)

def q(v,p):
    if not v: return None
    a=sorted(v); x=(len(a)-1)*p; i=math.floor(x); j=math.ceil(x)
    return a[i] if i==j else a[i]*(j-x)+a[j]*(x-i)

def parse(path):
    try:o=json.loads(path.read_text())
    except Exception:return None
    w=o.get('workflow') or {}
    spec=w.get('specification') or {}
    tasks=spec.get('tasks') or []
    if not tasks:return None
    ex=w.get('execution') or {}
    et=ex.get('tasks') or []
    rt={str(t.get('id')):float(t.get('runtimeInSeconds') or 0.0) for t in et if t.get('id') is not None}
    ids=[str(t.get('id')) for t in tasks if t.get('id') is not None]
    if not ids:return None
    par={str(t.get('id')):set(map(str,t.get('parents') or [])) for t in tasks if t.get('id') is not None}
    ch=defaultdict(list)
    indeg={i:0 for i in ids}
    for v,ps in par.items():
        for u in ps:
            if u in indeg:
                ch[u].append(v); indeg[v]+=1
    dq=deque([i for i in ids if indeg[i]==0]); topo=[]; level={i:0 for i in dq}
    while dq:
        u=dq.popleft();topo.append(u)
        for v in ch[u]:
            level[v]=max(level.get(v,0),level[u]+1); indeg[v]-=1
            if indeg[v]==0:dq.append(v)
    if len(topo)!=len(ids):return None
    widths=defaultdict(int)
    for i in ids:widths[level.get(i,0)]+=1
    runtime_complete=all(i in rt for i in ids)
    positive=sum(1 for i in ids if rt.get(i,0)>0)
    work=sum(rt.get(i,0.0) for i in ids)
    dist={}
    for v in topo:
        best=max((dist[u] for u in par.get(v,set()) if u in dist),default=0.0)
        dist[v]=best+rt.get(v,0.0)
    span=max(dist.values(),default=0.0)
    app=path.parent.name; system=path.parts[0] if path.parts else 'unknown'
    return {'system':system,'app':app,'file':path.name,'tasks':len(ids),'runtime_complete':int(runtime_complete),'positive_runtime_tasks':positive,'work_s':work,'span_s':span,'parallelism':work/span if span>0 else None,'max_width':max(widths.values()) if widths else 0,'levels':len(widths),'recorded_makespan_s':float(ex.get('makespanInSeconds') or 0.0)}

rows=[]
for p in ROOT.rglob('*.json'):
    # Ignore metadata/schema/docs JSON and only keep WfFormat instances.
    if any(x in p.parts for x in ('.git','docs')):continue
    r=parse(p)
    if r:
        rel=p.relative_to(ROOT); r['system']=rel.parts[0] if len(rel.parts)>1 else r['system'];rows.append(r)

weighted=[r for r in rows if r['parallelism'] is not None and r['runtime_complete']]
summary={'instances_parsed':len(rows),'weighted_complete':len(weighted),'systems':{},'overall':{}}
for key,subset in [('overall',weighted)]:
    vals=[r['parallelism'] for r in subset]; widths=[r['max_width'] for r in subset]; tasks=[r['tasks'] for r in subset]
    summary[key]={'n':len(subset),'parallelism_median':statistics.median(vals) if vals else None,'parallelism_mean':statistics.mean(vals) if vals else None,'parallelism_p10':q(vals,.1),'parallelism_p90':q(vals,.9),'parallelism_max':max(vals) if vals else None,'max_width_median':statistics.median(widths) if widths else None,'tasks_median':statistics.median(tasks) if tasks else None,'fraction_parallelism_gt2':sum(v>2 for v in vals)/len(vals) if vals else None,'fraction_parallelism_gt10':sum(v>10 for v in vals)/len(vals) if vals else None}
for s in sorted(set(r['system'] for r in weighted)):
    subset=[r for r in weighted if r['system']==s]; vals=[r['parallelism'] for r in subset]
    summary['systems'][s]={'n':len(subset),'parallelism_median':statistics.median(vals),'parallelism_p90':q(vals,.9),'parallelism_max':max(vals)}
apps={}
for a in sorted(set((r['system'],r['app']) for r in weighted)):
    subset=[r for r in weighted if (r['system'],r['app'])==a]; vals=[r['parallelism'] for r in subset]
    apps[a[0]+'/'+a[1]]={'n':len(subset),'median':statistics.median(vals),'p90':q(vals,.9),'max':max(vals)}
summary['apps']=apps
(OUT/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True))
cols=['system','app','file','tasks','runtime_complete','positive_runtime_tasks','work_s','span_s','parallelism','max_width','levels','recorded_makespan_s']
with (OUT/'instances.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
print(json.dumps(summary,indent=2,sort_keys=True))