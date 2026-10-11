#!/usr/bin/env python3
"""Controlled cache-residency checks on fixed, immutable data parts."""
import csv,json,hashlib,pathlib,random,statistics
import db_mixed_history_upgrade as P
S=P.S
R=pathlib.Path("c43c");R.mkdir(exist_ok=True)
P.R=R;P.T=("a","b")
S.ROOT=S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c43_cache";S.VOL="c43_cache_volume"
ARMS=(("cache_on",1,1),("cache_off",1,0),("statistics_off",0,1))
Q=list(range(1,14));TABLES=("a","b")
def dump(path,v):
 p=R/path;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")
def sql(s,qid=None,fmt=None):return P.sql(s,qid=qid,fmt=fmt)
def facts(label):
 data=P.snap(label)
 dump(label+"/fingerprints.json",data)
 return data
def measure(phase,queries,n,order,first=False):
 result=[]
 rng=random.Random(430000+(1 if order=="ab" else 2)+(0 if first else 17))
 queue=[]
 if first:
  qs=Q[:];rng.shuffle(qs)
  for qi in qs:
   tables=list(TABLES if order=="ab" else TABLES[::-1])
   for t in tables:
    for arm,u,c in ARMS:
     queue.append((qi,t,arm,u,c,-1))
 else:
  for k in range(n):
   qs=Q[:];rng.shuffle(qs)
   for qi in qs:
    combos=[(t,arm,u,c) for t in TABLES for arm,u,c in ARMS]
    rng.shuffle(combos)
    for t,arm,u,c in combos:
     queue.append((qi,t,arm,u,c,k))
 for i,(qi,t,arm,u,c,k) in enumerate(queue):
  qid=f"c43c_{phase}_{i}"
  sql(P.qstr(queries[qi-1],t,u,c),qid=qid,fmt="Null")
  result.append({"phase":phase,"round":k,"q":qi,"table":t,"arm":arm,"qid":qid,"sequence":i})
 sql("SYSTEM FLUSH LOGS")
 raw=sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c43c_"+phase+"_') FORMAT JSONEachRow")
 by={v["query_id"]:v for l in raw.splitlines() if l for v in [json.loads(l)]}
 if len(by)!=len(result):raise RuntimeError("Incomplete query log "+phase+" "+str(len(by))+"/"+str(len(result)))
 for item in result:
  v=by[item["qid"]];ev=v.get("ProfileEvents") or {}
  item.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],read_bytes=v["read_bytes"],
              load_us=ev.get("LoadedStatisticsMicroseconds",0),plan_us=ev.get("QueryPlanOptimizeMicroseconds",0),
              file_opens=ev.get("FileOpen",0))
 with (R/(phase+".csv")).open("w",newline="") as f:
  writer=csv.DictWriter(f,fieldnames=list(result[0]));writer.writeheader();writer.writerows(result)
 aggregate=[]
 for t in TABLES:
  for arm,_,_ in ARMS:
   vals=[v for v in result if v["table"]==t and v["arm"]==arm]
   aggregate.append({"table":t,"arm":arm,"n":len(vals),
       "mean_ms":statistics.mean(v["ms"] for v in vals),
       "mean_load_ms":statistics.mean(v["load_us"] for v in vals)/1000,
       "mean_plan_ms":statistics.mean(v["plan_us"] for v in vals)/1000})
 dump(phase+"_aggregate.json",aggregate)
 print("C43C",phase,json.dumps(aggregate),flush=True)
def main():
 for version in (S.OLD,S.NEW):
  S.run(["docker","pull","clickhouse/clickhouse-server:"+version],timeout=500)
 qs=S.base.pinned_sources(); native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  P.create("raw",cols)
  for t in TABLES:
   sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree "
       "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE, LO_ORDERKEY) "
       "SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
  with native.open("rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
       "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
       stdin=f,check=False,timeout=950)
  if p.returncode:raise RuntimeError(p.stderr.decode(errors="replace")[-1200:])
  native.unlink()
  for t in TABLES:
   sql("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw SETTINGS "
       "materialize_statistics_on_insert=1,max_threads=2,max_insert_threads=1,"
       "min_insert_block_size_rows=150000,min_insert_block_size_bytes=1")
   sql("SYSTEM STOP MERGES ssb."+t)
  base=facts("initial")
  if base["a"]["fingerprint"]!=base["b"]["fingerprint"] or base["a"]["rows"]!=6001215:raise RuntimeError("Data mismatch")
  if min(base[t]["stat_files"] for t in TABLES)==0:raise RuntimeError("No statistics produced")
  answers={}
  for qi in Q:
   expected=None
   for t in TABLES:
    for arm,u,c in ARMS:
     h=hashlib.sha256(sql(P.qstr(qs[qi-1],t,u,c)+" FORMAT TSV").encode()).hexdigest()
     if expected is not None and h!=expected:raise RuntimeError("SQL answer mismatch")
     expected=h
     answers[f"{qi}/{t}/{arm}"]=h
  dump("answers.json",answers)
  for order in ("ab","ba"):
   # A process restart clears process-local statistic cache, but leaves data volume intact.
   S.run(["docker","stop","-t","90",S.CT],timeout=130)
   S.run(["docker","rm",S.CT])
   S.start(S.NEW)
   for t in TABLES:sql("SYSTEM STOP MERGES ssb."+t)
   snap=facts("restart_"+order)
   if snap!=base:raise RuntimeError("Physical state changed at restart "+order)
   measure("first_"+order,qs,0,order,first=True)
   measure("warm_"+order,qs,5,order)
   check=facts("after_"+order)
   if check!=base:raise RuntimeError("Data mutated during cache probe")
  dump("FINAL_STATUS.json",{"valid":True,"same_disk_before_after":True,
       "cached_vs_bypass_measured":True,"stat_disabled_negative_control":True,
       "restarts":2,"replications_per_table_query_arm_after_warm":10,
       "limitations":["One runner; two separate process restart cycles",
          "OS page cache is not cleared by a server process restart",
          "First cache-ON measurement may warm other queries' shared statistics",
          "No mutation or DDL was performed in the measurement phases"]})
  print("C43C_SUCCESS",flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=110)
if __name__=="__main__":main()
