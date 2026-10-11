#!/usr/bin/env python3
"""Stage30b: same-release 26.8 retained 26.7 parts vs freshly inserted parts.
Focus: whether disappearance of statistics-loader events is restart/lineage-
dependent, not a true version performance regression.
"""
import csv,hashlib,json,pathlib,random,statistics,subprocess
import db_inplace_upgrade_replay as S

S.ROOT=pathlib.Path("s30b")
S.base.ROOT=S.ROOT
S.VOL="s30b_upgrade_volume"
S.CT="s30b_clickhouse"
S.base.VERSIONS=[S.OLD,S.OLD]
ROOT=S.ROOT
QIDS=(2,3,4)
REPS=8
WARM=2
T=("many","fresh")
E=("LoadedStatisticsMicroseconds","QueryPlanOptimizeMicroseconds","FileOpen","SelectedMarks")
def write(name,x):
 p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(x)
def jwrite(name,x):write(name,json.dumps(x,sort_keys=True,indent=2)+"\n")
def make_sql(q,t):
 return q.replace("FROM lineorder_flat","FROM "+t)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for ver in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=460)
 qs=S.base.pinned_sources();native=S.base.make_data()
 try:
  S.establish(qs,native) # all tables created by 26.7 in a persistent volume
  old=S.capture("before")
  if old["physical"]["many"]["parts"]!=734:raise RuntimeError("Expected inherited 734-part source")
  S.restart(S.NEW)
  after=S.capture("after")
  if after["inventory"]!=old["inventory"] or after["part_checksum_files"]!=old["part_checksum_files"]:
   raise RuntimeError("Inherited parts modified during upgrade")
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  S.sql(S.CT,"CREATE TABLE ssb.fresh ("+cols+
      ") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY) "
      "SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
  S.sql(S.CT,"SYSTEM STOP MERGES ssb.fresh")
  S.sql(S.CT,"INSERT INTO ssb.fresh SELECT * FROM ssb.many SETTINGS "
      "materialize_statistics_on_insert=1,max_threads=1,max_insert_threads=1,"
      "min_insert_block_size_rows=750152,min_insert_block_size_bytes=1,max_memory_usage=9000000000",timeout=650)
  count=S.sql(S.CT,"SELECT count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts "
       "WHERE active AND database='ssb' AND table='fresh' FORMAT TSV").strip()
  parts,rows,sz,marks=map(int,count.split("\t"))
  fresh_fp=S.fp("fresh")
  if parts!=734 or rows!=6001215 or fresh_fp!=after["physical"]["many"]["fingerprint"]:
   raise RuntimeError("Fresh data not matched "+count+" fp="+fresh_fp)
  print("S30B_FRESH_MATCHED "+json.dumps({"parts":parts,"rows":rows,"size":sz,"marks":marks}),flush=True)
  hashans={}
  for qi in QIDS:
   for tab in T:
    for use in (0,1):
     sql=make_sql(qs[qi-1],tab)+f" SETTINGS use_statistics={use},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0"
     out=S.sql(S.CT,sql+" FORMAT TSV")
     hashans[(qi,tab,use)]=hashlib.sha256(out.encode()).hexdigest()
     for kind,prefix in (("plan","EXPLAIN PLAN "),("indexes","EXPLAIN indexes=1 ")):
      if use==1:write(f"plans/q{qi}_{tab}_{kind}.txt",S.sql(S.CT,prefix+sql))
   if len(set(hashans[(qi,tab,use)] for tab in T for use in (0,1)))!=1:
    raise RuntimeError("Answers differ q"+str(qi))
  jwrite("answers_sha256.json",{f"q{qi}_{t}_{u}":h for (qi,t,u),h in hashans.items()})
  records=[]
  for r in range(-WARM,REPS):
   cells=[(t,q,u) for t in T for q in QIDS for u in (0,1)]
   random.Random(300500+r).shuffle(cells)
   for i,(tab,qi,u) in enumerate(cells):
    qid=f"s30b_{tab}_q{qi}_u{u}_r{r}_n{i}"
    S.sql(S.CT,make_sql(qs[qi-1],tab)+
      f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0",
      qid=qid,fmt="Null",timeout=150)
    records.append({"table":tab,"q":qi,"stats":u,"round":r,"warmup":int(r<0),"query_id":qid})
  S.sql(S.CT,"SYSTEM FLUSH LOGS")
  logs=S.sql(S.CT,"SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log "
       "WHERE type='QueryFinish' AND startsWith(query_id,'s30b_') FORMAT JSONEachRow")
  write("query_log.jsonl",logs)
  parsed={j["query_id"]:j for line in logs.splitlines() if line.strip() for j in [json.loads(line)]}
  if len(parsed)!=len(records):raise RuntimeError("Missing receipts")
  for x in records:
   z=parsed[x["query_id"]];ev=z.get("ProfileEvents") or {}
   x.update(query_duration_ms=z["query_duration_ms"],read_rows=z["read_rows"])
   x.update({e:ev.get(e,0) for e in E})
  with (ROOT/"receipts.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
  summary=[]
  for t in T:
   for qi in QIDS:
    for u in (0,1):
     xs=[x for x in records if x["table"]==t and x["q"]==qi and x["stats"]==u and x["round"]>=0]
     z={"table":t,"q":qi,"use_statistics":u,"n":len(xs)}
     for field in ("query_duration_ms","read_rows")+E:
      z[field+"_median"]=statistics.median(x[field] for x in xs)
     summary.append(z)
     print("S30B_MEASURE "+json.dumps(z),flush=True)
  with (ROOT/"medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
  relevant={(r["table"],r["q"],r["use_statistics"]):r for r in summary}
  results=[]
  for qi in QIDS:
   for t in T:
    on=relevant[(t,qi,1)];off=relevant[(t,qi,0)]
    results.append({"q":qi,"table":t,"stats_loading_ms":on["LoadedStatisticsMicroseconds_median"]/1000,
       "stats_on_minus_off_wall_ms":on["query_duration_ms_median"]-off["query_duration_ms_median"],
       "extra_file_opens":on["FileOpen_median"]-off["FileOpen_median"],
       "on_read_rows":on["read_rows_median"],"off_read_rows":off["read_rows_median"]})
  report={"valid":True,"old_version":S.OLD,"new_version":S.NEW,
       "part_integrity":True,"same_sql_answers":True,"new_rows":rows,
       "inherited_parts":734,"fresh_parts":734,"measured":len(T)*len(QIDS)*2*REPS,
       "warmups":len(T)*len(QIDS)*2*WARM,"results":results,
       "caution":"Fresh insert and inherited data can differ in physical part formats despite matched part counts, and this is one runner."}
  jwrite("FINAL_STATUS.json",report)
  print("S30B_FINAL "+json.dumps(report),flush=True)
 finally:
  S.run(["docker","rm","-f",S.CT],check=False,timeout=60)
if __name__=="__main__":main()
