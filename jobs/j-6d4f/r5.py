#!/usr/bin/env python3
import csv,json,pathlib,sys
from collections import defaultdict
P=pathlib.Path(sys.argv[1]); O=pathlib.Path(sys.argv[2]); O.mkdir(parents=True,exist_ok=True)
rows=list(csv.DictReader(P.open()))
D=defaultdict(lambda:defaultdict(lambda:{'g':0,'s':0,'gn':0,'sn':0}))
for r in rows:
 d=r['domain'];t=r['task_id'];hit=int(r['hit']);src=r['src'];fam='g' if src.startswith('gpt-') else 's'
 D[d][t][fam]+=hit;D[d][t][fam+'n']+=1
out={}
for d,tasks in D.items():
 g={t for t,v in tasks.items() if v['g']>0};s={t for t,v in tasks.items() if v['s']>0};both=g&s;either=g|s
 # strong replication: >=25% of traces hit in each family
 strong={t for t,v in tasks.items() if v['gn'] and v['sn'] and v['g']/v['gn']>=.25 and v['s']/v['sn']>=.25}
 out[d]={'tasks':len(tasks),'g_any':len(g),'s_any':len(s),'both_any':len(both),'either_any':len(either),'jaccard':len(both)/len(either) if either else 0,'both_any_frac_all':len(both)/len(tasks) if tasks else 0,'both_ge25pct':len(strong),'both_ge25pct_frac_all':len(strong)/len(tasks) if tasks else 0}
(O/'r5.json').write_text(json.dumps(out,indent=2,sort_keys=True));print(json.dumps(out,indent=2,sort_keys=True))