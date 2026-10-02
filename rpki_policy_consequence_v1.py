#!/usr/bin/env python3
import csv,json,math,time,urllib.parse,urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta
from pathlib import Path
import rpki_lifecycle_panel_v1 as v1

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_lifecycle_refine_v2_20261002'/'exact_lifecycle_v2.csv'
OUT=BASE/'results'/'rpki_policy_consequence_v1_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-policy-consequence-v1/1.1 research-contact-moon1002'
WORKERS=8

def getj(url,retries=5,timeout=45):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
        except Exception as e:
            err=e;time.sleep(min(10,1.5*(2**i)))
    raise RuntimeError(f'{url}: {err}')

def norm_status(x):
    if x is None:return None
    s=str(x).strip().lower().replace('-','_').replace(' ','_')
    if s in ('notfound','not_found'):s='unknown'
    if s.startswith('invalid'): return s if s in ('invalid_asn','invalid_length') else 'invalid'
    if s in ('valid','unknown'):return s
    return None

def extract_status(obj):
    if isinstance(obj,dict):
        for k in ('validity','validation','rpki_status','roa_status','route_status','result'):
            if k in obj:
                s=norm_status(obj[k])
                if s:return s
        if 'status' in obj:
            s=norm_status(obj['status'])
            if s:return s
        for k in ('data','payload','results','items'):
            if k in obj:
                s=extract_status(obj[k])
                if s:return s
        for v in obj.values():
            s=extract_status(v)
            if s:return s
    elif isinstance(obj,list):
        for v in obj:
            s=extract_status(v)
            if s:return s
    elif isinstance(obj,str):return norm_status(obj)
    return None

def validate(prefix,asn,day):
    q=urllib.parse.urlencode({'prefix':prefix,'asn':int(asn),'date':day.isoformat()})
    j=getj('https://api.bgpkit.com/v3/roas/validate?'+q)
    s=extract_status(j)
    if not s:raise RuntimeError('unparsed validation response '+json.dumps(j)[:1000])
    return s,j

def is_invalid(s):return str(s).startswith('invalid')

def one(r):
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B'])
    ed=datetime.fromisoformat(r['B_route_median'].replace('Z','+00:00')).date()
    bs,_=validate(p,B,ed);as_,_=validate(p,A,ed)
    rec=v1.roa_history(p);bi=v1.auth_intervals(rec,p,B)
    b_after=min((x[0] for x in bi if x[0]>ed),default=None)
    daily=[]
    if b_after and b_after>ed:
        for k in range((b_after-ed).days+1):
            d=ed+timedelta(days=k);st,_=validate(p,B,d);daily.append((d.isoformat(),st))
    invalid_run=0
    for _,st in daily:
        if is_invalid(st):invalid_run+=1
        else:break
    if not daily and is_invalid(bs):invalid_run=1
    first_noninvalid=next((d for d,s in daily if not is_invalid(s)),None)
    first_valid=next((d for d,s in daily if s=='valid'),None)
    return {'representative_prefix':p,'A':A,'B':B,'exact_event_date':ed.isoformat(),'exact_rrc_n':int(r.get('exact_rrc_n') or 0),
      'B_validation_event':bs,'A_validation_event':as_,'A_valid_B_invalid':(as_=='valid' and is_invalid(bs)),
      'B_invalid_event':is_invalid(bs),'B_unknown_event':(bs=='unknown'),'B_valid_event':(bs=='valid'),
      'B_next_matching_auth_start':b_after.isoformat() if b_after else '','daily_trace_n':len(daily),
      'event_start_invalid_run_days_observed':invalid_run,'first_noninvalid_day':first_noninvalid or '','first_valid_day':first_valid or '',
      'daily_trace':daily}

def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]

def main():
    if not INP.exists():raise SystemExit(f'missing corrected input: {INP}')
    rows=list(csv.DictReader(open(INP,encoding='utf8')))
    rows=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(one,r):r['representative_prefix'] for r in rows}
        for i,f in enumerate(as_completed(fs),1):
            try:out.append(f.result())
            except Exception as e:errs.append({'prefix':fs[f],'error':repr(e)})
            if i%20==0:print('policy',i,'/',len(fs),flush=True)
    out.sort(key=lambda z:(z['exact_event_date'],z['representative_prefix']))
    flat=[{k:v for k,v in z.items() if k!='daily_trace'} for z in out]
    if flat:
        with open(OUT/'cases.csv','w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    (OUT/'daily_traces.json').write_text(json.dumps({z['representative_prefix']:z['daily_trace'] for z in out if z['daily_trace']},indent=2),encoding='utf8')
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    bcnt=Counter(z['B_validation_event'] for z in out);acnt=Counter(z['A_validation_event'] for z in out);joint=Counter((z['A_validation_event'],z['B_validation_event']) for z in out)
    inv=[z for z in out if z['B_invalid_event']];inversion=[z for z in out if z['A_valid_B_invalid']]
    runs=[z['event_start_invalid_run_days_observed'] for z in out if z['event_start_invalid_run_days_observed']>0]
    delayed=[z for z in out if z['B_next_matching_auth_start']];delayed_invalid=[z for z in delayed if z['B_invalid_event']]
    summary={'input_exact_multi_rrc_n':len(rows),'completed_n':len(out),'errors_n':len(errs),
      'B_event_validation_counts':dict(bcnt),'A_event_validation_counts':dict(acnt),
      'joint_A_B_event_validation':{f'{a}|{b}':n for (a,b),n in sorted(joint.items())},
      'strict_rov_consequence':{'new_B_invalid_on_exact_transition_day_n':len(inv),'new_B_invalid_fraction':len(inv)/len(out) if out else None,
        'old_A_valid_new_B_invalid_n':len(inversion),'old_A_valid_new_B_invalid_fraction':len(inversion)/len(out) if out else None,
        'new_B_unknown_on_transition_day_n':sum(z['B_unknown_event'] for z in out),'new_B_valid_on_transition_day_n':sum(z['B_valid_event'] for z in out)},
      'delayed_matching_authorization_subset':{'n':len(delayed),'invalid_on_event_n':len(delayed_invalid),
        'invalid_on_event_fraction':len(delayed_invalid)/len(delayed) if delayed else None,
        'event_start_invalid_run_days':{'n':len(runs),'min':min(runs) if runs else None,'median':qtile(runs,.5),'p90':qtile(runs,.9),'p99':qtile(runs,.99),'max':max(runs) if runs else None}},
      'interpretation_guardrails':['Historical BGPKIT validation is evaluated on the exact multi-RRC transition calendar date.',
        'Invalid is operationally consequential only for networks that reject RPKI Invalid routes; Unknown/NotFound is not counted as rejected.',
        'A_valid_B_invalid is a counterfactual policy inversion, not proof that a specific AS enforced ROV or that traffic was lost.',
        'Daily invalid-run calculations are limited to cases with a later matching B authorization and report the contiguous run beginning on transition day.']}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
