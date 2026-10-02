#!/usr/bin/env python3
import csv,json,math
from datetime import date
from pathlib import Path
BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_lifecycle_panel_v1_20261002'/'exact_lifecycle.csv'
OUT=BASE/'results'/'rpki_delta_policy_v1_20261002';OUT.mkdir(parents=True,exist_ok=True)
CUTOFF=date(2026,10,2)

def qtile(v,q):
    if not v:return None
    x=sorted(v);return x[int(math.ceil(q*(len(x)-1)))]
def truth(x):return str(x).lower()=='true'
def main():
    if not INP.exists():print('lifecycle input not present; clean no-op');return
    rows=list(csv.DictReader(open(INP,encoding='utf8')))
    x=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    pre=[]
    for d in [0,1,2,3,7,14,30]:
        ok=sum(str(r.get('B_lead_calendar_days',''))!='' and int(r['B_lead_calendar_days'])>=d for r in x)
        pre.append({'delta_pre_days':d,'supported_n':ok,'denominator_n':len(x),'supported_fraction':ok/len(x) if x else None})
    post=[]
    for d in [1,3,7,14,21,30,45,60,90]:
        risk=[]
        for r in x:
            try:ed=date.fromisoformat(r['event_date'][:10])
            except:continue
            if (CUTOFF-ed).days<d:continue
            risk.append(r)
        still=sum(str(r.get('A_linger_observed_days',''))!='' and int(r['A_linger_observed_days'])>=d for r in risk)
        post.append({'days_after_transition':d,'observable_n':len(risk),'old_A_authorization_still_observed_n':still,
                     'observed_survival_fraction':still/len(risk) if risk else None})
    spreads=[float(r['B_route_spread_s']) for r in x if str(r.get('B_route_spread_s',''))!='']
    lead=[int(r['B_lead_calendar_days']) for r in x if str(r.get('B_lead_calendar_days',''))!='']
    linger=[int(r['A_linger_observed_days']) for r in x if str(r.get('A_linger_observed_days',''))!='']
    s={'exact_multi_rrc_population_n':len(x),'activation_policy_support':pre,'old_A_authorization_survival':post,
       'B_route_observation_spread_seconds':{'n':len(spreads),'median':qtile(spreads,.5),'p90':qtile(spreads,.9),'p95':qtile(spreads,.95),'p99':qtile(spreads,.99),'max':max(spreads) if spreads else None},
       'B_authorization_lead_calendar_days':{'n':len(lead),'median':qtile(lead,.5),'p10':qtile(lead,.1),'p90':qtile(lead,.9)},
       'old_A_linger_observed_calendar_days':{'n':len(linger),'median':qtile(linger,.5),'p10':qtile(linger,.1),'p90':qtile(linger,.9)},
       'boundary_counts':{'B_not_authorized_on_event_date':sum(not truth(r.get('B_auth_at_event')) for r in x),
                          'B_authorization_starts_event_day':sum(str(r.get('B_lead_calendar_days',''))=='0' for r in x),
                          'A_authorized_on_event_date':sum(truth(r.get('A_auth_at_event')) for r in x),
                          'A_retirement_right_censored':sum(truth(r.get('A_retirement_right_censored')) for r in x)},
       'interpretation_guardrail':'Policy-support curves describe observed lifecycle alignment, not a causal estimate of the minimum safe RPKI propagation margin. Sub-day CCR refinement is required for same-day activation boundaries; retirement guidance must combine routing stabilization with independent RPKI propagation evidence.'}
    (OUT/'policy_frontier.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2))
if __name__=='__main__':main()
