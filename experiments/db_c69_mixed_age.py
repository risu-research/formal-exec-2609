#!/usr/bin/env python3
"""C69 prespecified mixed-age deployment-path evaluation, primary five paths.

All datasets hold the exact same SF1 logical rows. Four monthly partition
fractions are replaced after the 26.7 -> 26.8 upgrade; a fifth condition
keeps only preserved parts. A planned 26.7 partial-rebuild control was
excluded before timing because its physical part state changed on restart.
Full HTTP TSV completion is primary, server QueryFinish is diagnostic.
This is controlled scenario evidence, not production prevalence.
"""
import csv
import hashlib
import json
import os
import pathlib
import random
import statistics
import time
import db_c68_http_client as E

S=E.S
C=E.C
P=E.P
R=pathlib.Path("c69").resolve()
R.mkdir(exist_ok=True)
E.R=R
C.R=R
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c69_server"
S.VOL="c69_storage"
E.HTTP_BENCH_KEY=None
E.TARGET_REPS=4
E.RESTART_FLAG=os.environ.get("C69_RESTART","")
if E.RESTART_FLAG not in ("0","1"):
    raise ValueError("Set C69_RESTART to 0 or 1")
E.RESTART_PHASES=E.RESTART_FLAG=="1"
GROUP=os.environ.get("RUN_GROUP","unknown")
T=(("retained",0),("age25",25),("age50",50),("age75",75),
   ("rebuilt",100))
NAMES=tuple(t for t,_ in T)
ARMS=("cache_on","cache_off","stats_off")
FAMILIES=(range(1,4),range(4,7),range(7,11),range(11,14))
FRACS=(25,50,75,100)
ROW_TARGET=6001215

def dump(filename,data):
    p=R/filename
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,sort_keys=True,indent=2)+"\n")

def query(s):
    return C.sql(s)

def months_and_cutoffs():
    raw=query("SELECT toYYYYMM(LO_ORDERDATE),count() FROM ssb.raw "
              "GROUP BY toYYYYMM(LO_ORDERDATE) ORDER BY 1 FORMAT TSV")
    month_rows={int(a):int(b) for line in raw.splitlines()
                for a,b in [line.split("\t")]}
    total=sum(month_rows.values())
    if total!=ROW_TARGET or len(month_rows)<30:
        raise RuntimeError(f"Unexpected SF1 month population {len(month_rows)} {total}")
    ordering=sorted(month_rows,key=lambda x:hashlib.sha256(
                      ("c69-fixed-month-order:"+str(x)).encode()).hexdigest())
    selected={0:[]}
    for pct in FRACS:
        if pct==100:
            selected[pct]=list(ordering)
        else:
            want=total*pct/100.0
            best=min(range(len(ordering)+1),
                     key=lambda n:abs(sum(month_rows[x] for x in ordering[:n])-want))
            selected[pct]=ordering[:best]
        frac=sum(month_rows[x] for x in selected[pct])/total
        if abs(frac-pct/100.0)>.025:
            raise RuntimeError("Unable to select target fraction "+str(pct))
    if any(not set(selected[FRACS[i]]).issubset(selected[FRACS[i+1]])
           for i in range(len(FRACS)-1)):
        raise RuntimeError("Selected partitions not nested")
    result={"month_rows":month_rows,"fixed_order":ordering,
            "selected_by_nominal_percent":selected,
            "actual_row_fraction":{str(k):sum(month_rows[x] for x in v)/total
                                   for k,v in selected.items()},
            "selector":"SHA-256 fixed month order and nearest target row mass",
            "total_rows":total}
    dump("month_plan.json",result)
    print("C69_MONTH_PLAN",json.dumps({"months":len(ordering),
          "actual_fractions":result["actual_row_fraction"]},sort_keys=True),flush=True)
    return month_rows,selected

def create_data_tables(cols):
    for t in NAMES:
        C.create(t,cols)
        C.insert(t,"1",0)
        if not C.wait_idle([t],limit=180)["idle"]:
            raise RuntimeError("Initial source table failed to settle "+t)
        C.stop([t])
        query(f"ALTER TABLE ssb.{t} MODIFY SETTING "
              "max_bytes_to_merge_at_max_space_in_pool=0")
        print("C69_OLD_TABLE_BUILT",t,flush=True)

def replace_months(t,chosen,policy):
    """Exactly replace, without duplicating, the canonical rows of each month."""
    if policy not in (0,1):
        raise ValueError("Invalid materialization policy")
    all_months=sorted(chosen)
    if not all_months:
        return
    for i,month in enumerate(all_months):
        # The canonical raw table stays fixed, with materialization disabled
        # at source creation. The destination insert can create new statistics.
        query(f"ALTER TABLE ssb.{t} DROP PARTITION {month} "
              "SETTINGS mutations_sync=2")
        query(f"INSERT INTO ssb.{t} SELECT * FROM ssb.raw "
              f"WHERE toYYYYMM(LO_ORDERDATE)={month} "
              "SETTINGS materialize_statistics_on_insert="+str(policy)+","
              "max_threads=2,max_insert_threads=1,"
              "min_insert_block_size_rows=100000,"
              "min_insert_block_size_bytes=1")
        if (i+1)%15==0 or i+1==len(all_months):
            print("C69_REPLACED",t,i+1,len(all_months),flush=True)
    if not C.wait_idle([t],limit=150)["idle"]:
        raise RuntimeError("Merge activity remained "+t)
    C.stop([t])

def census(label,selected):
    """Collect physical geometry, provenance tags and logical identity."""
    tables={}
    for name,pct in T:
        raw=query("SELECT partition,name,rows,bytes_on_disk,marks "
                  "FROM system.parts WHERE active AND database='ssb' AND "
                  f"table='{name}' ORDER BY partition,name FORMAT TSV")
        vals=[line.split("\t") for line in raw.splitlines() if line]
        if not vals:raise RuntimeError("Empty physical inventory "+name)
        tagged=set(selected[pct]) if name!="age50_old" else set()
        new_rows=sum(int(v[2]) for v in vals if int(v[0]) in tagged)
        new_bytes=sum(int(v[3]) for v in vals if int(v[0]) in tagged)
        new_parts=sum(int(v[0]) in tagged for v in vals)
        all_rows=sum(int(v[2]) for v in vals)
        all_bytes=sum(int(v[3]) for v in vals)
        stats=C.partmap(label+"_"+name,(name,),min(selected[25]))[name]["stats_parts"]
        tables[name]={"part_count":len(vals),"rows":all_rows,
                      "marks":sum(int(v[4]) for v in vals),
                      "bytes_on_disk":all_bytes,"stats_parts":stats,
                      "new_parts_tagged":new_parts,"new_rows_tagged":new_rows,
                      "new_bytes_tagged":new_bytes,
                      "actual_new_row_fraction":new_rows/all_rows,
                      "actual_new_byte_fraction":new_bytes/all_bytes,
                      "logical_fp":P.fp(name),
                      "inventory_sha256":hashlib.sha256(raw.encode()).hexdigest()}
        if all_rows!=ROW_TARGET:
            raise RuntimeError(f"Row count mismatch {label} {name} {all_rows}")
    fingerprint={v["logical_fp"] for v in tables.values()}
    if len(fingerprint)!=1:
        raise RuntimeError("Logical row fingerprint mismatch "+label)
    dump(label+"/census.json",tables)
    print("C69_CENSUS",label,json.dumps({n:{
        "parts":x["part_count"],"stats":x["stats_parts"],
        "new_row_fraction":round(x["actual_new_row_fraction"],4)}
        for n,x in tables.items()},sort_keys=True),flush=True)
    return tables

def stable(x,y):
    for name in NAMES:
        for key in ("rows","part_count","stats_parts",
                    "inventory_sha256","logical_fp"):
            if x[name][key]!=y[name][key]:
                raise RuntimeError("Physical data changed across measurement "+
                                   name+" "+key)

def summary_metrics(phase,table,arm,mode,metric):
    with (R/phase/"client_receipts.csv").open() as f:
        rows=[r for r in csv.DictReader(f) if r["table"]==table
              and r["arm"]==arm and r["output_mode"]==mode and
              int(r["round"])>=0]
    samples={}
    for q in range(1,14):
        values=[float(r[metric]) for r in rows if int(r["sql"])==q]
        expected=4
        if len(values)!=expected:
            raise RuntimeError("Incomplete cell "+str((phase,table,arm,mode,q)))
        samples[q]=values
    by={q:statistics.median(v) for q,v in samples.items()}
    fam=[statistics.mean(by[q] for q in group) for group in FAMILIES]
    return {"ms":statistics.mean(fam),"query_ms":by,
            "families_ms":fam,
            "replicate_values_ms":samples}

def decision(b,target):
    return E.gate(b,target)

def verify_phase_answer_equal():
    base=json.loads((R/"old_sham"/"answers.json").read_text())
    new=json.loads((R/"target"/"answers.json").read_text())
    sizes=json.loads((R/"target"/"answer_sizes.json").read_text())
    for q in range(1,14):
        for arm in ARMS:
            reference=base[f"{q}/retained/{arm}"]
            for t in NAMES:
                key=f"{q}/{t}/{arm}"
                if new[key]!=reference:
                    raise RuntimeError("SQL answer changed between release paths: "+key)
                if sizes[key]["bytes"]<1:
                    raise RuntimeError("Missing full response "+key)
    print("C69_OUTPUT_IDENTITY_OK",len(new),flush=True)

def analyse(before,after):
    rows=[]
    index={}
    for arm in ARMS:
        index[arm]={}
        for mode in ("Null","TSV"):
            index[arm][mode]={}
            for metric in ("ms","client_ms"):
                base=summary_metrics("old_sham","retained",arm,mode,metric)
                by={}
                for t in NAMES:
                    newer=summary_metrics("target",t,arm,mode,metric)
                    g=decision(base,newer)
                    by[t]=g
                    rows.append({"arm":arm,"format":mode,"metric":metric,
                                 "scenario":t,"nominal_new_pct":dict(T)[t],
                                 "actual_new_row_frac":after[t]["actual_new_row_fraction"],
                                 "old_ms":round(base["ms"],6),
                                 "new_ms":round(newer["ms"],6),
                                 "ratio":round(g["ratio"],7),"gate":g["gate"],
                                 "severe_sql":",".join(map(str,g["severe_sql"])),
                                 "parts":after[t]["part_count"],
                                 "stats_parts":after[t]["stats_parts"],
                                 "new_bytes_frac":after[t]["actual_new_byte_fraction"]})
                index[arm][mode][metric]=by
    with (R/"decision_matrix.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    decisions=index["cache_on"]["TSV"]["client_ms"]
    proxy=decisions["rebuilt"]["gate"]
    mismatches={name:decisions[name]["gate"]!=proxy
                for name in NAMES if name!="rebuilt"}
    report={"valid":True,"worker":GROUP,"restart":E.RESTART_PHASES,
            "parts":after,"decision_matrix":index,
            "proxy_full_rebuild_gate":proxy,
            "proxy_disagreements":mismatches,
            "scenario_disagreement_count":sum(mismatches.values()),
            "scenario_disagreement_denominator":len(mismatches),
            "scope":"fixed-data monthly-part scenarios; no production trace",
            "metric":"local Docker bridge complete HTTP TSV response",
            "threshold":"mean >1.10 OR two distinct SQL >=1.50x",
            "new_row_fractions":{t:after[t]["actual_new_row_fraction"]
                                 for t in NAMES}}
    dump("FINAL_STATUS.json",report)
    print("C69_DECISION_SUMMARY",json.dumps({
        "worker":GROUP,"restart":E.RESTART_PHASES,
        "primary":{k:{"ratio":round(v["ratio"],4),"gate":v["gate"],
                      "severe_sql":v["severe_sql"]}
                   for k,v in decisions.items()},
        "proxy":proxy,"proxy_disagreements":mismatches,
        "diag_cache_bypass":{k:{"ratio":round(v["ratio"],4),"gate":v["gate"]}
           for k,v in index["cache_off"]["TSV"]["client_ms"].items()},
        "diag_stats_off":{k:{"ratio":round(v["ratio"],4),"gate":v["gate"]}
           for k,v in index["stats_off"]["TSV"]["client_ms"].items()}
        },sort_keys=True),flush=True)
    return report

def run():
    dump("PRESPECIFICATION.json",{
        "study":"C69 fixed-logical-data mixed-age deployment-path audit",
        "source":"Pinned ClickBench SSB SF1",
        "sql_population":"all 13 SSB SQL, no outcome-driven query selection",
        "versions":[S.OLD,S.NEW],
        "states":[{"name":n,"planned_pct":p} for n,p in T],
        "old_rebuild_control":"deferred to separate validation; unstable across sham server restart",
        "monthly_selector":"SHA-256 seed c69-fixed-month-order, nearest row fraction",
        "primary":"complete HTTP TSV response, use_statistics_cache=1",
        "other_receipts":["server query_duration_ms","Null result","cache bypass","statistics off"],
        "baseline":"26.7 same-version restarted retained state",
        "gate":"BLOCK if 4-family mean latency ratio >1.10 or >=2 SQL median ratios >=1.5",
        "warmups_per_cell":1,"timed_repetitions_per_cell":4,
        "preparation":"process restart before target when C69_RESTART=1",
        "restart_flag":E.RESTART_PHASES,"worker":GROUP,
        "runner_design":"paired all scenarios within independent GitHub hosted runner",
        "no_os_cache_flush":True,"no_assumed_production_mix":True,
        "no_observed_slo_or_representative_replay":True,
        "stop_rules":["fail if SQL results differ","fail on incorrect row fingerprints",
                      "fail on incomplete query logs","fail on physical inventory change"]})
    for v in (S.OLD,S.NEW):
        S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=750)
    qs=S.base.pinned_sources()
    raw=S.base.make_data()
    S.run(["docker","volume","rm",S.VOL],check=False)
    S.run(["docker","volume","create",S.VOL])
    try:
        S.start(S.OLD)
        S.run(["docker","exec",S.CT,"clickhouse-client","--query",
               "CREATE DATABASE IF NOT EXISTS ssb"])
        cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
        P.create("raw",cols)
        with raw.open("rb") as f:
            p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
                 "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 "
                 "FORMAT Native"],stdin=f,timeout=1200,check=False)
        if p.returncode:
            raise RuntimeError("Source load failed "+p.stderr.decode(errors="replace")[-500:])
        raw.unlink()
        month_rows,selected=months_and_cutoffs()
        create_data_tables(cols)
        baseline=census("before_version_restart",selected)
        E.shutdown()
        S.start(S.OLD)
        C.stop(NAMES)
        old_sham=census("old_sham_before",selected)
        stable(baseline,old_sham)
        E.client_measure("old_sham",("retained",),qs,4)
        old_done=census("old_sham_after",selected)
        stable(old_sham,old_done)
        E.shutdown()
        S.start(S.NEW)
        C.stop(NAMES)
        newly_on_268=census("target_before_rebuild",selected)
        stable(old_done,newly_on_268)
        for t,frac in T:
            if t not in ("retained","age50_old"):
                replace_months(t,selected[frac],1)
        if E.RESTART_PHASES:
            E.reset_engine_cache("mixed_age")
        before=census("target_before_measure",selected)
        E.client_measure("target",NAMES,qs,4)
        after=census("target_after_measure",selected)
        stable(before,after)
        verify_phase_answer_equal()
        analyse(baseline,after)
    finally:
        E.shutdown()

if __name__=="__main__":
    run()
