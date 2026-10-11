#!/usr/bin/env python3
"""Controlled paired measurements before and after ordinary part consolidation."""
import csv, hashlib, json, pathlib, random, statistics, time
from collections import defaultdict
import db_natural_merge_decision_lifetime as X

P=X.P; S=X.S
R=pathlib.Path("s41")
R.mkdir(exist_ok=True)
X.R=P.R=S.ROOT=S.base.ROOT=R
S.CT="s41_ch"
S.VOL="s41_vol"
S.base.VERSIONS=[S.OLD,S.OLD]
TABLES=("old50","new50")
ARMS=(("on_cached",1,1),("on_uncached",1,0),("disabled",0,1))
Q=range(1,14)
N=8

def out(path,obj):
    target=R/path
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")

def measure(phase,queries):
    hashes={}
    for i in Q:
        expected=None
        for t in TABLES:
            for arm,u,c in ARMS:
                result=P.sql(P.qstr(queries[i-1],t,u,c)+" FORMAT TSV")
                digest=hashlib.sha256(result.encode()).hexdigest()
                hashes[f"{i}/{t}/{arm}"]=digest
                if expected is None:expected=digest
                elif expected!=digest:raise RuntimeError(f"Mismatch in SQL {i}, {t}, {arm}")
    out(phase+"/answers.json",hashes)
    tasks=[]
    for round_id in range(-1,N):
        rng=random.Random(410000+round_id)
        qs=list(Q);rng.shuffle(qs)
        for i in qs:
            arms=list(ARMS);rng.shuffle(arms)
            for arm,u,c in arms:
                order=list(TABLES)
                if rng.randrange(2):order.reverse()
                for t in order:
                    tasks.append((round_id,i,t,arm,u,c))
    pending=[]
    for k,(round_id,i,t,arm,u,c) in enumerate(tasks):
        qid=f"c41_{phase}_{k}"
        P.sql(P.qstr(queries[i-1],t,u,c),qid=qid,fmt="Null")
        pending.append({"phase":phase,"round":round_id,"q":i,"table":t,"arm":arm,"qid":qid,"order_index":k})
        if (k+1)%180==0:print("C41_PROGRESS",phase,k+1,len(tasks),flush=True)
    P.sql("SYSTEM FLUSH LOGS")
    raw=P.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c41_"+phase+"_') FORMAT JSONEachRow")
    (R/phase/"query_log.jsonl").write_text(raw)
    by_id={z["query_id"]:z for line in raw.splitlines() if line for z in [json.loads(line)]}
    if len(by_id)!=len(pending):raise RuntimeError(f"Missing receipts {len(by_id)} versus {len(pending)}")
    for row in pending:
        v=by_id[row["qid"]]; evt=v.get("ProfileEvents") or {}
        row.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],read_bytes=v["read_bytes"],
             stat_us=evt.get("LoadedStatisticsMicroseconds",0),
             optimize_us=evt.get("QueryPlanOptimizeMicroseconds",0),
             file_opens=evt.get("FileOpen",0))
    with (R/phase/"receipts.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=pending[0].keys());w.writeheader();w.writerows(pending)
    rows=[r for r in pending if r["round"]>=0]
    med={}
    for t in TABLES:
        for arm,_,_ in ARMS:
            for i in Q:
                sub=[r for r in rows if r["table"]==t and r["arm"]==arm and r["q"]==i]
                assert len(sub)==N
                med[(t,arm,i)]={key:statistics.median(r[key] for r in sub) for key in ("ms","read_rows","read_bytes","stat_us","optimize_us","file_opens")}
    results=[]
    for arm,_,_ in ARMS:
        qs={}
        for i in Q:
            a=med[("old50",arm,i)];b=med[("new50",arm,i)]
            qs[i]={"old_ms":a["ms"],"new_ms":b["ms"],"ratio":b["ms"]/max(a["ms"],.001),
                "old_stat_us":a["stat_us"],"new_stat_us":b["stat_us"],
                "old_read_rows":a["read_rows"],"new_read_rows":b["read_rows"],
                "old_optimize_us":a["optimize_us"],"new_optimize_us":b["optimize_us"]}
        families=[list(range(1,4)),list(range(4,7)),list(range(7,11)),list(range(11,14))]
        family_means={t:statistics.mean([statistics.mean(med[(t,arm,i)]["ms"] for i in fam) for fam in families]) for t in TABLES}
        old,new=family_means["old50"],family_means["new50"]
        n15=sum(qs[i]["ratio"]>=1.5 for i in Q)
        results.append({"phase":phase,"arm":arm,"old_ms":old,"new_ms":new,"ratio":new/old,
           "q_ge_1_5":n15,"gate":"BLOCK" if new/old>1.10 or n15>=2 else "APPROVE","sql":qs})
    out(phase+"/paired_results.json",results)
    print("C41_RESULTS",json.dumps([{k:v for k,v in row.items() if k!="sql"} for row in results]),flush=True)
    return results

def main():
    for ver in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=500)
    queries=S.base.pinned_sources()
    native=S.base.make_data()
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    all_results=[]
    try:
        S.start(S.NEW)
        S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
        cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
        for t in ("raw","delta50")+TABLES:P.create(t,cols)
        with open(native,"rb") as f:
            proc=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
                  "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
                  stdin=f,check=False,timeout=950)
        if proc.returncode:raise RuntimeError(proc.stderr.decode(errors="replace")[-1000:])
        native.unlink()
        n=6001215//2
        P.sql(f"INSERT INTO ssb.delta50 SELECT * FROM ssb.raw ORDER BY LO_ORDERKEY,LO_LINENUMBER LIMIT {n} SETTINGS materialize_statistics_on_insert=0,max_threads=2")
        for t in TABLES:P.insert(t,"raw",0)
        P.insert("old50","delta50",0)
        P.insert("new50","delta50",1)
        defaults=P.sql("SELECT name,value FROM system.settings WHERE name IN ('materialize_statistics_on_insert','materialize_statistics_on_merge','use_statistics','use_statistics_cache') ORDER BY name FORMAT TSV")
        (R/"settings.tsv").write_text(defaults)
        pre=X.stable_snapshot("pre")
        assert pre["old50"]["rows"]==pre["new50"]["rows"]==9001822
        assert pre["old50"]["parts"]==pre["new50"]["parts"]
        assert pre["old50"]["stat_files"]==0
        assert pre["new50"]["stat_files"]>0
        all_results+=measure("pre",queries)
        if X.stable_snapshot("pre_after_measure")!=pre:raise RuntimeError("Pre measurement altered disk")
        X.thaw()
        trace=[]
        reached=X.watch("consolidation",240,35,trace)
        out("merge_trace.json",trace)
        if not reached:raise RuntimeError("Ordinary merging failed to reach <=35 parts per table")
        post=X.stable_snapshot("post")
        if any(post[t]["parts"]>=pre[t]["parts"] for t in TABLES):raise RuntimeError("No reduction")
        all_results+=measure("post",queries)
        if X.stable_snapshot("post_after_measure")!=post:raise RuntimeError("Post measurement altered disk")
        out("FINAL_STATUS.json",{"valid":True,"source":"pinned ssb sf1","rows":9001822,
            "repetitions":N,"pre":pre,"post":post,"results":all_results,
            "limitations":["Single physical runner","Background merges paused at measurement checkpoints",
                "Rows replayed from same SF1 source","No claim about performance during active merges",
                "Settings and data are inspected at each checkpoint"]})
        print("C41_FINAL_SUCCESS",flush=True)
    finally:
        S.run(["docker","rm","-f",S.CT],check=False,timeout=90)
if __name__=="__main__":main()
