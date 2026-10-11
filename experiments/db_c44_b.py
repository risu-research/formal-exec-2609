#!/usr/bin/env python3
"""c44b: pinned upstream normalized joins under routine part maintenance.

TPC-H query texts Q3,Q5,Q10,Q12 remain intact except for table suffixes and
SETTINGS. SF10 public Parquet, key-restricted subset, NOT a standard TPC-H run.
No claims that any arm must outperform another.
"""
import csv,hashlib,json,pathlib,random,statistics,time,subprocess
import db_tpch_join_boundary as T
R=pathlib.Path("c44b").resolve();R.mkdir(exist_ok=True)
T.ROOT=R
T.KEY_CAP=1500000
T.REPS=5
T.WARMUPS=1
TABLES=T.TABLES
QUERIES=T.QUERIES
ARMS=(("neither",0,0,0),("prune",0,1,0),("load",1,0,0),("both",1,1,0),("cached",1,1,1))
VERSION="26.8.1.2041"
def save(name,obj):
 p=R/name;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def query(ct,s,qid=None,fmt=None,timeout=300):return T.q(ct,s,qid=qid,fmt=fmt,timeout=timeout)
def stmt(sql,mat,s,p,c):
 return T.substitute(sql,mat)+(
  f" SETTINGS use_statistics={s},use_statistics_for_part_pruning={p},"
  f"use_statistics_cache={c},max_threads=4,max_memory_usage=9000000000,"
  "use_query_condition_cache=0")
def idle(ct,tables,limit=180):
 ids=",".join("'"+x+"'" for x in tables)
 start=time.monotonic();idle_count=0
 while time.monotonic()-start<limit:
  active=int(query(ct,"SELECT count() FROM system.merges WHERE database='tpch' "
           "AND table IN ("+ids+") FORMAT TSV"))
  idle_count=idle_count+1 if active==0 else 0
  if idle_count>=3:return round(time.monotonic()-start,2)
  time.sleep(2)
 raise RuntimeError("Background merges did not settle")
def physical(ct,label):
 summary={}
 for t in TABLES:
  for mode in (0,1):
   tab=f"{t}_m{mode}"
   inventory=query(ct,"SELECT partition,name,rows,marks,path FROM system.parts "
     "WHERE database='tpch' AND table='"+tab+"' AND active ORDER BY name FORMAT TSV")
   arr=[r.split("\t") for r in inventory.splitlines() if r]
   paths="\n".join(x[-1] for x in arr)+"\n"
   check=T.run(["docker","exec","-i",ct,"bash","-c",
     'while IFS= read -r d; do test -f "$d/statistics.packed" && echo 1 || echo 0; done'],inp=paths.encode(),timeout=90)
   flags=[int(x) for x in check.splitlines()]
   if len(flags)!=len(arr):raise RuntimeError("Missing per-part metadata "+tab)
   summary[tab]={"parts":len(arr),"rows":sum(int(x[2]) for x in arr),
     "marks":sum(int(x[3]) for x in arr),"stats_parts":sum(flags),
     "inventory_sha":hashlib.sha256(inventory.encode()).hexdigest()}
 save(label+"/physical.json",summary)
 print("C44B_STATE",label,json.dumps({k:[v["parts"],v["stats_parts"]] for k,v in summary.items()}),flush=True)
 return summary
def main():
 sqls,defs=T.setup_source()
 T.run(["docker","pull","clickhouse/clickhouse-server:"+VERSION],timeout=500)
 ct=T.boot(VERSION)
 try:
  val=query(ct,"SELECT count() FROM s3('https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/nation_0.parquet',NOSIGN,'Parquet') FORMAT TSV",timeout=150).strip()
  if val!="25":raise RuntimeError("Upstream source verification failed")
  settings=query(ct,"SELECT name,value FROM system.settings WHERE name IN "
   "('use_statistics','use_statistics_cache','use_statistics_for_part_pruning',"
   "'query_plan_optimize_join_order_algorithm','materialize_statistics_on_merge',"
   "'auto_statistics_types') ORDER BY name FORMAT TSV")
  (R/"settings.tsv").write_text(settings)
  ingested=[]
  for tab in TABLES:
   cols,pk=defs[tab]
   for suffix in ("raw","m0","m1"):
    query(ct,f"CREATE TABLE tpch.{tab}_{suffix} ({cols}) ENGINE=MergeTree ORDER BY ({pk})")
   src="https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/"+T.SOURCE_PARQUET[tab]
   cond=(" WHERE o_orderkey <= "+str(T.KEY_CAP)) if tab=="orders" else (
      " WHERE l_orderkey <= "+str(T.KEY_CAP)) if tab=="lineitem" else ""
   start=time.monotonic()
   query(ct,"INSERT INTO tpch."+tab+"_raw SELECT "+T.SELECTS[tab]+
       " FROM s3('"+src+"',NOSIGN,'Parquet')"+cond+
       " SETTINGS materialize_statistics_on_insert=0,max_threads=4,"
       "max_insert_threads=4,max_memory_usage=9000000000",timeout=1100)
   count=int(query(ct,"SELECT count() FROM tpch."+tab+"_raw FORMAT TSV"))
   ingested.append({"table":tab,"raw_rows":count,"seconds":round(time.monotonic()-start,2)})
   for mode in (0,1):
    begin=time.monotonic()
    query(ct,f"INSERT INTO tpch.{tab}_m{mode} SELECT * FROM tpch.{tab}_raw "
          f"SETTINGS materialize_statistics_on_insert={mode},max_threads=4,"
          "max_insert_threads=2,max_memory_usage=9000000000",timeout=900)
    if int(query(ct,f"SELECT count() FROM tpch.{tab}_m{mode} FORMAT TSV"))!=count:
     raise RuntimeError("Loaded population not equal")
    ingested[-1][f"m{mode}_seconds"]=round(time.monotonic()-begin,2)
   save("ingest.json",ingested)
   print("C44B_INGEST",json.dumps(ingested[-1]),flush=True)
  names=[f"{t}_m{m}" for t in TABLES for m in (0,1)]
  waited=idle(ct,names)
  for name in names:query(ct,"SYSTEM STOP MERGES tpch."+name)
  start=physical(ct,"fixed")
  for tab in TABLES:
   if start[tab+"_m0"]["rows"]!=start[tab+"_m1"]["rows"]:
    raise RuntimeError("Data mismatch "+tab)
  answer_hash={}
  for qi,q in sqls.items():
   expected=None
   for mat in (0,1):
    for arm,s,p,c in ARMS:
     raw=query(ct,stmt(q,mat,s,p,c)+" FORMAT TSV",timeout=400)
     hashed=hashlib.sha256(raw.encode()).hexdigest()
     if expected is not None and hashed!=expected:
      raise RuntimeError("Query result mismatch "+str(qi))
     expected=hashed
     answer_hash[f"{qi}/{mat}/{arm}"]=hashed
  save("answers.json",answer_hash)
  plans={}
  for qi,q in sqls.items():
   for mat in (0,1):
    for arm,s,p,c in ARMS:
     plans[f"{qi}/{mat}/{arm}"]=query(ct,"EXPLAIN PLAN "+stmt(q,mat,s,p,c),timeout=180)
  save("plans.json",plans)
  records=[]
  for rnd in range(-1,T.REPS):
   rng=random.Random(440400+rnd);ordered=list(QUERIES);rng.shuffle(ordered)
   for qi in ordered:
    arm_list=list(ARMS);rng.shuffle(arm_list)
    for j,(arm,s,p,c) in enumerate(arm_list):
     mats=(0,1) if (qi+j+rnd)%2==0 else (1,0)
     for mat in mats:
      qid="c44b_"+str(len(records))
      query(ct,stmt(sqls[qi],mat,s,p,c),qid=qid,fmt="Null",timeout=400)
      records.append({"round":rnd,"query":qi,"materialize":mat,"arm":arm,"qid":qid})
   print("C44B_ROUND",rnd,len(records),flush=True)
  query(ct,"SYSTEM FLUSH LOGS")
  logs=query(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
      "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c44b_') FORMAT JSONEachRow")
  loc={v["query_id"]:v for ln in logs.splitlines() if ln for v in [json.loads(ln)]}
  if len(loc)!=len(records):raise RuntimeError("Missing query receipts")
  for rec in records:
   val=loc[rec["qid"]];ev=val.get("ProfileEvents") or {}
   rec.update(ms=val["query_duration_ms"],read_rows=val["read_rows"],
      read_bytes=val["read_bytes"],memory=val["memory_usage"],
      stat_us=ev.get("LoadedStatisticsMicroseconds",0),
      optimize_us=ev.get("QueryPlanOptimizeMicroseconds",0),
      selected_marks=ev.get("SelectedMarks",0))
  with (R/"receipts.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
  groups=[]
  for qi in QUERIES:
   for mat in (0,1):
    for arm,_,_,_ in ARMS:
     xs=[z for z in records if z["round"]>=0 and(z["query"],z["materialize"],z["arm"])==(qi,mat,arm)]
     if len(xs)!=T.REPS:raise RuntimeError("Missing full runs")
     groups.append({"query":qi,"materialize":mat,"arm":arm,
       **{k:statistics.median(x[k] for x in xs) for k in ("ms","read_rows","memory","stat_us","optimize_us","selected_marks")}})
  save("medians.json",groups)
  final=physical(ct,"verified")
  if final!=start:raise RuntimeError("Physical state mutated during measurements")
  save("FINAL_STATUS.json",{"valid":True,"origin":"ClickHouse/tpc-h-openhouse", 
    "pin":T.PIN,"sf":10,"orderkey_cap":T.KEY_CAP,"not_standard_tpch":True,
    "queries":QUERIES,"repetitions":T.REPS,"warmup":1,"natural_merge_idle_seconds":waited,
    "ingested":ingested,"statistics_settings":settings,"fixed":start,"medians":groups,
    "limitations":["One runner","All dimensional source rows but orderkey-subset fact rows",
      "Only four pre-registered JOIN queries","No claims of full TPC-H SF10 score",
      "Treatment groups may converge after routine merging"]})
  print("C44B_SUCCESS",json.dumps(groups),flush=True)
 finally:
  subprocess.run(["docker","rm","-f",ct],capture_output=True,timeout=90)
if __name__=="__main__":main()
