#!/usr/bin/env python3
from pathlib import Path
import argparse, json, re
import numpy as np
import pandas as pd

CC18 = {
3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,
2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,
7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,
10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,
146800,146817,146819,146820,146821,146822,146824,146825,167119,
167120,167121,167124,167125,167140,167141
}

RAW_FEATURES = {
    'nr_inst':'f__pymfe.general.nr_inst',
    'nr_attr':'f__pymfe.general.nr_attr',
    'nr_class':'f__pymfe.general.nr_class',
    'nr_num':'f__pymfe.general.nr_num',
    'freq_class_max':'f__pymfe.general.freq_class.max',
    'class_ent':'f__pymfe.info-theory.class_ent',
}
FEATURE_ORDER = ['log_nr_inst','log_nr_attr','log_nr_class','numeric_fraction','majority_class_fraction','normalized_class_entropy']

def parse_clean_task(s):
    m=re.search(r'__(\d+)$',str(s)); return int(m.group(1)) if m else None

def parse_meta_name(s):
    m=re.search(r'__(\d+)__fold_(\d+)$',str(s))
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def pair_rms(d):
    d=np.asarray(d,float)
    if len(d)<2:return float('nan')
    z=d-d.mean()
    return float(np.sqrt((2*len(d)/(len(d)-1))*np.mean(z*z)))

def bh(p):
    p=np.asarray(p,float); n=len(p); order=np.argsort(p); q=np.empty(n,float); prev=1.
    for rank in range(n,0,-1):
        idx=order[rank-1]; prev=min(prev,p[idx]*n/rank); q[idx]=prev
    return np.minimum(q,1.)

def transform_features(A):
    # A has task-level medians of raw historical MFE features.
    out=pd.DataFrame(index=A.index)
    out['log_nr_inst']=np.log1p(pd.to_numeric(A['nr_inst'],errors='coerce').clip(lower=0))
    out['log_nr_attr']=np.log1p(pd.to_numeric(A['nr_attr'],errors='coerce').clip(lower=0))
    out['log_nr_class']=np.log1p(pd.to_numeric(A['nr_class'],errors='coerce').clip(lower=0))
    denom=pd.to_numeric(A['nr_attr'],errors='coerce').replace(0,np.nan)
    out['numeric_fraction']=pd.to_numeric(A['nr_num'],errors='coerce')/denom
    out['majority_class_fraction']=pd.to_numeric(A['freq_class_max'],errors='coerce')
    nc=pd.to_numeric(A['nr_class'],errors='coerce')
    ce=pd.to_numeric(A['class_ent'],errors='coerce')
    # PyMFE class entropy is base-2; normalize by maximum entropy log2(K).
    entden=np.log2(nc.where(nc>1,np.nan))
    out['normalized_class_entropy']=ce/entden
    return out[FEATURE_ORDER]

def standardize_impute(X):
    X=np.asarray(X,float)
    med=np.nanmedian(X,axis=0)
    Xi=np.where(np.isnan(X),med[None,:],X)
    mu=Xi.mean(axis=0); sd=Xi.std(axis=0,ddof=1); sd=np.where(sd>1e-12,sd,1.)
    Z=(Xi-mu)/sd
    return Z,med,mu,sd

def design_fit(Y,X,M):
    D=np.column_stack([np.ones(len(M)),X,M])
    B=np.linalg.lstsq(D,Y,rcond=None)[0]
    beta=B[-1]
    return beta,D,B

def raw_beta(Y,M):
    D=np.column_stack([np.ones(len(M)),M])
    B=np.linalg.lstsq(D,Y,rcond=None)[0]
    return B[-1]

def freedman_lane_p(Y,X,M,obs,Bperm,seed):
    # Reduced model excludes membership. Permute reduced-model residual rows,
    # reconstruct under H0, then refit full model. Fixed X/M; global statistic is
    # pairwise RMS of the membership coefficient vector.
    D0=np.column_stack([np.ones(len(M)),X])
    B0=np.linalg.lstsq(D0,Y,rcond=None)[0]
    fit0=D0@B0; E=Y-fit0
    D=np.column_stack([np.ones(len(M)),X,M])
    pinv=np.linalg.pinv(D)
    w=pinv[-1]  # beta_M = w @ Y; w @ fit0 ~= 0
    rng=np.random.default_rng(seed)
    ge=0; done=0; batch=250
    n=len(M)
    while done<Bperm:
        b=min(batch,Bperm-done)
        # Uniform independent row permutations using random-key argsort.
        idx=np.argsort(rng.random((b,n)),axis=1)
        # beta shape b x algorithms
        beta=np.einsum('n,bna->ba',w,E[idx])
        beta0=beta-beta.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*beta.shape[1]/(beta.shape[1]-1))*np.mean(beta0*beta0,axis=1))
        ge += int(np.sum(vals >= obs-1e-15)); done += b
    return float((ge+1)/(Bperm+1))

def group_perm_feature_test(Z,M,Bperm,seed):
    # M=1 outside; fixed group size label randomization.
    obs=Z[M==1].mean(axis=0)-Z[M==0].mean(axis=0)
    obs_abs=np.abs(obs)
    global_obs=float(np.linalg.norm(obs))
    n=len(M); n1=int(M.sum()); total=Z.sum(axis=0)
    rng=np.random.default_rng(seed); ge=np.zeros(Z.shape[1],int); geg=0; done=0; batch=500
    while done<Bperm:
        b=min(batch,Bperm-done)
        U=rng.random((b,n)); idx=np.argpartition(U,n1-1,axis=1)[:,:n1]
        out=Z[idx].mean(axis=1); cc=(total[None,:]-Z[idx].sum(axis=1))/(n-n1)
        d=out-cc
        ge += np.sum(np.abs(d)>=obs_abs[None,:]-1e-15,axis=0)
        geg += int(np.sum(np.linalg.norm(d,axis=1)>=global_obs-1e-15)); done += b
    p=(ge+1)/(Bperm+1); q=bh(p)
    return obs,global_obs,float((geg+1)/(Bperm+1)),p,q

def crossfit(Y,X,task_ids,k=5):
    # Deterministic task-ID ordered folds; no membership is used in fitting.
    order=np.argsort(np.asarray(task_ids)); fold=np.empty(len(task_ids),int); fold[order]=np.arange(len(task_ids))%k
    pred=np.zeros_like(Y,float); base=np.zeros_like(Y,float)
    for f in range(k):
        te=fold==f; tr=~te
        mu=X[tr].mean(axis=0); sd=X[tr].std(axis=0,ddof=1); sd=np.where(sd>1e-12,sd,1.)
        Xtr=(X[tr]-mu)/sd; Xte=(X[te]-mu)/sd
        Dtr=np.column_stack([np.ones(tr.sum()),Xtr]); Dte=np.column_stack([np.ones(te.sum()),Xte])
        B=np.linalg.lstsq(Dtr,Y[tr],rcond=None)[0]
        pred[te]=Dte@B
        base[te]=Y[tr].mean(axis=0)
    resid=Y-pred
    resid=resid-resid.mean(axis=1,keepdims=True)
    sse=float(np.sum((Y-pred)**2)); sse0=float(np.sum((Y-base)**2))
    r2=float(1-sse/sse0) if sse0>0 else float('nan')
    return resid,r2,fold

def bootstrap_adjusted(Y,X,M,Bboot,seed):
    rng=np.random.default_rng(seed); a=np.flatnonzero(M==0); b=np.flatnonzero(M==1)
    vals=[]
    for _ in range(Bboot):
        ix=np.concatenate([rng.choice(a,len(a),replace=True),rng.choice(b,len(b),replace=True)])
        y=Y[ix]; x=X[ix]; m=M[ix]
        br=raw_beta(y,m); ba=design_fit(y,x,m)[0]
        tr=pair_rms(br); ta=pair_rms(ba)
        att=1-ta/tr if tr>1e-15 else np.nan
        vals.append((tr,ta,att))
    V=np.asarray(vals,float)
    return {
      'raw_T_ci95':[float(x) for x in np.nanpercentile(V[:,0],[2.5,97.5])],
      'adjusted_T_ci95':[float(x) for x in np.nanpercentile(V[:,1],[2.5,97.5])],
      'attenuation_ci95':[float(x) for x in np.nanpercentile(V[:,2],[2.5,97.5])],
    }

def subset_analysis(Y,X,Z,M,task_ids,algs,keep,Bperm,Bboot,seed):
    ix=[algs.index(a) for a in keep]
    W=Y[:,ix]
    R=W-W.mean(axis=1,keepdims=True)
    br=raw_beta(R,M); ba=design_fit(R,Z,M)[0]
    tr=pair_rms(br); ta=pair_rms(ba); att=float(1-ta/tr) if tr>1e-15 else np.nan
    pcond=freedman_lane_p(R,Z,M,ta,Bperm,seed)
    cfres,cf_r2,fold=crossfit(R,X,task_ids,k=5)
    bcf=raw_beta(cfres,M); tcf=pair_rms(bcf); attcf=float(1-tcf/tr) if tr>1e-15 else np.nan
    boot=bootstrap_adjusted(R,Z,M,Bboot,seed+10000)
    pairs=[]
    for i in range(len(keep)):
        for j in range(i+1,len(keep)):
            pairs.append((abs(ba[i]-ba[j]),keep[i],keep[j],float(ba[i]-ba[j])))
    pairs=sorted(pairs,reverse=True)[:10]
    return {
      'n_algorithms':len(keep),'algorithms':keep,
      'raw_membership_pair_rms':tr,'adjusted_membership_pair_rms':ta,
      'linear_adjustment_attenuation':att,
      'conditional_freedman_lane_p':pcond,'conditional_permutations':Bperm,
      'crossfit_covariate_profile_R2':cf_r2,'crossfit_residual_pair_rms':tcf,
      'crossfit_attenuation':attcf,'bootstrap':boot,
      'raw_membership_coefficient':{a:float(v) for a,v in zip(keep,br)},
      'adjusted_membership_coefficient':{a:float(v) for a,v in zip(keep,ba)},
      'top_adjusted_pairwise_shifts':[{'abs_shift':x,'algorithm_a':a,'algorithm_b':b,'signed_shift_a_minus_b':v} for x,a,b,v in pairs],
      'cv_fold_counts':pd.Series(fold).value_counts().sort_index().astype(int).to_dict(),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--clean',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--out',required=True)
    ap.add_argument('--perm',type=int,default=50000); ap.add_argument('--boot',type=int,default=3000)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    clean=pd.read_csv(a.clean)
    clean['task_id']=clean.dataset_name.map(parse_clean_task).astype(int)
    assert not clean.duplicated(['task_id','alg_name']).any()
    P=clean.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index()
    assert P.notna().all().all()
    algs=list(P.columns)

    usecols=['dataset_name']+list(RAW_FEATURES.values())
    meta=pd.read_csv(a.meta,usecols=usecols,low_memory=False)
    parsed=meta.dataset_name.map(parse_meta_name)
    meta['task_id']=[x[0] for x in parsed]; meta['fold']=[x[1] for x in parsed]
    bad=meta.task_id.isna().sum(); meta=meta.dropna(subset=['task_id']).copy(); meta['task_id']=meta.task_id.astype(int)
    meta=meta.rename(columns={v:k for k,v in RAW_FEATURES.items()})
    fold_counts=meta.groupby('task_id').fold.nunique()
    # Fold-level MFEs are aggregated by task median; this avoids treating folds as independent tasks.
    A=meta.groupby('task_id')[list(RAW_FEATURES.keys())].median(numeric_only=True)
    F=transform_features(A)

    perf_ids=set(map(int,P.index)); meta_ids=set(map(int,A.index)); matched=sorted(perf_ids & meta_ids)
    missing=sorted(perf_ids-meta_ids)
    extra=sorted(meta_ids-perf_ids)
    if len(matched)<30: raise RuntimeError(f'insufficient exact task-ID overlap: {len(matched)}')
    Pg=P.loc[matched]
    Fg=F.loc[matched]
    M=np.asarray([int(t not in CC18) for t in matched],int) # 0 CC18, 1 outside
    Y=Pg.to_numpy(float); Xraw=Fg.to_numpy(float)
    Z,med,mu,sd=standardize_impute(Xraw)

    # Selection-composition audit on six prespecified structural features.
    dZ,globalS,pGlobal,pFeat,qFeat=group_perm_feature_test(Z,M,a.perm,260929210)
    desc=[]
    for j,name in enumerate(FEATURE_ORDER):
        x=Xraw[:,j]; ximp=np.where(np.isnan(x),med[j],x)
        desc.append({
          'feature':name,'cc18_mean':float(np.mean(ximp[M==0])),'outside_mean':float(np.mean(ximp[M==1])),
          'cc18_median':float(np.median(ximp[M==0])),'outside_median':float(np.median(ximp[M==1])),
          'standardized_mean_difference_outside_minus_cc18':float(dZ[j]),
          'permutation_p':float(pFeat[j]),'bh_q':float(qFeat[j]),
          'missing_before_imputation':int(np.isnan(x).sum())
        })
    descdf=pd.DataFrame(desc).sort_values('bh_q')
    descdf.to_csv(out/'structural_feature_balance.csv',index=False)

    subsets={
      'all18':algs,
      'minus_TabNet':[x for x in algs if x!='TabNet'],
      'minus_VIME':[x for x in algs if x!='VIME'],
      'minus_TabNet_VIME':[x for x in algs if x not in {'TabNet','VIME'}],
      'classical4':[x for x in ['CatBoost','LightGBM','RandomForest','XGBoost'] if x in algs],
    }
    results={}
    for i,(name,keep) in enumerate(subsets.items()):
        results[name]=subset_analysis(Y,Xraw,Z,M,matched,algs,keep,a.perm,a.boot,260929300+i)

    # provenance/join diagnostics
    task_rows=[]
    for t in matched:
        task_rows.append({'task_id':t,'membership':'CC18' if t in CC18 else 'outside','meta_folds':int(fold_counts.get(t,0))})
    pd.DataFrame(task_rows).to_csv(out/'exact_task_id_join.csv',index=False)
    Fg.assign(membership=['CC18' if t in CC18 else 'outside' for t in matched]).to_csv(out/'task_structural_features.csv')
    summary={
      'clean_tasks':int(len(P)),'meta_parsed_rows':int(len(meta)),'meta_unparsed_rows':int(bad),
      'meta_unique_tasks':int(len(A)),'exact_id_matched_tasks':int(len(matched)),
      'matched_cc18':int(np.sum(M==0)),'matched_outside':int(np.sum(M==1)),
      'clean_tasks_missing_meta':missing,'meta_tasks_not_in_clean':extra,
      'matched_fold_count_distribution':{str(k):int(v) for k,v in fold_counts.loc[matched].value_counts().sort_index().items()},
      'prespecified_features':FEATURE_ORDER,
      'raw_feature_columns':RAW_FEATURES,
      'feature_global_standardized_mean_shift_norm':globalS,
      'feature_global_permutation_p':pGlobal,'feature_permutations':a.perm,
      'subsets':results,
      'interpretation_guardrail':'All contrasts are descriptive/model-adjusted associations in a fixed historical task set. Adjustment/attenuation is not causal mediation and does not identify an effect of CC18 curation.'
    }
    (out/'mechanism_results.json').write_text(json.dumps(summary,indent=2)+'\n')

    # concise human-readable report
    L=['# TabZilla CC18 membership: omnibus + structural mechanism analysis','',
       '## Exact-ID join','',
       f"- Cleaned performance tasks: **{len(P)}**",
       f"- Historical metafeature tasks parsed: **{len(A)}**",
       f"- Exact OpenML task-ID overlap: **{len(matched)}** ({int(np.sum(M==0))} CC18 / {int(np.sum(M==1))} outside)",
       f"- Cleaned tasks missing historical MFEs: **{len(missing)}**: {missing if missing else 'none'}",
       f"- Meta rows that failed `__(task_id)__fold_k` parsing: **{bad}**",'',
       '## Prespecified structural composition audit','',
       f"Global standardized 6-feature mean-shift norm = **{globalS:.4f}**, fixed-count permutation p = **{pGlobal:.6g}** (B={a.perm}).",'',
       '| feature | SMD outside−CC18 | p | BH q |', '|---|---:|---:|---:|']
    for _,r in descdf.iterrows():
        L.append(f"| {r['feature']} | {r['standardized_mean_difference_outside_minus_cc18']:.3f} | {r['permutation_p']:.5g} | {r['bh_q']:.5g} |")
    L += ['', '## Membership-associated algorithm-relative profile shift','',
          'Raw T is the RMS across all algorithm-pair differences in the outside-minus-CC18 coefficient. Adjusted T is the same statistic after the six prespecified structural features enter a multivariate linear model. Conditional p uses Freedman–Lane residual permutation under the reduced structural-feature model. Cross-fit attenuation is an overfitting-resistant diagnostic.','',
          '| subset | raw T | adjusted T | attenuation | conditional p | cross-fit R² | cross-fit residual T | cross-fit attenuation |',
          '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,r in results.items():
        L.append(f"| {name} | {r['raw_membership_pair_rms']:.5f} | {r['adjusted_membership_pair_rms']:.5f} | {r['linear_adjustment_attenuation']:.1%} | {r['conditional_freedman_lane_p']:.5g} | {r['crossfit_covariate_profile_R2']:.3f} | {r['crossfit_residual_pair_rms']:.5f} | {r['crossfit_attenuation']:.1%} |")
    L += ['', '## Guardrail','',
          'These are finite historical benchmark-membership associations. Structural adjustment can show whether a small, prespecified set of observable task properties accounts for part of the profile shift, but it is not causal mediation and does not identify an effect of CC18 curation.']
    (out/'REPORT.md').write_text('\n'.join(L)+'\n')
    print((out/'REPORT.md').read_text())

if __name__=='__main__': main()
