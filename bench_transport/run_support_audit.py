#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RepeatedStratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

CC18 = {
    3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,
    2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,
    7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,
    10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,
    146800,146817,146819,146820,146821,146822,146824,146825,167119,
    167120,167121,167124,167125,167140,167141
}

RAW = {
    "n_instances": "f__pymfe.general.nr_inst",
    "n_attributes": "f__pymfe.general.nr_attr",
    "n_classes": "f__pymfe.general.nr_class",
    "n_numeric": "f__pymfe.general.nr_num",
    "n_categorical": "f__pymfe.general.nr_cat",
    "class_freq_min": "f__pymfe.general.freq_class.min",
    "class_freq_max": "f__pymfe.general.freq_class.max",
    "class_freq_sd": "f__pymfe.general.freq_class.sd",
    "class_entropy": "f__pymfe.info-theory.class_ent",
}

CORE_FEATURES = [
    "log10_n_instances",
    "log10_n_attributes",
    "n_classes",
    "minority_majority_ratio",
    "categorical_fraction",
]

def task_id(x):
    m = re.search(r"__(\d+)$", str(x))
    return int(m.group(1)) if m else np.nan

def bh(p):
    p=np.asarray(p,float)
    q=np.full(len(p),np.nan)
    ok=np.isfinite(p)
    v=p[ok]
    if len(v)==0: return q
    order=np.argsort(v)
    r=v[order]*len(v)/np.arange(1,len(v)+1)
    r=np.minimum.accumulate(r[::-1])[::-1]
    r=np.minimum(r,1)
    inv=np.empty_like(order); inv[order]=np.arange(len(order))
    q[np.where(ok)[0]]=r[inv]
    return q

def smd(x1,x0):
    x1=pd.Series(x1).dropna().astype(float); x0=pd.Series(x0).dropna().astype(float)
    if len(x1)<2 or len(x0)<2: return np.nan
    sp=math.sqrt(((len(x1)-1)*x1.var(ddof=1)+(len(x0)-1)*x0.var(ddof=1))/(len(x1)+len(x0)-2))
    return float((x1.mean()-x0.mean())/sp) if sp>0 else np.nan

def build_frame(upstream:Path):
    outcome=upstream/"tabzilla_analysis/final-selected18-algs/cleaned_results/tuned_aggregated_results.csv"
    meta=upstream/"TabSurvey/metafeatures.csv"
    if not outcome.exists(): raise FileNotFoundError(outcome)
    if not meta.exists(): raise FileNotFoundError(meta)
    odf=pd.read_csv(outcome)
    tasks=odf[["dataset_name"]].drop_duplicates().copy()
    tasks["task_id"]=tasks.dataset_name.map(task_id)
    tasks=tasks[tasks.task_id.notna()].copy(); tasks.task_id=tasks.task_id.astype(int)
    tasks["in_cc18"]=tasks.task_id.isin(CC18)
    use=["dataset_name"]+[c for c in RAW.values()]
    header=pd.read_csv(meta,nrows=0).columns
    missing=[c for c in use if c not in header]
    if missing: raise ValueError(f"Expected structural metafeatures missing: {missing}")
    mdf=pd.read_csv(meta,usecols=use)
    merged=tasks.merge(mdf,on="dataset_name",how="left",validate="one_to_one",indicator=True)
    if not (merged._merge=="both").all():
        mdf2=mdf.copy(); mdf2["task_id"]=mdf2.dataset_name.map(task_id)
        mdf2=mdf2[mdf2.task_id.notna()].copy(); mdf2.task_id=mdf2.task_id.astype(int)
        mdf2=mdf2.drop(columns=["dataset_name"]).drop_duplicates("task_id")
        merged=tasks.merge(mdf2,on="task_id",how="left",validate="one_to_one",indicator=True)
    merged=merged.drop(columns=["_merge"],errors="ignore")
    for short,col in RAW.items(): merged[short]=pd.to_numeric(merged[col],errors="coerce")
    merged["log10_n_instances"]=np.log10(merged.n_instances.clip(lower=1))
    merged["log10_n_attributes"]=np.log10(merged.n_attributes.clip(lower=1))
    merged["minority_majority_ratio"]=merged.class_freq_min/merged.class_freq_max.replace(0,np.nan)
    merged["categorical_fraction"]=merged.n_categorical/merged.n_attributes.replace(0,np.nan)
    merged["numeric_fraction"]=merged.n_numeric/merged.n_attributes.replace(0,np.nan)
    merged["log10_attr_to_inst"]=np.log10(((merged.n_attributes+1)/(merged.n_instances+1)).clip(lower=1e-12))
    return odf, merged, meta

def selection_audit(frame, out):
    rows=[]
    for f in CORE_FEATURES:
        a=frame.loc[frame.in_cc18,f].dropna().astype(float); b=frame.loc[~frame.in_cc18,f].dropna().astype(float)
        ks=stats.ks_2samp(a,b,alternative="two-sided",method="auto")
        welch=stats.ttest_ind(a,b,equal_var=False,nan_policy="omit")
        rows.append({"feature":f,"n_cc18":len(a),"n_outside":len(b),"mean_cc18":a.mean(),"mean_outside":b.mean(),"median_cc18":a.median(),"median_outside":b.median(),"q25_cc18":a.quantile(.25),"q75_cc18":a.quantile(.75),"q25_outside":b.quantile(.25),"q75_outside":b.quantile(.75),"min_cc18":a.min(),"max_cc18":a.max(),"min_outside":b.min(),"max_outside":b.max(),"smd_cc18_minus_outside":smd(a,b),"ks_D":ks.statistic,"ks_p":ks.pvalue,"welch_p":welch.pvalue})
    tab=pd.DataFrame(rows); tab["ks_q_bh"]=bh(tab.ks_p); tab["welch_q_bh"]=bh(tab.welch_p)
    tab.to_csv(out/"structural_selection_audit.csv",index=False)
    return tab

def propensity_and_overlap(frame,out):
    X=frame[CORE_FEATURES].copy(); y=frame.in_cc18.astype(int).to_numpy()
    pipe=Pipeline([("impute",SimpleImputer(strategy="median")),("scale",StandardScaler()),("logit",LogisticRegression(C=1.0,max_iter=5000,solver="lbfgs"))])
    cv=RepeatedStratifiedKFold(n_splits=5,n_repeats=20,random_state=20260929)
    scores=cross_val_score(pipe,X,y,cv=cv,scoring="roc_auc",n_jobs=1)
    pipe.fit(X,y); ps=pipe.predict_proba(X)[:,1]
    f=frame[["task_id","dataset_name","in_cc18"]+CORE_FEATURES].copy(); f["membership_propensity_fullfit"]=ps
    p1=ps[y==1]; lo,hi=float(np.min(p1)),float(np.max(p1))
    f["outside_beyond_cc18_propensity_range"]=(~f.in_cc18)&((f.membership_propensity_fullfit<lo)|(f.membership_propensity_fullfit>hi))
    imp=SimpleImputer(strategy="median"); Z=imp.fit_transform(X); Z=StandardScaler().fit_transform(Z)
    idx1=np.where(y==1)[0]; idx0=np.where(y==0)[0]
    D=np.sqrt(((Z[:,None,:]-Z[None,:,:])**2).sum(axis=2))
    internal=[np.min(D[i,[j for j in idx1 if j!=i]]) for i in idx1]
    threshold=float(np.quantile(internal,.95)); outside_dist=[float(np.min(D[i,idx1])) for i in idx0]
    f["nn_distance_to_cc18"]=np.nan; f.loc[f.index[idx0],"nn_distance_to_cc18"]=outside_dist
    f["outside_beyond_cc18_nn95"]=(~f.in_cc18)&(f.nn_distance_to_cc18>threshold)
    f.to_csv(out/"structural_task_frame_with_overlap.csv",index=False)
    result={"features":CORE_FEATURES,"cv_auc_mean":float(np.mean(scores)),"cv_auc_sd":float(np.std(scores,ddof=1)),"cv_auc_q025":float(np.quantile(scores,.025)),"cv_auc_q975":float(np.quantile(scores,.975)),"cv_folds_total":int(len(scores)),"fullfit_cc18_propensity_min":lo,"fullfit_cc18_propensity_max":hi,"outside_beyond_cc18_propensity_range":int(f.outside_beyond_cc18_propensity_range.sum()),"cc18_internal_nn95_threshold":threshold,"outside_beyond_cc18_nn95":int(f.outside_beyond_cc18_nn95.sum())}
    (out/"selection_propensity_overlap.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result,f

def proxy_rules(frame,out):
    f=frame.copy(); f["proxy_size_low"]=f.n_instances<500; f["proxy_size_high"]=f.n_instances>100000; f["proxy_attr_high_raw"]=f.n_attributes>5000; f["proxy_imbalance"]=f.minority_majority_ratio<=0.05
    f["proxy_any"]=f[["proxy_size_low","proxy_size_high","proxy_attr_high_raw","proxy_imbalance"]].any(axis=1)
    cols=["task_id","dataset_name","in_cc18","n_instances","n_attributes","n_classes","minority_majority_ratio","proxy_size_low","proxy_size_high","proxy_attr_high_raw","proxy_imbalance","proxy_any"]
    f[cols].to_csv(out/"cc18_rule_proxy_violations_by_task.csv",index=False)
    summ={}
    for c in ["proxy_size_low","proxy_size_high","proxy_attr_high_raw","proxy_imbalance","proxy_any"]: summ[c]={"cc18":int(f.loc[f.in_cc18,c].sum()),"outside":int(f.loc[~f.in_cc18,c].sum())}
    (out/"cc18_rule_proxy_summary.json").write_text(json.dumps(summ,indent=2),encoding="utf-8")
    return f,summ

def contrast_feature_heterogeneity(odf, frame, out):
    d=odf.copy(); d["task_id"]=d.dataset_name.map(task_id); d=d[d.task_id.notna()].copy(); d.task_id=d.task_id.astype(int)
    pivot=d.pivot(index="task_id",columns="alg_name",values="Accuracy__test_mean"); meta=frame.set_index("task_id")[CORE_FEATURES]; common=pivot.index.intersection(meta.index); pivot=pivot.loc[common]; meta=meta.loc[common]
    rows=[]
    for a,b in itertools.combinations(sorted(pivot.columns),2):
        diff=pivot[a]-pivot[b]
        for feat in CORE_FEATURES:
            q=pd.concat([diff.rename("d"),meta[feat].rename("z")],axis=1).dropna()
            if len(q)<10 or q.z.nunique()<3: rho=p=np.nan
            else:
                s=stats.spearmanr(q.d,q.z); rho=float(s.statistic); p=float(s.pvalue)
            rows.append({"algorithm_a":a,"algorithm_b":b,"feature":feat,"n":len(q),"spearman_rho":rho,"p":p})
    tab=pd.DataFrame(rows); tab["q_bh_global"]=bh(tab.p); tab.to_csv(out/"contrast_feature_correlations.csv",index=False)
    mins=tab.groupby(["algorithm_a","algorithm_b"]).q_bh_global.min()
    counts={"tests":int(len(tab)),"tests_q_lt_05":int((tab.q_bh_global<.05).sum()),"pairs_with_any_q_lt_05":int(mins.lt(.05).sum())}
    (out/"contrast_feature_heterogeneity_summary.json").write_text(json.dumps(counts,indent=2),encoding="utf-8")
    return tab,counts

def criterion_ablation(odf, frame, out):
    d=odf.copy(); d["task_id"]=d.dataset_name.map(task_id); d=d[d.task_id.notna()].copy(); d.task_id=d.task_id.astype(int)
    pivot=d.pivot(index="task_id",columns="alg_name",values="Accuracy__test_mean"); f=frame.set_index("task_id").loc[pivot.index].copy()
    masks={"exact_cc18":f.in_cc18,"size_500_100k":(f.n_instances>=500)&(f.n_instances<=100000),"imbalance_gt_0.05":f.minority_majority_ratio>0.05,"raw_attr_le_5000_proxy":f.n_attributes<=5000,"all_three_proxies":((f.n_instances>=500)&(f.n_instances<=100000)&(f.minority_majority_ratio>0.05)&(f.n_attributes<=5000))}
    rows=[]
    for rule,mask in masks.items():
        pairrows=[]
        for a,b in itertools.combinations(sorted(pivot.columns),2):
            diff=(pivot[a]-pivot[b]).loc[f.index]; s=diff[mask]; o=diff[~mask]
            if len(s)<5 or len(o)<5: continue
            ds=float(s.mean()); do=float(o.mean()); pairrows.append((np.sign(ds)!=np.sign(do) and ds!=0 and do!=0,abs(do-ds)))
        rows.append({"rule":rule,"selected_tasks":int(mask.sum()),"excluded_tasks":int((~mask).sum()),"pairs_evaluable":len(pairrows),"sign_reversals":int(sum(x[0] for x in pairrows)),"median_abs_interaction":float(np.median([x[1] for x in pairrows])) if pairrows else np.nan,"max_abs_interaction":float(np.max([x[1] for x in pairrows])) if pairrows else np.nan,"jaccard_with_exact_cc18":float(np.sum(mask & f.in_cc18)/np.sum(mask | f.in_cc18)) if np.sum(mask|f.in_cc18) else np.nan})
    tab=pd.DataFrame(rows); tab.to_csv(out/"criterion_ablation_summary.csv",index=False); return tab

def robust_reversal_feature_context(matrix_path, corr, out):
    if not matrix_path.exists(): return pd.DataFrame()
    m=pd.read_csv(matrix_path); rev=m[(m.sign_reversal==True)&(m.cc18_ci_lo*m.cc18_ci_hi>0)&(m.outside_ci_lo*m.outside_ci_hi>0)].copy(); rows=[]
    for _,r in rev.iterrows():
        z=corr[(corr.algorithm_a==r.algorithm_a)&(corr.algorithm_b==r.algorithm_b)].copy(); z["abs_rho"]=z.spearman_rho.abs(); z=z.sort_values("abs_rho",ascending=False)
        for _,q in z.iterrows(): rows.append({"algorithm_a":r.algorithm_a,"algorithm_b":r.algorithm_b,"delta_cc18":r.delta_cc18,"delta_outside":r.delta_outside,"interaction":r.interaction_outside_minus_cc18,"feature":q.feature,"feature_spearman_rho":q.spearman_rho,"feature_q_bh_global":q.q_bh_global})
    tab=pd.DataFrame(rows); tab.to_csv(out/"robust_reversal_feature_context.csv",index=False); return tab

def markdown(out, sel, prop, proxy, hetero, ablation, robust, frame):
    outside=frame[~frame.in_cc18]; sel2=sel.copy(); sel2["abs_smd"]=sel2.smd_cc18_minus_outside.abs(); sel2=sel2.sort_values("abs_smd",ascending=False)
    lines=["# Structural support / selection audit","",f"- Complete historical frame with outcomes: **{len(frame)} tasks** ({int(frame.in_cc18.sum())} CC18, {int((~frame.in_cc18).sum())} outside).",f"- Predeclared rule-aligned features: `{', '.join(CORE_FEATURES)}`.",f"- Repeated 5-fold logistic membership AUC: **{prop['cv_auc_mean']:.3f} ± {prop['cv_auc_sd']:.3f}** (100 held-out fold scores; 2.5–97.5%: {prop['cv_auc_q025']:.3f}–{prop['cv_auc_q975']:.3f}).",f"- Outside tasks beyond the empirical CC18 propensity range: **{prop['outside_beyond_cc18_propensity_range']}/{len(outside)}**.",f"- Outside tasks farther from CC18 than the 95th percentile of CC18-to-CC18 NN distance: **{prop['outside_beyond_cc18_nn95']}/{len(outside)}**.",f"- Pair×feature heterogeneity tests: **{hetero['tests_q_lt_05']}/{hetero['tests']}** significant after global BH-FDR; **{hetero['pairs_with_any_q_lt_05']}/153 algorithm pairs** have at least one structural feature association at q<.05.","","## Selection shifts on predeclared structural dimensions","",sel2[["feature","mean_cc18","mean_outside","smd_cc18_minus_outside","ks_D","ks_q_bh"]].to_markdown(index=False),"","## CC18-rule proxy violations","","Feature count uses raw Pymfe attributes and is therefore only a proxy for CC18's post-encoding 5,000-feature rule.","",pd.DataFrame([{"proxy":k,**v} for k,v in proxy.items()]).to_markdown(index=False),"","## Criterion ablation","",ablation.to_markdown(index=False),""]
    if len(robust): lines += ["## Robust reversal × structural-feature context","",robust.to_markdown(index=False),""]
    lines += ["## Guardrail","","Membership separability and metadata imbalance establish selection/support differences, not causal effects of curation. The contrast-feature associations establish that relative algorithm performance varies along the same predeclared task dimensions. Together they supply the empirical ingredients for a transportability argument; hard support gaps still imply partial rather than point identification without extrapolation assumptions."]
    (out/"SUPPORT_AUDIT.md").write_text("\n".join(lines),encoding="utf-8")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--upstream",required=True); ap.add_argument("--out",default="results"); args=ap.parse_args()
    upstream=Path(args.upstream); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    odf,frame,meta_path=build_frame(upstream); frame.to_csv(out/"structural_task_frame.csv",index=False)
    (out/"METAFEATURE_PROVENANCE.txt").write_text(f"path={meta_path}\nsha256={hashlib.sha256(meta_path.read_bytes()).hexdigest()}\n",encoding="utf-8")
    sel=selection_audit(frame,out); prop,overlap=propensity_and_overlap(frame,out); framed,proxy=proxy_rules(frame,out); corr,hetero=contrast_feature_heterogeneity(odf,frame,out); ablation=criterion_ablation(odf,frame,out); robust=robust_reversal_feature_context(out/"accuracy_pairwise_interactions.csv",corr,out); markdown(out,sel,prop,proxy,hetero,ablation,robust,frame)
    headline={"tasks":len(frame),"cc18":int(frame.in_cc18.sum()),"outside":int((~frame.in_cc18).sum()),"cv_auc_mean":prop["cv_auc_mean"],"outside_beyond_nn95":prop["outside_beyond_cc18_nn95"],"pairs_with_structural_heterogeneity_q_lt_05":hetero["pairs_with_any_q_lt_05"]}
    (out/"support_headline.json").write_text(json.dumps(headline,indent=2),encoding="utf-8"); print(json.dumps(headline,indent=2)); print((out/"SUPPORT_AUDIT.md").read_text())

if __name__=="__main__": main()
