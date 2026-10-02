#!/usr/bin/env python3
import csv,json,math,time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime
from pathlib import Path
import rpki_lifecycle_panel_v1 as v1

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_lifecycle_panel_v1_20261002'/'exact_lifecycle.csv'
OUT=BASE/'results'/'rpki_lifecycle_refine_v2_20261002';OUT.mkdir(parents=True,exist_ok=True)
CUTOFF=date(2026,10,2);WORKERS=8

def truth(x):return str(x).lower()=='true'
def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]
def refine(r):
    if int(r.get('exact_rrc_n') or 0)<3 or not r.get('B_route_median'):return None
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B'])
    exact_dt=datetime.fromisoformat(r['B_route_median'].replace('Z','+00:00'));ed=exact_dt.date()
    rec=v1.roa_history(p);ai=v1.auth_intervals(rec,p,A);bi=v1.auth_intervals(rec,p,B)
    b_event=[x for x in bi if x[0]<=ed<=x[1]];a_event=[x for x in ai if x[0]<=ed<=x[1]]
    b_start=min((x[0] for x in b_event),default=None)
    b_after=min((x[0] for x in bi if x[0]>ed),default=None)
    a_after=[x for x in ai if x[1]>=ed];a_last=max((x[1] for x in a_after),default=None)
    return {**r,'exact_event_date':ed.isoformat(),
        'B_auth_at_exact_event_date':bool(b_event),'B_auth_start_exact_aligned':b_start.isoformat() if b_start else '',
        'B_next_auth_start_exact_aligned':b_after.isoformat() if b_after else '',
        'B_lead_calendar_days_exact_aligned':(ed-b_start).days if b_start else '',
        'A_auth_at_exact_event_date':bool(a_event),'A_auth_last_observed_exact_aligned':a_last.isoformat() if a_last else '',
        'A_linger_observed_days_exact_aligned':(a_last-ed).days if a_last else '',
        'A_retirement_right_censored_exact_aligned':bool(a_last and a_last>=CUTOFF)}
def main():
    if not INP.exists():print('v1 input absent; clean no-op');return
    rows=list(csv.DictReader(open(INP,encoding='utf8')));eligible=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(refine,r):r['representative_prefix'] for r in eligible}
        for i,f in enumerate(as_completed(fs),1):
            try:
                z=f.result()
                if z:out.append(z)
            except Exception as e:errs.append({'prefix':fs[f],'error':repr(e)})
            if i%25==0:print('refine',i,'/',len(fs),flush=True)
    out.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix']))
    if out:
        with open(OUT/'exact_lifecycle_v2.csv','w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    leads=[int(r['B_lead_calendar_days_exact_aligned']) for r in out if str(r.get('B_lead_calendar_days_exact_aligned',''))!='']
    lingers=[int(r['A_linger_observed_days_exact_aligned']) for r in out if str(r.get('A_linger_observed_days_exact_aligned',''))!='']
    spreads=[float(r['B_route_spread_s']) for r in out if str(r.get('B_route_spread_s',''))!='']
    s={'input_exact_ge3_n':len(eligible),'refined_n':len(out),'errors_n':len(errs),
       'B_authorized_on_exact_event_date_n':sum(truth(r['B_auth_at_exact_event_date']) for r in out),
       'B_authorization_first_observed_after_exact_event_date_n':sum((not truth(r['B_auth_at_exact_event_date'])) and bool(r.get('B_next_auth_start_exact_aligned')) for r in out),
       'B_authorization_lead_days':{'n':len(leads),'median':qtile(leads,.5),'p10':qtile(leads,.1),'p25':qtile(leads,.25),'p75':qtile(leads,.75),'p90':qtile(leads,.9),'p99':qtile(leads,.99)},
       'A_authorized_on_exact_event_date_n':sum(truth(r['A_auth_at_exact_event_date']) for r in out),
       'A_retirement_right_censored_n':sum(truth(r['A_retirement_right_censored_exact_aligned']) for r in out),
       'A_linger_observed_days':{'n':len(lingers),'median':qtile(lingers,.5),'p10':qtile(lingers,.1),'p25':qtile(lingers,.25),'p75':qtile(lingers,.75),'p90':qtile(lingers,.9),'p99':qtile(lingers,.99)},
       'B_transition_visibility_spread_s':{'n':len(spreads),'median':qtile(spreads,.5),'p90':qtile(spreads,.9),'p95':qtile(spreads,.95),'p99':qtile(spreads,.99),'max':max(spreads) if spreads else None},
       'same_day_activation_boundary_n':sum(str(r.get('B_lead_calendar_days_exact_aligned',''))=='0' for r in out),
       'guardrail':'All lifecycle date comparisons are realigned to exact multi-RRC BGP transition median date, not weekly detection date. Date-zero activation cases remain unordered within the day and are candidates for CCR refinement.'}
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8');(OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2))
if __name__=='__main__':main()
