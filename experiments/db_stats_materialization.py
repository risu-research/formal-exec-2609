#!/usr/bin/env python3
"""Stage24b: physical statistics materialization x release x PREWHERE.
Checks whether release-level stats-on-insert defaults confound optimization timing.
"""
import csv,json,pathlib,random,statistics,time
import db_sf1_probe as base
ROOT=pathlib.Path("s24b").resolve();base.ROOT=ROOT
RELEASES=base.VERSIONS
QIDS=[2,3,4]
REPS=10
def query(ct,sql,qid=None,fmt=None,timeout=300):
 p=base.client(ct,sql,qid=qid,fmt=fmt,check=False,timeout=timeout)
 if p.returncode:raise RuntimeError("SQL error "+sql[:130]+": "+p.stderr.decode(errors="replace")[-1300:])
 return p.stdout.decode(errors="replace")
def sql_no_db(ct,sql):base.run(["docker","exec",ct,"clickhouse-client","--query",sql],timeout=120)
def boot(ver):
 ct="s24b_"+ver.replace(".","_")
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"clickhouse/clickhouse-server:"+ver],timeout=120)
 for _ in range(100):
  if not base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=15).returncode:break
  time.sleep(1)
 else:raise RuntimeError("Unready "+ver)
 sql_no_db(ct,"CREATE DATABASE IF NOT EXISTS ssb")
 return ct
def test_release(ver,native,qs):
 dest=ROOT/ver;dest.mkdir(parents=True,exist_ok=True)
 ct=boot(ver)
 try:
  defaults=query(ct,"SELECT name,value FROM system.settings WHERE name IN ('materialize_statistics_on_insert','use_statistics','optimize_move_to_prewhere') ORDER BY name FORMAT TSV")
  (dest/"defaults.tsv").write_text(defaults)
  stats_mt=query(ct,"SELECT name,value FROM system.merge_tree_settings WHERE name LIKE '%statistic%' ORDER BY name FORMAT TSV")
  (dest/"mergetree_stat_defaults.tsv").write_text(stats_mt)
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  fp={}
  for flag in [0,1]:
   table=f"lineorder_flat_m{flag}"
   query(ct,f"CREATE TABLE ssb.{table} ({cols}) ENGINE=MergeTree PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
   with open(native,"rb") as f:
    statement=f"INSERT INTO ssb.{table} SETTINGS materialize_statistics_on_insert={flag} FORMAT Native"
    r=base.run(["docker","exec","-i",ct,"clickhouse-client","--query",statement],stdin=f,check=False,timeout=1000)
    if r.returncode:raise RuntimeError("Insert "+ver+" "+str(flag)+": "+r.stderr.decode(errors="replace")[-1200:])
   query(ct,f"SYSTEM STOP MERGES ssb.{table}")
   fp[flag]=query(ct,f"SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) FROM ssb.{table} FORMAT TSV").strip()
  if len(set(fp.values()))!=1:raise RuntimeError("Logically unequal tables "+ver)
  partstats=query(ct,"SELECT table,count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts WHERE active AND database='ssb' GROUP BY table ORDER BY table FORMAT TSV")
  (dest/"parts.tsv").write_text(partstats)
  columnstats=query(ct,"SELECT table,name,statistics FROM system.columns WHERE database='ssb' ORDER BY table,name LIMIT 40 FORMAT TSV",timeout=80)
  (dest/"columns_statistics.tsv").write_text(columnstats)
  results={}
  for q in QIDS:
   for flag in [0,1]:
    table=f"lineorder_flat_m{flag}"
    sql=qs[q-1].replace("FROM lineorder_flat","FROM "+table)
    out=query(ct,sql+" FORMAT TSV")
    results[f"q{q}_m{flag}"]=out
  for q in QIDS:
   if results[f"q{q}_m0"]!=results[f"q{q}_m1"]:raise RuntimeError(f"Different result mat0/mat1 q{q} ver{ver}")
  (dest/"result_hashes.json").write_text(json.dumps({k:base.hashlib.sha256(v.encode()).hexdigest() for k,v in results.items()},indent=2)+"\n")
  # Baselines plus interventions; explicitly set use_statistics=0, to see whether the
  # estimator is instantiated before the setting is checked.
  treatments=[("prewhere_on_stats_on","optimize_move_to_prewhere=1,use_statistics=1"),
              ("prewhere_on_stats_off","optimize_move_to_prewhere=1,use_statistics=0"),
              ("prewhere_off_stats_on","optimize_move_to_prewhere=0,use_statistics=1")]
  rows=[];seq=0
  for r in range(REPS+1):
   combos=[(q,m,n,s) for q in QIDS for m in [0,1] for n,s in treatments]
   random.Random(240000+r).shuffle(combos)
   for q,m,treat,settings in combos:
    sql=qs[q-1].replace("FROM lineorder_flat",f"FROM lineorder_flat_m{m}")
    qid=f"s24b_{ver.replace('.','_')}_q{q:02d}_m{m}_{treat}_r{r}_{seq}"
    seq+=1
    p=base.client(ct,sql+" SETTINGS "+settings,qid=qid,fmt="Null",check=False,timeout=220)
    if p.returncode:raise RuntimeError("Measure "+qid+" "+p.stderr.decode(errors="replace")[-900:])
    rows.append({"version":ver,"q":q,"materialized":m,"treatment":treat,"rep":r,"warmup":int(r==0),"query_id":qid})
  query(ct,"SYSTEM FLUSH LOGS")
  data=query(ct,"SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s24b_') FORMAT JSONEachRow")
  parsed={x["query_id"]:x for line in data.splitlines() if line.strip() for x in [json.loads(line)]}
  if len(rows)!=len(parsed):raise RuntimeError(f"Incomplete logs {len(rows)} {len(parsed)}")
  for row in rows:
   p=parsed[row["query_id"]];row.update(server_ms=p["query_duration_ms"],read_rows=p["read_rows"],
      plan_us=p.get("ProfileEvents",{}).get("QueryPlanOptimizeMicroseconds"),
      stats_loaded_us=p.get("ProfileEvents",{}).get("LoadedStatisticsMicroseconds"))
  with (dest/"runs.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
  summary=[]
  for q in QIDS:
   for m in [0,1]:
    for name,_ in treatments:
     sub=[x for x in rows if x["q"]==q and x["materialized"]==m and x["treatment"]==name and not x["warmup"]]
     d={"version":ver,"q":q,"materialized_on_insert":m,"treatment":name,"n":len(sub)}
     for k in ["server_ms","plan_us","read_rows","stats_loaded_us"]:
      values=[float(x[k]) for x in sub if x[k] is not None]
      d[k+"_median"]=statistics.median(values) if values else None
     summary.append(d)
     print("CONDITION "+json.dumps(d),flush=True)
  with (dest/"summary.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
  print("VERSION_DONE "+json.dumps({"version":ver,"fp":fp,"parts":partstats.strip(),"records":len(rows)}),flush=True)
  return {"version":ver,"fp":fp,"parts":partstats.strip(),"records":len(rows)}
 finally:base.run(["docker","rm","-f",ct],check=False)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for v in RELEASES:base.run(["docker","pull","clickhouse/clickhouse-server:"+v])
 sqls=base.pinned_sources();native=base.make_data()
 outcome=[test_release(v,native,sqls) for v in [RELEASES[1],RELEASES[0]]]
 if len(set(o["fp"][0] for o in outcome))!=1:raise RuntimeError("Cross release fingerprint mismatch")
 report={"valid":True,"outcomes":outcome,"source_native_sha256":base.digest(native)}
 (ROOT/"FINAL_STATUS.json").write_text(json.dumps(report,indent=2)+"\n")
 print("FINAL_STATUS "+json.dumps(report),flush=True)
if __name__=="__main__":main()
