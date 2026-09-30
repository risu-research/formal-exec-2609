#!/usr/bin/env python3
from pathlib import Path
import argparse, json, re
import numpy as np
import pandas as pd

CC18={3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,146800,146817,146819,146820,146821,146822,146824,146825,167119,167120,167121,167124,167125,167140,167141}

def task_clean(s):
    m=re.search(r'__(\d+)$',str(s)); return int(m.group(1)) if m else None

def task_meta(s):
    m=re.search(r'__(\d+)__fold_(\d+)$',str(s)); return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def pair_rms(d):
    d=np.asarray(d,float); z=d-d.mean(); A=len(d)
    return float(np.sqrt((2*A/(A-1))*np.mean(z*z))) if A>1 else np.nan

def raw_beta(R,M):
    D=np.column_stack([np.ones(len(M)),M]); return np.linalg.lstsq(D,R,rcond=None)[0][-1]

def adjusted_beta(R,X,M):
    D=np.column_stack([np.ones(len(M)),X,M]); return np.linalg.lstsq(D,R,rcond=None)[0][-1]

def freedman_lane(R,X,M,obs,B,seed):
    X=np.asarray(X,float); X=X[:,None] if X.ndim==1 else X
    D0=np.column_stack([np.ones(len(M)),X]); b0=np.linalg.lstsq(D0,R,rcond=None)[0]; E=R-D0@b0
    D=np.column_stack([np.ones(len(M)),X,M]); w=np.linalg.pinv(D)[-1]
    rng=np.random.default_rng(seed); ge=0; done=0; n=len(M); batch=250
    while done<B:
        b=min(batch,B-done); idx=np.argsort(rng.random((b,n)),axis=1)
        beta=np.einsum('n,bna->ba',w,E[idx]); beta-=beta.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*beta.shape[1]/(beta.shape[1]-1))*np.mean(beta*beta,axis=1))
        ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return float((ge+1)/(B+1))

def slope_perm(R,z,obs,B,seed):
    z=np.asarray(z,float); den=float(np.sum(z*z)); rng=np.random.default_rng(seed); ge=0; done=0; n=len(z); batch=500
    while done<B:
        b=min(batch,B-done); idx=np.argsort(rng.random((b,n)),axis=1); zp=z[idx]
        beta=np.einsum('bn,na->ba',zp,R)/den; beta-=beta.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*beta.shape[1]/(beta.shape[1]-1))*np.mean(beta*beta,axis=1))
        ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return float((ge+1)/(B+1))

def label_perm_T(R,M,B,seed):
    obs=pair_rms(raw_beta(R,M)); n=len(M); n1=int(M.sum()); total=R.sum(axis=0)
    rng=np.random.default_rng(seed); ge=0; done=0; batch=500
    while done<B:
        b=min(batch,B-done); U=rng.random((b,n)); idx=np.argpartition(U,n1-1,axis=1)[:,:n1]
        o=R[idx].mean(axis=1); c=(total[None,:]-R[idx].sum(axis=1))/(n-n1); d=o-c; d-=d.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*d.shape[1]/(d.shape[1]-1))*np.mean(d*d,axis=1)); ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return obs,float((ge+1)/(B+1))

def stratified_perm(R,M,bins,B,seed):
    obs=pair_rms(raw_beta(R,M)); n=len(M); n1=int(M.sum()); rng=np.random.default_rng(seed); ge=0; done=0; batch=250
    groups=[np.flatnonzero(bins==g) for g in sorted(np.unique(bins))]
    ks=[int(M[ix].sum()) for ix in groups]
    mixed=sum(0<k<len(ix) for ix,k in zip(groups,ks))
    while done<B:
        b=min(batch,B-done); Mp=np.zeros((b,n),dtype=np.int8)
        for ix,k in zip(groups,ks):
            if k==0: continue
            if k==len(ix): Mp[:,ix]=1; continue
            U=rng.random((b,len(ix))); sel=np.argpartition(U,k-1,axis=1)[:,:k]
            rows=np.arange(b)[:,None]; Mp[rows,ix[sel]]=1
        outs=(Mp@R)/n1; ccs=((1-Mp)@R)/(n-n1); d=outs-ccs; d-=d.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*d.shape[1]/(d.shape[1]-1))*np.mean(d*d,axis=1)); ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return {'q_bins':int(len(groups)),'mixed_bins':int(mixed),'observed_T':obs,'p':float((ge+1)/(B+1)),'B':B,
            'bin_counts':[{'bin':int(g),'n':int(len(ix)),'cc18':int(len(ix)-k),'outside':int(k)} for g,ix,k in zip(sorted(np.unique(bins)),groups,ks)]}

def subset(Rfull,algs,keep,z,M,B,seed):
    ix=[algs.index(x) for x in keep]; R=Rfull[:,ix]; R-=R.mean(axis=1,keepdims=True)
    br=raw_beta(R,M); tr=pair_rms(br)
    # Linear size bridge.
    bs=(z@R)/float(z@z); ts=pair_rms(bs); ps=slope_perm(R,z,ts,B,seed)
    dz=float(z[M==1].mean()-z[M==0].mean()); pred=bs*dz; tpred=pair_rms(pred)
    denom=np.linalg.norm(br)*np.linalg.norm(pred); cosine=float(np.dot(br,pred)/denom) if denom>1e-15 else np.nan
    projection=float(np.dot(br,pred)/np.dot(br,br)) if np.dot(br,br)>1e-15 else np.nan
    ba=adjusted_beta(R,z,M); ta=pair_rms(ba); pcond=freedman_lane(R,z,M,ta,B,seed+1000)
    # Quadratic size sensitivity; orthogonalization is unnecessary for fitted values, but center/scale z^2 for stability.
    q=z*z; q=(q-q.mean())/(q.std(ddof=1) if q.std(ddof=1)>1e-12 else 1.)
    X2=np.column_stack([z,q]); ba2=adjusted_beta(R,X2,M); ta2=pair_rms(ba2); pcond2=freedman_lane(R,X2,M,ta2,B,seed+2000)
    return {'raw_T':tr,'size_slope_T_per_1sd':ts,'size_slope_permutation_p':ps,'outside_minus_cc18_size_sd':dz,
            'size_predicted_membership_T':tpred,'predicted_to_raw_T_ratio':float(tpred/tr) if tr>0 else np.nan,
            'predicted_raw_vector_cosine':cosine,'predicted_projection_fraction_of_raw_vector':projection,
            'size_linear_adjusted_membership_T':ta,'size_linear_attenuation':float(1-ta/tr),'size_linear_conditional_p':pcond,
            'size_quadratic_adjusted_membership_T':ta2,'size_quadratic_attenuation':float(1-ta2/tr),'size_quadratic_conditional_p':pcond2,
            'size_slope_by_algorithm':{a:float(v) for a,v in zip(keep,bs)},
            'raw_membership_by_algorithm':{a:float(v) for a,v in zip(keep,br)},
            'size_predicted_membership_by_algorithm':{a:float(v) for a,v in zip(keep,pred)}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--clean',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--out',required=True); ap.add_argument('--perm',type=int,default=50000)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    c=pd.read_csv(a.clean); c['task_id']=c.dataset_name.map(task_clean).astype(int); P=c.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index(); assert P.notna().all().all()
    m=pd.read_csv(a.meta,usecols=['dataset_name','f__pymfe.general.nr_inst']); pr=m.dataset_name.map(task_meta); m['task_id']=[x[0] for x in pr]; m['fold']=[x[1] for x in pr]; assert m.task_id.notna().all(); m.task_id=m.task_id.astype(int)
    S=m.groupby('task_id')['f__pymfe.general.nr_inst'].median(); ids=sorted(set(P.index)&set(S.index)); assert len(ids)==len(P)==104
    P=P.loc[ids]; n=S.loc[ids].to_numpy(float); logn=np.log1p(n); z=(logn-logn.mean())/logn.std(ddof=1); M=np.array([int(t not in CC18) for t in ids],int); algs=list(P.columns); Rfull=P.to_numpy(dtype=float,copy=True); Rfull-=Rfull.mean(axis=1,keepdims=True)
    subsets={'all18':algs,'minus_TabNet':[x for x in algs if x!='TabNet'],'minus_VIME':[x for x in algs if x!='VIME'],'minus_TabNet_VIME':[x for x in algs if x not in {'TabNet','VIME'}],'classical4':[x for x in ['CatBoost','LightGBM','RandomForest','XGBoost'] if x in algs]}
    res={k:subset(Rfull.copy(),algs,v,z,M,a.perm,260929500+i*10) for i,(k,v) in enumerate(subsets.items())}
    # Coarsened size-conditional permutation for all18 (and two key sensitivities).
    strata={}
    for q in [4,5,6]:
        bins=pd.qcut(pd.Series(logn),q=q,labels=False,duplicates='drop').to_numpy(int)
        strata[f'q{q}']=stratified_perm(Rfull.copy(),M,bins,a.perm,260929700+q)
    # common support diagnostic
    lo=max(z[M==0].min(),z[M==1].min()); hi=min(z[M==0].max(),z[M==1].max()); keep=(z>=lo)&(z<=hi)
    tcs,pcs=label_perm_T(Rfull[keep].copy(),M[keep],a.perm,260929800)
    common={'z_interval':[float(lo),float(hi)],'n':int(keep.sum()),'cc18':int(np.sum(M[keep]==0)),'outside':int(np.sum(M[keep]==1)),'raw_T':tcs,'unstratified_permutation_p':pcs}
    # task size summary on historical fold-level MFE scale
    size_summary={'cc18_n_median':float(np.median(n[M==0])),'outside_n_median':float(np.median(n[M==1])),'cc18_n_mean':float(np.mean(n[M==0])),'outside_n_mean':float(np.mean(n[M==1])),
                  'log_size_smd_outside_minus_cc18':float(z[M==1].mean()-z[M==0].mean()),'common_support':common}
    data={'n_tasks':104,'cc18':int(np.sum(M==0)),'outside':int(np.sum(M==1)),'size_summary':size_summary,'subsets':res,'size_stratified_permutation_all18':strata,
          'guardrail':'Size is an observed historical task property. These bridge, adjustment, and stratified-permutation analyses support descriptive mechanism plausibility, not causal identification of benchmark curation effects.'}
    (out/'size_bridge_results.json').write_text(json.dumps(data,indent=2)+'\n')
    pd.DataFrame({'task_id':ids,'membership':['CC18' if x==0 else 'outside' for x in M],'historical_fold_median_nr_inst':n,'log_nr_inst':logn,'z_log_nr_inst':z}).to_csv(out/'task_size.csv',index=False)
    L=['# TabZilla task-size bridge analysis','',f"Exact matched tasks: **104** (46 CC18 / 58 outside).",
       f"Historical fold-median task size: CC18 median **{size_summary['cc18_n_median']:.0f}**, outside median **{size_summary['outside_n_median']:.0f}**; log-size SMD outside−CC18 = **{size_summary['log_size_smd_outside_minus_cc18']:.3f}**.",'',
       '## Size-to-performance bridge','',
       '| subset | raw membership T | size slope T / SD | slope p | predicted membership T from size | vector cosine | size-linear adjusted T | conditional p | size-quadratic adjusted T | conditional p |',
       '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k,r in res.items():
        L.append(f"| {k} | {r['raw_T']:.5f} | {r['size_slope_T_per_1sd']:.5f} | {r['size_slope_permutation_p']:.5g} | {r['size_predicted_membership_T']:.5f} | {r['predicted_raw_vector_cosine']:.3f} | {r['size_linear_adjusted_membership_T']:.5f} | {r['size_linear_conditional_p']:.5g} | {r['size_quadratic_adjusted_membership_T']:.5f} | {r['size_quadratic_conditional_p']:.5g} |")
    L+=['','## Size-stratified membership permutation — all18','']
    for k,r in strata.items(): L.append(f"- **{k}**: p={r['p']:.6g}, mixed bins={r['mixed_bins']}/{r['q_bins']}; bin composition={r['bin_counts']}")
    L+=['','## Common size support','',f"Restricting to common observed log-size support leaves **{common['n']}** tasks ({common['cc18']} CC18 / {common['outside']} outside): T={common['raw_T']:.5f}, ordinary fixed-count membership permutation p={common['unstratified_permutation_p']:.6g}.",'',
        '## Guardrail','','This establishes a descriptive bridge only: CC18 membership is associated with task size, task size can be associated with algorithm-relative performance, and conditioning on size can attenuate the membership profile shift. It does not identify a causal effect of CC18 curation.']
    (out/'REPORT.md').write_text('\n'.join(L)+'\n'); print((out/'REPORT.md').read_text())
if __name__=='__main__': main()
