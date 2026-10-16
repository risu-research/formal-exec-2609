#!/usr/bin/env python3
"""C67 full-response client wall compared with server timings.

Full TSV response verified bytewise against expected answer; two workers per
restart condition, 6 repetitions per SQL and output mode. The client is
executed via Docker on the runner, not over a production network.
"""
import csv, hashlib, json, pathlib, statistics, sys, time, os, random
import db_c42 as C

S=C.S
P=C.P
R=pathlib.Path("c67")
R.mkdir(exist_ok=True)
C.R=R
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c67_engine"
S.VOL="c67_storage"
RESTART_FLAG=os.environ.get("C67_RESTART","")
TARGET_REPS=int(os.environ.get("C67_REPS","6"))
if RESTART_FLAG not in ("0","1") or TARGET_REPS not in (4,6):
    raise ValueError("Require C67_RESTART=0/1 and C67_REPS=4/6")
RESTART_PHASES=(RESTART_FLAG=="1")
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
    print("C67_INTERVENTION_UNFREEZE",label,flush=True)
    C.resume(["fresh"])
    try:
        submitted=time.monotonic()
        print("C67_INTERVENTION_BEGIN",label,flush=True)
        C.sql(command)
        print("C67_INTERVENTION_RETURNED",label,round(time.monotonic()-submitted,2),flush=True)
        deadline=time.monotonic()+180
        while True:
            raw=C.sql("SELECT count(), countIf(latest_fail_reason != '') "
                      "FROM system.mutations WHERE database='ssb' "
                      "AND table='fresh' AND is_done=0 FORMAT TSV").strip()
            pending,errors=map(int,raw.split(chr(9)))
            print("C67_INTERVENTION_PROGRESS",label,"pending",pending,"errors",errors,flush=True)
            if errors:raise RuntimeError("Background mutation reported an error "+label)
            if pending==0:break
            if time.monotonic()>deadline:raise RuntimeError("Statistics mutation did not drain "+label)
            time.sleep(5)
    finally:
        C.stop(["fresh"])
        print("C67_INTERVENTION_REFROZEN",label,flush=True)

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
    print("C67_RESET_BEGIN",label,flush=True)
    shutdown()
    S.start(S.NEW)
    C.stop(["ref","fresh"])
    print("C67_RESET_DONE",label,C.sql("SELECT version() FORMAT TSV").strip(),flush=True)

def prepare_target(label):
    if RESTART_PHASES:
        reset_engine_cache(label)
    else:
        print("C67_RESET_SKIPPED",label,flush=True)


def client_measure(phase,tables,queries,nreps):
    """Server query durations and runner-side complete-response wall times."""
    expected={};digest={}
    for qi in range(1,14):
        reference=None
        for t in tables:
            for arm,u,cache in C.ARMS:
                b=C.sql(P.qstr(queries[qi-1],t,u,cache)+" FORMAT TSV").encode()
                h=hashlib.sha256(b).hexdigest()
                if reference is None:reference=h
                if reference!=h:raise RuntimeError(f"Answers differ {phase} q{qi} {t} {arm}")
                expected[(qi,t,arm)]=b;digest[f"{qi}/{t}/{arm}"]=h
    save(phase+"/answers.json",digest)
    save(phase+"/answer_sizes.json",{
        f"{q}/{t}/{a}":{"sha256":hashlib.sha256(b).hexdigest(),"bytes":len(b)}
        for (q,t,a),b in expected.items()})
    rows=[]
    for rnd in range(-1,nreps):
        rng=random.Random(670000+rnd)
        qlist=list(range(1,14));rng.shuffle(qlist)
        arms=list(C.ARMS);rng.shuffle(arms)
        for qi in qlist:
            for i,(arm,u,cache) in enumerate(arms):
                offset=(qi+i+rnd)%len(tables)
                paths=list(tables);paths=paths[offset:]+paths[:offset]
                for t in paths:
                    modes=["Null","TSV"];rng.shuffle(modes)
                    for mode in modes:
                        qid=f"c67_{phase}_{len(rows)}"
                        sql=P.qstr(queries[qi-1],t,u,cache)
                        start=time.monotonic_ns()
                        out=S.base.client(S.CT,sql,qid=qid,fmt=mode,
                                          check=False,timeout=500)
                        elapsed=(time.monotonic_ns()-start)/1e6
                        if out.returncode:
                            raise RuntimeError(f"Failed {phase} {qid}: "+
                                               out.stderr.decode(errors="replace")[-700:])
                        if mode=="Null" and out.stdout:
                            raise RuntimeError("Null emitted output")
                        if mode=="TSV" and out.stdout!=expected[(qi,t,arm)]:
                            raise RuntimeError(f"TSV result bytes differ: {phase} {qid}")
                        rows.append({"phase":phase,"round":rnd,"sql":qi,"table":t,
                                     "arm":arm,"output_mode":mode,"qid":qid,
                                     "order":len(rows),"client_ms":round(elapsed,6),
                                     "output_bytes":len(out.stdout),
                                     "output_sha256":hashlib.sha256(out.stdout).hexdigest()})
        print("C67_ROUND",phase,rnd,len(rows),flush=True)
    C.sql("SYSTEM FLUSH LOGS")
    raw=C.sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
              "FROM system.query_log WHERE type='QueryFinish' "
              "AND startsWith(query_id,'c67_"+phase+"_') FORMAT JSONEachRow")
    (R/phase).mkdir(parents=True,exist_ok=True)
    (R/phase/"query_log.jsonl").write_text(raw)
    byid={e["query_id"]:e for line in raw.splitlines() if line
          for e in [json.loads(line)]}
    if len(byid)!=len(rows):raise RuntimeError(
       f"Missing query log entries {phase}: {len(byid)} vs {len(rows)}")
    for row in rows:
        entry=byid.get(row["qid"])
        if entry is None:raise RuntimeError("Missing QueryFinish")
        events=entry.get("ProfileEvents") or {}
        row.update(ms=float(entry["query_duration_ms"]),
                   read_rows=int(entry["read_rows"]),read_bytes=int(entry["read_bytes"]),
                   stat_us=int(events.get("LoadedStatisticsMicroseconds",0)),
                   opt_us=int(events.get("QueryPlanOptimizeMicroseconds",0)),
                   selected_marks=int(events.get("SelectedMarks",0)))
    for name,subset in (("client_receipts.csv",rows),
                        ("receipts.csv",[r for r in rows if r["output_mode"]=="Null"])):
        with (R/phase/name).open("w",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader();writer.writerows(subset)
    if any(r["output_bytes"]<=0 for r in rows if r["output_mode"]=="TSV"):
        raise RuntimeError("SSB TSV result unexpectedly empty")
    print("C67_PHASE_VERIFIED",phase,len(rows),
          sum(r["output_bytes"] for r in rows if r["output_mode"]=="TSV"),
          flush=True)

def read_format(phase,table,arm,mode,metric):
    with (R/phase/"client_receipts.csv").open() as f:
        rows=[r for r in csv.DictReader(f) if r["table"]==table and
              r["arm"]==arm and r["output_mode"]==mode and int(r["round"])>=0]
    by={}
    for qi in range(1,14):
        samples=[float(r[metric]) for r in rows if int(r["sql"])==qi]
        if len(samples)!=(TARGET_REPS if phase=="target" else
                          3 if phase=="old_initial" else 4):
            raise RuntimeError("Incomplete cell "+str((phase,qi,table,arm,mode,metric)))
        by[qi]=statistics.median(samples)
    return {"ms":statistics.mean(statistics.mean(by[q] for q in fam)
                                  for fam in FAMILIES),"query_ms":by}

C.measure=client_measure

def main():
    save("PRESPECIFICATION.json",{
      "name":"C67 result-completion study",
      "baseline":"26.7 old_sham after same-version restart",
      "target":"26.8 inherited and rebuilt table, equal rows",
      "source":"pinned ClickBench SF1 SSB, 13 SQL, 4 equally weighted query families",
      "client_clock":"host monotonic wall including command startup and full returned stdout",
      "server_clock":"ClickHouse system.query_log.query_duration_ms",
      "formats":["Null","TSV"],
      "primary_policy":"statistics cache enabled",
      "diagnostics":["cache bypass","optimizer statistics disabled"],
      "intervention":"C67_RESTART=0/1 only at target measurement preparation",
      "worker_repetitions":TARGET_REPS,
      "warmups_per_cell":1,
      "run_group":os.environ.get("RUN_GROUP","unknown"),
      "os_page_cache":"not explicitly evicted",
      "limitations":["Docker exec client overhead is included",
                     "Docker-local result transfer is not remote-client transmission",
                     "one host per independent matrix cell"],
      "stop_rules":["SQL answer bytes must match across versions, paths and policies",
                    "all QueryFinish receipts present","part/checksum state stable",
                    "gate fixed 1.10 mean or at least 2 SQL >=1.50"]})
    for ver in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=650)
    qs=S.base.pinned_sources()
    native=S.base.make_data()
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    try:
        S.start(S.OLD)
        S.run(["docker","exec",S.CT,"clickhouse-client","--query", "CREATE DATABASE IF NOT EXISTS ssb"])
        cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
        P.create("raw",cols)
        C.create("ref",cols)
        with native.open("rb") as f:
            p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
               "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
                stdin=f,timeout=1000,check=False)
        if p.returncode:raise RuntimeError("Data import failed "+p.stderr.decode(errors="replace")[-1000:])
        native.unlink()
        cutoff,_,_=C.pick_cutoff()
        C.insert("ref","1",0)
        if not C.wait_idle(["ref"],limit=160)["idle"]:
            raise RuntimeError("Old-state merging did not quiesce")
        C.stop(["ref"])
        for t in ("raw","ref"):
            C.sql(f"ALTER TABLE ssb.{t} MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        old=snapshot("old_initial",cutoff,["ref"])
        C.measure("old_initial",("ref",),qs,3)
        if snapshot("old_initial_after_measure",cutoff,["ref"])["parts"]["ref"]!=old["parts"]["ref"]:
            raise RuntimeError("Original data moved during baseline")
        shutdown()
        S.start(S.OLD)
        C.stop(["ref"])
        sham=snapshot("old_sham_before",cutoff,["ref"])
        compare(old,sham,"sham restart")
        C.measure("old_sham",("ref",),qs,4)
        sham_end=snapshot("old_sham_after",cutoff,["ref"])
        compare(sham,sham_end,"old measurements")
        shutdown()
        S.start(S.NEW)
        C.stop(["ref"])
        inherited=snapshot("target_before_rebuild",cutoff,["ref"])
        compare(sham_end,inherited,"version upgrade")
        C.create("fresh",cols)
        C.insert("fresh","1",None)
        if not C.wait_idle(["fresh"],limit=180)["idle"]:
            raise RuntimeError("New-state merging did not quiesce")
        C.stop(["ref","fresh"])
        C.sql("ALTER TABLE ssb.fresh MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        prepare_target("native")
        before=snapshot("target_before_measure",cutoff,["ref","fresh"])
        if before["parts"]["ref"]["fingerprint"]!=before["parts"]["fresh"]["fingerprint"]:
            raise RuntimeError("Logical rows differ between target paths")
        C.measure("target",("ref","fresh"),qs,TARGET_REPS)
        after=snapshot("target_after_measure",cutoff,["ref","fresh"])
        compare(before,after,"target measurements")
        if before["parts"]["fresh"]!=after["parts"]["fresh"]:
            raise RuntimeError("Rebuild geometry changed during measurement")
        # Predeclared causal reversibility at the practical monthly-part layout.
        if before["parts"]["fresh"]["stats_parts"] != before["parts"]["fresh"]["parts"]:
            raise RuntimeError("Initial rebuild must have statistics on every part")
        identity=data_file_identity("fresh")
        save("identity/native.json",identity)
        crossphase_answers()
        report={}
        for arm in ARMS:
            report[arm]={}
            for mode in ("Null","TSV"):
                report[arm][mode]={}
                for metric in ("ms","client_ms"):
                    old=read_format("old_sham","ref",arm,mode,metric)
                    preserved=read_format("target","ref",arm,mode,metric)
                    fresh=read_format("target","fresh",arm,mode,metric)
                    report[arm][mode][metric]={
                        "preserved_upgrade":gate(old,preserved),
                        "rebuilt_upgrade":gate(old,fresh),
                        "rebuilt_vs_preserved":gate(preserved,fresh)}
        summary={"valid":True,"run_group":os.environ.get("RUN_GROUP","unknown"),
                 "restart":RESTART_PHASES,"nreps":TARGET_REPS,
                 "reference_parts":before["parts"]["ref"]["parts"],
                 "fresh_parts":before["parts"]["fresh"]["parts"],
                 "fresh_stats_parts":before["parts"]["fresh"]["stats_parts"],
                 "source_rows":before["parts"]["ref"]["rows"],
                 "client_boundary":"runner-side start of docker exec to complete "
                                   "stdout capture and process exit",
                 "server_boundary":"system.query_log.query_duration_ms",
                 "transport":"Docker exec local pipe, not network/WAN", "results":report}
        save("CLIENT_COMPARISONS.json",report)
        save("FINAL_STATUS.json",summary)
        for arm in ARMS:
            for mode in ("Null","TSV"):
                for metric in ("ms","client_ms"):
                    outcomes=report[arm][mode][metric]
                    print("C67_GATE",json.dumps({"arm":arm,"format":mode,
                          "metric":metric,"restart":RESTART_PHASES,
                          "worker":os.environ.get("RUN_GROUP","unknown"),
                          "preserved":outcomes["preserved_upgrade"],
                          "rebuilt":outcomes["rebuilt_upgrade"],
                          "path":outcomes["rebuilt_vs_preserved"]},sort_keys=True),
                          flush=True)
        print("C67_SUCCESS",json.dumps({
            "restart":RESTART_PHASES,"worker":os.environ.get("RUN_GROUP","unknown"),
            "parts":[before["parts"]["ref"]["parts"],before["parts"]["fresh"]["parts"]],
            "cache_on":{mode:{metric:{"ratio":report["cache_on"][mode][metric]["rebuilt_upgrade"]["ratio"],
                      "gate":report["cache_on"][mode][metric]["rebuilt_upgrade"]["gate"],
                      "severe":report["cache_on"][mode][metric]["rebuilt_upgrade"]["severe_sql"]}
                      for metric in ("ms","client_ms")} for mode in ("Null","TSV")}},
                      sort_keys=True),flush=True)
    finally:
        shutdown()
if __name__=="__main__":
    main()
