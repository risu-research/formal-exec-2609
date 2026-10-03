#!/usr/bin/env python3
import csv, json, math, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import rpki_lifecycle_panel_v1 as v1
import rpki_policy_consequence_v1 as pol

BASE = Path(__file__).resolve().parent
EXACT = BASE/'results'/'rpki_outcomeblind_validate_v2_20261002'/'exact_rov_validate.csv'
OLD185 = BASE/'inputs'/'rpki_exact185_policy_input_20261002.csv'
OUT = BASE/'results'/'rpki_lifecycle_full309_v1_20261002'
OUT.mkdir(parents=True, exist_ok=True)
CUTOFF = date(2026,10,2)
WORKERS = 8
RUN_WORKERS = 6


def truth(x):
    return str(x).strip().lower() == 'true'


def qtile(vals, q):
    if not vals:
        return None
    x = sorted(vals)
    return x[int(math.ceil(q*(len(x)-1)))]


def key(r):
    return (r['representative_prefix'], str(r['A']), str(r['B']))


def active(intervals, d):
    return any(s <= d <= e for s,e,*_ in intervals)


def lifecycle_one(r):
    p = r['representative_prefix']; A = int(r['A']); B = int(r['B'])
    ed = date.fromisoformat(r['exact_event_date'])
    rec = v1.roa_history(p)
    ai = v1.auth_intervals(rec,p,A); bi = v1.auth_intervals(rec,p,B)
    b_event = [x for x in bi if x[0] <= ed <= x[1]]
    a_event = [x for x in ai if x[0] <= ed <= x[1]]
    b_start = min((x[0] for x in b_event), default=None)
    b_after = min((x[0] for x in bi if x[0] > ed), default=None)
    a_after = [x for x in ai if x[1] >= ed]
    a_last = max((x[1] for x in a_after), default=None)
    out = dict(r)
    out.update({
        'B_auth_at_exact_event_date': bool(b_event),
        'B_auth_start_exact_aligned': b_start.isoformat() if b_start else '',
        'B_next_auth_start_exact_aligned': b_after.isoformat() if b_after else '',
        'B_lead_calendar_days_exact_aligned': (ed-b_start).days if b_start else '',
        'B_no_matching_auth_on_or_after_event': not bool(b_event or b_after),
        'A_auth_at_exact_event_date': bool(a_event),
        'A_auth_last_observed_exact_aligned': a_last.isoformat() if a_last else '',
        'A_linger_observed_days_exact_aligned': (a_last-ed).days if a_last else '',
        'A_retirement_right_censored_exact_aligned': bool(a_last and a_last >= CUTOFF),
        'roa_history_record_n': len(rec),
    })
    for h in (30,90,180):
        eligible = bool(a_event) and ed + timedelta(days=h) <= CUTOFF
        out[f'A_survival_{h}d_eligible'] = eligible
        out[f'A_authorized_at_{h}d'] = active(ai, ed+timedelta(days=h)) if eligible else ''
    return out


def invalid_run_one(r):
    ed = date.fromisoformat(r['exact_event_date'])
    nxt = r.get('B_next_auth_start_exact_aligned') or ''
    if not nxt:
        return None
    stop = date.fromisoformat(nxt)
    if stop <= ed:
        return None
    p = r['representative_prefix']; B = int(r['B'])
    frozen = str(r.get('B_rov','')).lower()
    trace=[]; err=''
    try:
        for k in range((stop-ed).days+1):
            d = ed + timedelta(days=k)
            st,_ = pol.validate(p,B,d)
            trace.append((d.isoformat(),st))
            if not pol.is_invalid(st):
                break
    except Exception as e:
        err = repr(e)
    day0 = trace[0][1] if trace else ''
    run = 0
    for _,st in trace:
        if pol.is_invalid(st): run += 1
        else: break
    return {
        'representative_prefix':p,'A':r['A'],'B':r['B'],'exact_event_date':ed.isoformat(),
        'frozen_B_rov':frozen,'day0_current_B_rov':day0,
        'day0_matches_frozen': bool(day0 and day0==frozen),
        'B_next_auth_start_exact_aligned':nxt,
        'event_start_invalid_run_days_observed':run,
        'first_noninvalid_day': next((d for d,s in trace if not pol.is_invalid(s)), ''),
        'first_valid_day': next((d for d,s in trace if s=='valid'), ''),
        'trace_n':len(trace),'error':err,'trace':trace,
    }


def subset_summary(rows):
    leads=[int(r['B_lead_calendar_days_exact_aligned']) for r in rows if str(r.get('B_lead_calendar_days_exact_aligned',''))!='']
    lingers=[int(r['A_linger_observed_days_exact_aligned']) for r in rows if truth(r.get('A_auth_at_exact_event_date')) and str(r.get('A_linger_observed_days_exact_aligned',''))!='']
    b_at=sum(truth(r.get('B_auth_at_exact_event_date')) for r in rows)
    b_after=sum((not truth(r.get('B_auth_at_exact_event_date'))) and bool(r.get('B_next_auth_start_exact_aligned')) for r in rows)
    b_none=sum((not truth(r.get('B_auth_at_exact_event_date'))) and not bool(r.get('B_next_auth_start_exact_aligned')) for r in rows)
    a_at=[r for r in rows if truth(r.get('A_auth_at_exact_event_date'))]
    s={
        'n':len(rows),
        'B_authorized_on_exact_event_date_n':b_at,
        'B_authorization_first_observed_after_exact_event_date_n':b_after,
        'B_no_matching_authorization_on_or_after_event_n':b_none,
        'B_pre_authorized_ge1d_n':sum(int(r['B_lead_calendar_days_exact_aligned'])>=1 for r in rows if str(r.get('B_lead_calendar_days_exact_aligned',''))!=''),
        'B_same_day_authorization_boundary_n':sum(str(r.get('B_lead_calendar_days_exact_aligned',''))=='0' for r in rows),
        'B_authorization_lead_days':{'n':len(leads),'median':qtile(leads,.5),'p10':qtile(leads,.1),'p25':qtile(leads,.25),'p75':qtile(leads,.75),'p90':qtile(leads,.9),'p99':qtile(leads,.99)},
        'A_authorized_on_exact_event_date_n':len(a_at),
        'A_retirement_right_censored_n':sum(truth(r.get('A_retirement_right_censored_exact_aligned')) for r in a_at),
        'A_linger_observed_days':{'n':len(lingers),'median':qtile(lingers,.5),'p25':qtile(lingers,.25),'p75':qtile(lingers,.75),'p90':qtile(lingers,.9)},
    }
    for h in (30,90,180):
        elig=[r for r in a_at if truth(r.get(f'A_survival_{h}d_eligible'))]
        yes=sum(truth(r.get(f'A_authorized_at_{h}d')) for r in elig)
        s[f'A_survival_{h}d']={'numerator':yes,'denominator':len(elig),'fraction':yes/len(elig) if elig else None}
    return s


def regression(old, newrows):
    oldmap={key(r):r for r in old}; newmap={key(r):r for r in newrows}
    fields=[
        ('exact_event_date','exact_event_date',lambda x:str(x)),
        ('B_auth_at_exact_event_date','B_auth_at_exact_event_date',truth),
        ('B_next_auth_start_exact_aligned','B_next_auth_start_exact_aligned',lambda x:str(x or '')),
        ('A_auth_at_exact_event_date','A_auth_at_exact_event_date',truth),
        ('A_auth_last_observed_exact_aligned','A_auth_last_observed_exact_aligned',lambda x:str(x or '')),
    ]
    mism=[]
    for k,o in oldmap.items():
        n=newmap.get(k)
        if not n:
            mism.append({'key':k,'field':'missing_new'})
            continue
        for of,nf,conv in fields:
            if conv(o.get(of,'')) != conv(n.get(nf,'')):
                mism.append({'key':k,'field':of,'old':o.get(of,''),'new':n.get(nf,'')})
    extra=[k for k in newmap if k not in oldmap]
    return {
        'prior185_n':len(oldmap),'overlap_n':sum(k in newmap for k in oldmap),
        'new_outside_prior185_n':len(extra),'field_mismatch_n':len(mism),
        'field_mismatches':mism[:100],
        'pass':len(oldmap)==185 and sum(k in newmap for k in oldmap)==185 and len(mism)==0,
    }


def main():
    if not EXACT.exists() or not OLD185.exists():
        raise SystemExit('required frozen inputs missing')
    exact=list(csv.DictReader(open(EXACT,encoding='utf8')))
    if len(exact)!=309:
        raise SystemExit(f'expected 309 exact rows, got {len(exact)}')
    old=list(csv.DictReader(open(OLD185,encoding='utf8')))
    oldkeys={key(r) for r in old}

    out=[]; errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(lifecycle_one,r):key(r) for r in exact}
        for i,f in enumerate(as_completed(fs),1):
            try: out.append(f.result())
            except Exception as e: errs.append({'key':fs[f],'stage':'lifecycle','error':repr(e)})
            if i%25==0: print('lifecycle',i,'/',len(fs),flush=True)
    out.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix'],int(r['A']),int(r['B'])))

    reg=regression(old,out)
    allsum=subset_summary(out)
    oldrows=[r for r in out if key(r) in oldkeys]
    newrows=[r for r in out if key(r) not in oldkeys]
    oldsum=subset_summary(oldrows); newsum=subset_summary(newrows)

    candidates=[r for r in out if str(r.get('B_rov','')).lower().startswith('invalid') and bool(r.get('B_next_auth_start_exact_aligned'))]
    runs=[]
    with ThreadPoolExecutor(max_workers=RUN_WORKERS) as ex:
        fs={ex.submit(invalid_run_one,r):key(r) for r in candidates}
        for i,f in enumerate(as_completed(fs),1):
            try:
                z=f.result()
                if z:runs.append(z)
            except Exception as e: errs.append({'key':fs[f],'stage':'invalid_run','error':repr(e)})
            if i%10==0: print('invalid-run',i,'/',len(fs),flush=True)
    runs.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix']))
    goodruns=[r for r in runs if not r['error'] and r['day0_matches_frozen'] and str(r['day0_current_B_rov']).startswith('invalid') and int(r['event_start_invalid_run_days_observed'])>0]
    runvals=[int(r['event_start_invalid_run_days_observed']) for r in goodruns]

    anchor_checks={
        'B_authorized_on_exact_event_date_n':oldsum['B_authorized_on_exact_event_date_n']==80,
        'B_pre_authorized_ge1d_n':oldsum['B_pre_authorized_ge1d_n']==69,
        'B_same_day_authorization_boundary_n':oldsum['B_same_day_authorization_boundary_n']==11,
        'B_authorization_lead_median_days':oldsum['B_authorization_lead_days']['median']==13,
        'A_authorized_on_exact_event_date_n':oldsum['A_authorized_on_exact_event_date_n']==98,
        'A_survival_30d':(oldsum['A_survival_30d']['numerator'],oldsum['A_survival_30d']['denominator'])==(94,98),
        'A_survival_90d':(oldsum['A_survival_90d']['numerator'],oldsum['A_survival_90d']['denominator'])==(71,77),
        'A_survival_180d':(oldsum['A_survival_180d']['numerator'],oldsum['A_survival_180d']['denominator'])==(33,45),
    }

    summary={
        'design':{
            'input':'309 frozen outcome-blind exact cases (>=3 RRCs)',
            'lifecycle_source':'BGPKIT ROA history /v3/roas/search using the same authorization interval definition as prior185',
            'exact_alignment':'exact multi-RRC BGP transition median calendar date',
            'cutoff':CUTOFF.isoformat(),
            'invalid_run_rule':'candidate selection uses frozen exact B_rov; duration admitted only if day-0 historical validator still matches frozen Invalid state',
        },
        'input_exact_n':len(exact),'completed_lifecycle_n':len(out),'lifecycle_errors_n':sum(e['stage']=='lifecycle' for e in errs),
        'regression_prior185':reg,
        'manuscript_anchor_checks':anchor_checks,
        'manuscript_anchors_all_pass':all(anchor_checks.values()),
        'all309':allsum,
        'prior185_recomputed':oldsum,
        'new124_only':newsum,
        'invalid_run':{
            'frozen_invalid_with_later_matching_auth_candidate_n':len(candidates),
            'completed_n':len(runs),'day0_match_n':sum(r['day0_matches_frozen'] for r in runs if not r['error']),
            'errors_n':sum(bool(r['error']) for r in runs),'admitted_n':len(goodruns),
            'days':{'n':len(runvals),'min':min(runvals) if runvals else None,'median':qtile(runvals,.5),'p90':qtile(runvals,.9),'p99':qtile(runvals,.99),'max':max(runvals) if runvals else None},
        },
        'usable_for_paper': bool(len(out)==309 and not any(e['stage']=='lifecycle' for e in errs) and reg['pass'] and all(anchor_checks.values())),
        'guardrails':[
            'ROA history is date-granular; same-day publication and BGP ordering is not inferred.',
            'A survival is evaluated only for cases authorized at the exact transition date and observable through each horizon.',
            'Right-censored A authorization at the Oct 2 cutoff is not treated as withdrawal.',
            'The old/new split is diagnostic: prior185 was lifecycle-enriched; all309 is the outcome-blind exact panel.',
            'Invalid-run duration is excluded when current historical validation no longer reproduces the frozen day-0 state.',
        ],
    }

    with open(OUT/'lifecycle_309.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]) if out else ['representative_prefix']);w.writeheader();w.writerows(out)
    flatruns=[{k:v for k,v in r.items() if k!='trace'} for r in runs]
    if flatruns:
        with open(OUT/'invalid_runs.csv','w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=list(flatruns[0]));w.writeheader();w.writerows(flatruns)
    (OUT/'invalid_run_traces.json').write_text(json.dumps({r['representative_prefix']:r['trace'] for r in runs if r.get('trace')},indent=2),encoding='utf8')
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':
    main()
