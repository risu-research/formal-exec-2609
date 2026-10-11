#!/usr/bin/env python3
"""Stage28: pre/post OPTIMIZE FINAL, matched unmerged holdout, stats ON/OFF.
A causal merge-state experiment; merging changes more than part count.
"""
import csv,hashlib,json,pathlib,random,statistics,time
import db_sf1_probe as base
VER="26.8.1.2041";ROOT=pathlib.Path("s28").resolve()
base.ROOT=ROOT;base.VERSIONS=[VER,VER]
QIDS=(2,3,4);REPS=8;WARM=2
EVENTS=("QueryPlanOptimizeMicroseconds","LoadedStatisticsMicroseconds","SelectedMarks","FileOpen","UserTimeMicroseconds")
FIELDS=("query_duration_ms","read_rows","read_bytes","memory_usage")+EVENTS
FP=("SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
    "sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)),"
    "min(LO_ORDERDATE),max(LO_ORDERDATE) FROM ssb.{} FORMAT TSV")
PARTS=("SELECT count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts "
       "WHERE active AND database='ssb' AND table='{}' FORMAT TSV")
def save(name,txt):
 p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(txt)
def jwrite(name,obj):save(name,json.dumps(obj,sort_keys=True,indent=2)+"\n")
def q(ct,sql,qid=None,fmt=None,timeout=150):
 p=base.client(ct,sql,qid=qid,fmt=fmt,check=False,timeout=timeout)
 if p.returncode:raise RuntimeError("SQL FAILED "+sql[:140]+": "+p.stderr.decode(errors="replace")[-1500:])
 return p.stdout.decode(errors="replace")
def boot():
 ct="s28_merge"
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144","clickhouse/clickhouse-server:"+VER],timeout=110)
 for _ in range(90):
  if not base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=10).returncode:break
  time.sleep(1)
 else:raise RuntimeError("Server startup failed")
 base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 return ct
def physical(ct,phase):
 out={}
 for t in ("holdout","merged"):
  n,rows,sz,marks=[int(x) for x in q(ct,PARTS.format(t)).strip().split("\t")]
  out[t]={"parts":n,"rows":rows,"bytes":sz,"marks":marks,"fingerprint":q(ct,FP.format(t)).strip()}
 jwrite(phase+"/physical.json",out)
 print("S28_PHYSICAL "+json.dumps({"phase":phase,"values":out}),flush=True)
 return out
def statement(sql,table):
 assert "FROM lineorder_flat" in sql
 return sql.replace("FROM lineorder_flat","FROM "+table)
def check_answers(ct,phase,queries):
 hashes={}
 for qi in QIDS:
  for table in ("holdout","merged"):
   for use in (0,1):
    sql=statement(queries[qi-1],table)+f" SETTINGS use_statistics={use},optimize_move_to_prewhere=1,max_threads=2"
    hashes[f"q{qi}_{table}_u{use}"]=hashlib.sha256(q(ct,sql+" FORMAT TSV").encode()).hexdigest()
    if use:
     for name,prefix in (("plan","EXPLAIN PLAN "),("indexes","EXPLAIN indexes=1 ")):
      save(f"{phase}/q{qi}_{table}_{name}.txt",q(ct,prefix+sql))
  if len({hashes[f"q{qi}_{t}_u{u}"] for t in ("holdout","merged") for u in (0,1)})!=1:
   raise RuntimeError("Unequal answers "+phase+" q"+str(qi))
 jwrite(phase+"/answer_sha256.json",hashes)
 return hashes
def measure(ct,phase,queries):
 trials=[]
 for roundid in range(-WARM,REPS):
  cells=[(t,qi,u) for t in ("holdout","merged") for qi in QIDS for u in (0,1)]
  random.Random(280000+roundid).shuffle(cells)
  for i,(t,qi,u) in enumerate(cells):
   qid=f"s28_{phase}_q{qi}_{t}_u{u}_r{roundid}_n{i}"
   sql=statement(queries[qi-1],t)+f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,max_memory_usage=9000000000"
   q(ct,sql,qid=qid,fmt="Null",timeout=110)
   trials.append({"phase":phase,"table":t,"q":qi,"use_statistics":u,
                  "round":roundid,"warmup":int(roundid<0),"query_id":qid})
 q(ct,"SYSTEM FLUSH LOGS")
 logs=q(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
        f"FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s28_{phase}_') FORMAT JSONEachRow")
 save(phase+"/query_log.jsonl",logs)
 lookup={z["query_id"]:z for line in logs.splitlines() if line.strip() for z in [json.loads(line)]}
 if len(lookup)!=len(trials):raise RuntimeError(f"Missing profile receipts {phase}: {len(lookup)}/{len(trials)}")
 for rec in trials:
  z=lookup[rec["query_id"]];ev=z.get("ProfileEvents") or {}
  for k in ("query_duration_ms","read_rows","read_bytes","memory_usage"):rec[k]=z[k]
  for k in EVENTS:rec[k]=ev.get(k,0)
 with (ROOT/(phase+"/receipts.csv")).open("w",newline="") as f:
  wr=csv.DictWriter(f,fieldnames=list(trials[0]));wr.writeheader();wr.writerows(trials)
 med=[]
 for t in ("holdout","merged"):
  for qi in QIDS:
   for u in (0,1):
    rr=[x for x in trials if x["table"]==t and x["q"]==qi and x["use_statistics"]==u and x["round"]>=0]
    x={"phase":phase,"table":t,"q":qi,"use_statistics":u,"n":len(rr)}
    for k in FIELDS:x[k+"_median"]=statistics.median(row[k] for row in rr)
    med.append(x)
    print("S28_CONDITION "+json.dumps(x),flush=True)
 with (ROOT/(phase+"/medians.csv")).open("w",newline="") as f:
  wr=csv.DictWriter(f,fieldnames=list(med[0]));wr.writeheader();wr.writerows(med)
 print("S28_PHASE_COMPLETE "+phase,flush=True)
 return med
def effect(pre,post):
 lookup={(z["phase"],z["table"],z["q"],z["use_statistics"]):z for z in pre+post}
 rows=[]
 for qi in QIDS:
  rec={"q":qi}
  for metric in ("query_duration_ms","LoadedStatisticsMicroseconds","QueryPlanOptimizeMicroseconds","FileOpen","read_rows","SelectedMarks"):
   v=lambda phase,t,u:float(lookup[(phase,t,qi,u)][metric+"_median"])
   did=lambda u:(v("after","merged",u)-v("before","merged",u))-(v("after","holdout",u)-v("before","holdout",u))
   rec["did_stats_on_"+metric]=did(1)
   rec["did_stats_off_"+metric]=did(0)
   rec["triple_difference_"+metric]=did(1)-did(0)
   for phase in ("before","after"):
    for table in ("merged","holdout"):
     for u in (0,1):rec[f"{phase}_{table}_u{u}_{metric}"]=v(phase,table,u)
  rows.append(rec)
  print("S28_EFFECT "+json.dumps({"q":qi,"stats_loading_did_ms":rec["did_stats_on_LoadedStatisticsMicroseconds"]/1000,
     "wall_triple_difference_ms":rec["triple_difference_query_duration_ms"],
     "extra_fileopen_triple_difference":rec["triple_difference_FileOpen"]}),flush=True)
 with (ROOT/"effects.csv").open("w",newline="") as f:
  wr=csv.DictWriter(f,fieldnames=list(rows[0]));wr.writeheader();wr.writerows(rows)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 print("S28_START "+VER,flush=True)
 base.run(["docker","pull","clickhouse/clickhouse-server:"+VER],timeout=400)
 queries=base.pinned_sources();native=base.make_data()
 print("S28_NATIVE_READY "+(ROOT/"SOURCE_IDENTITY.json").read_text().strip(),flush=True)
 ct=boot()
 try:
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw_source","holdout","merged"):
   q(ct,f"CREATE TABLE ssb.{t} ({cols}) ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
   q(ct,"SYSTEM STOP MERGES ssb."+t)
  with open(native,"rb") as f:
   p=base.run(["docker","exec","-i",ct,"clickhouse-client","--query",
              "INSERT INTO ssb.raw_source SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
              stdin=f,check=False,timeout=700)
   if p.returncode:raise RuntimeError("SOURCE INSERT FAILED: "+p.stderr.decode(errors="replace")[-1500:])
  native.unlink()
  print("S28_SOURCE_PARTS "+q(ct,PARTS.format("raw_source")).strip(),flush=True)
  for t in ("holdout","merged"):
   q(ct,f"INSERT INTO ssb.{t} SELECT * FROM ssb.raw_source "
     "SETTINGS materialize_statistics_on_insert=1,max_threads=1,max_insert_threads=1,max_memory_usage=9000000000",timeout=600)
   print("S28_CREATED "+t+" "+q(ct,PARTS.format(t)).strip(),flush=True)
  save("parts_statistics_schema.tsv",q(ct,"SELECT name FROM system.columns WHERE database='system' AND table='parts' AND name LIKE '%stat%' FORMAT TSV"))
  pre=physical(ct,"before")
  if pre["merged"]["parts"]!=pre["holdout"]["parts"] or pre["merged"]["parts"]<100:
   raise RuntimeError("Invalid matched high-part baseline "+str(pre))
  if pre["merged"]["fingerprint"]!=pre["holdout"]["fingerprint"]:
   raise RuntimeError("Physical arms have different logical contents")
  before_answers=check_answers(ct,"before",queries)
  before=measure(ct,"before",queries)
  print("S28_OPTIMIZE_START "+str(pre["merged"]["parts"]),flush=True)
  q(ct,"SYSTEM START MERGES ssb.merged")
  t0=time.monotonic()
  q(ct,"OPTIMIZE TABLE ssb.merged FINAL SETTINGS optimize_throw_if_noop=1",timeout=720)
  opt_s=round(time.monotonic()-t0,3)
  q(ct,"SYSTEM STOP MERGES ssb.merged")
  post=physical(ct,"after")
  print("S28_OPTIMIZE_DONE "+json.dumps({"seconds":opt_s,"before":pre["merged"]["parts"],"after":post["merged"]["parts"]}),flush=True)
  if post["merged"]["parts"]>=pre["merged"]["parts"]:raise RuntimeError("Merge did not reduce part count")
  if post["holdout"]["parts"]!=pre["holdout"]["parts"]:raise RuntimeError("Holdout changed part count")
  if post["merged"]["fingerprint"]!=pre["merged"]["fingerprint"] or post["holdout"]["fingerprint"]!=pre["holdout"]["fingerprint"]:
   raise RuntimeError("Logical content changed")
  if check_answers(ct,"after",queries)!=before_answers:raise RuntimeError("Answers changed after merging")
  after=measure(ct,"after",queries)
  effect(before,after)
  stat_retained=all(next(z for z in group if z["q"]==qi and z["table"]=="merged" and z["use_statistics"]==1)["LoadedStatisticsMicroseconds_median"]>0
      for group in (before,after) for qi in QIDS)
  report={"valid":stat_retained,"statistics_loading_path_preserved":stat_retained,
    "release":VER,"source":json.loads((ROOT/"SOURCE_IDENTITY.json").read_text()),
    "before":pre,"after":post,"optimize_seconds":opt_s,
    "same_query_answers":True,"same_logical_fingerprint":True,
    "measured_queries":2*2*len(QIDS)*REPS*2,
    "interpretation":"Merge-state intervention with unmerged control and use_statistics negative control; merge also changes marks and physical statistics"}
  jwrite("FINAL_STATUS.json",report)
  print("S28_FINAL "+json.dumps(report),flush=True)
  if not stat_retained:raise RuntimeError("Statistics path absent after merge, interpretation invalid")
 finally:
  base.run(["docker","rm","-f",ct],check=False,timeout=50)
if __name__=="__main__":main()
