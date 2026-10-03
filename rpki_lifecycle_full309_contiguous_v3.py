#!/usr/bin/env python3
import csv, json, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
import rpki_lifecycle_panel_v1 as v1

BASE=Path(__file__).resolve().parent
EXACT=BASE/'results'/'rpki_outcomeblind_validate_v2_20261002'/'exact_rov_validate.csv'
OLDKEYS=BASE/'results'/'rpki_outcomeblind_compare_v1_20261002'/'overlap_diagnostics.csv'
OUT=BASE/'results'/'rpki_lifecycle_full309_contiguous_v3_20261002';OUT.mkdir(parents=True,exist_ok=True)
CUTOFF=date(2026,10,2);WORKERS=8

def truth(x): return str(x).strip().lower()=='true'
def key(r): return (r['representative_prefix'],str(r['A']),str(r['B']))
def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]

def merge_intervals(xs):
    # authorization is a Boolean union over every covering VRP for the same ASN.
    # Date ranges are inclusive. Adjacent daily intervals are continuous at available resolution.
    spans=sorted((s,e) for s,e,*_ in xs)
    out=[]
    for s,e in spans:
        if not out or s>out[-1][1]+timedelta(days=1): out.append([s,e])
        elif e>out[-1][1]: out[-1][1]=e
    return [(s,e) for s,e in out]

def interval_covering(xs,d):
    return next(((s,e) for s,e in xs if s<=d<=e),None)

def first_after(xs,d):
    return next(((s,e) for s,e in xs if s>d),None)

def one(r):
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B']);ed=date.fromisoformat(r['exact_event_date'])
    rec=v1.roa_history(p)
    a=merge_intervals(v1.auth_intervals(rec,p,A)); b=merge_intervals(v1.auth_intervals(rec,p,B))
    ai=interval_covering(a,ed); bi=interval_covering(b,ed); bn=first_after(b,ed) if not bi else None
    z=dict(r)
    z.update({
      'B_auth_at_event_contiguous':bool(bi),
      'B_contiguous_auth_start':bi[0].isoformat() if bi else '',
      'B_contiguous_auth_end':bi[1].isoformat() if bi else '',
      'B_contiguous_lead_days':(ed-bi[0]).days if bi else '',
      'B_next_contiguous_auth_start':bn[0].isoformat() if bn else '',
      'B_no_matching_auth_on_or_after_event_contiguous':not bool(bi or bn),
      'A_auth_at_event_contiguous':bool(ai),
      'A_contiguous_auth_start':ai[0].isoformat() if ai else '',
      'A_contiguous_auth_end':ai[1].isoformat() if ai else '',
      'A_contiguous_linger_days':(ai[1]-ed).days if ai else '',
      'A_contiguous_right_censored':bool(ai and ai[1]>=CUTOFF),
      'A_merged_interval_n':len(a),'B_merged_interval_n':len(b),'roa_history_record_n_v3':len(rec)
    })
    for h in (30,90,180):
        elig=bool(ai) and ed+timedelta(days=h)<=CUTOFF
        z[f'A_contiguous_survival_{h}d_eligible']=elig
        z[f'A_contiguous_survives_{h}d']=bool(ai and ai[1]>=ed+timedelta(days=h)) if elig else ''
    return z

def summarize(rows):
    bat=[r for r in rows if truth(r['B_auth_at_event_contiguous'])]
    aat=[r for r in rows if truth(r['A_auth_at_event_contiguous'])]
    leads=[int(r['B_contiguous_lead_days']) for r in bat]
    lingers=[int(r['A_contiguous_linger_days']) for r in aat]
    s={
      'n':len(rows),
      'B_authorized_at_exact_event_n':len(bat),'B_authorized_at_exact_event_fraction':len(bat)/len(rows) if rows else None,
      'B_preauthorized_ge1d_n':sum(int(r['B_contiguous_lead_days'])>=1 for r in bat),
      'B_same_day_boundary_n':sum(int(r['B_contiguous_lead_days'])==0 for r in bat),
      'B_first_authorized_after_event_n':sum((not truth(r['B_auth_at_event_contiguous'])) and bool(r['B_next_contiguous_auth_start']) for r in rows),
      'B_no_matching_auth_on_or_after_event_n':sum(truth(r['B_no_matching_auth_on_or_after_event_contiguous']) for r in rows),
      'B_contiguous_lead_days':{'n':len(leads),'median':qtile(leads,.5),'p10':qtile(leads,.1),'p25':qtile(leads,.25),'p75':qtile(leads,.75),'p90':qtile(leads,.9),'p99':qtile(leads,.99),'max':max(leads) if leads else None},
      'A_authorized_at_exact_event_n':len(aat),'A_authorized_at_exact_event_fraction':len(aat)/len(rows) if rows else None,
      'A_right_censored_n':sum(truth(r['A_contiguous_right_censored']) for r in aat),
      'A_contiguous_linger_days':{'n':len(lingers),'median':qtile(lingers,.5),'p10':qtile(lingers,.1),'p25':qtile(lingers,.25),'p75':qtile(lingers,.75),'p90':qtile(lingers,.9),'p99':qtile(lingers,.99),'max':max(lingers) if lingers else None},
    }
    for h in (30,90,180):
        elig=[r for r in aat if truth(r[f'A_contiguous_survival_{h}d_eligible'])]
        yes=sum(truth(r[f'A_contiguous_survives_{h}d']) for r in elig)
        s[f'A_contiguous_survival_{h}d']={'numerator':yes,'denominator':len(elig),'fraction':yes/len(elig) if elig else None}
    return s

def main():
    rows=list(csv.DictReader(open(EXACT,encoding='utf8')))
    if len(rows)!=309: raise SystemExit(f'expected 309 exact rows, got {len(rows)}')
    oldkeys=set()
    if OLDKEYS.exists():
        for r in csv.DictReader(open(OLDKEYS,encoding='utf8')):
            p=r.get('prefix') or r.get('representative_prefix');oldkeys.add((p,str(r['A']),str(r['B'])))
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(one,r):key(r) for r in rows}
        for i,f in enumerate(as_completed(fs),1):
            try:out.append(f.result())
            except Exception as e:errs.append({'key':fs[f],'error':repr(e)})
            if i%25==0:print('contiguous',i,'/',len(fs),flush=True)
    out.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix'],int(r['A']),int(r['B'])))
    old=[r for r in out if key(r) in oldkeys]; new=[r for r in out if key(r) not in oldkeys]
    # Compare merged-contiguous against legacy fields on all 309 to expose exactly what changes.
    diffs={
      'B_at_event_state_diff_n':sum(truth(r['B_auth_at_event_contiguous']) != truth(r.get('B_auth_at_exact_event_date')) for r in out),
      'A_at_event_state_diff_n':sum(truth(r['A_auth_at_event_contiguous']) != truth(r.get('A_auth_at_exact_event_date')) for r in out),
      'B_lead_day_diff_n':sum(str(r.get('B_lead_calendar_days_exact_aligned',''))!=str(r.get('B_contiguous_lead_days','')) for r in out if truth(r['B_auth_at_event_contiguous'])),
      'A_linger_day_diff_n':sum(str(r.get('A_linger_observed_days_exact_aligned',''))!=str(r.get('A_contiguous_linger_days','')) for r in out if truth(r['A_auth_at_event_contiguous'])),
    }
    summary={
      'design':{
        'input':'all 309 frozen outcome-blind exact >=3-RRC cases',
        'authorization_boolean':'union of every covering VRP for the same origin ASN',
        'interval_rule':'merge overlapping or day-adjacent inclusive date ranges; lifecycle uses only the merged interval covering exact BGP transition date',
        'retirement_definition':'end of the continuous old-origin authorization interval that covers the event; later reauthorization after a gap is not counted as lingering',
        'cutoff':CUTOFF.isoformat(),
      },
      'completed_n':len(out),'errors_n':len(errs),'prior185_key_n':len(old),'new124_n':len(new),
      'legacy_vs_contiguous_field_differences':diffs,
      'prior185':summarize(old),'new124':summarize(new),'all309':summarize(out),
      'usable_as_primary_lifecycle':bool(len(out)==309 and len(errs)==0 and len(old)==185 and len(new)==124),
      'guardrails':[
        'Historical authorization is date-granular; same-day ordering relative to BGP is unresolved.',
        'Survival denominators include only A-authorized-at-event cases observable through each horizon.',
        'Intervals reaching the 2026-10-02 cutoff are right-censored, not observed withdrawals.',
        'This measures RPKI authorization persistence, not routing persistence, operator intent, or traffic impact.'
      ]
    }
    with open(OUT/'lifecycle_309_contiguous.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
