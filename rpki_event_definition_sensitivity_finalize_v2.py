#!/usr/bin/env python3
import csv,json
from pathlib import Path
BASE=Path(__file__).resolve().parent
DIR=BASE/'results'/'rpki_event_definition_sensitivity_v1_20261002'
CASES=DIR/'bgplay_cases.json'
CANON=BASE/'results'/'rpki_outcomeblind_validate_v2_20261002'/'exact_rov_validate.csv'
PRIOR=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'exact_reconstruction.csv'
WINDOWS=['direct_only','15m','1h','6h']

def readcsv(p):
 with open(p,newline='',encoding='utf8') as f:return list(csv.DictReader(f))
def key(r):return (r['event_date'],r['representative_prefix'],str(r['A']),str(r['B']))
def zkey(z):return tuple(str(x) for x in z['key'])

def main():
 z=json.loads(CASES.read_text(encoding='utf8')); canon=readcsv(CANON); prior=readcsv(PRIOR)
 ck={key(r) for r in canon}; ik={key(r) for r in canon if r['A_rov']=='valid' and r['B_rov']=='invalid'}
 assert len(canon)==309,len(canon);assert len(ck)==309,len(ck);assert len(ik)==41,len(ik)
 pd={key(r):r for r in prior}; zd={zkey(x):x for x in z}
 one={k for k,x in zd.items() if x['1h']['resolved_ge3']}
 mism=[]
 for k,x in zd.items():
  p=pd[k]; old=int(float(p.get('exact_rrc_n') or 0)); new=int(x['1h']['rrc_n'])
  if old!=new:mism.append({'key':k,'frozen_1h_rrc_n':old,'refetched_1h_rrc_n':new,'frozen_resolved_ge3':old>=3,'refetched_resolved_ge3':new>=3})
 rows=[]; detail={}
 for w in WINDOWS:
  res={k for k,x in zd.items() if x[w]['resolved_ge3']}
  cr=ck & res; ir=ik & res
  changed=[k for k in cr if zd[k][w]['median_date']!=zd[k]['1h']['median_date']]
  ichanged=[k for k in ir if zd[k][w]['median_date']!=zd[k]['1h']['median_date']]
  rec={'window':w,'all_419_refetched_resolved_ge3_n':len(res),'canonical_309_retained_n':len(cr),
       'canonical_309_retention':len(cr)/309,'canonical_41_inversions_retained_n':len(ir),
       'canonical_41_retention':len(ir)/41,'canonical_retained_median_date_changed_vs_1h_n':len(changed),
       'canonical_inversion_median_date_changed_vs_1h_n':len(ichanged)}
  rows.append(rec)
  detail[w]={'canonical_dropped':[list(k) for k in sorted(ck-res)],'canonical_inversions_dropped':[list(k) for k in sorted(ik-res)],
             'canonical_date_changed':[{'key':list(k),'canonical_1h_date':zd[k]['1h']['median_date'],'alternative_date':zd[k][w]['median_date']} for k in sorted(changed)],
             'canonical_inversion_date_changed':[{'key':list(k),'canonical_1h_date':zd[k]['1h']['median_date'],'alternative_date':zd[k][w]['median_date']} for k in sorted(ichanged)]}
 summary={'authority':'Canonical exact set and inversion labels are from rpki_outcomeblind_validate_v2_20261002/exact_rov_validate.csv (309 rows; 41 old-Valid/new-Invalid).',
          'canonical_exact_n':309,'canonical_inversion_n':41,
          'refetch_validation':{'cases_n':len(z),'one_hour_current_resolved_n':len(one),'rrc_count_mismatch_n':len(mism),'mismatches':mism,
             'threshold_membership_symmetric_difference_n':len(ck^one),'canonical_missing_from_refetched_1h_n':len(ck-one),'refetched_1h_extra_vs_canonical_n':len(one-ck)},
          'windows':rows,
          'interpretation_guardrail':'Alternative linkage windows are evaluated as observability/timing sensitivity anchored to the frozen canonical 309/41 set. Canonical ROV labels are not reassigned unless the alternative median calendar date changes; such inversion-date changes are explicitly counted for follow-up.',
          'discarded':'The earlier v1 summary fields labeled canonical_41 used superseded exact_rov.csv with 47 inversions and are non-authoritative.'}
 (DIR/'canonical_v2_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
 (DIR/'canonical_v2_details.json').write_text(json.dumps(detail,indent=2),encoding='utf8')
 with open(DIR/'canonical_v2_windows.csv','w',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
