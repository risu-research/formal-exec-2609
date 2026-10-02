#!/usr/bin/env python3
import csv,json,math
from datetime import date
from pathlib import Path
BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_lifecycle_refine_v2_20261002'/'exact_lifecycle_v2.csv'
OUT=BASE/'results'/'rpki_delta_policy_v2_20261002';OUT.mkdir(parents=True,exist_ok=True)
CUTOFF=date(2026,10,2)

def truth(x): return str(x).lower()=='true'
def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]

def main():
    if not INP.exists(): print('refined lifecycle absent; clean no-op'); return
    rows=list(csv.DictReader(open(INP,encoding='utf8')))
    # Exact multi-RRC set only; input is already clean-context proxy from v1.
    rows=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    auth=[r for r in rows if truth(r.get('B_auth_at_exact_event_date'))]
    delayed=[r for r in rows if (not truth(r.get('B_auth_at_exact_event_date'))) and bool(r.get('B_next_auth_start_exact_aligned'))]
    no_b_hist=[r for r in rows if (not truth(r.get('B_auth_at_exact_event_date'))) and not r.get('B_next_auth_start_exact_aligned')]
    leads=[int(r['B_lead_calendar_days_exact_aligned']) for r in auth if str(r.get('B_lead_calendar_days_exact_aligned',''))!='']
    lead_support=[]
    for d in [0,1,2,3,7,14,30,60,90]:
        n=sum(v>=d for v in leads)
        lead_support.append({'preauthorize_at_least_days':d,'n':n,'authorized_cases_with_lead_n':len(leads),'fraction':n/len(leads) if leads else None})
    # Retirement survival only among cases where A is definitely authorized on exact event date.
    a0=[r for r in rows if truth(r.get('A_auth_at_exact_event_date'))]
    survival=[]
    for d in [1,3,7,14,21,30,45,60,90,120,180,270]:
        observable=[]
        for r in a0:
            ed=date.fromisoformat(r['exact_event_date'])
            if (CUTOFF-ed).days>=d: observable.append(r)
        alive=sum(str(r.get('A_linger_observed_days_exact_aligned',''))!='' and int(r['A_linger_observed_days_exact_aligned'])>=d for r in observable)
        survival.append({'days_after_exact_transition':d,'observable_n':len(observable),'old_A_authorization_still_observed_n':alive,
                         'observed_survival_fraction':alive/len(observable) if observable else None})
    spreads=[float(r['B_route_spread_s']) for r in rows if str(r.get('B_route_spread_s',''))!='']
    linger=[int(r['A_linger_observed_days_exact_aligned']) for r in a0 if str(r.get('A_linger_observed_days_exact_aligned',''))!='']
    underlap_lb=[]
    for r in delayed:
        try:
            b=date.fromisoformat(r['exact_event_date']);a=date.fromisoformat(r['B_next_auth_start_exact_aligned']);underlap_lb.append((a-b).days)
        except: pass
    out={'exact_multi_rrc_clean_n':len(rows),
         'activation':{'authorized_on_exact_event_date_n':len(auth),'authorization_first_observed_after_exact_event_date_n':len(delayed),
                       'no_B_authorization_history_on_or_after_event_n':len(no_b_hist),
                       'same_day_unordered_n':sum(v==0 for v in leads),
                       'lead_calendar_days':{'n':len(leads),'p10':qtile(leads,.10),'p25':qtile(leads,.25),'median':qtile(leads,.50),'p75':qtile(leads,.75),'p90':qtile(leads,.90),'p95':qtile(leads,.95),'p99':qtile(leads,.99)},
                       'preauthorization_support_curve':lead_support,
                       'delayed_authorization_underlap_lower_bound_days':{'n':len(underlap_lb),'min':min(underlap_lb) if underlap_lb else None,'median':qtile(underlap_lb,.5),'p90':qtile(underlap_lb,.9),'max':max(underlap_lb) if underlap_lb else None}},
         'retirement':{'A_authorized_on_exact_event_date_n':len(a0),'right_censored_at_cutoff_n':sum(truth(r.get('A_retirement_right_censored_exact_aligned')) for r in a0),
                       'linger_observed_calendar_days':{'n':len(linger),'p10':qtile(linger,.1),'p25':qtile(linger,.25),'median':qtile(linger,.5),'p75':qtile(linger,.75),'p90':qtile(linger,.9),'p95':qtile(linger,.95),'p99':qtile(linger,.99)},
                       'old_authorization_survival_curve':survival},
         'routing_visibility':{'spread_seconds':{'n':len(spreads),'p50':qtile(spreads,.5),'p90':qtile(spreads,.9),'p95':qtile(spreads,.95),'p99':qtile(spreads,.99),'max':max(spreads) if spreads else None}},
         'policy_guardrail':{'delta_pre':'Observed lead is operator behavior, not the minimum RPKI propagation time. Same-day cases require CCR sub-day refinement.',
                             'delta_post':'Long old-origin authorization survival is not evidence that propagation requires weeks/months. A safe retirement rule should combine multi-collector routing quiescence with an independently measured RPKI propagation budget.',
                             'sampling':'The 500-case audit panel and prioritized exact subset are mechanism/distribution evidence, not an unbiased ecosystem prevalence estimator.'}}
    (OUT/'policy_frontier_v2.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(json.dumps(out,indent=2))
if __name__=='__main__': main()
