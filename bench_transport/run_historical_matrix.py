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

# Frozen OpenML-CC18 task IDs (suite 99), 72 tasks.
CC18 = {
    3,6,11,12,14,15,16,18,22,23,28,29,31,32,37,43,45,49,53,219,
    2074,2079,3021,3022,3481,3549,3560,3573,3902,3903,3904,3913,3917,3918,
    7592,9910,9946,9952,9957,9960,9964,9971,9976,9977,9978,9981,9985,
    10093,10101,14952,14954,14965,14969,14970,125920,125922,146195,
    146800,146817,146819,146820,146821,146822,146824,146825,167119,
    167120,167121,167124,167125,167140,167141
}
assert len(CC18) == 72

METRICS = {
    "accuracy": ("Accuracy__test_mean", +1),
    "f1": ("normalized_F1__test_mean", +1),
    # Log Loss is lower-is-better, so orient it so positive always means algorithm A better.
    "logloss": ("normalized_Log Loss__test_mean", -1),
}


def task_id_from_name(x: object) -> float:
    m = re.search(r"__(\d+)$", str(x))
    return float(m.group(1)) if m else np.nan


def bh_qvalues(pvals: pd.Series) -> pd.Series:
    p = pvals.astype(float).to_numpy()
    out = np.full(len(p), np.nan)
    ok = np.isfinite(p)
    vals = p[ok]
    if len(vals) == 0:
        return pd.Series(out, index=pvals.index)
    order = np.argsort(vals)
    ranked = vals[order]
    q = ranked * len(ranked) / np.arange(1, len(ranked) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.minimum(q, 1.0)
    inv = np.empty_like(order)
    inv[order] = np.arange(len(order))
    out[np.where(ok)[0]] = q[inv]
    return pd.Series(out, index=pvals.index)


def stable_seed(*parts: str) -> int:
    h = hashlib.sha256("||".join(parts).encode()).hexdigest()
    return int(h[:8], 16)


def bootstrap_two_strata(d1: np.ndarray, d0: np.ndarray, b: int, seed: int) -> dict:
    """Bootstrap CC18 (d1) and outside (d0) independently by task.

    Returns CIs for each mean contrast and for outside-minus-CC18 interaction,
    plus bootstrap probability of opposite winner signs.
    """
    rng = np.random.default_rng(seed)
    n1, n0 = len(d1), len(d0)
    if n1 < 2 or n0 < 2:
        return {}
    # Chunk to keep memory bounded.
    v1, v0, vint = [], [], []
    opposite = 0
    done = 0
    chunk = min(1000, b)
    while done < b:
        k = min(chunk, b - done)
        i1 = rng.integers(0, n1, size=(k, n1))
        i0 = rng.integers(0, n0, size=(k, n0))
        m1 = d1[i1].mean(axis=1)
        m0 = d0[i0].mean(axis=1)
        inter = m0 - m1
        v1.append(m1); v0.append(m0); vint.append(inter)
        opposite += int(np.sum(np.sign(m1) != np.sign(m0)))
        done += k
    b1 = np.concatenate(v1); b0 = np.concatenate(v0); bi = np.concatenate(vint)
    q = lambda a, x: float(np.quantile(a, x))
    return {
        "cc18_ci_lo": q(b1, .025), "cc18_ci_hi": q(b1, .975),
        "outside_ci_lo": q(b0, .025), "outside_ci_hi": q(b0, .975),
        "interaction_ci_lo": q(bi, .025), "interaction_ci_hi": q(bi, .975),
        "bootstrap_opposite_sign_prob": opposite / b,
    }


def permutation_interaction_p(d1: np.ndarray, d0: np.ndarray, b: int, seed: int) -> float:
    """Randomization p-value for difference in stratum means under exchangeability."""
    rng = np.random.default_rng(seed)
    x = np.concatenate([d1, d0])
    n1 = len(d1)
    obs = abs(float(d0.mean() - d1.mean()))
    ge = 1
    for _ in range(b):
        z = rng.permutation(x)
        val = abs(float(z[n1:].mean() - z[:n1].mean()))
        ge += (val >= obs)
    return ge / (b + 1)


def pairwise_metric(df: pd.DataFrame, col: str, orient: int, metric_name: str,
                    bootstrap_b: int, perm_b: int) -> pd.DataFrame:
    rows = []
    algs = sorted(df["alg_name"].dropna().unique())
    for a, b in itertools.combinations(algs, 2):
        A = df[df.alg_name == a][["task_id", col, "in_cc18"]].rename(columns={col:"ya"})
        B = df[df.alg_name == b][["task_id", col]].rename(columns={col:"yb"})
        M = A.merge(B, on="task_id", how="inner").dropna()
        # Positive means a better than b for every metric after orienting.
        M["d"] = orient * (M.ya - M.yb)
        g1 = M.loc[M.in_cc18, "d"].to_numpy(float)
        g0 = M.loc[~M.in_cc18, "d"].to_numpy(float)
        rec = {
            "metric": metric_name,
            "algorithm_a": a, "algorithm_b": b,
            "n_common_total": int(len(M)),
            "n_cc18_common": int(len(g1)), "n_outside_common": int(len(g0)),
            "delta_cc18": float(g1.mean()) if len(g1) else np.nan,
            "delta_outside": float(g0.mean()) if len(g0) else np.nan,
            "sd_cc18": float(g1.std(ddof=1)) if len(g1)>1 else np.nan,
            "sd_outside": float(g0.std(ddof=1)) if len(g0)>1 else np.nan,
        }
        if len(g1) and len(g0):
            rec["interaction_outside_minus_cc18"] = rec["delta_outside"] - rec["delta_cc18"]
            rec["sign_reversal"] = bool(np.sign(rec["delta_cc18"]) != np.sign(rec["delta_outside"]) and rec["delta_cc18"] != 0 and rec["delta_outside"] != 0)
        else:
            rec["interaction_outside_minus_cc18"] = np.nan
            rec["sign_reversal"] = False
        if len(g1) >= 2 and len(g0) >= 2:
            # Welch test is a secondary diagnostic; permutation is primary inferential diagnostic.
            welch = stats.ttest_ind(g0, g1, equal_var=False, nan_policy="omit")
            rec["welch_p"] = float(welch.pvalue)
            pooled = math.sqrt(((len(g1)-1)*np.var(g1,ddof=1) + (len(g0)-1)*np.var(g0,ddof=1)) / (len(g1)+len(g0)-2))
            rec["interaction_std_effect"] = rec["interaction_outside_minus_cc18"] / pooled if pooled > 0 else np.nan
            rec.update(bootstrap_two_strata(g1, g0, bootstrap_b, stable_seed(metric_name,a,b,"boot")))
            rec["permutation_p"] = permutation_interaction_p(g1, g0, perm_b, stable_seed(metric_name,a,b,"perm"))
        else:
            rec["welch_p"] = np.nan
            rec["interaction_std_effect"] = np.nan
            rec["permutation_p"] = np.nan
        rows.append(rec)
    out = pd.DataFrame(rows)
    out["permutation_q_bh"] = bh_qvalues(out["permutation_p"])
    out["welch_q_bh"] = bh_qvalues(out["welch_p"])
    return out


def algorithm_stratum_summary(df: pd.DataFrame, metric_col: str, orient: int, metric_name: str) -> pd.DataFrame:
    rows=[]
    for alg, g in df.groupby("alg_name"):
        for flag,label in [(True,"cc18"),(False,"outside")]:
            z=g[g.in_cc18==flag][metric_col].dropna().astype(float)
            rows.append({"metric":metric_name,"algorithm":alg,"stratum":label,"n":len(z),
                         "mean_oriented":float((orient*z).mean()) if len(z) else np.nan,
                         "mean_raw":float(z.mean()) if len(z) else np.nan,
                         "median_raw":float(z.median()) if len(z) else np.nan})
    return pd.DataFrame(rows)


def meta_inventory(upstream: Path, outdir: Path) -> pd.DataFrame:
    """Inventory plausible task/dataset metadata files without guessing a canonical one."""
    rows=[]
    for p in upstream.rglob("*"):
        if not p.is_file():
            continue
        name=p.name.lower()
        if not any(k in name for k in ["meta", "dataset", "openml", "feature"]):
            continue
        if p.suffix.lower() not in {".csv",".json",".jsonl",".txt",".tsv",".pkl",".pickle",".parquet"}:
            continue
        rec={"path":str(p.relative_to(upstream)),"bytes":p.stat().st_size,"suffix":p.suffix.lower()}
        if p.suffix.lower() in {".csv",".tsv"} and p.stat().st_size < 50_000_000:
            try:
                sep="\t" if p.suffix.lower()==".tsv" else ","
                q=pd.read_csv(p,nrows=5,sep=sep)
                rec["columns"]=" | ".join(map(str,q.columns))
            except Exception as e:
                rec["read_error"]=repr(e)
        rows.append(rec)
    inv=pd.DataFrame(rows).sort_values(["bytes","path"],ascending=[False,True]) if rows else pd.DataFrame(columns=["path","bytes","suffix"])
    inv.to_csv(outdir/"metafeature_candidate_inventory.csv",index=False)
    return inv


def manifest_inventory(upstream: Path, outdir: Path) -> dict:
    ids=[]
    source_rows=[]
    pat=re.compile(r"\b(?:TASK|TASK_ID|OPENML_TASK_ID)?[^0-9]{0,8}(\d{1,6})\b")
    for p in upstream.rglob("*.sh"):
        try: txt=p.read_text(errors="ignore")
        except Exception: continue
        if "DATASETS" in p.name.upper() or "openml" in txt.lower():
            nums=[int(x) for x in re.findall(r"(?<![\w.])(\d{1,6})(?![\w.])",txt)]
            # Keep only plausible task ids that occur in outcome data later; raw inventory only here.
            source_rows.append({"path":str(p.relative_to(upstream)),"integer_tokens":len(nums),"unique_integer_tokens":len(set(nums))})
            ids.extend(nums)
    pd.DataFrame(source_rows).to_csv(outdir/"manifest_script_inventory.csv",index=False)
    return {"candidate_integer_tokens_unique":len(set(ids))}


def write_markdown_summary(outdir: Path, summary: dict, pair_all: pd.DataFrame) -> None:
    acc = pair_all[pair_all.metric=="accuracy"].copy()
    eligible20=acc[(acc.n_cc18_common>=20)&(acc.n_outside_common>=20)]
    sig=eligible20[eligible20.permutation_q_bh < .05]
    rev=eligible20[eligible20.sign_reversal]
    robust_rev=rev[(rev.cc18_ci_lo*rev.cc18_ci_hi>0)&(rev.outside_ci_lo*rev.outside_ci_hi>0)]
    top=eligible20.assign(abs_interaction=eligible20.interaction_outside_minus_cc18.abs()).sort_values("abs_interaction",ascending=False).head(15)
    lines=[
        "# Historical CC18 × algorithm-contrast interaction audit",
        "",
        f"- Historical cleaned rows: **{summary['rows']}**",
        f"- Unique algorithms: **{summary['algorithms']}**",
        f"- Unique OpenML task IDs in cleaned outcomes: **{summary['tasks']}**",
        f"- CC18 tasks observed: **{summary['cc18_tasks_observed']}/72**",
        f"- Outside-CC18 tasks observed: **{summary['outside_tasks_observed']}**",
        f"- Accuracy pairs with >=20 common tasks in each stratum: **{len(eligible20)}**",
        f"- Accuracy winner sign reversals among those pairs: **{len(rev)}**",
        f"- Reversals with both stratum bootstrap 95% CIs excluding zero: **{len(robust_rev)}**",
        f"- Interaction tests significant after BH-FDR q<0.05: **{len(sig)}**",
        "",
        "## Largest accuracy interactions (outside minus CC18)",
        "",
        top[["algorithm_a","algorithm_b","n_cc18_common","n_outside_common","delta_cc18","delta_outside","interaction_outside_minus_cc18","sign_reversal","interaction_ci_lo","interaction_ci_hi","permutation_q_bh"]].to_markdown(index=False),
        "",
        "## Strict sign reversals (eligible pairs)",
        "",
        (rev[["algorithm_a","algorithm_b","delta_cc18","cc18_ci_lo","cc18_ci_hi","delta_outside","outside_ci_lo","outside_ci_hi","bootstrap_opposite_sign_prob","permutation_q_bh"]].to_markdown(index=False) if len(rev) else "None."),
        "",
        "## Interpretation guardrail",
        "",
        "These are task-level stratum interactions and winner reversals, not by themselves causal effects of benchmark curation. The causal/transport claim still requires explicit target-frame and support assumptions plus the metadata/support audit.",
    ]
    (outdir/"SUMMARY.md").write_text("\n".join(lines),encoding="utf-8")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--upstream",required=True)
    ap.add_argument("--out",default="results")
    ap.add_argument("--bootstrap",type=int,default=5000)
    ap.add_argument("--permutations",type=int,default=5000)
    args=ap.parse_args()
    upstream=Path(args.upstream)
    outdir=Path(args.out); outdir.mkdir(parents=True,exist_ok=True)
    csv=upstream/"tabzilla_analysis/final-selected18-algs/cleaned_results/tuned_aggregated_results.csv"
    if not csv.exists():
        raise FileNotFoundError(csv)
    df=pd.read_csv(csv)
    need={"alg_name","dataset_name"}
    missing=need-set(df.columns)
    if missing: raise ValueError(f"missing columns: {missing}")
    df["task_id"]=df.dataset_name.map(task_id_from_name)
    bad=df[df.task_id.isna()]
    if len(bad):
        bad.to_csv(outdir/"rows_without_task_id.csv",index=False)
    df=df[df.task_id.notna()].copy(); df.task_id=df.task_id.astype(int)
    df["in_cc18"]=df.task_id.isin(CC18)

    # Core provenance/coverage outputs.
    dataset_map=df[["task_id","dataset_name"]].drop_duplicates().sort_values(["task_id","dataset_name"])
    dataset_map.to_csv(outdir/"task_dataset_map.csv",index=False)
    cov=(df.groupby("alg_name").agg(rows=("task_id","size"),tasks=("task_id","nunique"),cc18_tasks=("in_cc18","sum"))
         .reset_index().sort_values("alg_name"))
    cov.to_csv(outdir/"algorithm_coverage.csv",index=False)

    pair_frames=[]; alg_frames=[]
    for metric,(col,orient) in METRICS.items():
        if col not in df.columns:
            continue
        pair_frames.append(pairwise_metric(df,col,orient,metric,args.bootstrap,args.permutations))
        alg_frames.append(algorithm_stratum_summary(df,col,orient,metric))
    pair_all=pd.concat(pair_frames,ignore_index=True)
    alg_all=pd.concat(alg_frames,ignore_index=True)
    pair_all.to_csv(outdir/"pairwise_interactions_all_metrics.csv",index=False)
    alg_all.to_csv(outdir/"algorithm_stratum_means.csv",index=False)

    # Accuracy-focused convenience tables.
    acc=pair_all[pair_all.metric=="accuracy"].copy()
    acc.to_csv(outdir/"accuracy_pairwise_interactions.csv",index=False)
    for m in [5,10,15,20,25,30]:
        z=acc[(acc.n_cc18_common>=m)&(acc.n_outside_common>=m)].copy()
        z.to_csv(outdir/f"accuracy_pairs_min{m}_per_stratum.csv",index=False)

    # Cross-stratum algorithm ordering based on all available tasks per algorithm, descriptive only.
    wide=[]
    for flag,label in [(True,"cc18"),(False,"outside")]:
        g=df[df.in_cc18==flag].groupby("alg_name")["Accuracy__test_mean"].agg(["mean","count"]).reset_index()
        g["stratum"]=label; wide.append(g)
    means=pd.concat(wide,ignore_index=True)
    means.to_csv(outdir/"accuracy_algorithm_means_by_stratum.csv",index=False)
    p=means.pivot(index="alg_name",columns="stratum",values="mean").dropna()
    if len(p)>=3:
        rho=stats.spearmanr(p.cc18,p.outside)
        tau=stats.kendalltau(p.cc18,p.outside)
        ordering={"spearman_rho":float(rho.statistic),"spearman_p":float(rho.pvalue),"kendall_tau":float(tau.statistic),"kendall_p":float(tau.pvalue)}
    else: ordering={}

    inv=meta_inventory(upstream,outdir)
    man=manifest_inventory(upstream,outdir)
    summary={
        "source_csv":str(csv),
        "source_csv_sha256":hashlib.sha256(csv.read_bytes()).hexdigest(),
        "rows":int(len(df)),
        "algorithms":int(df.alg_name.nunique()),
        "algorithm_names":sorted(map(str,df.alg_name.unique())),
        "tasks":int(df.task_id.nunique()),
        "dataset_name_labels":int(df.dataset_name.nunique()),
        "cc18_tasks_observed":int(df.loc[df.in_cc18,"task_id"].nunique()),
        "outside_tasks_observed":int(df.loc[~df.in_cc18,"task_id"].nunique()),
        "missing_cc18_tasks":sorted(CC18-set(df.task_id.unique())),
        "ordering_descriptive":ordering,
        "metafeature_candidate_files":int(len(inv)),
        "manifest_inventory":man,
        "bootstrap_reps":args.bootstrap,
        "permutation_reps":args.permutations,
    }
    (outdir/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    write_markdown_summary(outdir,summary,pair_all)
    print(json.dumps(summary,indent=2))
    print((outdir/"SUMMARY.md").read_text())

if __name__=="__main__":
    main()
