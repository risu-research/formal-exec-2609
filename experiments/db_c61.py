#!/usr/bin/env python3
"""A paired release-transition validation on one frozen source dataset."""
import csv, hashlib, json, pathlib, statistics, sys, time
import db_c42 as C

S=C.S
P=C.P
R=pathlib.Path("c61")
R.mkdir(exist_ok=True)
C.R=R
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c61_engine"
S.VOL="c61_storage"
FAMILIES=(range(1,4),range(4,7),range(7,11),range(11,14))
ARMS=("cache_on","cache_off","stats_off")

def save(name,obj):
    p=R/name
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")

def snapshot(label,cutoff,tables):
    phys=C.partmap(label,tables,cutoff)
    check=S.part_checksums()
    data=C.sql("SELECT table,name,partition,rows,marks,bytes_on_disk FROM system.parts "
               "WHERE active AND database='ssb' ORDER BY table,name FORMAT TSV")
    meta={"engine":C.sql("SELECT version() FORMAT TSV").strip(),
          "parts":phys,"active_inventory_sha256":hashlib.sha256(data.encode()).hexdigest(),
          "part_checksums":check}
    save(label+"/snapshot.json",meta)
    return meta

def shutdown():
    S.run(["docker","stop","-t","75",S.CT],timeout=110,check=False)
    S.run(["docker","rm","-f",S.CT],timeout=70,check=False)

def compare(s1,s2,tag):
    for table in ("ref",):
        if s1["parts"][table]!=s2["parts"][table]:
            raise RuntimeError("Physical part inventory changed during "+tag)
    if s1["active_inventory_sha256"]!=s2["active_inventory_sha256"]:
        raise RuntimeError("Part names/geometry changed during "+tag)
    if s1["part_checksums"]!=s2["part_checksums"]:
        raise RuntimeError("Checksums changed during "+tag)

def read_phase(name,table,arm):
    with (R/name/"receipts.csv").open() as f: rows=list(csv.DictReader(f))
    data=[x for x in rows if x["table"]==table and x["arm"]==arm and int(x["round"])>=0]
    by={}
    for q in range(1,14):
        durations=[float(x["ms"]) for x in data if int(x["sql"])==q]
        if len(durations)<3:raise RuntimeError("Incomplete per-query observations "+name)
        by[q]=statistics.median(durations)
    overall=statistics.mean(statistics.mean(by[q] for q in family) for family in FAMILIES)
    return {"ms":overall,"query_ms":by,"rounds":len(data)//13}

def gate(a,b):
    ratio=b["ms"]/a["ms"]
    severe=[q for q in range(1,14) if b["query_ms"][q]/max(0.001,a["query_ms"][q])>=1.5]
    return {"base_ms":a["ms"],"target_ms":b["ms"],"ratio":ratio,"severe_sql":severe,
            "gate":"BLOCK" if ratio>1.10 or len(severe)>=2 else "APPROVE"}

def crossphase_answers():
    d={}
    for phase in ("old_sham","target"):
        d[phase]=json.loads((R/phase/"answers.json").read_text())
    for q in range(1,14):
        for arm in ARMS:
            a=d["old_sham"][f"{q}/ref/{arm}"]
            b=d["target"][f"{q}/ref/{arm}"]
            c=d["target"][f"{q}/fresh/{arm}"]
            if len({a,b,c})!=1:raise RuntimeError("Result mismatch "+str(q)+" "+arm)


def data_file_identity(t):
    """Hash non-statistics column and index streams by stable logical part keys."""
    raw=C.sql("SELECT partition,min_block_number,max_block_number,level,rows,marks,path "
              "FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' "
              "ORDER BY partition,min_block_number,max_block_number,level FORMAT TSV")
    pts=[z.split("\t") for z in raw.splitlines() if z]
    assert pts and all(len(p)==7 for p in pts)
    mapped={p[6].rstrip("/"):":".join(p[:6]) for p in pts}
    paths=("\n".join(p[6] for p in pts)+"\n").encode()
    shell=r"""set -euo pipefail
while IFS= read -r directory; do
 find "$directory" -maxdepth 1 -type f \( -name '*.bin' -o -name '*.cmrk2' -o -name '*.mrk2' -o -name '*.mrk3' -o -name '*.cidx' -o -name '*.idx' \) -print0
done | sort -z | xargs -0 -r sha256sum"""
    proc=S.run(["docker","exec","-i",S.CT,"bash","-c",shell],inp=paths,timeout=300)
    entries=[]
    for line in proc.stdout.decode().splitlines():
        sha,fn=line.split(None,1)
        fn=fn.strip()
        directory=str(pathlib.PurePosixPath(fn).parent)
        if directory not in mapped:raise RuntimeError("File outside tracked parts")
        entries.append(mapped[directory]+"|"+pathlib.PurePosixPath(fn).name+"|"+sha)
    if len(entries)<len(pts):raise RuntimeError("Missing non-statistics streams")
    keylist=sorted(mapped.values())
    payload="\n".join(sorted(entries)).encode()
    return {"parts":len(pts),"rows":sum(int(p[4]) for p in pts),
            "file_count":len(entries),
            "geometry_sha":hashlib.sha256("\n".join(keylist).encode()).hexdigest(),
            "column_index_sha":hashlib.sha256(payload).hexdigest(),
            "logical_fp":P.fp(t)}

def stats_mutation(command,label):
    # MergeTree mutations must be allowed to run. The preceding query phase
    # is frozen by SYSTEM STOP MERGES; reopen only the treated table here.
    # Its table-level max_bytes_to_merge_at_max_space_in_pool remains zero,
    # preventing ordinary background coalescing during this intervention.
    print("C61_INTERVENTION_UNFREEZE",label,flush=True)
    C.resume(["fresh"])
    try:
        submitted=time.monotonic()
        print("C61_INTERVENTION_BEGIN",label,flush=True)
        C.sql(command)
        print("C61_INTERVENTION_RETURNED",label,round(time.monotonic()-submitted,2),flush=True)
        deadline=time.monotonic()+180
        while True:
            raw=C.sql("SELECT count(), countIf(latest_fail_reason != '') "
                      "FROM system.mutations WHERE database='ssb' "
                      "AND table='fresh' AND is_done=0 FORMAT TSV").strip()
            pending,errors=map(int,raw.split(chr(9)))
            print("C61_INTERVENTION_PROGRESS",label,"pending",pending,"errors",errors,flush=True)
            if errors:raise RuntimeError("Background mutation reported an error "+label)
            if pending==0:break
            if time.monotonic()>deadline:raise RuntimeError("Statistics mutation did not drain "+label)
            time.sleep(5)
    finally:
        C.stop(["fresh"])
        print("C61_INTERVENTION_REFROZEN",label,flush=True)

def confirm_data_identity(reference,phase):
    current=data_file_identity("fresh")
    save("identity/"+phase+".json",current)
    for field in ("parts","rows","file_count","geometry_sha","column_index_sha","logical_fp"):
        if current[field]!=reference[field]:
            raise RuntimeError("Non-statistics data/index changed "+phase+" "+field)

def compare_answers_by_phase(phases):
    answers={p:json.loads((R/p/"answers.json").read_text()) for p in phases}
    for q in range(1,14):
        for arm in ARMS:
            baseline=answers[phases[0]][f"{q}/ref/{arm}"]
            for p in phases:
                for table in ("ref","fresh"):
                    if answers[p][f"{q}/{table}/{arm}"]!=baseline:
                        raise RuntimeError("SQL output changed "+p+" "+arm+" "+str(q))

def reset_engine_cache(label):
    # New ClickHouse process, same Docker volume and physical parts.
    # OS page cache is not reset and must not be described as cold disk.
    print("C61_RESET_BEGIN",label,flush=True)
    shutdown()
    S.start(S.NEW)
    C.stop(["ref","fresh"])
    print("C61_RESET_DONE",label,C.sql("SELECT version() FORMAT TSV").strip(),flush=True)


# Each sample pair uses a fresh server process. Never run a data-table
# SELECT (including answer checking) before the FIRST recorded query.
import random
from collections import defaultdict

PROBES=[]
BLOCKS=3
CONTROL_BLOCKS=1

def boot(ver,tag):
    shutdown()
    S.start(ver)
    # A metadata-only check. Does not read user table/statistics streams.
    n=int(C.sql("SELECT count() FROM system.merges WHERE database='ssb' "
                "AND table IN ('ref','fresh') FORMAT TSV").strip())
    if n:raise RuntimeError("Active merge at "+tag)
    print("C61_BOOT",tag,ver,flush=True)

def sample_phase(phase,version,tables,queries,policy,blocks):
    for block in range(blocks):
        qlist=list(range(1,14))
        random.Random(610102+block*13+(800 if policy=="stats_off" else 0)).shuffle(qlist)
        schedule=[]
        for qi in qlist:
            tbls=list(tables)
            if len(tbls)==2 and (qi+block)%2:tbls.reverse()
            for table in tbls:
                tag=f"{phase}_{policy}_b{block}_q{qi}_{table}"
                boot(version,tag)
                stmt=P.qstr(queries[qi-1],table,0 if policy=="stats_off" else 1,1)
                for k in range(4):
                    stage="first" if k==0 else "warm"+str(k)
                    qid="c61_"+tag+"_"+stage
                    start=time.monotonic()
                    C.sql(stmt,qid=qid,fmt="Null")
                    PROBES.append({"phase":phase,"policy":policy,"block":block,
                                   "q":qi,"table":table,"stage":stage,"qid":qid,
                                   "client_ms":round((time.monotonic()-start)*1000,2)})
                schedule.append(tag)
        save("schedule/"+phase+"_"+policy+"_b"+str(block)+".json",{
            "order":schedule,"first_query_per_boot":True,
            "warm_sampling":"second-later and third-later invocation"})
        print("C61_BLOCK",phase,policy,block,len(schedule),flush=True)

def capture_old_log():
    """Durably collect all 26.7 query receipts BEFORE the 26.8 upgrade."""
    C.sql("SYSTEM FLUSH LOGS")
    raw=C.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
              "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c61_old_') "
              "FORMAT JSONEachRow")
    records=[json.loads(line) for line in raw.splitlines() if line]
    expected=sum(1 for z in PROBES if z["phase"]=="old")
    ids=[z["query_id"] for z in records]
    if len(records)!=expected or len(set(ids))!=expected:
        raise RuntimeError(f"Old-version receipts incomplete before upgrade: {len(records)}/{expected}")
    if any(not qid.startswith("c61_old_") for qid in ids):
        raise RuntimeError("Old receipt phase collision")
    (R/"old_query_log.jsonl").write_text(raw)
    print("C61_OLD_LOG_SAVED",len(records),flush=True)

def extract():
    if not (R/"old_query_log.jsonl").exists():
        raise RuntimeError("Old-phase receipts missing; abort without verdict")
    oldraw=(R/"old_query_log.jsonl").read_text()
    oldlines=[json.loads(x) for x in oldraw.splitlines() if x]
    expected_old=sum(1 for r in PROBES if r["phase"]=="old")
    if len(oldlines)!=expected_old:
        raise RuntimeError("Old query receipts corrupt")
    C.sql("SYSTEM FLUSH LOGS")
    raw=C.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
              "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c61_target_') "
              "FORMAT JSONEachRow")
    (R/"target_query_log.jsonl").write_text(raw)
    combined=oldraw.rstrip("\n")+"\n"+raw
    (R/"query_log.jsonl").write_text(combined)
    events={}
    for line in combined.splitlines():
        if line:
            x=json.loads(line)
            if x["query_id"] in events:raise RuntimeError("Duplicate query ID")
            events[x["query_id"]]=x
    if len(events)!=len(PROBES):
        raise RuntimeError(f"Missing query receipts {len(events)} of {len(PROBES)}")
    for r in PROBES:
        x=events[r["qid"]]
        ev=x.get("ProfileEvents") or {}
        r.update(ms=float(x["query_duration_ms"]),rows=int(x["read_rows"]),
                 read_bytes=int(x["read_bytes"]),
                 stat_us=int(ev.get("LoadedStatisticsMicroseconds",0)),
                 opt_us=int(ev.get("QueryPlanOptimizeMicroseconds",0)),
                 open_files=int(ev.get("FileOpen",0)))
    with (R/"receipts.csv").open("w",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(PROBES[0].keys()))
        w.writeheader()
        w.writerows(PROBES)
    print("C61_RECEIPTS",len(PROBES),flush=True)

def summarize():
    idx=defaultdict(list)
    for x in PROBES:idx[(x["phase"],x["policy"],x["table"],x["q"],x["stage"])].append(x)
    per={}
    for phase,policies,tables in (
            ("old",("stats_on",),("ref",)),
            ("target",("stats_on","stats_off"),("ref","fresh"))):
        for policy in policies:
            n=BLOCKS if policy=="stats_on" else CONTROL_BLOCKS
            for t in tables:
                for q in range(1,14):
                    first=idx[(phase,policy,t,q,"first")]
                    warm=idx[(phase,policy,t,q,"warm2")]+idx[(phase,policy,t,q,"warm3")]
                    if len(first)!=n or len(warm)!=n*2:raise RuntimeError("Incomplete SQL cell")
                    record={}
                    for label,rows in (("first",first),("warm",warm)):
                        record[label]={k:statistics.median(v[k] for v in rows)
                            for k in ("ms","rows","read_bytes","stat_us","opt_us","open_files")}
                    per[f"{phase}/{policy}/{t}/q{q}"]=record
    save("PER_QUERY.json",per)
    def agg(phase,policy,table,temp):
        p={q:per[f"{phase}/{policy}/{table}/q{q}"][temp] for q in range(1,14)}
        means={k:statistics.mean(statistics.mean(p[q][k] for q in family)
                                   for family in FAMILIES) for k in ("ms","stat_us","rows")}
        return p,means
    def gate(src,dst):
        a,avg0=src
        b,avg1=dst
        severe=[q for q in range(1,14) if b[q]["ms"]/max(.001,a[q]["ms"])>=1.5]
        ratio=avg1["ms"]/avg0["ms"]
        return {"ratio":ratio,"decision":"BLOCK" if ratio>1.10 or len(severe)>=2 else "APPROVE",
                "severe":severe,"base_ms":avg0["ms"],"target_ms":avg1["ms"],
                "base_load_ms":avg0["stat_us"]/1000,"target_load_ms":avg1["stat_us"]/1000,
                "base_read_rows":avg0["rows"],"target_read_rows":avg1["rows"]}
    result={}
    for temperature in ("first","warm"):
        old=agg("old","stats_on","ref",temperature)
        inherited=agg("target","stats_on","ref",temperature)
        fresh=agg("target","stats_on","fresh",temperature)
        result[temperature]={"preserved":gate(old,inherited),
                             "fresh":gate(old,fresh),
                             "fresh_over_preserved":gate(inherited,fresh)}
    result["control"]={}
    for table in ("ref","fresh"):
        onf=agg("target","stats_on",table,"first")[1]
        onw=agg("target","stats_on",table,"warm")[1]
        off=agg("target","stats_off",table,"first")[1]
        offw=agg("target","stats_off",table,"warm")[1]
        result["control"][table]={
          "on_first_over_warm":onf["ms"]/onw["ms"],
          "off_first_over_warm":off["ms"]/offw["ms"],
          "on_load_first_ms":onf["stat_us"]/1000,
          "on_load_warm_ms":onw["stat_us"]/1000,
          "off_load_first_ms":off["stat_us"]/1000,
          "off_load_warm_ms":offw["stat_us"]/1000}
    save("DECISIONS.json",result)
    print("C61_RESULTS",json.dumps(result,sort_keys=True),flush=True)
    return result

def answers_after_timing(queries):
    checks={}
    for table in ("ref","fresh"):
        for q in range(1,14):
            ans=C.sql(P.qstr(queries[q-1],table,1,1)+" FORMAT TSV")
            dig=hashlib.sha256(ans.encode()).hexdigest()
            checks[f"{table}/q{q}"]=dig
            if table=="fresh" and dig!=checks[f"ref/q{q}"]:
                raise RuntimeError("SQL correctness mismatch on q"+str(q))
    save("ANSWER_HASHES.json",checks)

def main():
    save("PREREGISTRATION.json",{
        "population":"ClickBench pinned SSB SF1, 13 SQLs, four equal-weight query families",
        "versions":[S.OLD,S.NEW],"comparison":"same 26.7 ref source, 26.8 inherited vs rebuilt",
        "primary":"use_statistics=1,use_statistics_cache=1,use_query_condition_cache=0",
        "negative_control":"use_statistics=0,use_statistics_cache=1",
        "blocks_per_SQL":BLOCKS,"negative_control_blocks":CONTROL_BLOCKS,
        "reset":"full ClickHouse process restart BEFORE every distinct SQL and data path",
        "first":"very first data query after process restart, without answer preflight",
        "warm":"median of third and fourth consecutive identical executions",
        "gating":"family-weighted mean >10% slowdown OR >=2 SQL with >=1.5x slowdown",
        "randomization":"fixed shuffled query order per block; target data path order alternates",
        "exclusions":"no posthoc query, threshold or settings changes",
        "receipt_capture":"26.7 logs flushed and saved before version change; 26.8 logs after target measurement",
        "analysis_preserved":"same SQL permutations, restarts, repetitions and gates as initial C61",
        "limitations":["OS page cache not flushed","metadata queries and dataset building may warm OS cache",
          "warm queries necessarily executed after first","first includes restart and plan-init effects",
          "replicates within a single hosted runner are not independent machines"]})
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
            out=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
                "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
                stdin=f,timeout=1000,check=False)
        if out.returncode:raise RuntimeError("Failed source ingest "+out.stderr.decode(errors="replace")[-1000:])
        native.unlink()
        cutoff,_,_=C.pick_cutoff()
        C.insert("ref","1",0)
        if not C.wait_idle(["ref"],limit=180)["idle"]:raise RuntimeError("Initial merges active")
        C.stop(["ref"])
        for table in ("raw","ref"):
            C.sql(f"ALTER TABLE ssb.{table} MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        old_before=snapshot("old_before",cutoff,["ref"])
        sample_phase("old",S.OLD,("ref",),qs,"stats_on",BLOCKS)
        capture_old_log()
        old_after=snapshot("old_after",cutoff,["ref"])
        if old_before["parts"]["ref"]!=old_after["parts"]["ref"] or old_before["part_checksums"]!=old_after["part_checksums"]:
            raise RuntimeError("Old physical inventory drift")
        shutdown()
        S.start(S.NEW)
        up_before=snapshot("before_rebuild",cutoff,["ref"])
        if old_after["parts"]["ref"]!=up_before["parts"]["ref"]:
            raise RuntimeError("Unexpected preserved data change on upgrade")
        C.create("fresh",cols)
        C.insert("fresh","1",None)
        if not C.wait_idle(["fresh"],limit=180)["idle"]:raise RuntimeError("New part merges active")
        C.stop(["ref","fresh"])
        C.sql("ALTER TABLE ssb.fresh MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        target_before=snapshot("target_before",cutoff,["ref","fresh"])
        if target_before["parts"]["ref"]["fingerprint"]!=target_before["parts"]["fresh"]["fingerprint"]:
            raise RuntimeError("Logical datasets differ")
        if target_before["parts"]["fresh"]["parts"]!=target_before["parts"]["fresh"]["stats_parts"]:
            raise RuntimeError("Fresh statistics not complete")
        sample_phase("target",S.NEW,("ref","fresh"),qs,"stats_on",BLOCKS)
        sample_phase("target",S.NEW,("ref","fresh"),qs,"stats_off",CONTROL_BLOCKS)
        target_after=snapshot("target_after",cutoff,["ref","fresh"])
        if target_before["parts"]!=target_after["parts"] or target_before["part_checksums"]!=target_after["part_checksums"]:
            raise RuntimeError("Physical parts drift during comparison")
        extract()
        answers_after_timing(qs)
        result=summarize()
        save("FINAL_STATUS.json",{"valid":True,"runner":__import__("os").environ.get("RUN_GROUP",""),
            "observations":len(PROBES),"rows":target_after["parts"]["ref"]["rows"],
            "decisions":result,"control_repetitions":CONTROL_BLOCKS,
            "scope":"first is engine-process first, not OS-page-cache cold"})
    finally:
        shutdown()

if __name__=="__main__":main()
