#!/usr/bin/env python3
import csv,json,time,urllib.parse,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
BASE=Path(__file__).resolve().parent
ALL=BASE/'results'/'rpki_population_v1_20261002'/'event_clusters.csv'
CAN=BASE/'results'/'rpki_full_census_fast_closeout_20261002'/'census_weekly_rov.csv'
OUT=BASE/'results'/'rpki_recurrence_filter_sensitivity_v1_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-recurrence-sensitivity-v1/1.0 research-contact-moon1002'

def read(p):
 with open(p,newline='',encoding='utf8') as f:return list(csv.DictReader(f))
def key(r):return (r['event_date'],r['representative_prefix'],str(r['A']),str(r['B']))
def norm(x):
 s=str(x).strip().lower().replace('-','_').replace(' ','_')
 if s in ('notfound','not_found'):s='unknown'
 if s.startswith('invalid'):return 'invalid'
 return s if s in ('valid','unknown') else None
def extract(o):
 if isinstance(o,dict):
  for k in ('validity','validation','rpki_status','roa_status','route_status','result','status'):
   if k in o:
    s=norm(o[k])
    if s:return s
  for k in ('data','payload','results','items'):
   if k in o:
    s=extract(o[k])
    if s:return s
  for v in o.values():
   s=extract(v)
   if s:return s
 elif isinstance(o,list):
  for v in o:
   s=extract(v)
   if s:return s
 elif isinstance(o,str):return norm(o)
 return None
def getj(url,retries=5):
 err=None
 for i in range(retries):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
   with urllib.request.urlopen(req,timeout=90) as r:return json.loads(r.read().decode('utf8','replace'))
  except Exception as e:err=e;time.sleep(min(8,1.5*(2**i)))
 raise RuntimeError(repr(err))
def val(p,a,d):
 q=urllib.parse.urlencode({'prefix':p,'asn':int(a),'date':d[:10]});s=extract(getj('https://api.bgpkit.com/v3/roas/validate?'+q))
 if not s:raise RuntimeError('unparsed');return s
 return s
def classify(r):
 a=val(r['representative_prefix'],r['A'],r['event_date']);b=val(r['representative_prefix'],r['B'],r['event_date'])
 return {**r,'A_rov_weekly':a,'B_rov_weekly':b,'weekly_inversion':a=='valid' and b=='invalid'}
def main():
 allr=read(ALL);can=read(CAN);cd={key(r):r for r in can};extra=[r for r in allr if key(r) not in cd]
 assert len(allr)==7198,(len(allr));assert len(can)==7046,len(can);assert len(extra)==152,len(extra)
 out=[];errs=[]
 with ThreadPoolExecutor(max_workers=12) as ex:
  fs={ex.submit(classify,r):r for r in extra}
  for i,f in enumerate(as_completed(fs),1):
   try:out.append(f.result())
   except Exception as e:
    r=fs[f];errs.append({'event_date':r['event_date'],'representative_prefix':r['representative_prefix'],'A':r['A'],'B':r['B'],'error':repr(e)})
   if i%25==0 or i==len(fs):print('extra',i,'/',len(fs),'errors',len(errs),flush=True)
 if errs:print(json.dumps(errs[:5],indent=2));raise SystemExit(f'classification errors {len(errs)}')
 combined=can+out
 def inv(rs):return sum(str(r['weekly_inversion']).lower()=='true' for r in rs)
 ci=inv(can);ei=inv(out);ai=inv(combined)
 summary={'canonical_no_recurrence_8w':{'n':len(can),'inversions':ci,'rate':ci/len(can)},
          'excluded_recurrence_within_8w':{'n':len(out),'inversions':ei,'rate':ei/len(out)},
          'all_persistent_clusters_without_recurrence_filter':{'n':len(combined),'inversions':ai,'rate':ai/len(combined)},
          'absolute_rate_change_when_filter_removed':ai/len(combined)-ci/len(can),
          'relative_rate_ratio_all_vs_canonical':(ai/len(combined))/(ci/len(can)),
          'guardrail':'This is an on/off sensitivity for the 8-week non-recurrence filter, not a 4/8/12-week threshold grid. The stored cluster output does not retain recurrence timing needed to reconstruct 4/12 weeks honestly.'}
 (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
 with open(OUT/'excluded_152_classified.csv','w',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(sorted(out,key=key))
 print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
