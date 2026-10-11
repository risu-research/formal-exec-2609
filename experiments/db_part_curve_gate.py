#!/usr/bin/env python3
"""Stage29 matched SSB SF1 physical-state gradient in a pinned ClickHouse release.
All three arms use identical DDL and logical data. State construction changes
physical layout as well as part count; do not infer pure count causality.
"""
import csv,datetime,hashlib,json,pathlib,random,statistics,sys,time
import db_sf1_probe as base
VER=sys.argv[1]
assert VER in ("26.7.1.1315","26.8.1.2041")
ROOT=pathlib.Path("s29")/VER
base.ROOT=ROOT
base.VERSIONS=[VER,VER]
QIDS=range(1,14)
WARM=1;REPS=6
EVENTS=("LoadedStatisticsMicroseconds","QueryPlanOptimizeMicroseconds","FileOpen","SelectedMarks")
def save(name,s):
 p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
def jsave(name,obj):save(name,json.dumps(obj,sort_keys=True,indent=2)+"\n")
def q(ct,sql,qid=None,fmt=None,timeout=180):
 p=base.client(ct,sql,qid=qid,fmt=fmt,check=False,timeout=timeout)
 if p.returncode:raise RuntimeError("SQL FAILED "+sql[:140]+" "+p.stderr.decode(errors="replace")[-1300:])
 return p.stdout.decode(errors="replace")
def boot():
 ct="s29_"+VER.replace(".","_")
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144","clickhouse/clickhouse-server:"+VER])
 for i in range(90):
  if not base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=10).returncode:break
  time.sleep(1)
 else:raise RuntimeError("Cannot start server "+VER)
 base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 return ct
def fp(ct,t):
 return q(ct,"SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
  "sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)),min(LO_ORDERDATE),max(LO_ORDERDATE) FROM ssb."+t+" FORMAT TSV").strip()
def physical(ct,t):
 s=q(ct,"SELECT count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' FORMAT TSV").strip()
 n,rows,disk,marks=map(int,s.split("\t"))
 return {"parts":n,"rows":rows,"bytes":disk,"marks":marks,"fingerprint":fp(ct,t)}
def prepared(ct,cols):
 for t in ("raw_source","many","middle","single"):
  q(ct,"CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
  q(ct,"SYSTEM STOP MERGES ssb."+t)
def insert_big(ct,dest,from_table):
 s=(f"INSERT INTO ssb.{dest} SELECT * FROM ssb.{from_table} SETTINGS materialize_statistics_on_insert=1,"
    "max_threads=1,max_insert_threads=1,min_insert_block_size_rows=750152,min_insert_block_size_bytes=1,"
    "max_memory_usage=9000000000")
 q(ct,s,timeout=620)
def quarter(k):
 return "%04d-%02d-01"%(1992+k//4,1+(k%4)*3)
def middle(ct):
 # Stage28 independently observed single INSERT creates 11 active parts.
 # Reuse that verified path instead of 27 SELECTs producing 994 tiny parts.
 q(ct,"INSERT INTO ssb.middle SELECT * FROM ssb.raw_source "
   "SETTINGS materialize_statistics_on_insert=1,max_threads=1,"
   "max_insert_threads=1,max_memory_usage=9000000000",timeout=580)
 print("S29_MIDDLE_DIRECT "+json.dumps(physical(ct,"middle")),flush=True)
def sqlfor(sql,t):
 assert "FROM lineorder_flat" in sql
 return sql.replace("FROM lineorder_flat","FROM "+t)
def check_answers(ct,sqls):
 hashes={}
 for qi in QIDS:
  for t in ("many","middle","single"):
   for u in (0,1):
    sql=sqlfor(sqls[qi-1],t)+f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0"
    hashes[f"q{qi}_{t}_u{u}"]=hashlib.sha256(q(ct,sql+" FORMAT TSV").encode()).hexdigest()
  if len(set(hashes[f"q{qi}_{t}_u{u}"] for t in ("many","middle","single") for u in (0,1)))!=1:
   raise RuntimeError("Query output mismatched "+str(qi))
 jsave("answers_sha256.json",hashes)
 return {f"q{qi}":hashes[f"q{qi}_many_u0"] for qi in QIDS}
def measure(ct,sqls,ps):
 records=[]
 for rnd in range(-WARM,REPS):
  cells=[(t,qi,u) for t in ("many","middle","single") for qi in QIDS for u in (0,1)]
  random.Random(290000+rnd).shuffle(cells)
  for i,(t,qi,u) in enumerate(cells):
   qid=f"s29_{VER.replace('.','_')}_{t}_q{qi:02d}_u{u}_r{rnd}_i{i}"
   sql=sqlfor(sqls[qi-1],t)+f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0,max_memory_usage=9000000000"
   q(ct,sql,qid=qid,fmt="Null",timeout=180)
   records.append({"version":VER,"state":t,"actual_parts":ps[t]["parts"],"q":qi,
      "family":("Q1" if qi<=3 else "Q2" if qi<=6 else "Q3" if qi<=10 else "Q4"),
      "use_statistics":u,"round":rnd,"warmup":int(rnd<0),"query_id":qid})
  print("S29_ROUND",VER,rnd,flush=True)
 q(ct,"SYSTEM FLUSH LOGS")
 raw=q(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents FROM system.query_log "
        "WHERE type='QueryFinish' AND startsWith(query_id,'s29_') FORMAT JSONEachRow")
 save("query_log.jsonl",raw)
 logs={x["query_id"]:x for line in raw.splitlines() if line.strip() for x in [json.loads(line)]}
 if len(logs)!=len(records):raise RuntimeError(f"Missing query receipts {len(logs)} of {len(records)}")
 for r in records:
  z=logs[r["query_id"]];ev=z.get("ProfileEvents") or {}
  for name in ("query_duration_ms","read_rows","read_bytes"):r[name]=z[name]
  for name in EVENTS:r[name]=ev.get(name,0)
 with (ROOT/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
 med=[]
 for t in ("many","middle","single"):
  for qi in QIDS:
   for u in (0,1):
    v=[r for r in records if r["state"]==t and r["q"]==qi and r["use_statistics"]==u and r["round"]>=0]
    assert len(v)==REPS
    z={"version":VER,"state":t,"parts":ps[t]["parts"],"q":qi,"family":v[0]["family"],"use_statistics":u,"n":REPS}
    for name in ("query_duration_ms","read_rows","read_bytes")+EVENTS:
     z[name+"_median"]=statistics.median(float(r[name]) for r in v)
    med.append(z)
 with (ROOT/"medians.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
 for qi in (2,3,4):
  for t in ("many","middle","single"):
   a=next(z for z in med if z["q"]==qi and z["state"]==t and z["use_statistics"]==1)
   b=next(z for z in med if z["q"]==qi and z["state"]==t and z["use_statistics"]==0)
   print("S29_SPOT "+json.dumps({"version":VER,"q":qi,"state":t,"parts":ps[t]["parts"],
      "wall_on":a["query_duration_ms_median"],"wall_off":b["query_duration_ms_median"],
      "stats_load_ms":a["LoadedStatisticsMicroseconds_median"]/1000,
      "extra_fileopen":a["FileOpen_median"]-b["FileOpen_median"]}),flush=True)
 return med
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 print("S29_START "+VER,flush=True)
 base.run(["docker","pull","clickhouse/clickhouse-server:"+VER],timeout=430)
 sqls=base.pinned_sources();native=base.make_data()
 ct=boot()
 try:
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  prepared(ct,cols)
  with open(native,"rb") as f:
   p=base.run(["docker","exec","-i",ct,"clickhouse-client","--query",
     "INSERT INTO ssb.raw_source SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
      stdin=f,check=False,timeout=700)
   if p.returncode:raise RuntimeError("Raw ingest failed "+p.stderr.decode(errors="replace")[-1300:])
  native.unlink()
  original=physical(ct,"raw_source")
  print("S29_SOURCE "+json.dumps(original),flush=True)
  if original["rows"]!=6001215:raise RuntimeError("Not official SF1 row count")
  insert_big(ct,"many","raw_source")
  print("S29_MANY "+json.dumps(physical(ct,"many")),flush=True)
  middle(ct)
  print("S29_MIDDLE "+json.dumps(physical(ct,"middle")),flush=True)
  insert_big(ct,"single","many")
  q(ct,"SYSTEM START MERGES ssb.single")
  t0=time.monotonic()
  q(ct,"OPTIMIZE TABLE ssb.single FINAL SETTINGS optimize_throw_if_noop=1",timeout=650)
  q(ct,"SYSTEM STOP MERGES ssb.single")
  print("S29_MERGE_SECONDS "+str(time.monotonic()-t0),flush=True)
  ps={t:physical(ct,t) for t in ("many","middle","single")}
  jsave("physical_states.json",ps)
  print("S29_STATES "+json.dumps({t:p["parts"] for t,p in ps.items()}),flush=True)
  if len(set([original["fingerprint"]]+[x["fingerprint"] for x in ps.values()]))!=1:
   raise RuntimeError("Logical data fingerprints mismatch")
  if not (ps["many"]["parts"]>=400 and 8<=ps["middle"]["parts"]<=100 and ps["single"]["parts"]==1):
   raise RuntimeError("Physical part gradient failed")
  hashes=check_answers(ct,sqls)
  for qi in (2,3,4,7,11):
   for t in ("many","middle","single"):
    sql=sqlfor(sqls[qi-1],t)+" SETTINGS use_statistics=1,optimize_move_to_prewhere=1"
    for kind,pref in (("indexes","EXPLAIN indexes=1 "),("plan","EXPLAIN PLAN ")):
     save(f"explain/q{qi}_{t}_{kind}.txt",q(ct,pref+sql))
  med=measure(ct,sqls,ps)
  valid=all(next(z for z in med if z["q"]==2 and z["state"]==t and z["use_statistics"]==1)["LoadedStatisticsMicroseconds_median"]>0
       for t in ("many","middle","single"))
  report={"valid":valid,"version":VER,"part_counts":{t:ps[t]["parts"] for t in ps},
    "source_fingerprint":original["fingerprint"],"answers":hashes,"measured":3*13*2*REPS,
    "warmups":3*13*2*WARM,
    "limitation":"different part constructions change marks, layout, and local statistics; count slope is descriptive"}
  jsave("FINAL_STATUS.json",report)
  print("S29_FINAL "+json.dumps(report),flush=True)
  if not valid:raise RuntimeError("Stat load path vanished in an arm")
 finally:base.run(["docker","rm","-f",ct],check=False,timeout=60)
if __name__=="__main__":main()
