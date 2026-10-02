#!/usr/bin/env python3
import csv,json,math,pathlib,statistics,sys
P=pathlib.Path(sys.argv[1]);O=pathlib.Path(sys.argv[2]);O.mkdir(parents=True,exist_ok=True);R=list(csv.DictReader(P.open()))
def q(a,p):
 a=sorted(a);x=(len(a)-1)*p;i=math.floor(x);j=math.ceil(x);return a[i] if i==j else a[i]*(j-x)+a[j]*(x-i)
rows=[]
for r in R:
 e=float(r['e2e_factor']);ser=float(r['serial_ms']);mi=float(r['minf_ms']);save=ser-mi
 if e<=1 or save<=0:continue
 t=e*save/(e-1);share=ser/t;save_share=save/t
 # Amdahl expression using local segment speedup S=ser/mi and segment share rho=ser/t
 S=ser/mi if mi>0 else float('inf');pred=1/((1-share)+share/S)
 rows.append({'trace':r['trace'],'src':r['src'],'service_share':share,'max_savable_share':save_share,'local_factor':S,'e2e_observed_bound':e,'e2e_amdahl':pred,'trace_ms_reconstructed':t})
out={'n':len(rows)}
for k in ['service_share','max_savable_share','local_factor','e2e_observed_bound']:
 a=[x[k] for x in rows];out[k]={'median':statistics.median(a),'mean':statistics.mean(a),'p90':q(a,.9),'max':max(a)}
out['amdahl_max_abs_error']=max(abs(x['e2e_amdahl']-x['e2e_observed_bound']) for x in rows)
(O/'r9.json').write_text(json.dumps(out,indent=2,sort_keys=True));print(json.dumps(out,indent=2,sort_keys=True))