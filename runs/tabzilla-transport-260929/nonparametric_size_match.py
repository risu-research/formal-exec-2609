#!/usr/bin/env python3
from pathlib import Path
import argparse, json, re
import numpy as np
import pandas as pd

CC18={3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,146800,146817,146819,146820,146821,146822,146824,146825,167119,167120,167121,167124,167125,167140,167141}

def clean_task(s):
    m=re.search(r'__(\d+)$',str(s)); return int(m.group(1)) if m else None

def meta_task(s):
    m=re.search(r'__(\d+)__fold_(\d+)$',str(s)); return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def pair_rms(d):
    d=np.asarray(d,float); A=len(d)
    if A<2:return float('nan')
    z=d-d.mean(); return float(np.sqrt((2*A/(A-1))*np.mean(z*z)))

def membership_vector(R,M):
    return R[M==1].mean(0)-R[M==0].mean(0)

def fixed_count_perm_p(R,M,obs,B,seed):
    rng=np.random.default_rng(seed); n=len(M); n1=int(M.sum()); total=R.sum(0)
    ge=0; done=0; batch=400
    while done<B:
        b=min(batch,B-done); U=rng.random((b,n)); idx=np.argpartition(U,n1-1,axis=1)[:,:n1]
        o=R[idx].mean(1); c=(total[None,:]-R[idx].sum(1))/(n-n1); d=o-c; d-=d.mean(1,keepdims=True)
        vals=np.sqrt((2*d.shape[1]/(d.shape[1]-1))*np.mean(d*d,1))
        ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return float((ge+1)/(B+1))

def optimal_match_1d(z_cc,z_out,caliper):
    # Sorted 1-D monotone optimal bipartite matching. Objective is lexicographic:
    # maximize number of matched pairs under caliper, then minimize total |z_i-z_j|.
    ic=np.argsort(z_cc); io=np.argsort(z_out); a=z_cc[ic]; b=z_out[io]
    m,n=len(a),len(b)
    # score[k] = (matches, cost); store full DP because m,n <= 58.
    nm=np.zeros((m+1,n+1),dtype=np.int16)
    cost=np.zeros((m+1,n+1),dtype=float)
    prev=np.zeros((m+1,n+1),dtype=np.int8) # 1 skip a, 2 skip b, 3 match
    def better(ma,ca,mb,cb):
        return (ma>mb) or (ma==mb and ca<cb-1e-14)
    for i in range(1,m+1): prev[i,0]=1
    for j in range(1,n+1): prev[0,j]=2
    for i in range(1,m+1):
        for j in range(1,n+1):
            bm=nm[i-1,j]; bc=cost[i-1,j]; bp=1
            m2=nm[i,j-1]; c2=cost[i,j-1]
            if better(m2,c2,bm,bc): bm,bc,bp=m2,c2,2
            gap=abs(a[i-1]-b[j-1])
            if gap<=caliper+1e-15:
                m3=nm[i-1,j-1]+1; c3=cost[i-1,j-1]+gap
                if better(m3,c3,bm,bc): bm,bc,bp=m3,c3,3
            nm[i,j]=bm; cost[i,j]=bc; prev[i,j]=bp
    pairs=[]; i=m; j=n
    while i>0 or j>0:
        p=prev[i,j]
        if p==3:
            pairs.append((int(ic[i-1]),int(io[j-1]))); i-=1; j-=1
        elif p==1: i-=1
        elif p==2: j-=1
        else: break
    pairs.reverse(); return pairs,float(cost[m,n])

def signflip_stats(D,B,boot,seed):
    n,A=D.shape; d=D.mean(0); obs=pair_rms(d)
    rng=np.random.default_rng(seed); ge=0; done=0; batch=500
    while done<B:
        b=min(batch,B-done); s=rng.choice(np.array([-1.,1.]),size=(b,n)); v=(s@D)/n; v-=v.mean(1,keepdims=True)
        vals=np.sqrt((2*A/(A-1))*np.mean(v*v,1)); ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    p=float((ge+1)/(B+1))
    vals=[]; done=0; batch=250
    while done<boot:
        b=min(batch,boot-done); idx=rng.integers(0,n,size=(b,n)); db=D[idx].mean(1); db-=db.mean(1,keepdims=True)
        vals.extend(np.sqrt((2*A/(A-1))*np.mean(db*db,1)).tolist()); done+=b
    ci=[float(x) for x in np.percentile(np.asarray(vals),[2.5,50,97.5])]
    return obs,p,ci,d

def stratified_perm(R,M,strata,B,seed):
    groups=[np.flatnonzero(strata==g) for g in sorted(np.unique(strata))]; ks=[int(M[ix].sum()) for ix in groups]
    n1=int(M.sum()); n0=len(M)-n1; obs=pair_rms(membership_vector(R,M)); rng=np.random.default_rng(seed)
    ge=0; done=0; batch=300
    while done<B:
        b=min(batch,B-done); Mp=np.zeros((b,len(M)),dtype=np.int8)
        for ix,k in zip(groups,ks):
            if k==0: continue
            if k==len(ix): Mp[:,ix]=1; continue
            U=rng.random((b,len(ix))); sel=np.argpartition(U,k-1,axis=1)[:,:k]; rows=np.arange(b)[:,None]; Mp[rows,ix[sel]]=1
        o=(Mp@R)/n1; c=((1-Mp)@R)/n0; d=o-c; d-=d.mean(1,keepdims=True)
        vals=np.sqrt((2*d.shape[1]/(d.shape[1]-1))*np.mean(d*d,1)); ge+=int(np.sum(vals>=obs-1e-15)); done+=b
    return {'T':obs,'p':float((ge+1)/(B+1)),'mixed_strata':int(sum(0<k<len(ix) for ix,k in zip(groups,ks))),
            'n_strata':int(len(groups)),'composition':[{'n':int(len(ix)),'cc18':int(len(ix)-k),'outside':int(k)} for ix,k in zip(groups,ks)]}

def contiguous_blocks(z,block_size):
    order=np.argsort(z); s=np.empty(len(z),int)
    for k,start in enumerate(range(0,len(z),block_size)): s[order[start:start+block_size]]=k
    return s

def one_scheme(R,z,M,task_ids,raw_vec,caliper,B,boot,seed):
    cc=np.flatnonzero(M==0); out=np.flatnonzero(M==1)
    pc=optimal_match_1d(z[cc],z[out],caliper)
    if len(pc)<2:return {'pairs':len(pc),'caliper_sd':None if np.isinf(caliper) else float(caliper)}
    ci=np.array([cc[i] for i,j in pc],int); oi=np.array([out[j] for i,j in pc],int)
    D=R[oi]-R[ci]; T,p,bootci,d=signflip_stats(D,B,boot,seed)
    gaps=np.abs(z[oi]-z[ci]); smd=float(z[oi].mean()-z[ci].mean())
    denom=np.linalg.norm(d)*np.linalg.norm(raw_vec); cosine=float(np.dot(d,raw_vec)/denom) if denom>1e-15 else np.nan
    return {'pairs':int(len(pc)),'caliper_sd':None if np.isinf(caliper) else float(caliper),
            'matched_size_smd_outside_minus_cc18':smd,'mean_abs_pair_gap_sd':float(gaps.mean()),'median_abs_pair_gap_sd':float(np.median(gaps)),'max_abs_pair_gap_sd':float(gaps.max()),
            'matched_T':T,'paired_signflip_p':p,'paired_bootstrap_T_q025_q50_q975':bootci,'matched_vs_raw_vector_cosine':cosine,
            'cc18_task_ids':[int(task_ids[i]) for i in ci],'outside_task_ids':[int(task_ids[i]) for i in oi]}

def subset_results(Rfull,algs,keep,z,M,ids,calipers,B,boot,seed):
    ix=[algs.index(a) for a in keep]; R=Rfull[:,ix].copy(); R-=R.mean(1,keepdims=True)
    raw=membership_vector(R,M); rawT=pair_rms(raw); rawp=fixed_count_perm_p(R,M,rawT,B,seed)
    schemes={}
    for j,c in enumerate(calipers): schemes['inf' if np.isinf(c) else f'{c:.2f}']=one_scheme(R,z,M,ids,raw,c,B,boot,seed+100+j)
    return {'algorithms':keep,'raw_common_support_T':rawT,'raw_common_support_perm_p':rawp,'matching':schemes}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--clean',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--out',required=True); ap.add_argument('--perm',type=int,default=50000); ap.add_argument('--boot',type=int,default=5000)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    c=pd.read_csv(a.clean); c['task_id']=c.dataset_name.map(clean_task).astype(int); P=c.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index(); assert P.shape==(104,18) and P.notna().all().all()
    m=pd.read_csv(a.meta,usecols=['dataset_name','f__pymfe.general.nr_inst']); pr=m.dataset_name.map(meta_task); m['task_id']=[x[0] for x in pr]; m['fold']=[x[1] for x in pr]; assert m.task_id.notna().all(); m.task_id=m.task_id.astype(int)
    S=m.groupby('task_id')['f__pymfe.general.nr_inst'].median(); ids=np.array(sorted(set(P.index)&set(S.index)),int); assert len(ids)==104
    P=P.loc[ids]; n=S.loc[ids].to_numpy(float); logn=np.log1p(n); z=(logn-logn.mean())/logn.std(ddof=1); M=np.array([int(t not in CC18) for t in ids],int)
    Y=P.to_numpy(float); algs=list(P.columns); Rfull=Y-Y.mean(1,keepdims=True)
    # Strict empirical common support on pooled-standardized log size.
    lo=max(float(z[M==0].min()),float(z[M==1].min())); hi=min(float(z[M==0].max()),float(z[M==1].max())); cs=(z>=lo)&(z<=hi)
    idc=ids[cs]; zc=z[cs]; Mc=M[cs]; Rc=Rfull[cs]
    subsets={'all18':algs,'minus_TabNet':[x for x in algs if x!='TabNet'],'minus_VIME':[x for x in algs if x!='VIME'],'minus_TabNet_VIME':[x for x in algs if x not in {'TabNet','VIME'}],'classical4':[x for x in ['CatBoost','LightGBM','RandomForest','XGBoost'] if x in algs]}
    calipers=[0.05,0.10,0.20,0.30,0.50,float('inf')]
    results={k:subset_results(Rc,algs,v,zc,Mc,idc,calipers,a.perm,a.boot,2609301000+i*100) for i,(k,v) in enumerate(subsets.items())}
    # Stratified randomization-style checks for all18 within common support.
    R=Rc.copy(); R-=R.mean(1,keepdims=True); strata={}
    for q in [4,5,6,8,10]:
        bins=pd.qcut(pd.Series(zc),q=q,labels=False,duplicates='drop').to_numpy(int)
        strata[f'quantile_q{q}']=stratified_perm(R,Mc,bins,a.perm,2609302000+q)
    for bs in [4,6,8,10,12]:
        bins=contiguous_blocks(zc,bs)
        strata[f'contiguous_block_{bs}']=stratified_perm(R,Mc,bins,a.perm,2609303000+bs)
    raw_all=membership_vector(R,Mc); rawT=pair_rms(raw_all)
    # Balance table across matching schemes, all18 matching is outcome-independent and common across subsets.
    balance=[]
    for key,r in results['all18']['matching'].items():
        if r.get('pairs',0)>=2:
            balance.append({'scheme':key,'pairs':r['pairs'],'size_smd':r['matched_size_smd_outside_minus_cc18'],'mean_abs_gap_sd':r['mean_abs_pair_gap_sd'],'max_abs_gap_sd':r['max_abs_pair_gap_sd']})
    pd.DataFrame(balance).to_csv(out/'matching_balance.csv',index=False)
    pd.DataFrame({'task_id':idc,'membership':['CC18' if x==0 else 'outside' for x in Mc],'historical_fold_median_nr_inst':n[cs],'log_nr_inst':logn[cs],'z_log_nr_inst':zc}).to_csv(out/'common_support_tasks.csv',index=False)
    summary={'design':'Outcome-blind 1-D size matching on strict empirical common support; all calipers prespecified and reported.',
             'full_n':104,'common_support_interval_z':[lo,hi],'common_support_n':int(cs.sum()),'common_support_cc18':int(np.sum(Mc==0)),'common_support_outside':int(np.sum(Mc==1)),
             'caliper_grid_sd':[0.05,0.10,0.20,0.30,0.50,'inf'],'permutations':a.perm,'bootstrap_pairs':a.boot,'subsets':results,'all18_size_stratified':strata,
             'guardrail':'Matched and stratified tests are observational sensitivity analyses. They condition more tightly on observed task size but do not turn CC18 membership into a randomized treatment or establish a causal curation effect.'}
    (out/'nonparametric_size_match_results.json').write_text(json.dumps(summary,indent=2)+'\n')
    L=['# TabZilla nonparametric size-matched / common-support sensitivity','',
       f"Strict empirical common support: **{int(cs.sum())}** tasks (**{int(np.sum(Mc==0))} CC18 / {int(np.sum(Mc==1))} outside**), z(log-size) in [{lo:.3f}, {hi:.3f}].",'',
       'Matching is 1:1 without replacement, uses **size only**, maximizes matched pairs under each caliper, then minimizes total absolute size distance. No performance outcome enters matching.','',
       '## All-18 matching sensitivity','',
       '| caliper (SD) | pairs | matched size SMD | mean |gap| SD | T | sign-flip p | bootstrap T 95% | cosine vs raw |',
       '|---|---:|---:|---:|---:|---:|---:|---:|']
    for k,r in results['all18']['matching'].items():
        if r.get('pairs',0)<2: L.append(f"| {k} | {r.get('pairs',0)} | — | — | — | — | — | — |")
        else:
            ci=r['paired_bootstrap_T_q025_q50_q975']; L.append(f"| {k} | {r['pairs']} | {r['matched_size_smd_outside_minus_cc18']:.3f} | {r['mean_abs_pair_gap_sd']:.3f} | {r['matched_T']:.5f} | {r['paired_signflip_p']:.5g} | [{ci[0]:.5f}, {ci[2]:.5f}] | {r['matched_vs_raw_vector_cosine']:.3f} |")
    L += ['', '## Subset robustness at caliper 0.20 SD','', '| subset | common-support raw T | raw perm p | pairs | matched T | sign-flip p | size SMD |', '|---|---:|---:|---:|---:|---:|---:|']
    for k,r in results.items():
        x=r['matching']['0.20']; L.append(f"| {k} | {r['raw_common_support_T']:.5f} | {r['raw_common_support_perm_p']:.5g} | {x.get('pairs',0)} | {x.get('matched_T',np.nan):.5f} | {x.get('paired_signflip_p',np.nan):.5g} | {x.get('matched_size_smd_outside_minus_cc18',np.nan):.3f} |")
    L += ['', '## Size-stratified all-18 permutation sensitivity','']
    for k,r in strata.items(): L.append(f"- **{k}**: T={r['T']:.5f}, p={r['p']:.6g}, mixed strata={r['mixed_strata']}/{r['n_strata']}")
    L += ['', '## Guardrail','', summary['guardrail']]
    (out/'REPORT.md').write_text('\n'.join(L)+'\n'); print((out/'REPORT.md').read_text())
if __name__=='__main__': main()
