#!/usr/bin/env python3
"""A paired release-transition validation on one frozen source dataset."""
import csv, hashlib, json, pathlib, statistics, sys, time
import db_c42 as C

S=C.S
P=C.P
# A predeclared release-transition control, fixed before any measurement.
S.OLD="26.7.1.1315"
S.NEW="26.8.1.2041"
R=pathlib.Path("c65")
R.mkdir(exist_ok=True)
C.R=R
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c65_engine"
S.VOL="c65_storage"
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

def main():
    save("PRESPECIFICATION.json",{
        "comparison":"26.7.1.1315 -> 26.8.1.2041, two independently rebuilt old-version table replicas plus one new-version reconstruction",
        "study_type":"Negative-control replicate of physical construction under the SAME source version",
        "prespecified_question":"Do two independently created 26.7 tables resemble each other more than a table rebuilt in 26.8?",
        "scope":"Within 26.7 baseline and within 26.8 target, 13 official SSB SQLs",
        "confounds":"old reference replicas may differ in asynchronous merges, stats counts and part geometry; report such differences",
        "do_not_claim":"Not byte-identical physical replicas, not randomized host trials",
        "reference_study":"C57-C60, same version transition and source population",
        "prespecified_null":"No requirement for a negative verdict; either direction reported",
        "analysis_rule":"Identical four SSB families, same 10 percent and >=2 SQL 1.5x guards, no exclusions",
        "versions":[S.OLD,S.NEW],"source":"Pinned SSB SF1", "sql_count":13,
        "gate":"BLOCK iff family-weighted latency ratio > 1.10 or >= 2 distinct SQL at >= 1.5x",
        "primary_arm":"cache_on","diagnostics":["cache_off","stats_off"],
        "baseline":"Both independent old-version tables after 26.7 same-version restart",
        "scope":"one release boundary and one SF1 SSB SQL population",
        "measurement_protocol":"Three initial and four sham/target timed repetitions, 13 SQL, three settings, randomized query order",
        "replicate":__import__("os").environ.get("RUN_GROUP","unknown"),
        "measurement":"same runner within run; independent SQL medians then 4-family equal weighting",
        "stop_rules":["abort if physical/checksum mismatch","abort if SQL results differ",
                      "no threshold changes after measurement"],
        "limits":["restart does not guarantee cold OS page cache",
                  "rebuilding is an evaluation path, not in-place user-data migration",
                  "sequential query latency, not production concurrent throughput",
                  "hosted-runner environment; one runner is not host-level replication"]})
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
        C.create("replica",cols)
        with native.open("rb") as f:
            p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
               "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
                stdin=f,timeout=1000,check=False)
        if p.returncode:raise RuntimeError("Data import failed "+p.stderr.decode(errors="replace")[-1000:])
        native.unlink()
        cutoff,_,_=C.pick_cutoff()
        # Each preserved table is independently constructed from identical rows
        # under the SAME old software version, independently settled, and frozen.
        C.insert("ref","1",0)
        if not C.wait_idle(["ref"],limit=160)["idle"]:
            raise RuntimeError("Reference old-version merges did not quiesce")
        C.insert("replica","1",0)
        if not C.wait_idle(["ref","replica"],limit=160)["idle"]:
            raise RuntimeError("Same-version replica merges did not quiesce")
        C.stop(["ref","replica"])
        for t in ("raw","ref","replica"):
            C.sql(f"ALTER TABLE ssb.{t} MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        old_settings=C.sql("SELECT name,value FROM system.settings WHERE name IN "
             "('materialize_statistics_on_insert','use_statistics','use_statistics_cache',"
             "'use_query_condition_cache') ORDER BY name FORMAT TSV")
        save("old_settings.json",{"server":C.sql("SELECT version() FORMAT TSV").strip(),
             "setting_rows":old_settings.strip().splitlines()})
        print("C65_OLD_SETTINGS",repr(old_settings),flush=True)
        old=snapshot("old_initial",cutoff,["ref","replica"])
        if old["parts"]["ref"]["fingerprint"]!=old["parts"]["replica"]["fingerprint"]:
            raise RuntimeError("Same-version replica logical mismatch")
        save("old_replica_balance.json",{
          "ref_parts":old["parts"]["ref"]["parts"],
          "replica_parts":old["parts"]["replica"]["parts"],
          "ref_stats_parts":old["parts"]["ref"]["stats_parts"],
          "replica_stats_parts":old["parts"]["replica"]["stats_parts"]})
        C.measure("old_initial",("ref","replica"),qs,3)
        old_end=snapshot("old_initial_after_measure",cutoff,["ref","replica"])
        if old_end["parts"]!=old["parts"] or old_end["part_checksums"]!=old["part_checksums"]:
            raise RuntimeError("Original part inventory changed during baseline")
        shutdown()
        S.start(S.OLD)
        C.stop(["ref","replica"])
        sham=snapshot("old_sham_before",cutoff,["ref","replica"])
        compare(old_end,sham,"sham restart")
        if sham["parts"]["replica"]!=old["parts"]["replica"]:
            raise RuntimeError("Replica changed during same-version restart")
        C.measure("old_sham",("ref","replica"),qs,4)
        sham_end=snapshot("old_sham_after",cutoff,["ref","replica"])
        compare(sham,sham_end,"old measurements")
        if sham_end["parts"]["replica"]!=sham["parts"]["replica"]:
            raise RuntimeError("Replica changed during old measurements")
        shutdown()
        S.start(S.NEW)
        C.stop(["ref","replica"])
        inherited=snapshot("target_before_rebuild",cutoff,["ref","replica"])
        compare(sham_end,inherited,"version upgrade")
        if sham_end["parts"]["replica"]!=inherited["parts"]["replica"]:
            raise RuntimeError("Old-version replica changed during upgrade")
        C.create("fresh",cols)
        C.insert("fresh","1",None)
        if not C.wait_idle(["fresh"],limit=180)["idle"]:
            raise RuntimeError("New-state merging did not quiesce")
        C.stop(["ref","replica","fresh"])
        C.sql("ALTER TABLE ssb.fresh MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=0")
        # Record actual statistics default and feature support on both sides.
        settings=C.sql("SELECT name,value FROM system.settings WHERE name IN "
              "('materialize_statistics_on_insert','use_statistics','use_statistics_cache',"
              "'use_query_condition_cache') ORDER BY name FORMAT TSV")
        save("target_settings.json",{"server":C.sql("SELECT version() FORMAT TSV").strip(),
             "setting_rows":settings.strip().splitlines()})
        print("C65_TARGET_SETTINGS",repr(settings),flush=True)
        before=snapshot("target_before_measure",cutoff,["ref","replica","fresh"])
        if before["parts"]["ref"]["fingerprint"]!=before["parts"]["fresh"]["fingerprint"]:
            raise RuntimeError("Logical rows differ between target paths")
        C.measure("target",("ref","replica","fresh"),qs,4)
        after=snapshot("target_after_measure",cutoff,["ref","replica","fresh"])
        compare(before,after,"target measurements")
        if before["parts"]!=after["parts"] or before["part_checksums"]!=after["part_checksums"]:
            raise RuntimeError("Some old or new physical part/checksum drifted")
        # Explicitly verify correctness for the independent 26.7 construction.
        check_rows=json.loads((R/"old_sham"/"answers.json").read_text())
        target_rows=json.loads((R/"target"/"answers.json").read_text())
        for q in range(1,14):
            for arm in ARMS:
                ans=check_rows[f"{q}/ref/{arm}"]
                for phase,table,entries in (("old_sham","replica",check_rows),
                    ("target","ref",target_rows),("target","replica",target_rows),
                    ("target","fresh",target_rows)):
                    if entries[f"{q}/{table}/{arm}"]!=ans:
                        raise RuntimeError("Replica or target SQL output mismatch "+str((q,arm,phase,table)))
        # Source reference and newly reconstructed target remain comparable.
        crossphase_answers()
        out={}
        for arm in ARMS:
            old0=read_phase("old_initial","ref",arm)
            base=read_phase("old_sham","ref",arm)
            preserved=read_phase("target","ref",arm)
            rebuilt=read_phase("target","fresh",arm)
            out[arm]={"restart_control":gate(old0,base),
                      "preserved_upgrade":gate(base,preserved),
                      "rebuilt_upgrade":gate(base,rebuilt),
                      "rebuilt_vs_preserved":gate(preserved,rebuilt)}
        # Difference against two independent old-version table constructions
        # rather than relying on a single path that may have atypical merges.
        control={}
        for arm in ARMS:
            old_ref=read_phase("old_sham","ref",arm)
            old_rep=read_phase("old_sham","replica",arm)
            upgraded_ref=read_phase("target","ref",arm)
            upgraded_rep=read_phase("target","replica",arm)
            fresh=read_phase("target","fresh",arm)
            control[arm]={
              "same_version_rebuild_control":gate(old_ref,old_rep),
              "both_old_parts_at_new_version":gate(upgraded_ref,upgraded_rep),
              "new_construction_vs_old_ref":gate(upgraded_ref,fresh),
              "new_construction_vs_old_rep":gate(upgraded_rep,fresh),
              "ref_upgrade":gate(old_ref,upgraded_ref),
              "replica_upgrade":gate(old_rep,upgraded_rep),
              "new_construction_upgrade_against_ref":gate(old_ref,fresh),
              "new_construction_upgrade_against_replica":gate(old_rep,fresh)}
        save("CONTROL_CONTRASTS.json",control)
        print("C65_CONTROL_CONTRASTS",json.dumps(control,sort_keys=True),flush=True)
        save("DECISIONS.json",out)
        save("FINAL_STATUS.json",{"valid":True,"engines":[S.OLD,S.NEW],
            "source_rows":before["parts"]["ref"]["rows"],"cutoff":cutoff,
            "comparison":out,
            "fresh_stats_parts":before["parts"]["fresh"]["stats_parts"],
            "inherited_stats_parts":before["parts"]["ref"]["stats_parts"],
            "warning":"Each run is a single hosted runner; compare independent runner outputs.",
            "replicate":__import__("os").environ.get("RUN_GROUP","unknown"),
            "same_version_construction_control":control})
        print("C65_RESULT",json.dumps(out,sort_keys=True),flush=True)
    finally:
        shutdown()
if __name__=="__main__":
    main()
