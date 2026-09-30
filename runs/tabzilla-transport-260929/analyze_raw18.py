#!/usr/bin/env python3
from pathlib import Path
import argparse, hashlib, itertools, json, re
import numpy as np
import pandas as pd

CC18={3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,146800,146817,146819,146820,146821,146822,146824,146825,167119,167120,167121,167124,167125,167140,167141}
SELECTED=['CatBoost','DANet','DecisionTree','FTTransformer','KNN','LightGBM','LinearModel','MLP','MLP-rtdl','NODE','RandomForest','ResNet','SAINT','STG','SVM','TabNet','VIME','XGBoost']

def tid(x):
 m=re.search(r'__(\d+)$',str(x)); return int(m.group(1)) if m else np.nan

def bh(p):
 p=np.asarray(p,float); out=np.full(len(p),np.nan); ok=np.isfinite(p); v=p[ok]
 if not len(v): return out
 o=np.argsort(v); r=v[o]; q=r*len(r)/(np.arange(len(r))+1); q=np.minimum.accumulate(q[::-1])[::-1]; q=np.clip(q,0,1); t=np.empty(len(v)); t[o]=q; out[np.where(ok)[0]]=t; return out

def boot(c,o,B,seed):
 rng=np.random.default_rng(seed); obs=float(o.mean()-c.mean()); vals=np.empty(B)
 for i in range(B): vals[i]=rng.choice(o,len(o),replace=True).mean()-rng.choice(c,len(c),replace=True).mean()
 lo,hi=np.quantile(vals,[.025,.975]); return obs,float(lo),float(hi)

def perm(c,o,B,seed):
 rng=np.random.default_rng(seed); obs=abs(float(o.mean()-c.mean())); z=np.r_[c,o]; n=len(c); ge=0
 for _ in range(B):
  q=rng.permutation(z); ge+=abs(float(q[n:].mean()-q[:n].mean()))>=obs-1e-15
 return (ge+1)/(B+1)

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--csv',required=True); ap.add_argument('--out',required=True); ap.add_argument('--boot',type=int,default=5000); ap.add_argument('--perm',type=int,default=10000); a=ap.parse_args()
 O=Path(a.out); O.mkdir(parents=True,exist_ok=True); raw=Path(a.csv).read_bytes(); df=pd.read_csv(a.csv)
 df['task_id']=df.dataset_name.map(tid); df=df[df.task_id.notna()].copy(); df.task_id=df.task_id.astype(int)
 audit={'raw_rows':len(df),'raw_algorithms':sorted(df.alg_name.unique()),'raw_algorithm_count':df.alg_name.nunique(),'raw_tasks':df.task_id.nunique()}
 d=df[df.alg_name.isin(SELECTED)].copy(); d['in_cc18']=d.task_id.isin(CC18)
 audit.update({'selected_rows':len(d),'selected_algorithms':d.alg_name.nunique(),'selected_tasks_union':d.task_id.nunique(),'cc18_union':d.loc[d.in_cc18,'task_id'].nunique(),'outside_union':d.loc[~d.in_cc18,'task_id'].nunique(),'source_sha256':hashlib.sha256(raw).hexdigest()})
 # Exact duplicate audit.
 dup=d.duplicated(['alg_name','task_id'],keep=False); d.loc[dup].sort_values(['alg_name','task_id']).to_csv(O/'duplicate_rows.csv',index=False)
 if dup.any(): raise RuntimeError(f'duplicate alg-task rows {dup.sum()}')
 cov=d.groupby('alg_name').agg(tasks=('task_id','nunique'),cc18=('in_cc18','sum')).reset_index(); cov['outside']=cov.tasks-cov.cc18; cov.to_csv(O/'algorithm_coverage.csv',index=False)
 # Complete-case task set for all 18, for sensitivity to author-cleaned 104-task matrix.
 counts=d.groupby('task_id').alg_name.nunique(); complete=set(counts[counts==len(SELECTED)].index); audit['complete_case_tasks']=len(complete); audit['complete_case_cc18']=len(complete & CC18); audit['complete_case_outside']=len(complete-CC18)
 pd.DataFrame({'task_id':sorted(complete),'in_cc18':[x in CC18 for x in sorted(complete)]}).to_csv(O/'complete_case_task_ids.csv',index=False)
 # Coverage by task.
 tc=d.groupby('task_id').agg(dataset_name=('dataset_name','first'),algorithm_count=('alg_name','nunique'),in_cc18=('in_cc18','first')).reset_index(); tc.to_csv(O/'task_coverage.csv',index=False)
 rows=[]
 for k,(aa,bb) in enumerate(itertools.combinations(SELECTED,2)):
  A=d[d.alg_name==aa][['task_id','Accuracy__test_mean','in_cc18']].rename(columns={'Accuracy__test_mean':'ya'}); B=d[d.alg_name==bb][['task_id','Accuracy__test_mean']].rename(columns={'Accuracy__test_mean':'yb'}); m=A.merge(B,on='task_id',validate='one_to_one'); m['diff']=m.ya-m.yb
  c=m.loc[m.in_cc18,'diff'].to_numpy(float); o=m.loc[~m.in_cc18,'diff'].to_numpy(float); rec={'algorithm_a':aa,'algorithm_b':bb,'n_common':len(m),'n_cc18':len(c),'n_outside':len(o)}
  if len(c)>=2 and len(o)>=2:
   est,lo,hi=boot(c,o,a.boot,26092900+k); pp=perm(c,o,a.perm,26093900+k); rec.update(delta_cc18=float(c.mean()),delta_outside=float(o.mean()),interaction=est,ci_lo=lo,ci_hi=hi,perm_p=pp,sign_reversal=bool(np.sign(c.mean())!=np.sign(o.mean()) and c.mean()!=0 and o.mean()!=0))
  rows.append(rec)
 P=pd.DataFrame(rows); P['perm_q_bh']=bh(P.perm_p); P['abs_interaction']=P.interaction.abs(); P['ci_excludes_zero']=(P.ci_lo>0)|(P.ci_hi<0); P=P.sort_values(['sign_reversal','abs_interaction'],ascending=[False,False]); P.to_csv(O/'pairwise_raw18.csv',index=False)
 P[P.sign_reversal].to_csv(O/'sign_reversals.csv',index=False); P[P.perm_q_bh<.05].to_csv(O/'interaction_q05.csv',index=False)
 audit.update({'pairs':len(P),'eligible_pairs':int(P.interaction.notna().sum()),'reversals':int(P.sign_reversal.sum()),'q05':int((P.perm_q_bh<.05).sum()),'ci_excludes_zero':int(P.ci_excludes_zero.sum()),'max_abs_interaction':float(P.abs_interaction.max()),'common_task_min':int(P.n_common.min()),'common_task_max':int(P.n_common.max()),'cc18_common_min':int(P.n_cc18.min()),'cc18_common_max':int(P.n_cc18.max()),'outside_common_min':int(P.n_outside.min()),'outside_common_max':int(P.n_outside.max())})
 (O/'summary.json').write_text(json.dumps(audit,indent=2))
 print(json.dumps(audit,indent=2)); print(P.head(35).to_string(index=False))
if __name__=='__main__': main()
