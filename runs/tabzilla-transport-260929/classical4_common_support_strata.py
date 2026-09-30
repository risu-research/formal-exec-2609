#!/usr/bin/env python3
from pathlib import Path
import argparse,json,re
import numpy as np
import pandas as pd
CC18={3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,146800,146817,146819,146820,146821,146822,146824,146825,167119,167120,167121,167124,167125,167140,167141}
def ct(s):
 m=re.search(r'__(\d+)$',str(s)); return int(m.group(1))
def mt(s):
 m=re.search(r'__(\d+)__fold_(\d+)$',str(s)); return int(m.group(1))
def T(R,M):
 d=R[M==1].mean(0)-R[M==0].mean(0); d-=d.mean(); A=len(d); return float(np.sqrt((2*A/(A-1))*np.mean(d*d)))
def strata_p(R,M,S,B,seed):
 obs=T(R,M); groups=[np.flatnonzero(S==x) for x in np.unique(S)]; ks=[int(M[g].sum()) for g in groups]
 n1=int(M.sum()); n0=len(M)-n1; rng=np.random.default_rng(seed); ge=0; done=0
 while done<B:
  b=min(300,B-done); G=np.zeros((b,len(M)),dtype=np.int8)
  for ix,k in zip(groups,ks):
   if k==0: continue
   if k==len(ix): G[:,ix]=1; continue
   u=rng.random((b,len(ix))); sel=np.argpartition(u,k-1,axis=1)[:,:k]; G[np.arange(b)[:,None],ix[sel]]=1
  a=(G@R)/n1; c=((1-G)@R)/n0; d=a-c; d-=d.mean(1,keepdims=True); A=d.shape[1]
  vals=np.sqrt((2*A/(A-1))*np.mean(d*d,1)); ge+=int(np.sum(vals>=obs-1e-15)); done+=b
 return {'T':obs,'p':float((ge+1)/(B+1)),'mixed':int(sum(0<k<len(ix) for ix,k in zip(groups,ks))),'strata':len(groups)}
def blocks(z,k):
 o=np.argsort(z); s=np.empty(len(z),int)
 for j,i in enumerate(range(0,len(z),k)): s[o[i:i+k]]=j
 return s
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--clean',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--out',required=True); ap.add_argument('--perm',type=int,default=50000); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 c=pd.read_csv(a.clean); c['task_id']=c.dataset_name.map(ct); P=c.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index()
 m=pd.read_csv(a.meta,usecols=['dataset_name','f__pymfe.general.nr_inst']); m['task_id']=m.dataset_name.map(mt); S=m.groupby('task_id')['f__pymfe.general.nr_inst'].median(); ids=np.array(sorted(set(P.index)&set(S.index)),int); P=P.loc[ids]
 n=S.loc[ids].to_numpy(float); z=(np.log1p(n)-np.log1p(n).mean())/np.log1p(n).std(ddof=1); M=np.array([int(i not in CC18) for i in ids]); lo=max(z[M==0].min(),z[M==1].min()); hi=min(z[M==0].max(),z[M==1].max()); keep=(z>=lo)&(z<=hi); z=z[keep]; M=M[keep]; P=P.iloc[np.flatnonzero(keep)]
 subsets={'all18':list(P.columns),'classical4':['CatBoost','LightGBM','RandomForest','XGBoost']}; schemes={}
 for q in [4,5,6,8,10]: schemes[f'q{q}']=pd.qcut(pd.Series(z),q=q,labels=False,duplicates='drop').to_numpy(int)
 for k in [4,6,8,10,12]: schemes[f'block{k}']=blocks(z,k)
 res={}
 for si,(name,algs) in enumerate(subsets.items()):
  R=P[algs].to_numpy(dtype=float,copy=True); R-=R.mean(1,keepdims=True); res[name]={k:strata_p(R,M,s,a.perm,2609309000+si*100+j) for j,(k,s) in enumerate(schemes.items())}
 data={'n':len(M),'cc18':int(np.sum(M==0)),'outside':int(np.sum(M==1)),'permutations':a.perm,'results':res}; (out/'results.json').write_text(json.dumps(data,indent=2)+'\n')
 L=['# Classical-4 targeted common-support stratified test','',f"N={len(M)} ({int(np.sum(M==0))} CC18 / {int(np.sum(M==1))} outside), B={a.perm}.",'','| subset | q4 | q5 | q6 | q8 | q10 | block4 | block6 | block8 | block10 | block12 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
 for name,r in res.items(): L.append('| '+name+' | '+' | '.join(f"{r[k]['p']:.5g}" for k in schemes)+' |')
 (out/'REPORT.md').write_text('\n'.join(L)+'\n'); print((out/'REPORT.md').read_text())
if __name__=='__main__': main()
