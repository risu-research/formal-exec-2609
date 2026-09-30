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
assert len(CC18) == 72

def task_id(x):
    m=re.search(r'__(\d+)$', str(x))
    return int(m.group(1)) if m else np.nan

def pair_rms(d):
    if len(d)<2: return float('nan')
    q=d[:,None]-d[None,:]
    iu=np.triu_indices(len(d),1)
    return float(np.sqrt(np.mean(q[iu]**2)))

def max_pair(d):
    if len(d)<2: return float('nan')
    return float(np.max(d)-np.min(d))

def stat(R, s):
    d=R[~s].mean(axis=0)-R[s].mean(axis=0)
    return d, pair_rms(d), max_pair(d), float(np.linalg.norm(d))

def perm_p(R, s, obs, B, seed):
    rng=np.random.default_rng(seed)
    n=len(s); n1=int(s.sum())
    ge=0; done=0
    batch=500
    total=R.sum(axis=0)
    while done<B:
        b=min(batch,B-done)
        # independent random subsets of fixed size, equivalent to label permutation
        U=rng.random((b,n))
        idx=np.argpartition(U,n1-1,axis=1)[:,:n1]
        cc=R[idx].mean(axis=1)
        out=(total[None,:]-R[idx].sum(axis=1))/(n-n1)
        D=out-cc
        # pairwise RMS identity: mean_{i<j}(di-dj)^2 = A/(A-1)*2*mean((d-mean d)^2)
        # d is already approximately sum-zero after row centering, but compute directly robustly.
        D0=D-D.mean(axis=1,keepdims=True)
        vals=np.sqrt((2*D.shape[1]/(D.shape[1]-1))*np.mean(D0**2,axis=1))
        ge += int(np.sum(vals >= obs-1e-15))
        done += b
    return float((ge+1)/(B+1))

def run_subset(Y, s, algs, keep, B, seed):
    ix=[algs.index(a) for a in keep]
    Z=Y[:,ix]
    R=Z-Z.mean(axis=1,keepdims=True)
    d,t,mx,l2=stat(R,s)
    p=perm_p(R,s,t,B,seed)
    top=[]
    for i in range(len(keep)):
        for j in range(i+1,len(keep)):
            top.append((abs(d[i]-d[j]),keep[i],keep[j],float(d[i]-d[j])))
    top=sorted(top,reverse=True)[:10]
    return {
        'algorithms':keep,'n_algorithms':len(keep),'pairwise_rms_interaction':t,
        'max_abs_pairwise_interaction':mx,'centroid_l2':l2,
        'permutation_B':B,'permutation_p':p,
        'group_centroid_shift_by_algorithm':{a:float(v) for a,v in zip(keep,d)},
        'top_pairwise_shifts':[{'abs_shift':x,'algorithm_a':a,'algorithm_b':b,'signed_shift_a_minus_b':v} for x,a,b,v in top]
    }

def numeric_overlap(series, clean_ids):
    vals=pd.to_numeric(series,errors='coerce').dropna().astype('int64')
    u=set(vals.tolist())
    return len(u & clean_ids), len(u), sorted(u & clean_ids)[:20]

def suffix_overlap(series, clean_ids):
    ids=[]
    for x in series.dropna().astype(str).head(200000):
        m=re.search(r'(?:__|task[_ -]?id[=: ]*|openml[_ -]?id[=: ]*)(\d+)(?:\D*$|$)',x,re.I)
        if m: ids.append(int(m.group(1)))
    u=set(ids)
    return len(u & clean_ids), len(u), sorted(u & clean_ids)[:20]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--clean',required=True)
    ap.add_argument('--meta',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--perm',type=int,default=20000)
    a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    df=pd.read_csv(a.clean)
    df['task_id']=df.dataset_name.map(task_id).astype(int)
    df['in_cc18']=df.task_id.isin(CC18)
    assert not df.duplicated(['task_id','alg_name']).any()
    P=df.pivot(index='task_id',columns='alg_name',values='Accuracy__test_mean').sort_index()
    assert P.notna().all().all(), 'cleaned matrix must be rectangular'
    membership=pd.Series(P.index.isin(CC18),index=P.index)
    s=membership.to_numpy(bool)
    Y=P.to_numpy(float); algs=list(P.columns)
    subsets={
      'all18':algs,
      'minus_TabNet':[x for x in algs if x!='TabNet'],
      'minus_VIME':[x for x in algs if x!='VIME'],
      'minus_TabNet_VIME':[x for x in algs if x not in {'TabNet','VIME'}],
      'classical4':[x for x in ['CatBoost','LightGBM','RandomForest','XGBoost'] if x in algs],
    }
    omnibus={k:run_subset(Y,s,algs,v,a.perm,260929100+i) for i,(k,v) in enumerate(subsets.items())}
    om_summary={
      'rows':int(len(df)),'tasks':int(len(P)),'cc18_tasks':int(s.sum()),'outside_tasks':int((~s).sum()),
      'algorithms':algs,'subsets':omnibus,
      'statistic_definition':'RMS across all algorithm-pair differences in the outside-minus-CC18 centroid shift after task-wise row centering.',
      'null':'CC18 membership labels are exchangeable across the 104 fixed historical tasks under no membership-associated algorithm-relative profile shift.'
    }
    (out/'omnibus_probe.json').write_text(json.dumps(om_summary,indent=2)+'\n')

    # Schema-only / overlap probe for the historical TabSurvey metafeature table.
    meta_path=Path(a.meta)
    meta=pd.read_csv(meta_path,low_memory=False)
    clean_ids=set(map(int,P.index))
    cols=list(meta.columns)
    idish=[c for c in cols if any(k in c.lower() for k in ['task','dataset','openml','name','id'])]
    structural=[c for c in cols if any(k in c.lower() for k in [
        'instance','sample','feature','attribute','class','categor','numeric','missing','imbalance','minority','majority','dimension','row','column'])]
    probes=[]
    for c in idish[:120]:
        ser=meta[c]
        rec={'column':c,'dtype':str(ser.dtype),'non_null':int(ser.notna().sum()),'nunique':int(ser.nunique(dropna=True))}
        try:
            ov,nu,ex=numeric_overlap(ser,clean_ids); rec.update({'numeric_task_overlap':ov,'numeric_unique':nu,'numeric_examples':ex})
        except Exception as e: rec['numeric_error']=repr(e)
        try:
            ov,nu,ex=suffix_overlap(ser,clean_ids); rec.update({'suffix_task_overlap':ov,'suffix_unique':nu,'suffix_examples':ex})
        except Exception as e: rec['suffix_error']=repr(e)
        probes.append(rec)
    schema={
       'path':str(meta_path),'shape':[int(meta.shape[0]),int(meta.shape[1])],
       'columns':cols,'dtypes':{c:str(meta[c].dtype) for c in cols},
       'id_like_columns':idish,'structural_name_candidates':structural,
       'id_overlap_probes':probes,
       'head_id_like':meta[idish[:20]].head(5).astype(str).to_dict(orient='records') if idish else []
    }
    (out/'metafeature_schema_probe.json').write_text(json.dumps(schema,indent=2)+'\n')
    pd.DataFrame(probes).to_csv(out/'metafeature_id_overlap_probe.csv',index=False)
    pd.DataFrame({'column':cols,'dtype':[str(meta[c].dtype) for c in cols]}).to_csv(out/'metafeature_columns.csv',index=False)

    md=['# TabZilla omnibus + historical metafeature probe','',
        f"Cleaned matrix: {len(P)} tasks ({int(s.sum())} CC18 / {int((~s).sum())} outside), {len(algs)} algorithms.",'',
        '## Omnibus interaction', '']
    for k,v in omnibus.items():
        md += [f"- **{k}**: RMS pair-shift={v['pairwise_rms_interaction']:.8f}; max pair-shift={v['max_abs_pairwise_interaction']:.8f}; permutation p={v['permutation_p']:.8g} (B={v['permutation_B']})."]
    md += ['', '## Historical metafeature source','',
           f"- shape: {meta.shape[0]} rows × {meta.shape[1]} columns",
           f"- id-like columns: {len(idish)}",
           f"- structural-name candidates: {len(structural)}",
           '', 'The probe does not interpret or select a mechanism model yet; it freezes the schema and task-ID join evidence first.']
    (out/'REPORT.md').write_text('\n'.join(md)+'\n')
    print((out/'REPORT.md').read_text())

if __name__=='__main__': main()
