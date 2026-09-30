#!/usr/bin/env python3
from pathlib import Path
import argparse, json
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr, kendalltau

def key(a,b): return ' || '.join(sorted([str(a),str(b)]))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--clean',required=True); ap.add_argument('--raw',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    c=pd.read_csv(a.clean); r=pd.read_csv(a.raw)
    c['pair_key']=[key(x,y) for x,y in zip(c.algorithm_a,c.algorithm_b)]
    r['pair_key']=[key(x,y) for x,y in zip(r.algorithm_a,r.algorithm_b)]
    C=c[['pair_key','algorithm_a','algorithm_b','interaction_outside_minus_cc18','sign_reversal','permutation_q_bh','interaction_ci_lo','interaction_ci_hi']].copy()
    C=C.rename(columns={'interaction_outside_minus_cc18':'interaction_clean','sign_reversal':'reversal_clean','permutation_q_bh':'q_clean','interaction_ci_lo':'ci_lo_clean','interaction_ci_hi':'ci_hi_clean'})
    R=r[['pair_key','interaction','sign_reversal','perm_q_bh','ci_lo','ci_hi','n_common','n_cc18','n_outside']].copy()
    R=R.rename(columns={'interaction':'interaction_raw','sign_reversal':'reversal_raw','perm_q_bh':'q_raw','ci_lo':'ci_lo_raw','ci_hi':'ci_hi_raw'})
    m=C.merge(R,on='pair_key',how='outer',validate='one_to_one',indicator=True)
    if not (m._merge=='both').all(): raise RuntimeError('primary/raw pair keys do not match exactly')
    x=m.interaction_clean.to_numpy(float); y=m.interaction_raw.to_numpy(float)
    revc=m.reversal_clean.fillna(False).astype(bool); revr=m.reversal_raw.fillna(False).astype(bool)
    q5c=m.q_clean.lt(.05).fillna(False); q5r=m.q_raw.lt(.05).fillna(False)
    cic=((m.ci_lo_clean>0)|(m.ci_hi_clean<0)).fillna(False); cir=((m.ci_lo_raw>0)|(m.ci_hi_raw<0)).fillna(False)
    m['same_interaction_sign']=np.sign(x)==np.sign(y)
    m['abs_interaction_difference']=np.abs(x-y)
    m['reversal_both']=revc&revr; m['q05_both']=q5c&q5r; m['ci_nonzero_both']=cic&cir
    m.drop(columns=['_merge']).sort_values('abs_interaction_difference',ascending=False).to_csv(out/'pair_alignment.csv',index=False)
    rev_union=int((revc|revr).sum()); q_union=int((q5c|q5r).sum())
    summary={
      'pairs':int(len(m)),
      'pearson_interaction':float(pearsonr(x,y).statistic),
      'spearman_interaction':float(spearmanr(x,y).statistic),
      'kendall_interaction':float(kendalltau(x,y).statistic),
      'same_interaction_sign_count':int(m.same_interaction_sign.sum()),
      'same_interaction_sign_rate':float(m.same_interaction_sign.mean()),
      'mean_abs_interaction_difference':float(m.abs_interaction_difference.mean()),
      'median_abs_interaction_difference':float(m.abs_interaction_difference.median()),
      'clean_reversals':int(revc.sum()),'raw_reversals':int(revr.sum()),'reversal_overlap':int((revc&revr).sum()),
      'reversal_jaccard':float((revc&revr).sum()/rev_union) if rev_union else None,
      'clean_q05':int(q5c.sum()),'raw_q05':int(q5r.sum()),'q05_overlap':int((q5c&q5r).sum()),
      'q05_jaccard':float((q5c&q5r).sum()/q_union) if q_union else None,
      'clean_ci_excludes_zero':int(cic.sum()),'raw_ci_excludes_zero':int(cir.sum()),'ci_nonzero_overlap':int((cic&cir).sum()),
      'raw_pair_common_tasks_min':int(m.n_common.min()),'raw_pair_common_tasks_max':int(m.n_common.max())
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
