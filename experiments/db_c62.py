#!/usr/bin/env python3
"""Balanced statistics-enabled/disabled first-access contrast on 26.8."""
import csv,hashlib,json,pathlib,random,statistics,time,os
from collections import defaultdict
import db_c61 as B
C,P,S=B.C,B.P,B.S
R=pathlib.Path("c62");R.mkdir(exist_ok=True)
B.R=C.R=P.R=S.ROOT=S.base.ROOT=R
S.CT="c62_engine";S.VOL="c62_volume"
S.base.VERSIONS=[S.OLD,S.OLD]
VERSION=S.NEW
BLOCKS=3
FAMILIES=(range(1,4),range(4,7),range(7,11),range(11,14))
RECORDS=[]

def save(path,obj):
    q=R/path;q.parent.mkdir(parents=True,exist_ok=True)
    q.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")

def restart(key):
    B.shutdown()
    S.start(VERSION)
    merges=int(C.sql("SELECT count() FROM system.merges WHERE database='ssb' "
                     "AND table IN ('ref','fresh') FORMAT TSV").strip())
    if merges:raise RuntimeError("Unexpected merge before first-access measurement")
    print("C62_START",key,flush=True)

def capture(label,cutoff):
    obj=B.snapshot(label,cutoff,("ref","fresh"))
    if obj["parts"]["ref"]["rows"]!=6001215:raise RuntimeError("Unexpected data population")
    return obj

def run_measure(qs):
    for block in range(BLOCKS):
        queries=list(range(1,14))
        random.Random(620001+block*47).shuffle(queries)
        orders=[]
        for qi in queries:
            pairs=[("ref","stats_on"),("fresh","stats_on"),
                   ("ref","stats_off"),("fresh","stats_off")]
            shift=(qi+block)%4
            pairs=pairs[shift:]+pairs[:shift]
            for table,policy in pairs:
                key=f"b{block}_q{qi}_{table}_{policy}"
                restart(key)
                query=P.qstr(qs[qi-1],table,1 if policy=="stats_on" else 0,1)
                for seq in range(4):
                    stage="first" if seq==0 else "warm"+str(seq)
                    qid="c62_"+key+"_"+stage
                    tick=time.monotonic()
                    C.sql(query,qid=qid,fmt="Null")
                    RECORDS.append({"block":block,"sql":qi,"table":table,"policy":policy,
                                    "stage":stage,"qid":qid,"client_ms":round((time.monotonic()-tick)*1000,2)})
                orders.append(key)
        save("orders/block"+str(block)+".json",orders)
        print("C62_BLOCK",block,len(orders),len(RECORDS),flush=True)

def receipts():
    C.sql("SYSTEM FLUSH LOGS")
    raw=C.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
              "FROM system.query_log WHERE type='QueryFinish' "
              "AND startsWith(query_id,'c62_') FORMAT JSONEachRow")
    (R/"query_log.jsonl").write_text(raw)
    seen={}
    for line in raw.splitlines():
        if line:
            item=json.loads(line)
            k=item["query_id"]
            if k in seen:raise RuntimeError("Duplicate query ID")
            seen[k]=item
    if len(seen)!=len(RECORDS):raise RuntimeError(f"Missing receipts {len(seen)} of {len(RECORDS)}")
    for r in RECORDS:
        x=seen[r["qid"]];ev=x.get("ProfileEvents") or {}
        r.update(ms=float(x["query_duration_ms"]),rows=int(x["read_rows"]),
                 bytes=int(x["read_bytes"]),
                 stat_us=int(ev.get("LoadedStatisticsMicroseconds",0)),
                 optimize_us=int(ev.get("QueryPlanOptimizeMicroseconds",0)),
                 file_open=int(ev.get("FileOpen",0)),
                 selected_marks=int(ev.get("SelectedMarks",0)))
    with (R/"receipts.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=RECORDS[0]);w.writeheader();w.writerows(RECORDS)
    print("C62_RECEIPTS",len(RECORDS),flush=True)

def answers(qs):
    hashes={}
    for q in range(1,14):
        current=set()
        for table in ("ref","fresh"):
            for policy in (0,1):
                result=C.sql(P.qstr(qs[q-1],table,policy,1)+" FORMAT TSV")
                digest=hashlib.sha256(result.encode()).hexdigest()
                hashes[f"q{q}/{table}/{policy}"]=digest
                current.add(digest)
        if len(current)!=1:raise RuntimeError("SQL results disagree after measurement "+str(q))
    save("ANSWER_HASHES.json",hashes)

def report():
    groups=defaultdict(list)
    for r in RECORDS:groups[(r["table"],r["policy"],r["sql"],r["stage"])].append(r)
    rows={}
    for t in ("ref","fresh"):
        for p in ("stats_on","stats_off"):
            for q in range(1,14):
                first=groups[(t,p,q,"first")]
                warm=groups[(t,p,q,"warm2")]+groups[(t,p,q,"warm3")]
                if len(first)!=BLOCKS or len(warm)!=2*BLOCKS:
                    raise RuntimeError("Unbalanced independent SQL cells")
                rows[(t,p,q)]={}
                for stage,v in (("first",first),("warm",warm)):
                    rows[(t,p,q)][stage]={k:statistics.median(x[k] for x in v)
                        for k in ("ms","stat_us","rows","bytes","optimize_us","file_open")}
    save("PER_SQL.json",{"/".join(map(str,key)):v for key,v in rows.items()})
    def mean4(x):return statistics.mean(statistics.mean(x[i] for i in f) for f in FAMILIES)
    summary={}
    for table in ("ref","fresh"):
        summary[table]={}
        for policy in ("stats_on","stats_off"):
            summary[table][policy]={}
            for stage in ("first","warm"):
                d={q:rows[(table,policy,q)][stage] for q in range(1,14)}
                summary[table][policy][stage]={
                    k:mean4({q:d[q][k] for q in d}) for k in ("ms","stat_us","rows","optimize_us")}
            f=summary[table][policy]["first"]
            w=summary[table][policy]["warm"]
            summary[table][policy]["first_over_warm"]=f["ms"]/w["ms"]
            summary[table][policy]["extra_ms"]=f["ms"]-w["ms"]
        summary[table]["excess_startup_on_minus_off_ms"]=(
            summary[table]["stats_on"]["extra_ms"]-
            summary[table]["stats_off"]["extra_ms"])
    out={"paths":summary,"first_vs_warm":{"stats_on":{},"stats_off":{}}}
    for policy in ("stats_on","stats_off"):
        for stage in ("first","warm"):
            src=summary["ref"][policy][stage]["ms"]
            dst=summary["fresh"][policy][stage]["ms"]
            severe=[q for q in range(1,14) if
                rows[("fresh",policy,q)][stage]["ms"]/
                max(.001,rows[("ref",policy,q)][stage]["ms"])>=1.5]
            ratio=dst/src
            out["first_vs_warm"][policy][stage]={"ref_ms":src,"fresh_ms":dst,"ratio":ratio,
                "decision":"BLOCK" if ratio>1.10 or len(severe)>=2 else "APPROVE",
                "severe_sql":severe}
    save("FINAL_DECISIONS.json",out)
    print("C62_FINAL",json.dumps(out,sort_keys=True),flush=True)
    return out

def main():
    save("PREREGISTRATION.json",{
        "objective":"Isolate statistics-specific excess first-access latency from general startup overhead",
        "database_version":VERSION,"source":"Pinned ClickBench SSB SF1",
        "population":"All 13 SQLs and four equally weighted query families",
        "paths":["inherited","rebuilt"],"treatment":["use_statistics=1","use_statistics=0"],
        "statistics_cache_setting":1,"process_restarts":"one restart per SQL-path-treatment-block",
        "measured_invocations":"first SQL, then three identical repeats",
        "warm_summary":"median of invocation 3 and 4 across blocks",
        "balanced_blocks":BLOCKS,"order":"deterministic shuffle and rotated path/treatment order",
        "negative_control":"first vs warm with statistics OFF, also three blocks",
        "primary_estimand":"(first-warm)_stats_on minus (first-warm)_stats_off, per path",
        "secondary":"fresh vs inherited gate by treatment and temperature",
        "stop_if":["SQL mismatch","part/checksum drift","missing query receipt"],
        "limitations":["not a Linux page-cache flush","first includes other process initialization costs",
                       "one release target only","per-query first and warm are sequential",
                       "hosted runner and OS cache vary"]})
    for v in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=650)
    qs=S.base.pinned_sources()
    native=S.base.make_data()
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    try:
        S.start(S.OLD)
        S.run(["docker","exec",S.CT,"clickhouse-client","--query",
               "CREATE DATABASE IF NOT EXISTS ssb"])
        cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
        P.create("raw",cols)
        C.create("ref",cols)
        with native.open("rb") as f:
            import_data=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
               "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
               stdin=f,timeout=1000,check=False)
        if import_data.returncode:raise RuntimeError("Native ingest failed")
        native.unlink()
        cutoff,_,_=C.pick_cutoff()
        C.insert("ref","1",0)
        if not C.wait_idle(["ref"],limit=160)["idle"]:raise RuntimeError("Preserved merges active")
        C.stop(["ref"])
        for t in ("raw","ref"):
            C.sql(f"ALTER TABLE ssb.{t} MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        orig=C.partmap("original",("ref",),cutoff)
        B.shutdown()
        S.start(VERSION)
        inherited=C.partmap("target_inherited",("ref",),cutoff)
        if orig["ref"]!=inherited["ref"]:raise RuntimeError("Preserved part drift")
        C.create("fresh",cols)
        C.insert("fresh","1",None)
        if not C.wait_idle(["fresh"],limit=180)["idle"]:raise RuntimeError("Rebuild did not settle")
        C.stop(["ref","fresh"])
        C.sql("ALTER TABLE ssb.fresh MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        before=capture("before",cutoff)
        if before["parts"]["ref"]["fingerprint"]!=before["parts"]["fresh"]["fingerprint"]:
            raise RuntimeError("Logical data differs between physical histories")
        if before["parts"]["fresh"]["stats_parts"]!=before["parts"]["fresh"]["parts"]:
            raise RuntimeError("No complete statistics treatment")
        run_measure(qs)
        after=capture("after",cutoff)
        if before["parts"]!=after["parts"] or before["part_checksums"]!=after["part_checksums"]:
            raise RuntimeError("Physical state changed during measures")
        receipts()
        answers(qs)
        result=report()
        save("FINAL_STATUS.json",{"valid":True,"runner":os.environ.get("RUN_GROUP",""),
            "receipt_count":len(RECORDS),"row_count":before["parts"]["ref"]["rows"],"decision":result})
    finally:B.shutdown()

if __name__=="__main__":main()
