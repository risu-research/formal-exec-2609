#!/usr/bin/env python3
from pathlib import Path
import argparse, hashlib, itertools, json, re
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

CC18 = {
3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,
2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,
7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,
10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,
146800,146817,146819,146820,146821,146822,146824,146825,167119,
167120,167121,167124,167125,167140,167141
}
assert len(CC18) == 72
EXPECTED_COMMIT='feca5e554148bdc4ac17c3379984c2a71244cd32'
EXPECTED_BLOB='6afbc26593d51fad95927729305ab3b98ab0e56a'

def task_id(x):
    m=re.search(r'__(\d+)$', str(x))
    return int(m.group(1)) if m else np.nan

def bh(p):
    p=np.asarray(p,float)
    out=np.full(len(p),np.nan)
    ok=np.isfinite(p)
    vals=p[ok]
    if not len(vals): return out
    order=np.argsort(vals); ranked=vals[order]
    q=ranked*len(ranked)/(np.arange(len(ranked))+1)
    q=np.minimum.accumulate(q[::-1])[::-1]
    q=np.clip(q,0,1)
    tmp=np.empty(len(vals)); tmp[order]=q
    out[np.where(ok)[0]]=tmp
    return out

def strat_boot(g1,g0,B,seed):
    rng=np.random.default_rng(seed)
    # interaction = outside - cc18
    obs=float(g0.mean()-g1.mean())
    vals=np.empty(B)
    # sequential to keep memory bounded and deterministic
    for i in range(B):
        vals[i]=rng.choice(g0,len(g0),replace=True).mean()-rng.choice(g1,len(g1),replace=True).mean()
    lo,hi=np.quantile(vals,[.025,.975])
    pboot=2*min((vals<=0).mean(),(vals>=0).mean())
    return obs,float(lo),float(hi),float(min(1,pboot))

def perm_test(g1,g0,B,seed):
    rng=np.random.default_rng(seed)
    obs=abs(float(g0.mean()-g1.mean()))
    z=np.concatenate([g1,g0]); n1=len(g1)
    ge=0
    for _ in range(B):
        q=rng.permutation(z)
        stat=abs(float(q[n1:].mean()-q[:n1].mean()))
        ge += stat >= obs-1e-15
    return (ge+1)/(B+1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--csv',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--boot',type=int,default=5000)
    ap.add_argument('--perm',type=int,default=10000)
    a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    src=Path(a.csv)
    raw=src.read_bytes()
    sha256=hashlib.sha256(raw).hexdigest()
    df=pd.read_csv(src)
    required={'alg_name','dataset_name','Accuracy__test_mean'}
    assert required.issubset(df.columns), sorted(required-set(df.columns))
    df['task_id']=df.dataset_name.map(task_id)
    assert df.task_id.notna().all()
    df['task_id']=df.task_id.astype(int)
    df['in_cc18']=df.task_id.isin(CC18)

    algs=sorted(df.alg_name.unique())
    tasks=sorted(df.task_id.unique())
    # Duplicate key audit; keep only unique alg-task cells.
    dup=df.duplicated(['alg_name','task_id'],keep=False)
    dup_rows=df.loc[dup].sort_values(['alg_name','task_id'])
    dup_rows.to_csv(out/'duplicate_algorithm_task_rows.csv',index=False)
    if dup.any():
        raise RuntimeError(f'duplicate alg-task rows: {dup.sum()}')

    coverage=(df.groupby('alg_name').agg(rows=('task_id','size'),tasks=('task_id','nunique'),
              cc18_tasks=('in_cc18','sum')).reset_index())
    coverage['outside_tasks']=coverage.tasks-coverage.cc18_tasks
    coverage.to_csv(out/'algorithm_coverage.csv',index=False)

    task_cov=(df.groupby('task_id').agg(dataset_name=('dataset_name','first'),algorithms=('alg_name','nunique'),
              in_cc18=('in_cc18','first')).reset_index())
    task_cov.to_csv(out/'task_coverage.csv',index=False)

    rows=[]
    for k,(aa,bb) in enumerate(itertools.combinations(algs,2)):
        A=df[df.alg_name==aa][['task_id','Accuracy__test_mean','in_cc18']].rename(columns={'Accuracy__test_mean':'ya'})
        B=df[df.alg_name==bb][['task_id','Accuracy__test_mean']].rename(columns={'Accuracy__test_mean':'yb'})
        m=A.merge(B,on='task_id',how='inner',validate='one_to_one')
        m['d']=m.ya-m.yb
        c=m.loc[m.in_cc18,'d'].to_numpy(float)
        o=m.loc[~m.in_cc18,'d'].to_numpy(float)
        rec={'algorithm_a':aa,'algorithm_b':bb,'n_common_total':len(m),
             'n_cc18_common':len(c),'n_outside_common':len(o)}
        if len(c)>=2 and len(o)>=2:
            est,lo,hi,pb=strat_boot(c,o,a.boot,260929000+k)
            pp=perm_test(c,o,a.perm,260930000+k)
            rec.update({
                'delta_cc18':float(c.mean()),'delta_outside':float(o.mean()),
                'median_delta_cc18':float(np.median(c)),'median_delta_outside':float(np.median(o)),
                'interaction_outside_minus_cc18':est,
                'interaction_ci_lo':lo,'interaction_ci_hi':hi,
                'bootstrap_p_two_sided':pb,'permutation_p_two_sided':pp,
                'mw_p_two_sided':float(mannwhitneyu(c,o,alternative='two-sided').pvalue),
                'sign_reversal':bool(np.sign(c.mean())!=np.sign(o.mean()) and c.mean()!=0 and o.mean()!=0),
                'cc18_winner':aa if c.mean()>0 else (bb if c.mean()<0 else 'tie'),
                'outside_winner':aa if o.mean()>0 else (bb if o.mean()<0 else 'tie'),
            })
        rows.append(rec)
    P=pd.DataFrame(rows)
    P['permutation_q_bh']=bh(P.get('permutation_p_two_sided',pd.Series(np.nan,index=P.index)))
    P['bootstrap_q_bh']=bh(P.get('bootstrap_p_two_sided',pd.Series(np.nan,index=P.index)))
    P['abs_interaction']=P.get('interaction_outside_minus_cc18',pd.Series(np.nan,index=P.index)).abs()
    P=P.sort_values(['sign_reversal','abs_interaction'],ascending=[False,False])
    P.to_csv(out/'pairwise_interaction_matrix.csv',index=False)
    P[P.sign_reversal.fillna(False)].to_csv(out/'sign_reversals.csv',index=False)
    P[P.permutation_q_bh.lt(.05,fill_value=False)].to_csv(out/'interaction_q05.csv',index=False)

    # Dataset identity collision audit: multiple OpenML tasks can share the same base name.
    namebase=df[['task_id','dataset_name']].drop_duplicates().copy()
    namebase['base_name']=namebase.dataset_name.str.replace(r'__\d+$','',regex=True)
    collisions=(namebase.groupby('base_name').filter(lambda x:len(x)>1).sort_values(['base_name','task_id']))
    collisions.to_csv(out/'dataset_name_task_collisions.csv',index=False)

    summary={
      'source_commit_expected':EXPECTED_COMMIT,'source_blob_expected':EXPECTED_BLOB,
      'source_csv_sha256':sha256,'rows':int(len(df)),'algorithms':len(algs),
      'algorithm_names':algs,'unique_task_ids':len(tasks),
      'paper_reported_dataset_count':176,
      'cc18_ids_total':72,'cc18_overlap_tasks':int(df.loc[df.in_cc18,'task_id'].nunique()),
      'outside_cc18_tasks':int(df.loc[~df.in_cc18,'task_id'].nunique()),
      'missing_cc18_ids':sorted(CC18-set(tasks)),
      'algorithm_pairs':int(len(P)),
      'eligible_pairs':int(P.interaction_outside_minus_cc18.notna().sum()),
      'sign_reversals':int(P.sign_reversal.fillna(False).sum()),
      'permutation_q_lt_0_05':int(P.permutation_q_bh.lt(.05,fill_value=False).sum()),
      'bootstrap_ci_excludes_zero':int(((P.interaction_ci_lo>0)|(P.interaction_ci_hi<0)).fillna(False).sum()),
      'dataset_name_collision_rows':int(len(collisions)),
      'max_abs_interaction':float(P.abs_interaction.max()),
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    top=P.head(30)
    md=['# Historical TabZilla task-level interaction audit','',
        '## Headline', '',
        f"- Rows: **{summary['rows']}**",
        f"- Algorithms: **{summary['algorithms']}**",
        f"- Unique OpenML task IDs in cleaned matrix: **{summary['unique_task_ids']}**",
        f"- CC18 overlap / outside: **{summary['cc18_overlap_tasks']} / {summary['outside_cc18_tasks']}**",
        f"- Algorithm pairs: **{summary['algorithm_pairs']}**",
        f"- Sign reversals: **{summary['sign_reversals']}**",
        f"- BH q<.05 permutation interactions: **{summary['permutation_q_lt_0_05']}**",
        f"- Bootstrap 95% CI excluding zero: **{summary['bootstrap_ci_excludes_zero']}**",'',
        '## Strongest / reversal pairs','',
        top[['algorithm_a','algorithm_b','n_cc18_common','n_outside_common','delta_cc18','delta_outside','interaction_outside_minus_cc18','interaction_ci_lo','interaction_ci_hi','permutation_q_bh','sign_reversal']].to_markdown(index=False),'',
        '## Interpretation guardrail','',
        'This is a descriptive interaction audit on a fixed historical result matrix. '
        'A CC18-vs-outside contrast is not by itself a causal effect of any one CC18 curation rule.'
    ]
    (out/'REPORT.md').write_text('\n'.join(md))
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
