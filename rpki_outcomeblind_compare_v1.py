#!/usr/bin/env python3
import csv, json, math, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE=Path(__file__).resolve().parent
NEW=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'exact_rov.csv'
OLD_POLICY=BASE/'results'/'rpki_policy_consequence_v1_20261002'/'cases.csv'
OUT=BASE/'results'/'rpki_outcomeblind_compare_v1_20261002'
OUT.mkdir(parents=True,exist_ok=True)

# First CLI arg is directory containing the recovered corrected lifecycle artifact.
RECOVERED=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/corrected')


def read_csv(p):
    with open(p,encoding='utf8') as f:return list(csv.DictReader(f))

def truth(x):return str(x).strip().lower()=='true'
def key(r):return (r['representative_prefix'],int(r['A']),int(r['B']))
def ts(s):
    if not s:return None
    return datetime.fromisoformat(str(s).replace('Z','+00:00')).timestamp()
def norm_old(s):
    x=str(s).strip().lower()
    if x in ('unknown','notfound','not_found'):return 'notfound'
    if x.startswith('invalid'):return 'invalid'
    if x=='valid':return 'valid'
    return x

def find_corrected_exact(root):
    candidates=list(root.rglob('exact_lifecycle.csv'))
    if not candidates:
        raise SystemExit(f'no exact_lifecycle.csv under {root}')
    # Prefer artifact file with real exact rows.
    best=None;bestn=-1
    for p in candidates:
        rows=read_csv(p)
        n=sum(int(r.get('exact_rrc_n') or 0)>=3 and bool(r.get('B_route_median')) for r in rows)
        if n>bestn:best=(p,rows);bestn=n
    return best[0],best[1]

def write_csv(p,rows):
    if not rows:
        p.write_text('',encoding='utf8');return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(p,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def main():
    corrected_path, old_all=find_corrected_exact(RECOVERED)
    old=[r for r in old_all if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    new=read_csv(NEW)
    old_policy=read_csv(OLD_POLICY)
    od={key(r):r for r in old}; nd={key(r):r for r in new}; pd={key(r):r for r in old_policy}

    diag=[]
    missing=[]
    date_diff=[]
    timestamp_diff=[]
    rrc_diff=[]
    old_state_disagree=[]
    overlap_state=Counter()
    for k,o in sorted(od.items()):
        n=nd.get(k)
        if not n:
            missing.append({'prefix':k[0],'A':k[1],'B':k[2]});continue
        ot=o.get('B_route_median',''); nt=n.get('B_route_median','')
        odt=ot[:10] if ot else ''; ndt=n.get('exact_event_date','') or (nt[:10] if nt else '')
        dtsec=(ts(nt)-ts(ot)) if ot and nt else None
        rr_old=int(o.get('exact_rrc_n') or 0);rr_new=int(n.get('exact_rrc_n') or 0)
        p=pd.get(k)
        oldA=norm_old(p.get('A_validation_event')) if p else ''
        oldB=norm_old(p.get('B_validation_event')) if p else ''
        newA=n.get('A_rov','');newB=n.get('B_rov','')
        if p:
            overlap_state[(oldA,oldB,newA,newB)]+=1
            if oldA!=newA or oldB!=newB:
                old_state_disagree.append({'prefix':k[0],'A':k[1],'B':k[2],'old_date':p.get('exact_event_date',''),'new_date':ndt,'old_A':oldA,'old_B':oldB,'new_A':newA,'new_B':newB})
        rec={'prefix':k[0],'A':k[1],'B':k[2],'old_B_route_median':ot,'new_B_route_median':nt,
             'timestamp_delta_seconds':dtsec,'old_exact_rrc_n':rr_old,'new_exact_rrc_n':rr_new,
             'old_A_rov':oldA,'old_B_rov':oldB,'new_A_rov':newA,'new_B_rov':newB}
        diag.append(rec)
        if odt!=ndt:date_diff.append(rec)
        if dtsec not in (None,0):timestamp_diff.append(rec)
        if rr_old!=rr_new:rrc_diff.append(rec)

    old_inv=[r for r in old_policy if truth(r.get('A_valid_B_invalid'))]
    new_inv=[r for r in new if r.get('A_rov')=='valid' and r.get('B_rov')=='invalid']
    old_inv_keys={key(r) for r in old_inv};old_selected_keys=set(od);new_inv_keys={key(r) for r in new_inv}
    new_inv_in_old=[r for r in new_inv if key(r) in old_selected_keys]
    new_inv_outside=[r for r in new_inv if key(r) not in old_selected_keys]
    old_inv_still_new=[r for r in old_inv if key(r) in new_inv_keys]
    old_inv_not_new=[r for r in old_inv if key(r) not in new_inv_keys]

    # Compare exact-resolved cases outside prior 185 to make clear where added evidence comes from.
    new_outside=[r for r in new if key(r) not in old_selected_keys]
    new_inside=[r for r in new if key(r) in old_selected_keys]

    # Any same-prefix A/B mismatch between recovered old exact and new exact set.
    opfx={r['representative_prefix']:key(r) for r in old};npfx={r['representative_prefix']:key(r) for r in new}
    key_mismatch=[]
    for p in sorted(set(opfx)&set(npfx)):
        if opfx[p]!=npfx[p]:key_mismatch.append({'prefix':p,'old_key':str(opfx[p]),'new_key':str(npfx[p])})

    write_csv(OUT/'overlap_diagnostics.csv',diag)
    write_csv(OUT/'date_differences.csv',date_diff)
    write_csv(OUT/'state_differences.csv',old_state_disagree)
    write_csv(OUT/'new_inversions_outside_prior185.csv',new_inv_outside)
    write_csv(OUT/'old_inversions_not_new.csv',old_inv_not_new)
    (OUT/'key_mismatches.json').write_text(json.dumps(key_mismatch,indent=2),encoding='utf8')

    summary={
      'recovered_corrected_exact_path':str(corrected_path),
      'prior_corrected_exact_ge3_n':len(old),
      'new_outcome_blind_exact_ge3_n':len(new),
      'prior_exact_key_overlap_in_new_n':len(diag),
      'prior_exact_missing_from_new_n':len(missing),
      'prefix_AB_key_mismatch_n':len(key_mismatch),
      'exact_calendar_date_match_n':len(diag)-len(date_diff),
      'exact_calendar_date_difference_n':len(date_diff),
      'exact_timestamp_identical_n':sum(r['timestamp_delta_seconds']==0 for r in diag),
      'exact_timestamp_difference_n':len(timestamp_diff),
      'rrc_count_difference_n':len(rrc_diff),
      'prior_policy_rows_n':len(old_policy),
      'prior_policy_rows_matching_corrected_exact_key_n':sum(key(r) in od for r in old_policy),
      'prior_old_valid_new_invalid_n':len(old_inv),
      'prior_inversions_still_inversion_new_n':len(old_inv_still_new),
      'prior_inversions_not_inversion_new_n':len(old_inv_not_new),
      'overlap_policy_state_disagreement_n':len(old_state_disagree),
      'new_old_valid_new_invalid_n':len(new_inv),
      'new_inversions_within_prior185_n':len(new_inv_in_old),
      'new_inversions_outside_prior185_n':len(new_inv_outside),
      'new_exact_cases_within_prior185_n':len(new_inside),
      'new_exact_cases_outside_prior185_n':len(new_outside),
      'new_inversion_rate_within_prior185_exact':len(new_inv_in_old)/len(new_inside) if new_inside else None,
      'new_inversion_rate_outside_prior185_exact':len(new_inv_outside)/len(new_outside) if new_outside else None,
      'state_transition_counts_old_to_new':{f'{a}/{b}->{c}/{d}':n for (a,b,c,d),n in sorted(overlap_state.items())},
      'interpretation':'This audit compares the outcome-blind full-panel exact reconstruction against the recovered corrected lifecycle artifact. It distinguishes evidence added by broader outcome-blind coverage from changes in timing or ROV classification.'
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
