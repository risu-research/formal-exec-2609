#!/usr/bin/env python3
"""A paired release-transition validation on one frozen source dataset."""
import csv, hashlib, json, pathlib, statistics, sys, time
import db_c42 as C

S=C.S
P=C.P
R=pathlib.Path("c59")
R.mkdir(exist_ok=True)
C.R=R
P.R=R
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c59_engine"
S.VOL="c59_storage"
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
    C.sql(command)
    pending=int(C.sql("SELECT count() FROM system.mutations WHERE database='ssb' "
                      "AND table='fresh' AND is_done=0 FORMAT TSV").strip())
    if pending:raise RuntimeError("Unfinished statistics mutation "+label)

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

def main():
    save("PRESPECIFICATION.json",{
        "comparison":"Single 26.7 source, same-version restart control, 26.8 preserved vs freshly rebuilt",
        "versions":[S.OLD,S.NEW],"source":"Pinned SSB SF1", "sql_count":13,
        "gate":"BLOCK iff family-weighted latency ratio > 1.10 or >= 2 distinct SQL at >= 1.5x",
        "primary_arm":"cache_on","diagnostics":["cache_off","stats_off"],
        "baseline":"26.7 old_sham, same preserved parts after same-version restart",
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
        before=snapshot("target_before_measure",cutoff,["ref","fresh"])
        if before["parts"]["ref"]["fingerprint"]!=before["parts"]["fresh"]["fingerprint"]:
            raise RuntimeError("Logical rows differ between target paths")
        C.measure("target",("ref","fresh"),qs,6)
        after=snapshot("target_after_measure",cutoff,["ref","fresh"])
        compare(before,after,"target measurements")
        if before["parts"]["fresh"]!=after["parts"]["fresh"]:
            raise RuntimeError("Rebuild geometry changed during measurement")
        # Predeclared causal reversibility at the practical monthly-part layout.
        if before["parts"]["fresh"]["stats_parts"] != before["parts"]["fresh"]["parts"]:
            raise RuntimeError("Initial rebuild must have statistics on every part")
        identity=data_file_identity("fresh")
        save("identity/native.json",identity)
        stats_mutation("ALTER TABLE ssb.fresh CLEAR STATISTICS ALL","cleared")
        clear_before=snapshot("cleared_before",cutoff,["ref","fresh"])
        if clear_before["parts"]["fresh"]["stats_parts"]!=0:
            raise RuntimeError("CLEAR did not remove statistics objects")
        confirm_data_identity(identity,"cleared")
        C.measure("cleared",("ref","fresh"),qs,4)
        clear_after=snapshot("cleared_after",cutoff,["ref","fresh"])
        compare(clear_before,clear_after,"cleared phase ref")
        if clear_before["parts"]["fresh"]!=clear_after["parts"]["fresh"]:
            raise RuntimeError("Mutation-phase part inventory changed during query measurements")
        confirm_data_identity(identity,"cleared_after")
        stats_mutation("ALTER TABLE ssb.fresh MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2","restored")
        restored_before=snapshot("restored_before",cutoff,["ref","fresh"])
        if restored_before["parts"]["fresh"]["stats_parts"]!=restored_before["parts"]["fresh"]["parts"]:
            raise RuntimeError("MATERIALIZE did not restore all statistics objects")
        confirm_data_identity(identity,"restored")
        C.measure("restored",("ref","fresh"),qs,4)
        restored_after=snapshot("restored_after",cutoff,["ref","fresh"])
        compare(restored_before,restored_after,"restored phase ref")
        if restored_before["parts"]["fresh"]!=restored_after["parts"]["fresh"]:
            raise RuntimeError("Restored part inventory changed during query measurements")
        confirm_data_identity(identity,"restored_after")
        compare_answers_by_phase(("target","cleared","restored"))
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
        causal={}
        for arm in ARMS:
            base=read_phase("old_sham","ref",arm)
            initial=read_phase("target","fresh",arm)
            cleared=read_phase("cleared","fresh",arm)
            restored=read_phase("restored","fresh",arm)
            causal[arm]={"native":gate(base,initial),"cleared":gate(base,cleared),
                         "restored":gate(base,restored),
                         "cleared_vs_native":gate(initial,cleared),
                         "restored_vs_cleared":gate(cleared,restored)}
        save("REVERSAL_GATES.json",causal)
        print("C59_REVERSAL",json.dumps(causal,sort_keys=True),flush=True)
        save("DECISIONS.json",out)
        save("FINAL_STATUS.json",{"valid":True,"engines":[S.OLD,S.NEW],
            "source_rows":before["parts"]["ref"]["rows"],"cutoff":cutoff,
            "comparison":out,
            "fresh_stats_parts":before["parts"]["fresh"]["stats_parts"],
            "inherited_stats_parts":before["parts"]["ref"]["stats_parts"],
            "warning":"Each run is a single hosted runner; compare independent runner outputs.",
            "replicate":__import__("os").environ.get("RUN_GROUP","unknown")})
        print("C59_RESULT",json.dumps(out,sort_keys=True),flush=True)
    finally:
        shutdown()
if __name__=="__main__":
    main()
