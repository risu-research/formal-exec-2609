#!/usr/bin/env python3
"""Stage24: query-planner CPU stacks in pinned ClickHouse 26.7 vs 26.8.
This file profiles CPU samples, never invents per-pass wall timings.
"""
import csv,hashlib,json,pathlib,statistics,subprocess,sys,time
import db_sf1_probe as base

ROOT=pathlib.Path("s24").resolve()
base.ROOT=ROOT
VERSIONS=base.VERSIONS
QUERIES=[2,3,4]  # Q1.2, Q1.3, Q2.1
REPS=18
PERIOD_NS=250000
FLAGS={"ON":"optimize_move_to_prewhere=1","OFF":"optimize_move_to_prewhere=0"}
KEYWORDS=("QueryPlan::optimize", "optimizeTreeSecondPass", "optimizePrewhere",
          "MergeTreeWhereOptimizer", "getColumnSizes", "getConditionSelectivityEstimator",
          "updatePrewhereInfo", "optimizeReadInOrder", "optimizePrimaryKeyConditionAndLimit",
          "optimizeTreeFirstPass","ReadFromMergeTree")
def execsql(name,query,ct,limit=240):
 p=base.client(ct,query,check=False,timeout=limit)
 if p.returncode:raise RuntimeError(f"{name}: "+p.stderr.decode(errors="replace")[-1800:])
 return p.stdout.decode(errors="replace")
def mk_server(ver,native):
 ct="s24_"+ver.replace(".","_")
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144",
           "clickhouse/clickhouse-server:"+ver],timeout=120)
 for _ in range(100):
  if not base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=10).returncode:break
  time.sleep(0.7)
 else:raise RuntimeError("Could not start "+ver)
 base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
 ddl=f"CREATE TABLE ssb.lineorder_flat ({cols}) ENGINE=MergeTree PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE, LO_ORDERKEY)"
 execsql("ddl",ddl,ct)
 with open(native,"rb") as f:
  base.run(["docker","exec","-i",ct,"clickhouse-client","--query","INSERT INTO ssb.lineorder_flat FORMAT Native"],stdin=f,timeout=1050)
 execsql("stop_merges","SYSTEM STOP MERGES ssb.lineorder_flat",ct)
 return ct
def run_version(ver,native,qs):
 directory=ROOT/ver
 directory.mkdir(parents=True,exist_ok=True)
 ct=mk_server(ver,native)
 try:
  fp=execsql("fingerprint","SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) FROM ssb.lineorder_flat FORMAT TSV",ct).strip()
  (directory/"fingerprint.tsv").write_text(fp+"\n")
  # Do not guess settings or schema. Verify capabilities and log actual supported fields.
  settings=execsql("setting_inventory",
     "SELECT name, value FROM system.settings WHERE name LIKE '%prewhere%' OR name LIKE '%statistics%' OR name LIKE '%query_plan%' OR name LIKE '%profiler%' ORDER BY name FORMAT TSV",
     ct)
  (directory/"settings_inventory.tsv").write_text(settings)
  columns=execsql("trace_cols",
     "SELECT name FROM system.columns WHERE database='system' AND table='trace_log' ORDER BY name FORMAT TSV",ct).split()
  (directory/"trace_columns.json").write_text(json.dumps(columns,indent=2)+"\n")
  if "symbols" not in columns:raise RuntimeError("symbolized trace_log unavailable; no hotspot attribution made")
  header=["version","q","flag","rep","qid","elapsed_client_ms","server_ms","plan_us","samples"]
  records=[]
  # Deterministic balanced within each query; ON and OFF alternate over rounds.
  for rep in range(REPS+1):
   for q in QUERIES:
    for flag in (["ON","OFF"] if (rep+q)%2==0 else ["OFF","ON"]):
     qid=f"s24_q{q:02d}_{flag}_r{rep}"
     setting=FLAGS[flag]+f",query_profiler_cpu_time_period_ns={PERIOD_NS},query_profiler_real_time_period_ns=0"
     t=time.perf_counter()
     p=base.client(ct,qs[q-1]+" SETTINGS "+setting,qid=qid,fmt="Null",check=False,timeout=170)
     if p.returncode:raise RuntimeError("Profile query failed "+qid+": "+p.stderr.decode(errors="replace")[-900:])
     records.append({"version":ver,"q":q,"flag":flag,"rep":rep,"qid":qid,"elapsed_client_ms":round(1000*(time.perf_counter()-t),3)})
  execsql("flush_logs","SYSTEM FLUSH LOGS",ct)
  querylogs=execsql("querylogs","SELECT query_id,query_duration_ms,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s24_') FORMAT JSONEachRow",ct)
  qmap={}
  for line in querylogs.splitlines():
   if not line.strip():continue
   x=json.loads(line);qmap[x["query_id"]]=x
  if len(qmap)!=len(records):raise RuntimeError(f"Missing server timing records: {len(qmap)} vs {len(records)}")
  for x in records:
   ev=qmap[x["qid"]].get("ProfileEvents") or {}
   x["server_ms"]=qmap[x["qid"]]["query_duration_ms"]
   x["plan_us"]=ev.get("QueryPlanOptimizeMicroseconds")
  # Return compact aggregate counters and stacks; no huge raw trace export needed.
  samples=execsql("counts",
     "SELECT query_id,count() samples FROM system.trace_log WHERE trace_type='CPU' AND startsWith(query_id,'s24_') GROUP BY query_id FORMAT TSV",ct,limit=270)
  byqid={}
  for line in samples.splitlines():
   v,n=line.split("\t");byqid[v]=int(n)
  for x in records:x["samples"]=byqid.get(x["qid"],0)
  with (directory/"query_runs.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=header);w.writeheader();w.writerows(records)
  summaries=[]
  for q in QUERIES:
   for flag in FLAGS:
    select=f"WHERE trace_type='CPU' AND startsWith(query_id,'s24_q{q:02d}_{flag}_') AND NOT endsWith(query_id,'_r0')"
    # arrayJoin(symbols) traces exact observed function frames in release binary.
    top=execsql("top_functions",f"SELECT arrayJoin(symbols) frame,count() frame_samples FROM system.trace_log {select} GROUP BY frame ORDER BY frame_samples DESC LIMIT 130 FORMAT TSV",ct)
    (directory/f"q{q:02d}_{flag}_top_functions.tsv").write_text(top)
    for keyword in KEYWORDS:
     safe=keyword.replace("'","''")
     qry=(f"SELECT count(),countIf(arrayExists(s -> position(s,'{safe}')>0,symbols)) "
          f"FROM system.trace_log {select} FORMAT TSV")
     counts=execsql("frame_count_"+keyword,qry,ct).strip()
     v=counts.split("\t")
     summaries.append({"version":ver,"q":q,"flag":flag,"keyword":keyword,"all_cpu_samples":int(v[0]),"stack_matches":int(v[1])})
    # Raw example stacks only for relevant optimizer samples, enough to audit call paths.
    raw=execsql("optimizer_stack",
        "SELECT arrayStringConcat(symbols,' || ') stack,count() sample_count FROM system.trace_log "+
        select+" AND arrayExists(s -> position(s,'QueryPlan::optimize')>0,symbols) "
        "GROUP BY stack ORDER BY sample_count DESC LIMIT 80 FORMAT TSV",ct)
    (directory/f"q{q:02d}_{flag}_optimizer_stacks.tsv").write_text(raw)
    filtered=[x for x in records if x["q"]==q and x["flag"]==flag and x["rep"]>0]
    print("S24_CONDITION "+json.dumps({"version":ver,"q":q,"flag":flag,
          "server_ms_median":statistics.median(x["server_ms"] for x in filtered),
          "plan_us_median":statistics.median(x["plan_us"] for x in filtered if x["plan_us"] is not None),
          "cpu_samples":sum(x["samples"] for x in filtered)}),flush=True)
  with (directory/"stack_hit_summary.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(summaries[0]));w.writeheader();w.writerows(summaries)
  valid=all(x["samples"]>0 for x in records if x["rep"]>0 and x["flag"]=="ON")
  (directory/"STATUS.json").write_text(json.dumps({"version":ver,"fingerprint":fp,"runs":len(records),"trace_samples":sum(x["samples"] for x in records),"valid_samples_for_every_on_query":valid,"columns":columns},indent=2)+"\n")
  print("S24_VERSION_DONE "+json.dumps({"version":ver,"fingerprint":fp,"profiles":len(records),"samples":sum(x["samples"] for x in records),"sample_sufficient":valid}),flush=True)
  return fp,summaries,records
 finally:base.run(["docker","rm","-f",ct],check=False)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for ver in VERSIONS:base.run(["docker","pull","clickhouse/clickhouse-server:"+ver])
 qs=base.pinned_sources()
 native=base.make_data()
 allframes,allruns=[],[]
 fps=[]
 for ver in [VERSIONS[1],VERSIONS[0]]:
  fingerprint,frames,runs=run_version(ver,native,qs)
  fps.append(fingerprint);allframes+=frames;allruns+=runs
 if len(set(fps))>1:raise RuntimeError("Cross-release logical fingerprints mismatch")
 out={"versions":VERSIONS,"queries":QUERIES,"reps_measured_per_condition":REPS,
      "total_runs":len(allruns),"source_native_sha256":base.digest(native),
      "identical_logical_fingerprint":True,"per_version":fps}
 (ROOT/"FINAL_STATUS.json").write_text(json.dumps(out,indent=2)+"\n")
 print("S24_FINAL "+json.dumps(out),flush=True)
if __name__=="__main__":main()
