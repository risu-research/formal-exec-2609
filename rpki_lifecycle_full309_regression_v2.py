#!/usr/bin/env python3
import csv, json, math, sys
from pathlib import Path

BASE=Path(__file__).resolve().parent
NEW=BASE/'results'/'rpki_lifecycle_full309_v1_20261002'/'lifecycle_309.csv'
RUNS=BASE/'results'/'rpki_lifecycle_full309_v1_20261002'/'invalid_runs.csv'
OUT=BASE/'results'/'rpki_lifecycle_full309_regression_v2_20261002'
OUT.mkdir(parents=True,exist_ok=True)
REC=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/corrected')


def read_csv(p):
    with open(p,encoding='utf8') as f:return list(csv.DictReader(f))

def truth(x):return str(x).strip().lower()=='true'
def key(r):return (r['representative_prefix'],str(r['A']),str(r['B']))
def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]
def find_corrected(root):
    candidates=list(root.rglob('exact_lifecycle.csv'))+list(root.rglob('exact_lifecycle_v2.csv'))
    if not candidates: raise SystemExit('corrected lifecycle artifact missing')
    best=None; bestn=-1
    for p in candidates:
        rows=read_csv(p)
        elig=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
        if len(elig)>bestn:best=(p,elig);bestn=len(elig)
    return best

def subset_summary(rows):
    leads=[int(r['B_lead_calendar_days_exact_aligned']) for r in rows if str(r.get('B_lead_calendar_days_exact_aligned',''))!='']
    lingers=[int(r['A_linger_observed_days_exact_aligned']) for r in rows if truth(r.get('A_auth_at_exact_event_date')) and str(r.get('A_linger_observed_days_exact_aligned',''))!='']
    b_at=[r for r in rows if truth(r.get('B_auth_at_exact_event_date'))]
    a_at=[r for r in rows if truth(r.get('A_auth_at_exact_event_date'))]
    s={
      'n':len(rows),
      'B_authorized_on_exact_event_date_n':len(b_at),
      'B_authorized_on_exact_event_date_fraction':len(b_at)/len(rows) if rows else None,
      'B_authorization_first_observed_after_exact_event_date_n':sum((not truth(r.get('B_auth_at_exact_event_date'))) and bool(r.get('B_next_auth_start_exact_aligned')) for r in rows),
      'B_no_matching_authorization_on_or_after_event_n':sum((not truth(r.get('B_auth_at_exact_event_date'))) and not bool(r.get('B_next_auth_start_exact_aligned')) for r in rows),
      'B_pre_authorized_ge1d_n':sum(int(r['B_lead_calendar_days_exact_aligned'])>=1 for r in rows if str(r.get('B_lead_calendar_days_exact_aligned',''))!=''),
      'B_same_day_authorization_boundary_n':sum(str(r.get('B_lead_calendar_days_exact_aligned',''))=='0' for r in rows),
      'B_authorization_lead_days':{'n':len(leads),'median':qtile(leads,.5),'p10':qtile(leads,.1),'p25':qtile(leads,.25),'p75':qtile(leads,.75),'p90':qtile(leads,.9),'p99':qtile(leads,.99)},
      'A_authorized_on_exact_event_date_n':len(a_at),
      'A_authorized_on_exact_event_date_fraction':len(a_at)/len(rows) if rows else None,
      'A_retirement_right_censored_n':sum(truth(r.get('A_retirement_right_censored_exact_aligned')) for r in a_at),
      'A_linger_observed_days':{'n':len(lingers),'median':qtile(lingers,.5),'p25':qtile(lingers,.25),'p75':qtile(lingers,.75),'p90':qtile(lingers,.9),'p99':qtile(lingers,.99)},
    }
    for h in (30,90,180):
        elig=[r for r in a_at if truth(r.get(f'A_survival_{h}d_eligible'))]
        yes=sum(truth(r.get(f'A_authorized_at_{h}d')) for r in elig)
        s[f'A_survival_{h}d']={'numerator':yes,'denominator':len(elig),'fraction':yes/len(elig) if elig else None}
    return s

def main():
    corrected_path, old= find_corrected(REC)
    new=read_csv(NEW)
    if len(old)!=185: raise SystemExit(f'expected 185 corrected rows, got {len(old)}')
    if len(new)!=309: raise SystemExit(f'expected 309 new rows, got {len(new)}')
    od={key(r):r for r in old}; nd={key(r):r for r in new}
    missing=[k for k in od if k not in nd]
    fields=['exact_event_date','B_auth_at_exact_event_date','B_auth_start_exact_aligned','B_next_auth_start_exact_aligned','B_lead_calendar_days_exact_aligned','A_auth_at_exact_event_date','A_auth_last_observed_exact_aligned','A_linger_observed_days_exact_aligned','A_retirement_right_censored_exact_aligned']
    mism=[]
    for k,o in od.items():
        n=nd.get(k)
        if not n:continue
        for f in fields:
            ov=str(o.get(f,'')).strip().lower(); nv=str(n.get(f,'')).strip().lower()
            if ov!=nv:mism.append({'key':k,'field':f,'old':o.get(f,''),'new':n.get(f,'')})
    old_new=[nd[k] for k in od if k in nd]
    new124=[r for r in new if key(r) not in od]
    oldsum=subset_summary(old_new); allsum=subset_summary(new); newsum=subset_summary(new124)
    anchors={
      'prior185_B_at_event_80':oldsum['B_authorized_on_exact_event_date_n']==80,
      'prior185_B_pre_ge1d_69':oldsum['B_pre_authorized_ge1d_n']==69,
      'prior185_B_same_day_11':oldsum['B_same_day_authorization_boundary_n']==11,
      'prior185_B_lead_median_13':oldsum['B_authorization_lead_days']['median']==13,
      'prior185_A_at_event_98':oldsum['A_authorized_on_exact_event_date_n']==98,
      'prior185_A_survival30_94of98':(oldsum['A_survival_30d']['numerator'],oldsum['A_survival_30d']['denominator'])==(94,98),
      'prior185_A_survival90_71of77':(oldsum['A_survival_90d']['numerator'],oldsum['A_survival_90d']['denominator'])==(71,77),
      'prior185_A_survival180_33of45':(oldsum['A_survival_180d']['numerator'],oldsum['A_survival_180d']['denominator'])==(33,45),
    }
    runs=read_csv(RUNS) if RUNS.exists() else []
    good=[r for r in runs if not r.get('error') and truth(r.get('day0_matches_frozen')) and str(r.get('day0_current_B_rov','')).lower().startswith('invalid') and int(r.get('event_start_invalid_run_days_observed') or 0)>0]
    vals=[int(r['event_start_invalid_run_days_observed']) for r in good]
    invalid_summary={
      'candidate_n':len(runs),'admitted_day0_reproduced_n':len(good),
      'day0_reproduction_fraction':len(good)/len(runs) if runs else None,
      'days':{'n':len(vals),'min':min(vals) if vals else None,'median':qtile(vals,.5),'p90':qtile(vals,.9),'p99':qtile(vals,.99),'max':max(vals) if vals else None}
    }
    # Primary gate: exact corrected lifecycle fields must reproduce. Survival anchors are a second independent manuscript gate.
    regression_pass=(not missing and not mism)
    anchor_pass=all(anchors.values())
    summary={
      'corrected_authority_path':str(corrected_path),
      'corrected_prior185_n':len(old),'new_full309_n':len(new),'overlap_n':len(old_new),'new124_n':len(new124),
      'corrected_field_regression':{'missing_n':len(missing),'mismatch_n':len(mism),'pass':regression_pass,'mismatches':mism[:100]},
      'manuscript_anchor_checks':anchors,'manuscript_anchor_checks_all_pass':anchor_pass,
      'prior185_recomputed':oldsum,'new124_only':newsum,'all309':allsum,
      'invalid_run_conservative':invalid_summary,
      'paper_gate_pass': bool(regression_pass and anchor_pass),
      'interpretation_if_pass':'The lifecycle extension removes the prior lifecycle-enriched selection asymmetry: the same exact-date authorization definitions now cover all 309 outcome-blind >=3-RRC cases.',
      'guardrails':[
        'ROA history is date-granular; same-day BGP/ROA ordering is not inferred.',
        'A-survival denominators include only cases authorized at the exact event and observable through each horizon.',
        'Right-censored authorizations at the 2026-10-02 cutoff are not treated as withdrawal.',
        'Invalid-run duration is descriptive only for cases whose historical day-0 validator state still reproduces the frozen Invalid state.'
      ]
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    with open(OUT/'field_mismatches.csv','w',newline='',encoding='utf8') as f:
        names=['key','field','old','new'];w=csv.DictWriter(f,fieldnames=names);w.writeheader();w.writerows(mism)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
