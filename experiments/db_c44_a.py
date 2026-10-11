#!/usr/bin/env python3
"""c44a: statistics effects after natural MergeTree maintenance at two partition granularities."""
import csv,hashlib,json,pathlib,random,statistics,time
import db_mixed_history_upgrade as P
S=P.S
R=pathlib.Path("c44a");R.mkdir(exist_ok=True)
P.R=R;P.T=("mo0","mo1","yr0","yr1")
S.ROOT=S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c44a_ch";S.VOL="c44a_vol"
TABLES=P.T
ARMS=(("neither",0,0,0),("prune",0,1,0),("load",1,0,0),("both",1,1,0),("cached",1,1,1))
REPS=5
FAMILY=(range(1,4),range(4,7),range(7,11),range(11,14))
def sql(s,qid=None,fmt=None):return P.sql(s,qid=qid,fmt=fmt)
def save(p,obj):
 t=R/p;t.parent.mkdir(parents=True,exist_ok=True);t.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def wait_idle(tables,seconds=135):
 names=",".join("'"+t+"'" for t in tables)
 start=time.monotonic();count=0
 while time.monotonic()-start<seconds:
  x=int(sql("SELECT count() FROM system.merges WHERE database='ssb' AND table IN ("+names+") FORMAT TSV"))
  count=count+1 if x==0 else 0
  if count>=3:return round(time.monotonic()-start,2)
  time.sleep(2)
 raise RuntimeError("Ordinary background merges did not quiesce")
def config(q,t,s,p,c):
 return q.replace("FROM lineorder_flat","FROM "+t)+(
  f" SETTINGS use_statistics={s},use_statistics_for_part_pruning={p},"
  f"use_statistics_cache={c},max_threads=2,use_query_condition_cache=0")
def main():
 for ver in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=500)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False);S.run(["docker","volume","create",S.VOL])
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  P.create("raw",cols)
  with native.open("rb") as f:
   out=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
    stdin=f,check=False,timeout=950)
  if out.returncode:raise RuntimeError("Native import: "+out.stderr.decode(errors="replace")[-1000:])
  native.unlink()
  ingest=[]
  for t in TABLES:
   partition="toYYYYMM(LO_ORDERDATE)" if t.startswith("mo") else "toYear(LO_ORDERDATE)"
   mode=int(t.endswith("1"))
   sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree "
        "PARTITION BY "+partition+" ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
   start=time.monotonic()
   sql("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw SETTINGS "
       f"materialize_statistics_on_insert={mode},"
       "max_threads=2,max_insert_threads=1,min_insert_block_size_rows=100000,"
       "min_insert_block_size_bytes=1")
   elapsed=round(time.monotonic()-start,2)
   ingest.append({"table":t,"mode":mode,"seconds":elapsed})
   print("C44A_INGEST",t,elapsed,flush=True)
  t_idle=wait_idle(TABLES)
  for t in TABLES:sql("SYSTEM STOP MERGES ssb."+t)
  state=P.snap("fixed")
  if any(state[t]["rows"]!=6001215 for t in TABLES):raise RuntimeError("Unequal row count")
  if len({state[t]["fingerprint"] for t in TABLES})!=1:raise RuntimeError("Different logical rows")
  meta=sql("SELECT table,partition,count(),sum(rows),sum(marks) FROM system.parts "
   "WHERE database='ssb' AND active AND table IN ('mo0','mo1','yr0','yr1') "
   "GROUP BY table,partition ORDER BY table,partition FORMAT TSV")
  (R/"partition_inventory.tsv").write_text(meta)
  answers={}
  for qi,q in enumerate(qs,1):
   expect=None
   for t in TABLES:
    for arm,s,p,c in ARMS:
     digest=hashlib.sha256(sql(config(q,t,s,p,c)+" FORMAT TSV").encode()).hexdigest()
     if expect is not None and digest!=expect:
      raise RuntimeError(f"SQL answer differs Q{qi} {t} {arm}")
     expect=digest
     answers[f"{qi}/{t}/{arm}"]=digest
  save("answers.json",answers)
  plans={}
  for qi in (1,2,3,7,10,13):
   for t in ("mo0","mo1","yr0","yr1"):
    for arm,s,p,c in ARMS[:4]:
     plans[f"{qi}/{t}/{arm}"]=sql("EXPLAIN indexes=1 "+config(qs[qi-1],t,s,p,c))
  save("plans.json",plans)
  records=[]
  for rnd in range(-1,REPS):
   rng=random.Random(440000+rnd);query_order=list(range(1,14));rng.shuffle(query_order)
   for qi in query_order:
    arms=list(ARMS);rng.shuffle(arms)
    for ai,(arm,s,p,c) in enumerate(arms):
     for suffix in ("mo","yr"):
      opts=[suffix+"0",suffix+"1"]
      if (qi+ai+rnd)%2:opts.reverse()
      for t in opts:
       qid=f"c44a_{len(records)}"
       sql(config(qs[qi-1],t,s,p,c),qid=qid,fmt="Null")
       records.append({"round":rnd,"query":qi,"table":t,"arm":arm,"qid":qid})
   print("C44A_ROUND",rnd,len(records),flush=True)
  sql("SYSTEM FLUSH LOGS")
  log=sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
    "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c44a_') FORMAT JSONEachRow")
  log_index={x["query_id"]:x for ln in log.splitlines() if ln for x in [json.loads(ln)]}
  if len(log_index)!=len(records):raise RuntimeError("Missing query logs")
  for z in records:
   rec=log_index[z["qid"]];ev=rec.get("ProfileEvents") or {}
   z.update(ms=rec["query_duration_ms"],read_rows=rec["read_rows"],read_bytes=rec["read_bytes"],
     stat_us=ev.get("LoadedStatisticsMicroseconds",0),opt_us=ev.get("QueryPlanOptimizeMicroseconds",0),
     selected_marks=ev.get("SelectedMarks",0))
  with (R/"receipts.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
  m={}
  for t in TABLES:
   for arm,_,_,_ in ARMS:
    for qi in range(1,14):
     a=[x for x in records if x["round"]>=0 and (x["table"],x["arm"],x["query"])==(t,arm,qi)]
     if len(a)!=REPS:raise RuntimeError("Missing repeated cells")
     m[(t,arm,qi)]={field:statistics.median(x[field] for x in a)
       for field in ("ms","stat_us","opt_us","read_rows","selected_marks")}
  pairs=[]
  for prefix in ("mo","yr"):
   for arm,_,_,_ in ARMS:
    def score(t):
     return statistics.mean(statistics.mean(m[(t,arm,i)]["ms"] for i in fam) for fam in FAMILY)
    a=score(prefix+"0");b=score(prefix+"1")
    n15=sum(m[(prefix+"1",arm,i)]["ms"] >= 1.5*m[(prefix+"0",arm,i)]["ms"] for i in range(1,14))
    pairs.append({"partition":prefix,"arm":arm,"ratio":b/a,"old_ms":a,"new_ms":b,
       "q_ge_1_5":n15,"gate":"BLOCK" if b/a>1.10 or n15>=2 else "APPROVE"})
  save("medians.json",{"|".join(map(str,k)):v for k,v in m.items()})
  save("pairs.json",pairs)
  after=P.snap("verified")
  if state!=after:raise RuntimeError("State changed while benchmarking")
  save("FINAL_STATUS.json",{"valid":True,"rows":6001215,"queries":13,
    "partitioning":["monthly","yearly"],"natural_idle_seconds":t_idle,
    "merge_disabled_during_measure_only":True,"ingest":ingest,
    "state":state,"pairs":pairs,"rounds":REPS,"warmups":1,
    "limitations":["One runner","Policy effect may vanish after natural merges",
      "Monthly and yearly partition strategies differ in number of parts and their boundaries",
      "SSB does not establish benefit for normalized join workloads"]})
  print("C44A_SUCCESS",json.dumps(pairs),flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=120)
if __name__=="__main__":main()
