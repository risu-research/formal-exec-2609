#!/usr/bin/env python3
from pathlib import Path
import argparse, json, math, re
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

CC18={3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,146800,146817,146819,146820,146821,146822,146824,146825,167119,167120,167121,167124,167125,167140,167141}


def center_rows(Y):
    return Y-Y.mean(axis=1,keepdims=True)


def functionals(Y):
    Y=np.asarray(Y,float)
    lo=Y.min(axis=1,keepdims=True); hi=Y.max(axis=1,keepdims=True)
    den=hi-lo
    N=np.divide(Y-lo,den,out=np.full_like(Y,0.5),where=den>0)
    R=pd.DataFrame(Y).rank(axis=1,method='average',ascending=True).to_numpy(float)
    return {'raw':Y,'normalized':N,'rank':R}


def contrast(mask):
    mask=np.asarray(mask,bool); n1=int(mask.sum()); n0=len(mask)-n1
    if n1<2 or n0<2: raise ValueError((n1,n0))
    return np.where(mask,-1.0/n1,1.0/n0) # outside - CC18


def stat_obs(C,mask):
    v=contrast(mask); d=v@C
    return float(d@d),float(np.max(np.abs(d))),d


def fixed_masks(B,n,n1,seed):
    rng=np.random.default_rng(seed)
    keys=rng.random((B,n),dtype=np.float32)
    ix=np.argpartition(keys,n1-1,axis=1)[:,:n1]
    M=np.zeros((B,n),dtype=bool)
    M[np.arange(B)[:,None],ix]=True
    return M


def masks_to_contrasts(M):
    n1=M.sum(axis=1).astype(float); n0=M.shape[1]-n1
    return np.where(M,-1.0/n1[:,None],1.0/n0[:,None]).astype(np.float32)


def l2_from_contrasts(C,V):
    K=(C@C.T).astype(np.float64)
    # batch to avoid materializing V@K for the full simulation bank.
    out=np.empty(len(V),float)
    for s in range(0,len(V),1000):
        vv=V[s:s+1000].astype(np.float64,copy=False)
        out[s:s+len(vv)]=np.einsum('bi,bi->b',vv@K,vv,optimize=True)
    return out


def max_from_contrasts(C,V):
    out=np.empty(len(V),float)
    Cf=C.astype(np.float32,copy=False)
    for s in range(0,len(V),200):
        vv=V[s:s+200]
        D=vv@Cf
        out[s:s+len(vv)]=np.max(np.abs(D),axis=1)
    return out


def perm_test(C,mask,B,seed,do_max=False):
    C=np.asarray(C,float)
    obs_l2,obs_max,d=stat_obs(C,mask)
    M=fixed_masks(B,len(mask),int(np.sum(mask)),seed)
    V=masks_to_contrasts(M)
    l2=l2_from_contrasts(C,V)
    rec={'n_tasks':int(len(mask)),'n_cc18':int(np.sum(mask)),'n_outside':int(len(mask)-np.sum(mask)),
         'n_frameworks':int(C.shape[1]),'stat_l2':obs_l2,
         'p_l2':float((np.sum(l2>=obs_l2-1e-15)+1)/(B+1)),
         'stat_max_abs':obs_max,'permutations_l2':int(B),'effect_shift':d}
    if do_max:
        mx=max_from_contrasts(C,V)
        rec['p_max_abs']=float((np.sum(mx>=obs_max-1e-15)+1)/(B+1))
        rec['permutations_max_abs']=int(B)
    return rec


def stratified_masks(mask,bins,B,seed):
    mask=np.asarray(mask,bool); bins=np.asarray(bins)
    rng=np.random.default_rng(seed); n=len(mask)
    M=np.zeros((B,n),dtype=bool)
    for lab in pd.unique(bins):
        idx=np.where(bins==lab)[0]; k=int(mask[idx].sum())
        if k==0: continue
        if k==len(idx): M[:,idx]=True; continue
        keys=rng.random((B,len(idx)),dtype=np.float32)
        take=np.argpartition(keys,k-1,axis=1)[:,:k]
        M[np.arange(B)[:,None],idx[take]]=True
    assert np.all(M.sum(axis=1)==mask.sum())
    return M


def stratified_test(C,mask,x,q,B,seed):
    # qcut depends only on the frozen structural covariate.
    bins=pd.qcut(pd.Series(x),q=q,labels=False,duplicates='drop').to_numpy()
    M=stratified_masks(mask,bins,B,seed)
    V=masks_to_contrasts(M)
    obs_l2,obs_max,d=stat_obs(C,mask)
    l2=l2_from_contrasts(C,V)
    return {'q_requested':int(q),'q_realized':int(len(np.unique(bins))),
            'n_tasks':len(mask),'n_cc18':int(mask.sum()),'n_outside':int((~mask).sum()),
            'stat_l2':obs_l2,'p_l2':float((np.sum(l2>=obs_l2-1e-15)+1)/(B+1)),
            'stat_max_abs':obs_max,'permutations':B}


def smd(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float)
    den=np.sqrt((np.var(a,ddof=1)+np.var(b,ddof=1))/2)
    return float((np.mean(a)-np.mean(b))/den) if den>0 else np.nan


def support(frame,mask):
    x=frame.log1p_n.to_numpy(float)
    a=x[mask]; b=x[~mask]
    lo=max(float(a.min()),float(b.min())); hi=min(float(a.max()),float(b.max()))
    common=(x>=lo)&(x<=hi)
    ks=ks_2samp(a,b)
    rows=[]
    for col in ['log1p_n','log1p_p','log1p_classes','numeric_fraction','symbolic_fraction']:
        z=frame[col].to_numpy(float); aa=z[mask]; bb=z[~mask]
        rows.append({'covariate':col,'mean_cc18':float(np.nanmean(aa)),'mean_outside':float(np.nanmean(bb)),
                     'smd_cc18_minus_outside':smd(aa[np.isfinite(aa)],bb[np.isfinite(bb)])})
    return {'log1p_n_smd_cc18_minus_outside':smd(a,b),'log1p_n_ks':float(ks.statistic),
            'log1p_n_ks_p':float(ks.pvalue),'common_lo':lo,'common_hi':hi,
            'common_mask':common,'n_common':int(common.sum()),
            'n_common_cc18':int((common&mask).sum()),'n_common_outside':int((common&~mask).sum()),
            'covariate_rows':rows}


def residualize(A,X):
    # X includes intercept; pinv handles collinearity deterministically.
    return A-X@(np.linalg.pinv(X)@A)


def freedman_lane(C,mask,frame,covar_cols,B,seed):
    Z=frame[covar_cols].to_numpy(float)
    # deterministic median imputation from this frozen analysis frame only.
    for j in range(Z.shape[1]):
        bad=~np.isfinite(Z[:,j]); med=np.nanmedian(Z[:,j]); Z[bad,j]=med
    mu=Z.mean(0); sd=Z.std(0,ddof=1); sd[~np.isfinite(sd)|(sd==0)]=1
    Z=(Z-mu)/sd
    X=np.c_[np.ones(len(Z)),Z]
    R=residualize(C,X)
    g=mask.astype(float)
    gr=residualize(g[:,None],X)[:,0]
    denom=float(gr@gr)
    if denom<=1e-14: raise RuntimeError('membership residual has zero variance')
    beta=(gr@R)/denom
    obs=float(beta@beta)
    K=(R@R.T)/(denom*denom)
    rng=np.random.default_rng(seed)
    vals=np.empty(B,float)
    for s in range(0,B,1000):
        m=min(1000,B-s)
        W=np.empty((m,len(gr)),float)
        for i in range(m): W[i]=gr[rng.permutation(len(gr))]
        vals[s:s+m]=np.einsum('bi,bi->b',W@K,W,optimize=True)
    return {'covariates':';'.join(covar_cols),'n_tasks':len(mask),'n_frameworks':C.shape[1],
            'stat_beta_l2':obs,'p_l2':float((np.sum(vals>=obs-1e-15)+1)/(B+1)),'permutations':B}


def build_frame(task_frame,task_ids):
    f=task_frame[task_frame.tid.isin(task_ids)].copy().set_index('tid').loc[list(task_ids)].reset_index()
    f['log1p_classes']=np.log1p(f.NumberOfClasses.astype(float))
    denom=f.NumberOfFeatures.astype(float).replace(0,np.nan)
    f['numeric_fraction']=f.NumberOfNumericFeatures.astype(float)/denom
    f['symbolic_fraction']=f.NumberOfSymbolicFeatures.astype(float)/denom
    return f


def build_score(df,task_ids,frameworks):
    sub=df[df.tid.isin(task_ids)&df.framework.isin(frameworks)].copy()
    g=sub.groupby(['tid','framework'],sort=False).agg(error=('metric_error','mean'),folds=('fold','nunique'),rows=('fold','size')).reset_index()
    bad=g[(g.folds!=3)|(g.rows!=3)]
    if len(bad): raise RuntimeError(f'non-3-fold frozen cell count: {len(bad)}')
    P=g.pivot(index='tid',columns='framework',values='error').reindex(index=list(task_ids),columns=list(frameworks))
    if P.isna().any().any():
        raise RuntimeError(f'non-finite/missing frozen metric cells: {int(P.isna().sum().sum())}')
    E=P.to_numpy(float)
    if not np.isfinite(E).all(): raise RuntimeError('non-finite metric_error values')
    return -E


def family(name):
    return re.sub(r'_(?:r\d+|c\d+).*$', '', str(name))


def analyze_one(label,Y,frame,frameworks,B,seed,do_max):
    F=functionals(Y)
    mask=frame.in_cc18.to_numpy(bool)
    supp=support(frame,mask)
    out={'label':label,'support':{k:v for k,v in supp.items() if k not in {'common_mask','covariate_rows'}},
         'support_covariates':supp['covariate_rows'],'functionals':{}}
    common=supp['common_mask']
    for j,(fname,A) in enumerate(F.items()):
        C=center_rows(A)
        u=perm_test(C,mask,B,seed+100*j,do_max=do_max)
        com=perm_test(C[common],mask[common],B,seed+100*j+1,do_max=False)
        st=[]
        for q in [3,4,5]:
            st.append(stratified_test(C[common],mask[common],frame.loc[common,'log1p_n'].to_numpy(float),q,B,seed+100*j+10+q))
        fl_size=freedman_lane(C,mask,frame,['log1p_n'],B,seed+100*j+30)
        fl_struct=freedman_lane(C,mask,frame,['log1p_n','log1p_p','log1p_classes','numeric_fraction','symbolic_fraction'],B,seed+100*j+31)
        norm_ratio=math.sqrt(com['stat_l2']/u['stat_l2']) if u['stat_l2']>0 else np.nan
        rec={'unadjusted':{k:v for k,v in u.items() if k!='effect_shift'},
             'common_support':{k:v for k,v in com.items() if k!='effect_shift'},
             'effect_norm_ratio_common_to_unadjusted':float(norm_ratio),
             'stratified_common_support':st,'freedman_lane_size':fl_size,'freedman_lane_structural':fl_struct}
        out['functionals'][fname]=rec
        eff=u['effect_shift']
        tab=pd.DataFrame({'framework':frameworks,'effect_shift_outside_minus_cc18':eff})
        tab['abs_shift']=tab.effect_shift_outside_minus_cc18.abs(); tab['family']=tab.framework.map(family)
        tab.sort_values('abs_shift',ascending=False).to_csv(OUTDIR/f'{label}_{fname}_framework_shifts.csv',index=False)
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--parquet',required=True); ap.add_argument('--freeze',required=True); ap.add_argument('--task-frame',required=True); ap.add_argument('--out',required=True); ap.add_argument('--perm',type=int,default=20000)
    a=ap.parse_args(); global OUTDIR; OUTDIR=Path(a.out); OUTDIR.mkdir(parents=True,exist_ok=True)
    freeze=json.loads(Path(a.freeze).read_text()); task_frame=pd.read_csv(a.task_frame)
    # Only now, after all freezes, read the test outcome column.
    df=pd.read_parquet(a.parquet,columns=['tid','fold','framework','metric_error'])
    raw_tasks=[int(x) for x in freeze['raw_task_ids']]; raw_fw=list(freeze['raw_frameworks'])
    cls_tasks=[int(x) for x in freeze['classification_task_ids']]; cls_fw=list(freeze['classification_frameworks'])
    raw_frame=build_frame(task_frame,raw_tasks); cls_frame=build_frame(task_frame,cls_tasks)
    Yraw=build_score(df,raw_tasks,raw_fw); Ycls=build_score(df,cls_tasks,cls_fw)

    # A: homogeneous primary raw frame. Analyze all 3 functionals on the same frozen frame (matched-frame amendment).
    raw_all=analyze_one('matched_raw_frame',Yraw,raw_frame,raw_fw,a.perm,2609301000,do_max=True)
    # B: full classification frame. Raw is not pooled across mixed metrics; retain only normalized/rank outputs.
    cls_all=analyze_one('full_classification_frame',Ycls,cls_frame,cls_fw,a.perm,2609305000,do_max=True)
    cls_all['functionals'].pop('raw',None)

    # Compact preregistered question summary.
    q1={'classification_support':raw_all['support'] if False else cls_all['support'],
        'raw_metric_support':raw_all['support']}
    def pull(obj,f):
        r=obj['functionals'][f]
        return {'unadjusted_p_l2':r['unadjusted']['p_l2'],'unadjusted_p_max_abs':r['unadjusted'].get('p_max_abs'),
                'common_support_p_l2':r['common_support']['p_l2'],
                'effect_norm_ratio_common_to_unadjusted':r['effect_norm_ratio_common_to_unadjusted'],
                'stratified_p_l2':{str(x['q_requested']):x['p_l2'] for x in r['stratified_common_support']},
                'freedman_lane_size_p_l2':r['freedman_lane_size']['p_l2'],
                'freedman_lane_structural_p_l2':r['freedman_lane_structural']['p_l2']}
    summary={
      'source_parquet_sha256':freeze['source_parquet_sha256'],
      'primary_raw_metric':freeze['primary_raw_metric'],'permutations':a.perm,
      'frames':{'raw':{'tasks':len(raw_tasks),'cc18':int(raw_frame.in_cc18.sum()),'outside':int((~raw_frame.in_cc18).sum()),'frameworks':len(raw_fw)},
                'classification':{'tasks':len(cls_tasks),'cc18':int(cls_frame.in_cc18.sum()),'outside':int((~cls_frame.in_cc18).sum()),'frameworks':len(cls_fw)}},
      'Q1_support_shift':q1,
      'Q2_raw_interaction_support_control':pull(raw_all,'raw'),
      'Q3_matched_raw_frame':{f:pull(raw_all,f) for f in ['raw','normalized','rank']},
      'Q3_full_classification':{f:pull(cls_all,f) for f in ['normalized','rank']},
      'guardrail':'Finite-frame observational benchmark-membership contrasts; no causal interpretation of CC18 membership.'
    }
    (OUTDIR/'SUMMARY.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    (OUTDIR/'DETAILS.json').write_text(json.dumps({'matched_raw_frame':raw_all,'full_classification_frame':cls_all},indent=2,allow_nan=False)+'\n')

    # Human-readable report.
    rows=[]
    for frame_name,obj,fs in [('matched raw frame',raw_all,['raw','normalized','rank']),('full classification',cls_all,['normalized','rank'])]:
        for f in fs:
            p=pull(obj,f); rows.append({'frame':frame_name,'functional':f,**p})
    T=pd.DataFrame(rows); T.to_csv(OUTDIR/'functional_summary.csv',index=False)
    md=['# Independent TabRepo replication — preregistered primary analysis','',
        f"- Frozen raw metric: **{freeze['primary_raw_metric']}**",
        f"- Raw matched frame: **{len(raw_tasks)} tasks ({int(raw_frame.in_cc18.sum())} CC18 / {int((~raw_frame.in_cc18).sum())} outside), {len(raw_fw)} frameworks**",
        f"- Full classification: **{len(cls_tasks)} tasks ({int(cls_frame.in_cc18.sum())} / {int((~cls_frame.in_cc18).sum())}), {len(cls_fw)} frameworks**",'',
        '## Q1 — Structural support','',
        f"Full classification log1p(n) SMD: **{cls_all['support']['log1p_n_smd_cc18_minus_outside']:.4f}**; KS p **{cls_all['support']['log1p_n_ks_p']:.6g}**; common support {cls_all['support']['n_common']}/{len(cls_tasks)}.",
        f"Raw-metric frame log1p(n) SMD: **{raw_all['support']['log1p_n_smd_cc18_minus_outside']:.4f}**; KS p **{raw_all['support']['log1p_n_ks_p']:.6g}**; common support {raw_all['support']['n_common']}/{len(raw_tasks)}.",'',
        '## Q2/Q3 — Interaction tests','',T.to_markdown(index=False),'',
        '## Guardrail','','These are finite-frame observational membership contrasts. CC18 membership is not randomized and the results are not causal effects of suite curation.']
    (OUTDIR/'REPORT.md').write_text('\n'.join(md)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
