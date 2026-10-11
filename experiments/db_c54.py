#!/usr/bin/env python3
"""Frozen active parts, process-reset cold / warm / cache-bypass cross-over."""
import os,json,csv,hashlib,pathlib,random,statistics
from collections import defaultdict
os.environ["C52_LAYOUT"]="monthly"
import db_c53 as G
B=G.B
T=G.T
N=G.BATCHES
assert N in (1,8)
R=pathlib.Path("s54_batch"+str(N))
R.mkdir(exist_ok=True)
G.R=R
T.R=R
B.R=R
B.P.R=R
B.S.ROOT=R
B.S.base.ROOT=R
B.S.base.VERSIONS=[B.S.OLD,B.S.OLD]
B.S.CT="c54_"+str(N)+"_ch"
B.S.VOL="c54_"+str(N)+"_vol"
B.RUNS=6
Q="D4_keys_and_line_prices"
TABLES=("ref","hist","fresh")
CONDS=("cold","warm1","bypass","warm2","stats_off")
ARMS={"cold":"cache_on","warm1":"cache_on","bypass":"cache_off",
      "warm2":"cache_on","stats_off":"stats_off"}

def med(xs):
    return statistics.median(xs)

def physical():
    data=B.sql("SELECT table,name,partition,rows,marks,bytes_on_disk,path "
        "FROM system.parts WHERE active AND database='ssb' "
        "AND table IN ('ref','hist','legacy','fresh') ORDER BY table,name FORMAT TSV")
    paths="\n".join(v.split("\t")[-1] for v in data.splitlines())+"\n"
    cmd='set -euo pipefail; while IFS= read -r p; do sha256sum "$p/checksums.txt"; if test -f "$p/statistics.packed"; then sha256sum "$p/statistics.packed"; fi; done'
    result=B.S.run(["docker","exec","-i",B.S.CT,"bash","-c",cmd],
                    inp=paths.encode(),timeout=180)
    return {"hash":hashlib.sha256(data.encode()+b"\0"+result.stdout).hexdigest(),
            "parts":len(data.splitlines()),
            "stats_files":result.stdout.count(b"statistics.packed"),
            "part_manifest_hash":hashlib.sha256(result.stdout).hexdigest()}

def reset(expected,phase,i):
    B.S.start(B.S.NEW)
    for tab in B.TABLES:
        B.sql("SYSTEM STOP MERGES ssb."+tab)
    state=physical()
    if state!=expected:
        B.dump(phase+"/reset_changed_"+str(i)+".json",state)
        raise RuntimeError("ACTIVE_PARTS_CHANGED_DURING_RESTART "+phase+" cycle="+str(i))
    return state

def get_logs(phase):
    B.sql("SYSTEM FLUSH LOGS")
    raw=B.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
      "FROM system.query_log WHERE type='QueryFinish' AND "
      "startsWith(query_id,'c54_"+str(N)+"_"+phase+"_') FORMAT JSONEachRow")
    return {v["query_id"]:v for line in raw.splitlines() if line for v in [json.loads(line)]}

def bootstrap(ds,seed):
    rng=random.Random(seed)
    n=len(ds)
    if n<2:return [None,None]
    draws=sorted(med([ds[rng.randrange(n)] for _ in range(n)]) for _ in range(1000))
    return [draws[24],draws[974]]

def analyze(phase,receipts,cycles):
    by=defaultdict(dict)
    for x in receipts:
        by[(x["table"],x["condition"])][x["cycle"]]=x
    profiles=[]
    for t in TABLES:
        for cond in CONDS:
            rs=[by[(t,cond)][i] for i in range(cycles)]
            profiles.append({"table":t,"condition":cond,"phase":phase,"n":cycles,
              "median_ms":med([x["ms"] for x in rs]),
              "median_loaded_statistics_ms":med([x["stat_us"]/1000 for x in rs]),
              "median_read_rows":med([x["read_rows"] for x in rs])})
    contrasts=[]
    for t in TABLES:
        for title,a,b in (("cold_minus_warm","cold","warm1"),
                          ("bypass_minus_warm","bypass","warm1"),
                          ("warm2_minus_warm1","warm2","warm1"),
                          ("cold_minus_off","cold","stats_off")):
            ds=[by[(t,a)][i]["ms"]-by[(t,b)][i]["ms"] for i in range(cycles)]
            es=[(by[(t,a)][i]["stat_us"]-by[(t,b)][i]["stat_us"])/1000
                for i in range(cycles)]
            contrasts.append({"table":t,"contrast":title,"phase":phase,"n":cycles,
                "median_paired_ms":med(ds),"ci95_ms":bootstrap(ds,5410+len(t)+len(title)),
                "median_statistics_loading_difference_ms":med(es),
                "positive_fraction":sum(d>0 for d in ds)/cycles})
    decisions=[]
    for t in ("hist","fresh"):
        for cond in CONDS:
            base=next(x for x in profiles if x["table"]=="ref" and x["condition"]==cond)
            target=next(x for x in profiles if x["table"]==t and x["condition"]==cond)
            delta=target["median_ms"]-base["median_ms"]
            ratio=target["median_ms"]/max(1,base["median_ms"])
            item={"phase":phase,"table":t,"condition":cond,
              "baseline_ms":base["median_ms"],"target_ms":target["median_ms"],
              "delta_ms":delta,"ratio":ratio,"passes_30ms_and_10pct":delta>=30 and ratio>=1.10,
              "load_delta_ms":target["median_loaded_statistics_ms"]-base["median_loaded_statistics_ms"],
              "read_rows_delta":target["median_read_rows"]-base["median_read_rows"]}
            decisions.append(item)
            B.log("C54_DECISION",phase,t,cond,"delta",delta,"ratio",round(ratio,3),
                  "load",round(item["load_delta_ms"],3),item["passes_30ms_and_10pct"])
    B.dump(phase+"/profiles.json",profiles)
    B.dump(phase+"/paired_contrasts.json",contrasts)
    B.dump(phase+"/history_decisions.json",decisions)

def measure(phase,tables,queries,reps):
    if phase=="old_engine":
        B.dump(phase+"/skipped.json",{"reason":"single inherited table only"})
        return []
    if tuple(tables)!=B.TABLES:raise RuntimeError("unexpected tables")
    T.check_originals(phase,tables,queries)
    T.verify_new_answers(phase,tables,[Q])
    B.dump("SELECTED.json",{"selected":[Q],"fixed_by":"previous study Stage 52"})
    baseline=physical()
    B.dump(phase+"/frozen_physical.json",baseline)
    cycles=6 if phase=="post_ingest" else 4
    rec=[]
    for cy in range(cycles):
        reset(baseline,phase,cy)
        order=list(TABLES)
        random.Random(5400+cy).shuffle(order)
        for t in order:
            for cond in CONDS:
                qid="c54_"+str(N)+"_"+phase+"_"+str(cy)+"_"+t+"_"+cond
                T.sq(Q,t,ARMS[cond],qid=qid,fmt="Null")
                rec.append({"phase":phase,"cycle":cy,"table":t,
                            "condition":cond,"query_id":qid})
        B.log("C54_CYCLE",phase,cy+1)
        if physical()!=baseline:raise RuntimeError("PARTS_CHANGED_DURING_CYCLE")
    log=get_logs(phase)
    for x in rec:
        if x["query_id"] not in log:raise RuntimeError("MISSING_QUERY_RECEIPT")
        v=log[x["query_id"]]
        e=v.get("ProfileEvents") or {}
        x.update(ms=v["query_duration_ms"],stat_us=e.get("LoadedStatisticsMicroseconds",0),
                 read_rows=v["read_rows"],read_bytes=v["read_bytes"],
                 opt_us=e.get("QueryPlanOptimizeMicroseconds",0))
    with (R/phase/"receipts.csv").open("w",newline="") as fp:
        writer=csv.DictWriter(fp,fieldnames=list(rec[0]))
        writer.writeheader();writer.writerows(rec)
    analyze(phase,rec,cycles)
    if physical()!=baseline:raise RuntimeError("PARTS_CHANGED_AT_END")
    B.dump(phase+"/validation.json",{"valid":True,"physical_unchanged":True,
      "cycles":cycles,"receipts":len(rec),"original_sqls_verified":13,
      "new_sql_verified":Q,"frozen_physical":baseline,
      "caveat":"restart also resets non-statistics caches"})
    return []

B.measure=measure
if __name__=="__main__":
    B.dump("PREREGISTERED.json",{
      "batch_count":N,"query":Q,"selected_before_study":True,
      "arms":ARMS,"cold":"process restart same persistent volume",
      "warm":"repeat identical SQL with statistics cache enabled",
      "bypass":"use_statistics_cache=0",
      "negative_control":"use_statistics=0",
      "cycles_initial":6,"cycles_later":4,
      "fixed_physical":"system.parts + checksums manifests and stats files",
      "material_regr_ms":30,"material_regr_ratio":1.10,
      "limitations":["restart resets other caches","single runner for each batch",
        "intervention fails if background merges change any active part",
        "original query outputs independently checked"]})
    import sys
    if "--preflight" in sys.argv:
        assert N in (1,8) and B.RUNS==6 and len(T.C.GOLDEN_ANSWERS)==13
        assert Q in T.SQL_CANDIDATES and len(CONDS)==5
        print("C54_PREFLIGHT_OK",N,flush=True)
    else:
        B.main()
        p=R/"FINAL_STATUS.json"
        v=json.loads(p.read_text())
        v["study"]="54-cache-cross-over"
        v["batches"]=N
        p.write_text(json.dumps(v,sort_keys=True,indent=2)+"\n")
        print("C54_FINAL_SUCCESS",N,flush=True)
