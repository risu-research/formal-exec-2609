#!/usr/bin/env python3
"""Stage25: all 13 original SSB queries, two releases, materialize_at_insert x use_statistics.
The upgrade gates are PRESPECIFIED below, not tuned to observed results.
All numeric decisions are descriptive SF1 workload scenarios, not SF100 generalizations.
"""
import csv,hashlib,json,math,pathlib,random,statistics,time
from collections import defaultdict
import db_sf1_probe as base

ROOT=pathlib.Path("s25").resolve()
base.ROOT=ROOT
RELEASES=base.VERSIONS
QUERIES=list(range(1,14))
REPS=10
WARMUPS=2
POLICY=[(m,u) for m in (0,1) for u in (0,1)]
EVENTS=("QueryPlanOptimizeMicroseconds","LoadedStatisticsMicroseconds",
        "SelectedMarks","SelectedRows","FileOpen","UserTimeMicroseconds")
SCENARIOS={
 "equal_per_query": {"Q1":.25,"Q2":.25,"Q3":.25,"Q4":.25},
 "Q1_heavy": {"Q1":.60,"Q2":.15,"Q3":.15,"Q4":.10},
 "Q2_heavy": {"Q1":.10,"Q2":.60,"Q3":.20,"Q4":.10},
 "Q34_heavy":{"Q1":.10,"Q2":.10,"Q3":.45,"Q4":.35},
}
# Thresholds fixed before observing timings.
SLOWDOWN_THRESHOLD=1.5
AGGREGATE_REGRESSION_THRESHOLD=1.10
def family(q):
 return "Q1" if q<=3 else "Q2" if q<=6 else "Q3" if q<=10 else "Q4"
def med(xs):
 vals=[float(x) for x in xs if x is not None]
 return statistics.median(vals) if vals else None
def save(path,txt):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(txt)
def qsql(ct,s,qid=None,fmt=None):
 p=base.client(ct,s,qid=qid,fmt=fmt,check=False,timeout=350)
 if p.returncode:raise RuntimeError(f"SQL failed {s[:100]}: {p.stderr.decode(errors='replace')[-1200:]}")
 return p.stdout
def qtext(ct,s):return qsql(ct,s).decode(errors="replace")
def boot(ver):
 ct="s25_"+ver.replace(".","_")
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144",
           "clickhouse/clickhouse-server:"+ver],timeout=100)
 for _ in range(110):
  p=base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=10)
  if p.returncode==0:break
  time.sleep(1)
 else:raise RuntimeError("ClickHouse failed to start "+ver)
 base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 return ct
def qreplace(qs,q,m):
 old="FROM lineorder_flat"
 assert old in qs[q-1],f"Source query {q} changed unexpectedly"
 return qs[q-1].replace(old,f"FROM lineorder_flat_m{m}")
def run_release(ver,native,qs):
 root=ROOT/ver;root.mkdir(parents=True,exist_ok=True)
 ct=boot(ver)
 try:
  save(root/"session_settings.tsv",
       qtext(ct,"SELECT name,value FROM system.settings WHERE name IN "
             "('materialize_statistics_on_insert','use_statistics','optimize_move_to_prewhere',"
             "'max_threads','use_query_condition_cache') ORDER BY name FORMAT TSV"))
  save(root/"merge_tree_settings.tsv",
       qtext(ct,"SELECT name,value FROM system.merge_tree_settings WHERE name LIKE '%statistic%' ORDER BY name FORMAT TSV"))
  schema=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  ingest=[]
  fingerprints=[]
  for m in (0,1):
   table=f"lineorder_flat_m{m}"
   qsql(ct,f"CREATE TABLE ssb.{table} ({schema}) ENGINE=MergeTree "
           "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE, LO_ORDERKEY)")
   statement=f"INSERT INTO ssb.{table} SETTINGS materialize_statistics_on_insert={m} FORMAT Native"
   t0=time.perf_counter()
   with open(native,"rb") as inp:
    p=base.run(["docker","exec","-i",ct,"clickhouse-client","--query",statement],
                stdin=inp,check=False,timeout=1050)
   duration=time.perf_counter()-t0
   if p.returncode:raise RuntimeError("Insert failed "+ver+" m="+str(m)+": "+p.stderr.decode(errors="replace")[-1200:])
   qsql(ct,f"SYSTEM STOP MERGES ssb.{table}")
   fp=qtext(ct,f"SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),min(LO_ORDERDATE),max(LO_ORDERDATE),"
             f"sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) "
             f"FROM ssb.{table} FORMAT TSV").strip()
   fingerprints.append(fp)
   ingest.append({"version":ver,"materialize":m,"ingest_client_s":round(duration,4),"fingerprint":fp})
   print("S25_INSERT "+json.dumps(ingest[-1]),flush=True)
  if len(set(fingerprints))!=1 or not fingerprints[0].startswith("6001215\t"):raise RuntimeError("logical fingerprint mismatch")
  save(root/"fingerprint.tsv",fingerprints[0]+"\n")
  parts=qtext(ct,"SELECT table,partition,count() active_parts,sum(rows) rows,sum(bytes_on_disk) bytes,"
                   "sum(marks) marks FROM system.parts WHERE active AND database='ssb' "
                   "GROUP BY table,partition ORDER BY table,partition FORMAT TSVWithNames")
  save(root/"parts.tsv",parts)
  save(root/"stats_columns.tsv",qtext(ct,
      "SELECT table,name,statistics FROM system.columns WHERE database='ssb' AND table LIKE 'lineorder_flat_m%' "
      "ORDER BY table,name FORMAT TSV"))
  # Fix logical equality of 13 answers for both physical policies and session statistics settings.
  outcomes={}
  for q in QUERIES:
   for m,u in POLICY:
    sql=qreplace(qs,q,m)+f" SETTINGS optimize_move_to_prewhere=1,use_statistics={u}"
    answer=qsql(ct,sql+" FORMAT TSV")
    outcomes[(q,m,u)]=hashlib.sha256(answer).hexdigest()
   if len({outcomes[(q,m,u)] for m,u in POLICY})!=1:raise RuntimeError("Answer differs q="+str(q)+" release="+ver)
  save(root/"answers_sha256.json",json.dumps({f"q{q:02d}_m{m}_u{u}":v for (q,m,u),v in outcomes.items()},sort_keys=True,indent=2)+"\n")
  # Explicit warmups (r=-2,-1) then 10 measured waves.  All four policies randomized in each query round.
  recorded=[];counter=0
  for rnd in range(-WARMUPS,REPS):
   qorder=QUERIES.copy()
   random.Random(25000+rnd).shuffle(qorder)
   for q in qorder:
    arms=POLICY.copy()
    random.Random(252500+100*q+rnd).shuffle(arms)
    for m,u in arms:
     counter+=1
     sql=qreplace(qs,q,m)+f" SETTINGS optimize_move_to_prewhere=1,use_statistics={u}"
     qid=f"s25_{ver.replace('.','_')}_q{q:02d}_m{m}_u{u}_r{rnd}_n{counter}"
     start=time.perf_counter()
     qsql(ct,sql,qid=qid,fmt="Null")
     recorded.append({"version":ver,"q":q,"family":family(q),"materialize":m,"use_statistics":u,
                      "round":rnd,"warmup":int(rnd<0),"query_id":qid,
                      "client_ms":round(1000*(time.perf_counter()-start),3)})
  qsql(ct,"SYSTEM FLUSH LOGS")
  p=qtext(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
              "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s25_') "
              "FORMAT JSONEachRow")
  save(root/"query_log.jsonl",p)
  prof={x["query_id"]:x for line in p.splitlines() if line for x in [json.loads(line)]}
  if len(prof)!=len(recorded):raise RuntimeError(f"Missing query profiles {len(prof)} != {len(recorded)}")
  for rec in recorded:
   z=prof[rec["query_id"]];rec.update({k:z.get(k) for k in ("query_duration_ms","read_rows","read_bytes","memory_usage")})
   ev=z.get("ProfileEvents",{}) or {}
   rec.update({e:ev.get(e,0) for e in EVENTS})
  cols=list(recorded[0])
  with (root/"all_receipts.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(recorded)
  summaries=[]
  for q in QUERIES:
   for m,u in POLICY:
    rows=[x for x in recorded if x["q"]==q and x["materialize"]==m and x["use_statistics"]==u and x["round"]>=0]
    assert len(rows)==REPS
    z={"version":ver,"q":q,"family":family(q),"materialize":m,"use_statistics":u,"n":len(rows)}
    for k in ("query_duration_ms","read_rows","read_bytes","QueryPlanOptimizeMicroseconds",
              "LoadedStatisticsMicroseconds","SelectedMarks","FileOpen"):
     z[k+"_median"]=med(x[k] for x in rows)
    summaries.append(z)
  with (root/"medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(summaries[0]));w.writeheader();w.writerows(summaries)
  with (root/"ingest.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(ingest[0]));w.writeheader();w.writerows(ingest)
  print("S25_VERSION_DONE "+json.dumps({"version":ver,"records":len(recorded),"measured":len(summaries)*REPS,"all_answers_equal":True,"fp":fingerprints[0]}),flush=True)
  for q in QUERIES:
   ss=[x for x in summaries if x["q"]==q]
   print("S25_Q "+json.dumps({"version":ver,"q":q,
      "wall_medians":{f"m{x['materialize']}u{x['use_statistics']}":x["query_duration_ms_median"] for x in ss},
      "stats_load_ms":{f"m{x['materialize']}u{x['use_statistics']}":x["LoadedStatisticsMicroseconds_median"]/1000 for x in ss}}),flush=True)
  return {"version":ver,"fingerprint":fingerprints[0],"ingest":ingest,"parts":parts},summaries,outcomes
 finally:base.run(["docker","rm","-f",ct],check=False,timeout=70)
def scenario_mean(ss,weights,key="query_duration_ms_median"):
 fam=defaultdict(list)
 for x in ss:fam[x["family"]].append(x[key])
 return sum(weights[f]*statistics.mean(fam[f]) for f in weights)
def summarize(results,medians,answers):
 full={(x["version"],x["q"],x["materialize"],x["use_statistics"]):x for x in medians}
 for q in QUERIES:
  hs=[answers[ver][(q,m,u)] for ver in RELEASES for m,u in POLICY]
  if len(set(hs))>1:raise RuntimeError(f"Different result cross-release {q}")
 perquery=[]
 for q in QUERIES:
  old=full[(RELEASES[0],q,0,1)]
  new=full[(RELEASES[1],q,1,1)]
  matched=full[(RELEASES[1],q,0,1)]
  off=full[(RELEASES[1],q,1,0)]
  old_m1=full[(RELEASES[0],q,1,1)]
  benefit=new["query_duration_ms_median"]-off["query_duration_ms_median"]
  x={"q":q,"family":family(q),"old_native_ms":old["query_duration_ms_median"],
     "new_native_ms":new["query_duration_ms_median"],"new_matched_m0_ms":matched["query_duration_ms_median"],
     "new_m1_stats_disabled_ms":off["query_duration_ms_median"],
     "old_m1_ms":old_m1["query_duration_ms_median"],
     "old_native_to_new_native_ratio":new["query_duration_ms_median"]/max(1,old["query_duration_ms_median"]),
     "old_native_to_new_matched_ratio":matched["query_duration_ms_median"]/max(1,old["query_duration_ms_median"]),
     "stat_runtime_benefit_ms":off["query_duration_ms_median"]-new["query_duration_ms_median"],
     "stat_read_rows_delta":new["read_rows_median"]-off["read_rows_median"],
     "stat_read_bytes_delta":new["read_bytes_median"]-off["read_bytes_median"],
     "stat_selected_marks_delta":new["SelectedMarks_median"]-off["SelectedMarks_median"],
     "stats_load_ms":new["LoadedStatisticsMicroseconds_median"]/1000}
  perquery.append(x)
 with (ROOT/"decision_query_table.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(perquery[0]));w.writeheader();w.writerows(perquery)
 scenarios=[]
 for scen,weights in SCENARIOS.items():
  arms={}
  for label,ver,m,u in [("old_native",RELEASES[0],0,1),("new_native",RELEASES[1],1,1),
                         ("new_matched_policy",RELEASES[1],0,1),
                         ("new_stats_off",RELEASES[1],1,0),
                         ("new_matched_stats_off",RELEASES[1],0,0)]:
   arms[label]=scenario_mean([full[(ver,q,m,u)] for q in QUERIES],weights)
  slowdown_count=sum(x["old_native_to_new_native_ratio"]>=SLOWDOWN_THRESHOLD for x in perquery)
  match_slowdown_count=sum(x["old_native_to_new_matched_ratio"]>=SLOWDOWN_THRESHOLD for x in perquery)
  native_ratio=arms["new_native"]/arms["old_native"]
  match_ratio=arms["new_matched_policy"]/arms["old_native"]
  # Example predeclared safety gate: block if 2+ queries exceed 1.5x, or weighted mean exceeds +10%.
  native_block=slowdown_count>=2 or native_ratio>AGGREGATE_REGRESSION_THRESHOLD
  matched_block=match_slowdown_count>=2 or match_ratio>AGGREGATE_REGRESSION_THRESHOLD
  v={"scenario":scen,"old_ms":arms["old_native"],"new_native_ms":arms["new_native"],
     "new_matched_ms":arms["new_matched_policy"],"new_use_statistics_off_ms":arms["new_stats_off"],
     "native_ratio":native_ratio,"matched_ratio":match_ratio,
     "native_q_ge_1_5":slowdown_count,"matched_q_ge_1_5":match_slowdown_count,
     "native_gate":"BLOCK" if native_block else "APPROVE",
     "matched_gate":"BLOCK" if matched_block else "APPROVE",
     "gate_flips":native_block!=matched_block,
     "runtime_benefit_stats_off_minus_native_ms":arms["new_stats_off"]-arms["new_native"]}
  scenarios.append(v)
  print("S25_GATE "+json.dumps(v),flush=True)
 with (ROOT/"upgrade_gate_scenarios.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(scenarios[0]));w.writeheader();w.writerows(scenarios)
 ingest={(x["version"],x["materialize"]):x for result in results for x in result["ingest"]}
 insert_penalty={
  v:ingest[(v,1)]["ingest_client_s"]-ingest[(v,0)]["ingest_client_s"] for v in RELEASES}
 for scen in scenarios:
  benefit_sec=-scen["runtime_benefit_stats_off_minus_native_ms"]/1000
  # N inserts are not implicitly normalized by the number of rows; minimum queries depends on
  # incremental ingest duration and per-query runtime benefits.
  scen["break_even_executions_when_benefit_positive"]=None if benefit_sec<=0 else (
     math.ceil(max(0,insert_penalty[RELEASES[1]])/benefit_sec))
 (ROOT/"break_even.json").write_text(json.dumps({"insert_extra_seconds":insert_penalty,
     "scenarios":scenarios,"provenance":"SF1, 13 official SQL; descriptive balanced q-family mixes"},indent=2)+"\n")
 return scenarios,perquery
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for ver in RELEASES:base.run(["docker","pull","clickhouse/clickhouse-server:"+ver])
 qs=base.pinned_sources();native=base.make_data()
 result,medians,answers=[],[],{}
 for ver in RELEASES:
  rr,mm,aa=run_release(ver,native,qs)
  result.append(rr);medians+=mm;answers[ver]=aa
 assert len({r["fingerprint"] for r in result})==1
 scenarios,perquery=summarize(result,medians,answers)
 state={"valid":True,"release_order":RELEASES,"source_native_sha256":base.digest(native),
        "source_sf":1,"dataset_rows":6001215,"logical_fingerprints_equal":True,
        "all_13_query_answers_equal_across_releases_and_policies":True,
        "total_executions":len(RELEASES)*len(QUERIES)*len(POLICY)*(REPS+WARMUPS),
        "measured_executions":len(RELEASES)*len(QUERIES)*len(POLICY)*REPS,
        "partitions":result,"gates":scenarios,
        "caution":"SF1 experiment; does not prove the cause of official SF100 regressions"}
 (ROOT/"FINAL_STATUS.json").write_text(json.dumps(state,indent=2)+"\n")
 print("S25_FINAL "+json.dumps({"valid":True,"total_executions":state["total_executions"],
       "measured":state["measured_executions"],"gates":scenarios}),flush=True)
if __name__=="__main__":main()
