#!/usr/bin/env python3
from pathlib import Path
import argparse,json,re
import numpy as np
import pandas as pd

def family(x):
    s=str(x)
    s=re.sub(r'_(?:r\d+|c\d+)_BAG_L1$','',s)
    s=re.sub(r'_BAG_L1$','',s)
    return s

def center(A): return A-A.mean(axis=1,keepdims=True)

def funs(Y):
    lo=Y.min(1,keepdims=True); hi=Y.max(1,keepdims=True); den=hi-lo
    N=np.divide(Y-lo,den,out=np.full_like(Y,.5),where=den>0)
    R=pd.DataFrame(Y).rank(axis=1,method='average',ascending=True).to_numpy(float)
    return {'raw':Y,'normalized':N,'rank':R}

def obs(C,m):
    n1=m.sum(); n0=len(m)-n1
    v=np.where(m,-1/n1,1/n0); d=v@C
    return float(d@d)

def perm(C,m,B,seed):
    o=obs(C,m); rng=np.random.default_rng(seed); n=len(m); n1=int(m.sum())
    K=C@C.T; ge=0
    for s in range(0,B,1000):
        b=min(1000,B-s); keys=rng.random((b,n),dtype=np.float32)
        ix=np.argpartition(keys,n1-1,axis=1)[:,:n1]
        M=np.zeros((b,n),dtype=float); M[np.arange(b)[:,None],ix]=1
        V=1/(n-n1)-M*(1/n1+1/(n-n1))
        z=np.einsum('bi,bi->b',V@K,V,optimize=True)
        ge+=int(np.sum(z>=o-1e-15))
    return {'stat_l2':o,'p_l2':(ge+1)/(B+1),'permutations':B}

def build(df,tids,frameworks):
    z=df[df.tid.isin(tids)&df.framework.isin(frameworks)].copy()
    z['family']=z.framework.map(family)
    g=z.groupby(['tid','framework']).agg(err=('metric_error','mean'),folds=('fold','nunique')).reset_index()
    if not (g.folds==3).all(): raise RuntimeError('non-3fold cell')
    g['family']=g.framework.map(family); g['score']=-g.err
    fam=g.groupby(['tid','family']).score.mean().reset_index()
    P=fam.pivot(index='tid',columns='family',values='score').reindex(index=tids)
    if P.isna().any().any(): raise RuntimeError('family matrix incomplete')
    counts=(g.groupby('family').framework.nunique().sort_values(ascending=False).rename('n_configs').reset_index())
    return P.to_numpy(float),list(P.columns),counts

def frame(tf,tids):
    f=tf.set_index('tid').loc[tids].reset_index(); f['in_cc18']=f.in_cc18.astype(bool); return f

def run(label,Y,F,common,B,seed,include_raw):
    out={}
    for j,(name,A) in enumerate(funs(Y).items()):
        if name=='raw' and not include_raw: continue
        C=center(A); m=F.in_cc18.to_numpy(bool)
        out[name]={'unadjusted':perm(C,m,B,seed+j*10),'common_support':perm(C[common],m[common],B,seed+j*10+1)}
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--parquet',required=True); ap.add_argument('--freeze',required=True); ap.add_argument('--task-frame',required=True); ap.add_argument('--out',required=True); ap.add_argument('--perm',type=int,default=20000)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fr=json.load(open(a.freeze)); tf=pd.read_csv(a.task_frame)
    df=pd.read_parquet(a.parquet,columns=['tid','fold','framework','metric_error'])
    rt=[int(x) for x in fr['raw_task_ids']]; rf=list(fr['raw_frameworks']); ct=[int(x) for x in fr['classification_task_ids']]; cf=list(fr['classification_frameworks'])
    Yr,rfn,rc=build(df,rt,rf); Yc,cfn,cc=build(df,ct,cf); Fr=frame(tf,rt); Fc=frame(tf,ct)
    def common(F):
        x=F.log1p_n.to_numpy(float); m=F.in_cc18.to_numpy(bool); lo=max(x[m].min(),x[~m].min()); hi=min(x[m].max(),x[~m].max()); return (x>=lo)&(x<=hi)
    cr=common(Fr); ccx=common(Fc)
    R={'status':'post-hoc robustness, not preregistered','family_rule':'strip terminal _rN_BAG_L1, _cN_BAG_L1, or _BAG_L1',
       'raw_frame':{'tasks':len(rt),'families':len(rfn),'family_names':rfn,'cc18':int(Fr.in_cc18.sum()),'outside':int((~Fr.in_cc18).sum()),'common_tasks':int(cr.sum()),'tests':run('raw',Yr,Fr,cr,a.perm,2609308000,True)},
       'classification_frame':{'tasks':len(ct),'families':len(cfn),'family_names':cfn,'cc18':int(Fc.in_cc18.sum()),'outside':int((~Fc.in_cc18).sum()),'common_tasks':int(ccx.sum()),'tests':run('cls',Yc,Fc,ccx,a.perm,2609309000,False)}}
    rc.to_csv(out/'raw_family_config_counts.csv',index=False); cc.to_csv(out/'classification_family_config_counts.csv',index=False)
    (out/'SUMMARY.json').write_text(json.dumps(R,indent=2)+'\n')
    rows=[]
    for frame_name,key in [('matched raw','raw_frame'),('full classification','classification_frame')]:
        for fn,t in R[key]['tests'].items(): rows.append({'frame':frame_name,'functional':fn,'n_families':R[key]['families'],'p_unadjusted':t['unadjusted']['p_l2'],'p_common_support':t['common_support']['p_l2']})
    T=pd.DataFrame(rows); T.to_csv(out/'family_functional_summary.csv',index=False)
    (out/'REPORT.md').write_text('# Post-hoc family-equalized robustness\n\n**Post-hoc; not preregistered.**\n\n'+T.to_markdown(index=False)+'\n')
    print(json.dumps(R,indent=2))
if __name__=='__main__': main()
