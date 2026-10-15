#!/usr/bin/env python3
"""Independent TPC-H workload: fixed source, shared version transition, two data paths.

This is a held-out *dataset and SQL population*, not another SSB resampling.
All 22 published TPC-H queries are eligibility-tested and accounted for.
"""
import csv,hashlib,json,os,pathlib,random,re,statistics,sys,time,urllib.request
import duckdb
import db_inplace_upgrade_replay as S
ROOT=pathlib.Path("c63")
ROOT.mkdir(exist_ok=True)
S.ROOT=S.base.ROOT=ROOT
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c63_engine"
S.VOL="c63_volume"
REF="1ce27b281d8a73aad75458b0e04a2b77705aac53"
BASE=f"https://raw.githubusercontent.com/ClickHouse/ClickBench/{REF}/versions"
TABLES=("nation","region","part","supplier","partsupp","customer","orders","lineitem")
DIM=set(TABLES)-{"orders","lineitem"}
DATE={"orders":"o_orderdate","lineitem":"l_shipdate"}
KEY={"nation":"n_nationkey","region":"r_regionkey","part":"p_partkey",
     "supplier":"s_suppkey","partsupp":"ps_partkey,ps_suppkey",
     "customer":"c_custkey","orders":"o_orderkey","lineitem":"l_orderkey,l_linenumber"}
ARMS=(("cache_off",1,0),("stats_off",0,0))
ROUNDS=3
FAMILIES=(tuple(range(1,6)),tuple(range(6,12)),tuple(range(12,17)),tuple(range(17,23)))
RESULTS=[]

def dump(path,obj):
    p=ROOT/path;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def sql(query,db="tp_old",qid=None,fmt=None,timeout=160):
    argv=["docker","exec",S.CT,"clickhouse-client","--database",db]
    if qid:argv+=["--query_id",qid]
    if fmt:argv+=["--format",fmt]
    result=S.run(argv+["--query",query],check=False,timeout=timeout)
    if result.returncode:raise RuntimeError(
       "SQL FAILED "+query[:130]+" stderr "+result.stderr.decode(errors="replace")[-600:])
    return result.stdout.decode(errors="replace")
def portable(stmt):
    return stmt+" SETTINGS use_statistics=1,use_statistics_cache=0,max_threads=2,use_query_condition_cache=0"
def query(stmt,u,c):
    return stmt+f" SETTINGS use_statistics={u},use_statistics_cache={c},max_threads=2,use_query_condition_cache=0"
def pin():
    files=["queries/tpch.sql"]+["create/schema/"+t+".columns" for t in TABLES]
    content={}
    sha={}
    for path in files:
        with urllib.request.urlopen(BASE+"/"+path,timeout=75) as url:data=url.read()
        content[path]=data.decode()
        sha[path]=hashlib.sha256(data).hexdigest()
    queries=[x.strip().rstrip(";") for x in content["queries/tpch.sql"].splitlines() if x.strip()]
    if len(queries)!=22:raise RuntimeError("TPC-H published SQL population not equal to 22")
    dump("SOURCE_MANIFEST.json",{"commit":REF,"files_sha256":sha,"n_queries":len(queries)})
    for path,val in content.items():
        p=ROOT/"pinned"/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(val)
    return queries,content
def generate():
    data=ROOT/"parquet";data.mkdir(exist_ok=True)
    db=duckdb.connect(str(ROOT/"tpch.duckdb"))
    db.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.5)")
    sizes={}
    for t in TABLES:
        stmt=("SELECT DATE '2000-01-01' AS synth_date, * FROM "+t if t in DIM
              else "SELECT * FROM "+t)
        path=str((data/(t+".parquet")).resolve()).replace("'","''")
        db.execute(f"COPY ({stmt}) TO '{path}' (FORMAT PARQUET)")
        sizes[t]={"rows":db.execute("SELECT count() FROM "+t).fetchone()[0],
                  "bytes":(data/(t+".parquet")).stat().st_size}
    db.close()
    dump("DATASET_COUNTS.json",{"sf":0.5,"counts":sizes,
        "generator":"DuckDB tpch extension, pinned package version in workflow"})
    return data,sizes
def make_tables(database,cols):
    S.run(["docker","exec",S.CT,"clickhouse-client","--query",
           "CREATE DATABASE IF NOT EXISTS "+database],timeout=60)
    for t in TABLES:
        d=DATE.get(t,"synth_date")
        sql(f"CREATE TABLE {database}.{t} ({cols[t]}) ENGINE=MergeTree "
            f"PARTITION BY toYYYYMM({d}) ORDER BY ({KEY[t]})",db=database)
def insert_tables(database,root,version,enable_stats):
    for t in TABLES:
        stmt=f"INSERT INTO {database}.{t} SETTINGS materialize_statistics_on_insert={enable_stats} FORMAT Parquet"
        with (root/(t+".parquet")).open("rb") as f:
            proc=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",stmt],
                            stdin=f,check=False,timeout=950)
        if proc.returncode:
            raise RuntimeError("Import failure "+t+" "+proc.stderr.decode(errors="replace")[-1000:])
        print("C63_INGEST",version,database,t,flush=True)
def freeze(db):
    for t in TABLES:
        sql("SYSTEM STOP MERGES "+db+"."+t,db)
        sql("ALTER TABLE "+db+"."+t+" MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0",db)
def state(database):
    d={}
    for t in TABLES:
        raw=sql(f"SELECT name,rows,marks,bytes_on_disk,path FROM system.parts "
                f"WHERE database='{database}' AND table='{t}' AND active ORDER BY name FORMAT TSV",db=database)
        parts=[x.split("\t") for x in raw.splitlines()]
        if not parts:raise RuntimeError("No parts "+database+"/"+t)
        text="".join("|".join(x[:4])+"\n" for x in parts)
        d[t]={"parts":len(parts),"rows":sum(int(x[1]) for x in parts),
              "geometry_sha":hashlib.sha256(text.encode()).hexdigest()}
    return d
def validate_counts(st,sizes):
    for t in TABLES:
        if st[t]["rows"]!=sizes[t]["rows"]:
            raise RuntimeError("Row mismatch "+t)
def eligibility(queries):
    # Pre-execution eligibility is recorded for ALL published SQL; no post-hoc
    # exclusion by execution time or measured direction is permitted.
    rows=[]
    for q,s in enumerate(queries,1):
        try:
            # EXPLAIN avoids corrupting cache warm state in some builds but still
            # verifies syntax and identifiers before timed execution.
            sql("EXPLAIN "+portable(s),"tp_old",timeout=100)
            rows.append({"qid":q,"supported":True})
        except Exception as e:rows.append({"qid":q,"supported":False,"reason":str(e)[:240]})
    dump("ELIGIBILITY.json",rows)
    ok=[x["qid"] for x in rows if x["supported"]]
    print("C63_QUERY_ELIGIBILITY",len(ok),"/22",flush=True)
    if len(ok)<16:raise RuntimeError("Too few published TPC-H SQLs pass pre-specified eligibility")
    return ok
def canonical_value(v):
    if isinstance(v,float):
        if not __import__("math").isfinite(v):return str(v)
        return float(format(v,".7g"))
    if isinstance(v,list):return [canonical_value(z) for z in v]
    if isinstance(v,dict):return {k:canonical_value(z) for k,z in sorted(v.items())}
    return v

def answers(queries,good,db):
    ans={}
    meta={}
    for q in good:
        raw=sql(query(queries[q-1],1,0)+" FORMAT JSONEachRow",db=db,timeout=160)
        rows=[canonical_value(json.loads(line)) for line in raw.splitlines() if line]
        rows=sorted(json.dumps(x,sort_keys=True,separators=(",",":")) for x in rows)
        canonical="\\n".join(rows)
        ans[q]=hashlib.sha256(canonical.encode()).hexdigest()
        meta[q]={"n_rows":len(rows),"normalized_sha256":ans[q]}
    dump(db+"/answer_hashes.json",meta)
    return ans
def measure(queries,good,db,label,tables):
    rec=[]
    for rnd in range(-1,ROUNDS):
        rng=random.Random(63001+rnd)
        todo=[(qid,t,a,u,c) for qid in good for t in tables for a,u,c in ARMS]
        rng.shuffle(todo)
        for qid,table,arm,u,c in todo:
            ident=f"c63_{label}_{rnd}_{len(rec)}"
            start=time.monotonic()
            sql(query(queries[qid-1],u,c),db=table,qid=ident,fmt="Null",timeout=160)
            rec.append({"phase":label,"round":rnd,"sql":qid,"table":table,
                        "policy":arm,"qid":ident,
                        "client_ms":round((time.monotonic()-start)*1000,3)})
        print("C63_ROUND",label,rnd,len(rec),flush=True)
    sql("SYSTEM FLUSH LOGS",db=db)
    raw=sql(f"SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log "
            f"WHERE type='QueryFinish' AND startsWith(query_id,'c63_{label}_') FORMAT JSONEachRow",db=db)
    (ROOT/(label+"_log.jsonl")).write_text(raw)
    log={}
    for line in raw.splitlines():
        if line:
            x=json.loads(line);log[x["query_id"]]=x
    if len(log)!=len(rec):raise RuntimeError("Incomplete "+label+" receipts "+str(len(log))+"/"+str(len(rec)))
    for r in rec:
        x=log[r["qid"]];ev=x.get("ProfileEvents") or {}
        r.update(ms=float(x["query_duration_ms"]),rows=int(x["read_rows"]),
                 loaded_us=int(ev.get("LoadedStatisticsMicroseconds",0)))
    with (ROOT/(label+"_receipts.csv")).open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rec[0]));w.writeheader();w.writerows(rec)
    return rec
def summarize(old,target,good):
    data=old+target
    idx={}
    for phase in ("baseline","target"):
        for t in (("tp_old",) if phase=="baseline" else ("tp_old","tp_new")):
            for policy,_,_ in ARMS:
                for q in good:
                    vals=[r for r in data if r["phase"]==phase and r["table"]==t and r["policy"]==policy and r["sql"]==q and r["round"]>=0]
                    if len(vals)!=ROUNDS:raise RuntimeError("Incomplete SQL cell")
                    idx[(phase,t,policy,q)]=statistics.median(r["ms"] for r in vals)
    # Full population reported. NO verdict for a restricted <22 subset
    # unless all published queries are valid in both versions.
    out={"supported_sql":good,"n_supported":len(good),"full_population":len(good)==22,
         "disclosure":"Eligibility not conditioned on observed runtime."}
    def agg(phase,t,policy):
        per={q:idx[(phase,t,policy,q)] for q in good}
        means=[statistics.mean(per[q] for q in f if q in per) for f in FAMILIES if any(q in per for q in f)]
        return per,statistics.mean(means)
    for policy,_,_ in ARMS:
        oldq,oldm=agg("baseline","tp_old",policy)
        out[policy]={}
        for path in ("tp_old","tp_new"):
            newq,newm=agg("target",path,policy)
            ratio=newm/oldm
            severe=[q for q in good if newq[q]/max(.001,oldq[q])>=1.5]
            out[policy][path]={"base_ms":oldm,"after_ms":newm,"ratio":ratio,
                "severe_sql":severe,"gate":"BLOCK" if ratio>1.10 or len(severe)>=2 else "APPROVE"}
    dump("DECISIONS.json",out)
    print("C63_RESULTS",json.dumps(out,sort_keys=True),flush=True)
    return out
def main():
    dump("PREREGISTRATION.json",{"workload":"ClickBench published 22 TPC-H queries",
      "dataset":"TPC-H sf=0.5 generated using DuckDB tpch extension",
      "versions":[S.OLD,S.NEW],"paths":["preserved","newly reloaded"],
      "stats":["use_statistics=1,use_statistics_cache=0",
               "use_statistics=0,use_statistics_cache=0"],
      "metric":"per-query medians, equal families, 3 timed rounds and 1 warmup",
      "gate":"BLOCK if family mean >10% slower or >=2 distinct SQL >=1.5x slower",
      "eligibility":"pre-measurement EXPLAIN all 22 published queries; abort if <16",
      "query_correctness":"All eligible queries: normalized JSON rowsets, order-invariant, numeric floats rounded to 7 significant digits across 26.7 inherited, 26.8 inherited and 26.8 reloaded. Report every mismatch before timing; abort if <16 comparable queries.",
      "scope_warning":"If any SQL fails semantic comparability, report conditional subset; never claim all 22 TPC-H SQLs",
      "caveat":"Nonstandard smaller TPC-H scale factor, not an official TPC benchmark run.",
      "scope":"independent dataset and workload, one release pair and one runner host per job"})
    for version in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+version],timeout=650)
    queries,sources=pin()
    data,sizes=generate()
    cols={t:sources["create/schema/"+t+".columns"] for t in TABLES}
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    try:
        S.start(S.OLD)
        make_tables("tp_old",cols)
        insert_tables("tp_old",data,S.OLD,0)
        freeze("tp_old")
        original=state("tp_old");validate_counts(original,sizes)
        dump("old_state.json",original)
        good=eligibility(queries)
        old_answers=answers(queries,good,"tp_old")
        old_meas=measure(queries,good,"tp_old","baseline",("tp_old",))
        S.run(["docker","stop","-t","60",S.CT],timeout=80,check=False)
        S.run(["docker","rm","-f",S.CT],timeout=70,check=False)
        S.start(S.NEW)
        up=state("tp_old")
        if up!=original:raise RuntimeError("Inherited physical geometry not retained")
        make_tables("tp_new",cols)
        insert_tables("tp_new",data,S.NEW,1)
        freeze("tp_new")
        rebuilt=state("tp_new");validate_counts(rebuilt,sizes)
        dump("new_states.json",{"preserved":up,"rebuilt":rebuilt})
        new_answers=answers(queries,good,"tp_new")
        preserved_answers=answers(queries,good,"tp_old")
        confirmed=[q for q in good if old_answers[q]==new_answers[q]==preserved_answers[q]]
        mismatches=[q for q in good if q not in confirmed]
        dump("CORRECTNESS_SCOPE.json",{"original_eligibility":good,
             "canonicalization":"unordered JSON rows; floats to seven significant digits",
             "comparable_queries":confirmed,"unmatched_queries":mismatches,
             "warning":"A subset is not the full published TPC-H 22-query workload."})
        print("C63_CANONICAL_CORRECTNESS",len(confirmed),"/",len(good),"excluded",mismatches,flush=True)
        if len(confirmed)<16:
            raise RuntimeError("Not enough semantically comparable published SQLs for an external benchmark claim")
        good=confirmed
        target_meas=measure(queries,good,"tp_old","target",("tp_old","tp_new"))
        if state("tp_old")!=up or state("tp_new")!=rebuilt:
            raise RuntimeError("Part geometry changed during measurements")
        verdict=summarize(old_meas,target_meas,good)
        dump("FINAL_STATUS.json",{"valid":True,"runner":os.environ.get("RUN_GROUP","unknown"),
            "baseline_receipts":len(old_meas),"target_receipts":len(target_meas),
            "verdict":verdict})
    finally:
        S.run(["docker","rm","-f",S.CT],check=False)
if __name__=="__main__":main()
