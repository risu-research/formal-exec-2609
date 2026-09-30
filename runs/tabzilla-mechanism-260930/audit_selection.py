#!/usr/bin/env python3
from pathlib import Path
import argparse, hashlib, json, re
import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance, ks_2samp

CC18 = {
3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,
2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,
7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,
10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,
146800,146817,146819,146820,146821,146822,146824,146825,167119,
167120,167121,167124,167125,167140,167141
}
assert len(CC18) == 72
SOURCE_COMMIT='feca5e554148bdc4ac17c3379984c2a71244cd32'
CLEAN_BLOB='6afbc26593d51fad95927729305ab3b98ab0e56a'
META_BLOB='a0408b880be9d474ce93e2fc0c23b0c9886b9c9b'

CORE_SUFFIX_PRIORITY = [
    'general.nr_inst','general.nr_attr','general.nr_class','general.nr_num','general.nr_cat',
    'general.attr_to_inst','general.inst_to_attr','general.cat_to_num','general.num_to_cat',
    'general.freq_class.mean','general.freq_class.sd','general.freq_class.min','general.freq_class.max',
    'info-theory.class_ent','info-theory.attr_ent.mean','info-theory.attr_ent.sd',
    'info-theory.mut_inf.mean','info-theory.mut_inf.sd','info-theory.eq_num_attr',
    'statistical.sd.mean','statistical.skewness.mean','statistical.kurtosis.mean',
    'landmarking.best_node.mean','landmarking.naive_bayes.mean','landmarking.one_nn.mean',
    'landmarking.linear_discr.mean','landmarking.random_node.mean','landmarking.worst_node.mean'
]

def task_id_outcome(x):
    m=re.search(r'__(\d+)$', str(x))
    return int(m.group(1)) if m else np.nan

def task_id_meta(x):
    m=re.search(r'__(\d+)__fold_\d+$', str(x))
    if m: return int(m.group(1))
    m=re.search(r'__(\d+)$', str(x))
    return int(m.group(1)) if m else np.nan

def bh(p):
    p=np.asarray(p,float); out=np.full(len(p),np.nan)
    ok=np.isfinite(p); vals=p[ok]
    if len(vals)==0: return out
    order=np.argsort(vals); ranked=vals[order]
    q=ranked*len(ranked)/(np.arange(len(ranked))+1)
    q=np.minimum.accumulate(q[::-1])[::-1]; q=np.clip(q,0,1)
    tmp=np.empty(len(vals)); tmp[order]=q; out[np.where(ok)[0]]=tmp
    return out

def standardized_mean_diff(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float)
    va=np.var(a,ddof=1) if len(a)>1 else np.nan
    vb=np.var(b,ddof=1) if len(b)>1 else np.nan
    den=np.sqrt((va+vb)/2)
    if not np.isfinite(den) or den==0: return np.nan
    return float((np.mean(a)-np.mean(b))/den)

def robust_interval_overlap(a,b,qlo=.05,qhi=.95):
    alo,ahi=np.quantile(a,[qlo,qhi]); blo,bhi=np.quantile(b,[qlo,qhi])
    inter=max(0.0,min(ahi,bhi)-max(alo,blo)); union=max(ahi,bhi)-min(alo,blo)
    return float(inter/union) if union>0 else 1.0

def global_stat(C,mask):
    # C: task-centered task x algorithm matrix; mask True = CC18.
    d=C[~mask].mean(axis=0)-C[mask].mean(axis=0)
    return float(np.dot(d,d)), float(np.max(np.abs(d))), d

def perm_global(C,mask,B,seed):
    rng=np.random.default_rng(seed)
    obs_l2,obs_max,d=global_stat(C,mask)
    n=len(mask); n1=int(mask.sum())
    ge_l2=0; ge_max=0
    for _ in range(B):
        idx=rng.permutation(n)
        pm=np.zeros(n,dtype=bool); pm[idx[:n1]]=True
        l2,mx,_=global_stat(C,pm)
        ge_l2 += l2 >= obs_l2-1e-15
        ge_max += mx >= obs_max-1e-15
    return {
        'n_tasks':int(n),'n_cc18':n1,'n_outside':int(n-n1),'n_algorithms':int(C.shape[1]),
        'stat_l2':obs_l2,'stat_max_abs':obs_max,
        'p_l2':float((ge_l2+1)/(B+1)),'p_max_abs':float((ge_max+1)/(B+1)),
        'algorithm_effect_shift':d.tolist(),'permutations':int(B)
    }

def meta_global(Z,mask,B,seed):
    # Standardize features globally, median-impute only within the observed joined frame.
    X=np.asarray(Z,float).copy()
    med=np.nanmedian(X,axis=0)
    bad=np.where(~np.isfinite(med))[0]
    if len(bad):
        keep=np.ones(X.shape[1],dtype=bool); keep[bad]=False
        X=X[:,keep]; med=med[keep]
    inds=np.where(~np.isfinite(X)); X[inds]=med[inds[1]]
    mu=X.mean(axis=0); sd=X.std(axis=0,ddof=1); sd[~np.isfinite(sd)|(sd==0)]=1
    X=(X-mu)/sd
    obs=float(np.sum((X[mask].mean(axis=0)-X[~mask].mean(axis=0))**2))
    rng=np.random.default_rng(seed); n=len(mask); n1=int(mask.sum()); ge=0
    for _ in range(B):
        idx=rng.permutation(n); pm=np.zeros(n,dtype=bool); pm[idx[:n1]]=True
        st=float(np.sum((X[pm].mean(axis=0)-X[~pm].mean(axis=0))**2))
        ge += st >= obs-1e-15
    return {'stat_standardized_mean_vector_l2':obs,'p_permutation':float((ge+1)/(B+1)),
            'permutations':int(B),'n_features_used':int(X.shape[1])}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--clean',required=True)
    ap.add_argument('--meta',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--perm',type=int,default=50000)
    args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)

    clean_path=Path(args.clean); meta_path=Path(args.meta)
    clean_sha=hashlib.sha256(clean_path.read_bytes()).hexdigest()
    meta_sha=hashlib.sha256(meta_path.read_bytes()).hexdigest()

    df=pd.read_csv(clean_path)
    req={'alg_name','dataset_name','Accuracy__test_mean'}
    assert req.issubset(df.columns), sorted(req-set(df.columns))
    df['task_id']=df.dataset_name.map(task_id_outcome)
    assert df.task_id.notna().all(); df['task_id']=df.task_id.astype(int)
    assert not df.duplicated(['task_id','alg_name']).any()
    pv=df.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index().sort_index(axis=1)
    assert not pv.isna().any().any(), 'Primary cleaned matrix must be rectangular.'
    tasks=pv.index.to_numpy(int); algs=list(pv.columns)
    mask=np.array([t in CC18 for t in tasks],dtype=bool)
    assert mask.sum()==46 and (~mask).sum()==58, (mask.sum(),(~mask).sum())
    Y=pv.to_numpy(float)
    C=Y-Y.mean(axis=1,keepdims=True)

    global_rows=[]
    subsets=[('all',algs),
             ('exclude_TabNet',[a for a in algs if a!='TabNet']),
             ('exclude_VIME',[a for a in algs if a!='VIME']),
             ('exclude_TabNet_VIME',[a for a in algs if a not in {'TabNet','VIME'}])]
    for j,(name,keep_algs) in enumerate(subsets):
        ix=[algs.index(a) for a in keep_algs]
        Ys=Y[:,ix]; Cs=Ys-Ys.mean(axis=1,keepdims=True)
        rec=perm_global(Cs,mask,args.perm,260930100+j)
        rec['subset']=name; rec['algorithms']=';'.join(keep_algs)
        global_rows.append(rec)
    G=pd.DataFrame(global_rows)
    G.to_csv(out/'omnibus_interaction_tests.csv',index=False)

    all_rec=global_rows[0]
    shift=pd.DataFrame({'algorithm':algs,'centered_effect_shift_outside_minus_cc18':all_rec['algorithm_effect_shift']})
    shift['abs_shift']=shift.centered_effect_shift_outside_minus_cc18.abs()
    shift=shift.sort_values('abs_shift',ascending=False)
    shift.to_csv(out/'algorithm_effect_shift.csv',index=False)

    # Historical PyMFE metafeatures are one row per dataset fold.
    mf=pd.read_csv(meta_path)
    assert 'dataset_name' in mf.columns, list(mf.columns[:20])
    mf['task_id']=mf.dataset_name.map(task_id_meta)
    parsed=mf.task_id.notna()
    unparsed=mf.loc[~parsed,['dataset_name']].drop_duplicates()
    unparsed.to_csv(out/'metafeature_unparsed_dataset_names.csv',index=False)
    mf=mf.loc[parsed].copy(); mf['task_id']=mf.task_id.astype(int)
    numeric=[c for c in mf.columns if c not in {'dataset_name','task_id'} and pd.api.types.is_numeric_dtype(mf[c])]
    fold_counts=mf.groupby('task_id').size().rename('metafeature_rows').reset_index()
    meta_task=mf.groupby('task_id')[numeric].mean(numeric_only=True)

    joined_ids=sorted(set(tasks)&set(meta_task.index))
    missing_ids=sorted(set(tasks)-set(meta_task.index))
    extra_meta_ids=sorted(set(meta_task.index)-set(tasks))
    pd.DataFrame({'task_id':missing_ids}).to_csv(out/'cleaned_tasks_missing_metafeatures.csv',index=False)
    pd.DataFrame({'task_id':extra_meta_ids}).to_csv(out/'metafeature_tasks_not_in_cleaned.csv',index=False)
    fold_counts[fold_counts.task_id.isin(joined_ids)].sort_values('task_id').to_csv(out/'joined_task_metafeature_row_counts.csv',index=False)

    M=meta_task.loc[joined_ids].copy()
    Jmask=np.array([t in CC18 for t in joined_ids],dtype=bool)
    audit=[]
    for col in numeric:
        c=pd.to_numeric(M[col],errors='coerce')
        a=c[Jmask].dropna().to_numpy(float); b=c[~Jmask].dropna().to_numpy(float)
        rec={'feature':col,'n_cc18':len(a),'n_outside':len(b),'missing_total':int(c.isna().sum())}
        if len(a)>=3 and len(b)>=3:
            pool=np.sqrt((np.var(a,ddof=1)+np.var(b,ddof=1))/2)
            ks=ks_2samp(a,b,alternative='two-sided',method='auto')
            rec.update({
                'mean_cc18':float(a.mean()),'mean_outside':float(b.mean()),
                'median_cc18':float(np.median(a)),'median_outside':float(np.median(b)),
                'smd_cc18_minus_outside':standardized_mean_diff(a,b),
                'wasserstein':float(wasserstein_distance(a,b)),
                'wasserstein_std':float(wasserstein_distance(a,b)/pool) if np.isfinite(pool) and pool>0 else np.nan,
                'ks_ecdf_distance':float(ks.statistic),'ks_p':float(ks.pvalue),
                'robust_5_95_overlap':robust_interval_overlap(a,b)
            })
        audit.append(rec)
    A=pd.DataFrame(audit)
    A['ks_q_bh']=bh(A.get('ks_p',pd.Series(np.nan,index=A.index)))
    A['abs_smd']=A.get('smd_cc18_minus_outside',pd.Series(np.nan,index=A.index)).abs()
    A=A.sort_values(['abs_smd','ks_ecdf_distance'],ascending=[False,False],na_position='last')
    A.to_csv(out/'metafeature_all_univariate_audit.csv',index=False)

    def find_core(suffix):
        exact='f__pymfe.'+suffix
        if exact in M.columns: return exact
        # tolerate PyMFE naming variants by matching the tail after f__pymfe.
        cand=[c for c in M.columns if c.startswith('f__pymfe.') and c.endswith(suffix)]
        return cand[0] if cand else None
    core=[]
    for s in CORE_SUFFIX_PRIORITY:
        c=find_core(s)
        if c and c not in core: core.append(c)
    # If exact-priority names differ, retain interpretable general/info-theory summary features.
    if len(core)<8:
        fallback=[c for c in M.columns if c.startswith('f__pymfe.general.') or c.startswith('f__pymfe.info-theory.')]
        fallback=[c for c in fallback if any(k in c for k in ['nr_','freq_class','class_ent','attr_ent','mut_inf','attr_to_inst','inst_to_attr'])]
        for c in sorted(fallback):
            if c not in core: core.append(c)
    core_stats=A[A.feature.isin(core)].copy().sort_values('abs_smd',ascending=False,na_position='last')
    core_stats.to_csv(out/'metafeature_core_selection_audit.csv',index=False)
    M[core].reset_index().to_csv(out/'metafeature_core_task_matrix.csv',index=False)

    meta_omnibus = meta_global(M[core].to_numpy(float),Jmask,args.perm,260930900) if core else {}
    schema={
        'metafeature_csv_rows':int(len(pd.read_csv(meta_path,usecols=['dataset_name']))),
        'metafeature_numeric_columns':int(len(numeric)),
        'metafeature_column_names':list(mf.columns),
        'core_features_selected':core,
        'parsed_metafeature_rows':int(len(mf)),
        'unparsed_unique_dataset_names':int(len(unparsed)),
        'joined_cleaned_tasks':int(len(joined_ids)),
        'joined_cc18_tasks':int(Jmask.sum()),
        'joined_outside_tasks':int((~Jmask).sum()),
        'cleaned_tasks_missing_metafeatures':missing_ids,
        'metafeature_tasks_not_in_cleaned_count':int(len(extra_meta_ids))
    }
    (out/'metafeature_schema_and_join.json').write_text(json.dumps(schema,indent=2,allow_nan=False)+'\n')

    summary={
        'source_commit':SOURCE_COMMIT,
        'expected_clean_blob':CLEAN_BLOB,
        'expected_metafeature_blob':META_BLOB,
        'clean_csv_sha256':clean_sha,
        'metafeature_csv_sha256':meta_sha,
        'primary_tasks':int(len(tasks)),'primary_algorithms':int(len(algs)),
        'primary_cc18':int(mask.sum()),'primary_outside':int((~mask).sum()),
        'global_interaction_all':{k:v for k,v in all_rec.items() if k not in {'algorithm_effect_shift','algorithms'}},
        'metafeature_join':{k:schema[k] for k in ['joined_cleaned_tasks','joined_cc18_tasks','joined_outside_tasks','cleaned_tasks_missing_metafeatures']},
        'meta_selection_omnibus':meta_omnibus,
        'guardrail':'Association/finite-frame diagnostics. Suite membership is not randomized; neither omnibus test is a causal effect of CC18 curation.'
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')

    top_core=core_stats.head(20)
    md=['# TabZilla benchmark-membership interaction and selection audit','',
        '## Global algorithm-by-membership interaction','',
        G[['subset','n_tasks','n_cc18','n_outside','n_algorithms','stat_l2','p_l2','stat_max_abs','p_max_abs','permutations']].to_markdown(index=False),'',
        'The omnibus statistic is computed after centering each task across algorithms, so task-level overall difficulty is removed before comparing the CC18 and outside-CC18 algorithm-effect vectors. Membership labels are permuted across tasks while holding the 46/58 group sizes fixed.','',
        '## Historical PyMFE selection audit','',
        f"- Joined cleaned tasks: **{len(joined_ids)}/{len(tasks)}** ({int(Jmask.sum())} CC18, {int((~Jmask).sum())} outside)",
        f"- Numeric PyMFE columns: **{len(numeric)}**",
        f"- Pre-prioritized/interpretable core features found: **{len(core)}**",'',
        '### Core feature shifts','',
        top_core[['feature','n_cc18','n_outside','mean_cc18','mean_outside','smd_cc18_minus_outside','wasserstein_std','ks_ecdf_distance','robust_5_95_overlap','ks_q_bh']].to_markdown(index=False) if len(top_core) else '(No core features resolved.)','',
        '## Guardrail','',
        'These are finite historical association diagnostics. CC18 membership was not randomized, and a membership contrast must not be described as a causal effect of any one curation rule.'
    ]
    (out/'REPORT.md').write_text('\n'.join(md)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    main()
