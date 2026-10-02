#!/usr/bin/env python3
import csv,json,pathlib,statistics,sys
P=pathlib.Path(sys.argv[1]);O=pathlib.Path(sys.argv[2]);O.mkdir(parents=True,exist_ok=True);rows=list(csv.DictReader(P.open()))
def q(a,p):
 a=sorted(float(x) for x in a);i=(len(a)-1)*p;lo=int(i);hi=min(lo+1,len(a)-1);return a[lo]*(hi-i)+a[hi]*(i-lo)
out={'n':len(rows)}
for k in ['local_factor','e2e_d0','e2e_d10','e2e_d100','e2e_d500','e2e_d1000']:
 a=[float(r[k]) for r in rows if r.get(k) not in ('',None)]
 out[k]={'median':statistics.median(a),'mean':statistics.mean(a),'p90':q(a,.9),'max':max(a)}
(O/'r6.json').write_text(json.dumps(out,indent=2,sort_keys=True));print(json.dumps(out,indent=2,sort_keys=True))