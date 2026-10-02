#!/usr/bin/env python3
import csv,json
from collections import Counter
from pathlib import Path
BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_lifecycle_panel_v1_20261002'/'screened_panel.csv'
OUT=BASE/'results'/'rpki_panel_policy_audit_20261002';OUT.mkdir(parents=True,exist_ok=True)
def t(x): return str(x).strip().lower()=='true'
def main():
    rows=list(csv.DictReader(open(INP,encoding='utf8')))
    clean=[r for r in rows if r.get('context_class')=='clean_context_proxy']
    states=Counter()
    inv=[]
    prefix_weight=Counter()
    total_prefixes=0
    for r in clean:
        a=t(r.get('A_auth_at_event')); b=t(r.get('B_auth_at_event'))
        key=('A_valid' if a else 'A_not_valid')+'|'+('B_valid' if b else 'B_not_valid')
        states[key]+=1
        w=int(r.get('cluster_prefix_n') or 1);prefix_weight[key]+=w;total_prefixes+=w
        if a and not b: inv.append(r)
    out={
      'panel_design':'500-case month-stratified deterministic audit; top 2 largest clusters per month retained, remaining slots selected by deterministic SHA-256 ordering',
      'screened_panel_n':len(rows),'clean_context_n':len(clean),
      'cluster_state_counts':dict(states),
      'clean_panel_policy_inversion_n':len(inv),
      'clean_panel_policy_inversion_fraction':len(inv)/len(clean) if clean else None,
      'cluster_prefix_weighted_state_counts':dict(prefix_weight),
      'clean_panel_prefix_weight_n':total_prefixes,
      'policy_inversion_prefix_weight_n':sum(int(r.get('cluster_prefix_n') or 1) for r in inv),
      'policy_inversion_prefix_weight_fraction':(sum(int(r.get('cluster_prefix_n') or 1) for r in inv)/total_prefixes if total_prefixes else None),
      'interpretation':{
        'logic':'At the date-granular weekly transition checkpoint, A_auth_at_event=true means a covering VRP validates the observed prefix for old origin A. If B_auth_at_event=false for that same prefix, at least that A VRP covers the route but no matching B VRP validates it; therefore the new B route is ROV Invalid, not NotFound.',
        'sampling_guardrail':'This deterministic audit is substantially less selected on lifecycle outcome than the exact-timing subset, but it deliberately retains two largest clusters per month and is not a probability sample. Report it as an audit-panel rate, not an ecosystem prevalence estimate.',
        'timing_guardrail':'Weekly event_date is a checkpoint, not the exact BGP transition time. The 185-case multi-RRC replay provides exact-date confirmation of the mechanism.'
      }
    }
    (OUT/'summary.json').write_text(json.dumps(out,indent=2),encoding='utf8')
    if inv:
      with open(OUT/'inversions.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(inv[0]));w.writeheader();w.writerows(inv)
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
