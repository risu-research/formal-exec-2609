#!/usr/bin/env python3
"""c42: dated partitions with ordinary maintenance and paired old/new data paths.

The treatment contrasts are pre-specified: preserve-and-append, preserve-and-
append with legacy insert policy, inherited complete table, and target rebuild.
No forced OPTIMIZE and no hidden retries of an unfavorable benchmark result.
"""
import csv, hashlib, json, pathlib, random, statistics, time
from collections import defaultdict
import db_mixed_history_upgrade as P

S=P.S
R=pathlib.Path("s42")
R.mkdir(exist_ok=True)
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c42_ch"
S.VOL="c42_vol"
TABLES=("ref","hist","legacy","fresh")
ARMS=(("cache_on",1,1),("cache_off",1,0),("stats_off",0,1))
FAMILIES=(range(1,4),range(4,7),range(7,11),range(11,14))
RUNS=8

def log(*args):
    print("C42",*args,flush=True)

def sql(query,qid=None,fmt=None):
    return P.sql(query,qid=qid,fmt=fmt)

def save(path,content):
    p=R/path
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(content)

def dump(path,data):
    save(path,json.dumps(data,indent=2,sort_keys=True)+"\n")

def create(t,cols):
    sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree "
        "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")

def insert(t,predicate,mode=None):
    clause="" if mode is None else f"materialize_statistics_on_insert={mode},"
    query=("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw WHERE "+predicate
           +" SETTINGS "+clause+"max_threads=2,max_insert_threads=1,"
           "min_insert_block_size_rows=100000,min_insert_block_size_bytes=1")
    start=time.monotonic()
    sql(query)
    log("INSERT",t,predicate,mode,"elapsed_s",round(time.monotonic()-start,2))

def wait_idle(tables,limit=90):
    start=time.monotonic()
    idle=0
    names=",".join("'"+t+"'" for t in tables)
    while time.monotonic()-start < limit:
        n=int(sql("SELECT count() FROM system.merges WHERE database='ssb' "
                  "AND table IN ("+names+") FORMAT TSV").strip())
        idle=idle+1 if n==0 else 0
        if idle>=3:return {"idle":True,"seconds":round(time.monotonic()-start,2)}
        time.sleep(2)
    return {"idle":False,"seconds":round(time.monotonic()-start,2)}

def stop(tables):
    for t in tables:sql("SYSTEM STOP MERGES ssb."+t)
    if not wait_idle(tables,60)["idle"]:
        raise RuntimeError("Merges active after stop; invalid frozen checkpoint")

def resume(tables):
    for t in tables:sql("SYSTEM START MERGES ssb."+t)

def partmap(label,tables,cutoff):
    physical={}
    cutoff_year=int(cutoff)//100
    cutoff_month=int(cutoff)%100
    for t in tables:
        raw=sql("SELECT partition,path,rows FROM system.parts WHERE active AND database='ssb' "
                "AND table='"+t+"' ORDER BY partition,name FORMAT TSV")
        inp="\n".join("\t".join(row.split("\t")[:2]) for row in raw.splitlines())+"\n"
        script='while IFS="$(printf "\\t")" read -r part path; do if test -f "$path/statistics.packed"; then echo "$part 1"; else echo "$part 0"; fi; done'
        proc=S.run(["docker","exec","-i",S.CT,"bash","-c",script],
                   inp=inp.encode(),timeout=150)
        flags=proc.stdout.decode().splitlines()
        data=raw.splitlines()
        if len(flags)!=len(data):raise RuntimeError("Metadata inventory missing "+t)
        entries=[]
        for row,flag in zip(data,flags):
            month,path,rows=row.split("\t")
            fm,has=flag.split()
            if fm!=month:raise RuntimeError("Flag alignment "+t)
            entries.append({"month":int(month),"rows":int(rows),"stats":int(has)})
        physical[t]={
            "parts":len(entries),
            "stats_parts":sum(e["stats"] for e in entries),
            "old_parts":sum(e["month"]<cutoff for e in entries),
            "old_stats_parts":sum(e["stats"] for e in entries if e["month"]<cutoff),
            "new_parts":sum(e["month"]>=cutoff for e in entries),
            "new_stats_parts":sum(e["stats"] for e in entries if e["month"]>=cutoff),
            "rows":sum(e["rows"] for e in entries),
            "per_month":entries,
            "fingerprint":P.fp(t)
        }
    dump(label+"/state.json",physical)
    log("STATE",label,[(k,v["parts"],v["stats_parts"],v["old_stats_parts"],
                         v["new_stats_parts"]) for k,v in physical.items()])
    return physical

def pick_cutoff():
    raw=sql("SELECT toYYYYMM(LO_ORDERDATE),count() FROM ssb.raw "
            "GROUP BY toYYYYMM(LO_ORDERDATE) ORDER BY 1 FORMAT TSV")
    months=[(int(a),int(b)) for a,b in
            (line.split("\t") for line in raw.splitlines())]
    total=sum(n for _,n in months)
    if total!=6001215 or len(months)<20:raise RuntimeError("Unexpected date distribution")
    valid=[]
    old=0
    for i,(month,count) in enumerate(months):
        if i>=8 and (len(months)-i)>=8:
            valid.append((abs(old-total/2),month,old,total-old))
        old+=count
    _,cutoff,old,recent=min(valid)
    if min(old,recent)<int(total*.25):raise RuntimeError("No balanced natural date cutoff")
    dump("calendar.json",{"months":months,"cutoff":cutoff,"old_rows":old,
                          "new_rows":recent,"full_rows":total,
                          "selection":"median-by-cumulative-volume, prior to any performance measurements"})
    log("CUTOFF",cutoff,old,recent,len(months))
    return cutoff,old,recent

def measure(phase,tables,queries,nreps):
    hashes={}
    for qi in range(1,14):
        expect=None
        for t in tables:
            for arm,u,c in ARMS:
                ans=sql(P.qstr(queries[qi-1],t,u,c)+" FORMAT TSV")
                digest=hashlib.sha256(ans.encode()).hexdigest()
                if expect is None: expect=digest
                if digest!=expect:raise RuntimeError(f"Different query answer {qi}/{t}/{arm}")
                hashes[f"{qi}/{t}/{arm}"]=digest
    dump(phase+"/answers.json",hashes)
    receipts=[]
    for rnd in range(-1,nreps):
        rand=random.Random(42000+rnd)
        qlist=list(range(1,14))
        rand.shuffle(qlist)
        arm_orders=list(ARMS)
        rand.shuffle(arm_orders)
        for qi in qlist:
            for arm_index,(arm,u,c) in enumerate(arm_orders):
                offset=(qi+arm_index+rnd)%len(tables)
                order=list(tables)[offset:]+list(tables)[:offset]
                for t in order:
                    query_id=f"c42_{phase}_{len(receipts)}"
                    sql(P.qstr(queries[qi-1],t,u,c),qid=query_id,fmt="Null")
                    receipts.append({"phase":phase,"round":rnd,"sql":qi,"table":t,
                                     "arm":arm,"qid":query_id,
                                     "order":len(receipts)})
        log("ROUND",phase,rnd,len(receipts))
    sql("SYSTEM FLUSH LOGS")
    raw=sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
            "FROM system.query_log WHERE type='QueryFinish' "
            "AND startsWith(query_id,'c42_"+phase+"_') FORMAT JSONEachRow")
    save(phase+"/query_log.jsonl",raw)
    by_id={v["query_id"]:v for l in raw.splitlines() if l for v in [json.loads(l)]}
    if len(by_id)!=len(receipts):raise RuntimeError("Query log incomplete")
    for r in receipts:
        v=by_id[r["qid"]]
        ev=v.get("ProfileEvents") or {}
        r.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],
                 read_bytes=v["read_bytes"],
                 stat_us=ev.get("LoadedStatisticsMicroseconds",0),
                 opt_us=ev.get("QueryPlanOptimizeMicroseconds",0),
                 selected_marks=ev.get("SelectedMarks",0))
    with (R/phase/"receipts.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(receipts[0].keys()))
        w.writeheader();w.writerows(receipts)
    med={}
    samples=[r for r in receipts if r["round"]>=0]
    for t in tables:
        for arm,_,_ in ARMS:
            for qi in range(1,14):
                subset=[r for r in samples if (r["table"],r["arm"],r["sql"])==(t,arm,qi)]
                if len(subset)!=nreps:raise RuntimeError("Missing replicates")
                med[(t,arm,qi)]={k:statistics.median(r[k] for r in subset)
                     for k in ("ms","stat_us","opt_us","read_rows","read_bytes","selected_marks")}
    def weighted(t,arm):
        return statistics.mean([statistics.mean(med[(t,arm,i)]["ms"] for i in fam)
                                for fam in FAMILIES])
    comparisons=[]
    pairs=[("hist_vs_legacy","legacy","hist"),
           ("hist_vs_inherited","ref","hist"),
           ("rebuild_vs_history","hist","fresh"),
           ("fresh_vs_inherited","ref","fresh")]
    pairs=[(name,a,b) for name,a,b in pairs if a in tables and b in tables]
    for arm,_,_ in ARMS:
        for name,a,b in pairs:
            wa,wb=weighted(a,arm),weighted(b,arm)
            qrat={str(q):round(med[(b,arm,q)]["ms"]/max(.001,med[(a,arm,q)]["ms"]),6)
                   for q in range(1,14)}
            n15=sum(v>=1.5 for v in qrat.values())
            comparisons.append({"phase":phase,"comparison":name,"arm":arm,
                                "ratio":wb/wa,"old_ms":wa,"new_ms":wb,
                                "q_ge_1_5":n15,"gate":"BLOCK" if wb/wa>1.10 or n15>=2 else "APPROVE",
                                "per_sql":qrat})
    dump(phase+"/comparisons.json",comparisons)
    log("GATES",phase,[(r["comparison"],r["arm"],round(r["ratio"],3),r["gate"])
                       for r in comparisons])
    return comparisons

def main():
    for ver in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=500)
    qs=S.base.pinned_sources()
    native=S.base.make_data()
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    old_tables=("ref","hist","legacy")
    try:
        S.start(S.OLD)
        S.run(["docker","exec",S.CT,"clickhouse-client","--query",
               "CREATE DATABASE IF NOT EXISTS ssb"])
        columns=(R/"official/create/schema/lineorder_flat.columns").read_text()
        P.create("raw",columns)  # source-only staging table, not a measured treatment
        for t in old_tables:create(t,columns)
        with native.open("rb") as f:
            p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
                "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
                stdin=f,check=False,timeout=950)
        if p.returncode:raise RuntimeError("Source import failed "+p.stderr.decode(errors="replace")[-1200:])
        native.unlink()
        cutoff,nold,nnew=pick_cutoff()
        predicate=f"toYYYYMM(LO_ORDERDATE)<{cutoff}"
        insert("ref","1",0)
        insert("hist",predicate,0)
        insert("legacy",predicate,0)
        before_idle=wait_idle(old_tables)
        if not before_idle["idle"]:raise RuntimeError("Old phase did not quiesce")
        stop(old_tables)
        before=partmap("old_state",old_tables,cutoff)
        if before["hist"]["rows"]!=nold or before["legacy"]["rows"]!=nold:
            raise RuntimeError("Old slice mismatch")
        if before["ref"]["rows"]!=nold+nnew:raise RuntimeError("Original reference mismatch")
        before_results=measure("old_engine",("ref",),qs,4)
        resume(old_tables)
        S.run(["docker","stop","-t","90",S.CT],timeout=150)
        S.run(["docker","rm",S.CT],timeout=50)
        S.start(S.NEW)
        create("fresh",columns)
        post_version=partmap("new_engine_before_writes",old_tables,cutoff)
        insert("hist",f"toYYYYMM(LO_ORDERDATE)>={cutoff}",None)
        insert("legacy",f"toYYYYMM(LO_ORDERDATE)>={cutoff}",0)
        insert("fresh","1",None)
        idle=wait_idle(TABLES,120)
        if not idle["idle"]:raise RuntimeError("Natural merges not quiescent")
        stop(TABLES)
        initial=partmap("post_ingest",TABLES,cutoff)
        fprints={v["fingerprint"] for v in initial.values()}
        if len(fprints)!=1 or any(v["rows"]!=6001215 for v in initial.values()):
            raise RuntimeError("Logical rows or fingerprints diverge")
        results=measure("post_ingest",TABLES,qs,RUNS)
        if partmap("post_ingest_after_measure",TABLES,cutoff)!=initial:
            raise RuntimeError("Physical state mutated during first checkpoint")
        resume(TABLES)
        time.sleep(25)
        later_idle=wait_idle(TABLES,110)
        if not later_idle["idle"]:raise RuntimeError("Natural maintenance not quiescent")
        stop(TABLES)
        later=partmap("later",TABLES,cutoff)
        if any(v["fingerprint"]!=initial[t]["fingerprint"] for t,v in later.items()):
            raise RuntimeError("Logical change during aging")
        later_results=measure("later",TABLES,qs,4)
        if partmap("later_after_measure",TABLES,cutoff)!=later:
            raise RuntimeError("State mutated during follow-up")
        settings=sql("SELECT name,value FROM system.settings WHERE name IN "
                     "('materialize_statistics_on_insert','materialize_statistics_on_merge',"
                     "'auto_statistics_types','materialize_statistics_on_insert_max_table_size') "
                     "ORDER BY name FORMAT TSV")
        save("settings.tsv",settings)
        dump("FINAL_STATUS.json",{
            "valid":True,"source":"Pinned ClickBench SSB SF1","rows":6001215,
            "cutoff_month":cutoff,"old_rows":nold,"new_rows":nnew,
            "old_engine":"26.7.1.1315","new_engine":"26.8.1.2041",
            "before_idle":before_idle,"after_idle":idle,"later_idle":later_idle,
            "old_state":before,"post_upgrade_before_writes":post_version,
            "post_ingest":initial,"later":later,
            "comparisons_post_ingest":results,"comparisons_later":later_results,
            "limitations":["Historical and target tables are distinct controlled deployment operations",
              "Monthly partitioning of SF1 is not automatically an optimal production schema",
              "Merges enabled during writes and idle intervals, paused symmetrically for measurements",
              "New records have later dates from the original SF1 source; they are not newly collected real orders",
              "One GitHub runner; independent host replication remains necessary"]})
        log("FINAL_SUCCESS",cutoff)
    finally:
        S.run(["docker","rm","-f",S.CT],check=False,timeout=90)

if __name__=="__main__":main()
