#!/usr/bin/env python3
"""Stage26: independent OFFICIAL TPC-H (ClickHouse/tpc-h-openhouse) matched-statistics study.
Source: aa6515bdf405b8e8633d685f0d0fb50e1b1f42e4
SF10 public Parquet, referentially consistent *key-restricted subset* (NOT standard TPC-H SF10 run).
Query text from source unmodified modulo table-name prefixes and session SETTINGS.
"""
import csv,hashlib,json,pathlib,random,re,statistics,subprocess,time,urllib.request
ROOT=pathlib.Path("s26").resolve();ROOT.mkdir(parents=True,exist_ok=True)
PIN="aa6515bdf405b8e8633d685f0d0fb50e1b1f42e4"
BASE="https://raw.githubusercontent.com/ClickHouse/tpc-h-openhouse/"+PIN+"/clickhouse-cloud/"
VERSIONS=["26.8.1.2041","26.7.1.1315"]
TABLES=("nation","region","supplier","customer","orders","lineitem")
QUERIES=(3,5,10,12)
SOURCE_PARQUET={"nation":"nation_0.parquet","region":"region_0.parquet","supplier":"supplier_0.parquet",
"customer":"customer0.parquet","orders":"orders_0.parquet","lineitem":"lineitem_0.parquet"}
SELECTS={
"nation":"n_nationkey,toFixedString(rpad(n_name,25,' '),25),n_regionkey,n_comment",
"region":"r_regionkey,toFixedString(rpad(r_name,25,' '),25),r_comment",
"supplier":"s_suppkey,toFixedString(rpad(s_name,25,' '),25),s_address,s_nationkey,toFixedString(rpad(s_phone,15,' '),15),s_acctbal,s_comment",
"customer":"c_custkey,c_name,c_address,c_nationkey,toFixedString(rpad(c_phone,15,' '),15),c_acctbal,toFixedString(rpad(c_mktsegment,10,' '),10),c_comment",
"orders":"o_orderkey,o_custkey,toFixedString(rpad(o_orderstatus,1,' '),1),o_totalprice,o_orderdate,toFixedString(rpad(o_orderpriority,15,' '),15),toFixedString(rpad(o_clerk,15,' '),15),o_shippriority,o_comment",
"lineitem":"l_orderkey,l_partkey,l_suppkey,l_linenumber,l_quantity,l_extendedprice,l_discount,l_tax,toFixedString(rpad(l_returnflag,1,' '),1),toFixedString(rpad(l_linestatus,1,' '),1),l_shipdate,l_commitdate,l_receiptdate,toFixedString(rpad(l_shipinstruct,25,' '),25),toFixedString(rpad(l_shipmode,10,' '),10),l_comment"
}
KEY_CAP=3000000
REPS=6;WARMUPS=2
ARMS=((0,0),(0,1),(1,0),(1,1))
def run(cmd,inp=None,timeout=250):
 p=subprocess.run(cmd,input=inp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if p.returncode:raise RuntimeError("CMD ERROR "+str(cmd[:6])+" err="+p.stderr.decode(errors="replace")[-1900:])
 return p.stdout.decode(errors="replace")
def q(ct,sql,qid=None,fmt=None,timeout=250):
 cmd=["docker","exec",ct,"clickhouse-client","--database","tpch"]
 if qid:cmd+=["--query_id",qid]
 if fmt:cmd+=["--format",fmt]
 cmd+=["--query",sql]
 return run(cmd,timeout=timeout)
def fetch(s):
 with urllib.request.urlopen(BASE+s,timeout=100) as r:return r.read().decode()
def setup_source():
 schema=fetch("setup/init.sql");(ROOT/"upstream_schema.sql").write_text(schema)
 sqls={i:fetch(f"queries/query_{i:02d}.sql").strip().rstrip(";") for i in QUERIES}
 for i,s in sqls.items():(ROOT/f"upstream_tpch_q{i:02d}.sql").write_text(s+";\n")
 defs={}
 for raw in re.split(r";",schema):
  cleaned="\n".join(s for s in raw.splitlines() if not s.lstrip().startswith("--")).strip()
  if not cleaned:continue
  m=re.match(r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)\s*PRIMARY\s+KEY\s*\((.*?)\)\s*$",cleaned,re.S|re.I)
  if m:defs[m.group(1)]=(m.group(2),m.group(3))
 for t in TABLES:
  if t not in defs:raise RuntimeError("Cannot parse official DDL for "+t)
 return sqls,defs
def boot(ver):
 ct="s26_"+ver.replace(".","_")
 subprocess.run(["docker","rm","-f",ct],capture_output=True)
 run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144",
      "clickhouse/clickhouse-server:"+ver],timeout=110)
 for _ in range(105):
  p=subprocess.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],capture_output=True,timeout=10)
  if p.returncode==0:break
  time.sleep(1)
 else:raise RuntimeError("Server not responding "+ver)
 run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS tpch"])
 return ct
def substitute(sql,mat):
 # No changes to join predicates, constants, projections or aggregations.
 return re.sub(r"\b("+"|".join(TABLES)+r")\b",lambda m:"tpch."+m.group(1)+f"_m{mat}",sql)
def main_for_version(ver,sqls,defs):
 root=ROOT/ver;root.mkdir(parents=True,exist_ok=True)
 ct=boot(ver)
 try:
  settings=q(ct,"SELECT name,value FROM system.settings WHERE name IN ('materialize_statistics_on_insert','use_statistics','max_memory_usage','query_plan_optimize_join_order_algorithm') ORDER BY name FORMAT TSV")
  (root/"settings.tsv").write_text(settings)
  # Confirm public TPC-H source available before loading: no stale cached local source.
  sample=q(ct,"SELECT count() FROM s3('https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/nation_0.parquet', NOSIGN,'Parquet') FORMAT TSV",timeout=75).strip()
  if sample!="25":raise RuntimeError("Public source nation mismatch: "+sample)
  ingest=[]
  for tbl in TABLES:
   columns,pk=defs[tbl]
   for suffix in ("raw","m0","m1"):
    q(ct,f"CREATE TABLE tpch.{tbl}_{suffix} ({columns}) ENGINE=MergeTree ORDER BY ({pk})")
   parq=SOURCE_PARQUET[tbl]
   url="https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/"+parq
   cond=(" WHERE o_orderkey <= "+str(KEY_CAP)) if tbl=="orders" else (
         " WHERE l_orderkey <= "+str(KEY_CAP)) if tbl=="lineitem" else ""
   t0=time.perf_counter()
   q(ct,f"INSERT INTO tpch.{tbl}_raw SELECT {SELECTS[tbl]} FROM s3('{url}',NOSIGN,'Parquet')"+cond+
      " SETTINGS materialize_statistics_on_insert=0,max_threads=4,max_insert_threads=4,max_memory_usage=9000000000",timeout=1100)
   rawsecs=time.perf_counter()-t0
   n=int(q(ct,f"SELECT count() FROM tpch.{tbl}_raw FORMAT TSV"))
   if n<=0:raise RuntimeError("zero rows "+tbl)
   ingest.append({"version":ver,"table":tbl,"m":"raw","rows":n,"ingest_seconds":round(rawsecs,3)})
   print("SOURCE_TABLE "+json.dumps(ingest[-1]),flush=True)
   for mat in (0,1):
    t1=time.perf_counter()
    q(ct,f"INSERT INTO tpch.{tbl}_m{mat} SELECT * FROM tpch.{tbl}_raw "
      f"SETTINGS materialize_statistics_on_insert={mat},max_threads=4,max_insert_threads=4,max_memory_usage=9000000000",timeout=650)
    ingest.append({"version":ver,"table":tbl,"m":mat,"rows":n,"ingest_seconds":round(time.perf_counter()-t1,3)})
    q(ct,f"SYSTEM STOP MERGES tpch.{tbl}_m{mat}")
   # Save checkpoint after each table to inspect early failures
   with (root/"ingest.csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(ingest[0]));w.writeheader();w.writerows(ingest)
  counts={}
  for tbl in TABLES:
   for m in (0,1):
    counts[f"{tbl}_m{m}"]=int(q(ct,f"SELECT count() FROM tpch.{tbl}_m{m} FORMAT TSV"))
   if counts[f"{tbl}_m0"]!=counts[f"{tbl}_m1"]:raise RuntimeError("Unequal rows "+tbl)
  (root/"table_counts.json").write_text(json.dumps(counts,indent=2)+"\n")
  parts=q(ct,"SELECT table,count(),sum(bytes_on_disk),sum(rows),sum(marks) FROM system.parts "
         "WHERE database='tpch' AND active GROUP BY table ORDER BY table FORMAT TSV")
  (root/"physical_parts.tsv").write_text(parts)
  # Output equivalence before measuring; preserve independent TPC-H semantics on subset.
  answers={}
  for qi,sql in sqls.items():
   for m,u in ARMS:
    ss=substitute(sql,m)+f" SETTINGS use_statistics={u},max_threads=4,max_memory_usage=9000000000,query_cache_nondeterministic_function_handling='ignore'"
    # Final setting may not exist on some versions: avoid unsupported query cache switch
    ss=ss.replace(",query_cache_nondeterministic_function_handling='ignore'","")
    out=q(ct,ss+" FORMAT TSV",timeout=280)
    answers[(qi,m,u)]=hashlib.sha256(out.encode()).hexdigest()
   if len({answers[(qi,m,u)] for m,u in ARMS})!=1:
    raise RuntimeError(f"Results differ q{qi} {ver}")
  (root/"answer_sha256.json").write_text(json.dumps({f"q{qi:02d}_m{m}u{u}":v for (qi,m,u),v in answers.items()},sort_keys=True,indent=2)+"\n")
  # Plans can show whether stats alter JOIN shape, not just cost.
  for qi,sql in sqls.items():
   for m,u in ARMS:
    statement=substitute(sql,m)+f" SETTINGS use_statistics={u},max_threads=4"
    try:plan=q(ct,"EXPLAIN PLAN "+statement,timeout=90)
    except Exception as e:plan="EXPLAIN ERROR "+str(e)
    (root/f"plan_q{qi:02d}_m{m}u{u}.txt").write_text(plan)
  # Balanced repeated trials per query per physical table, randomized blocks, explicit warmup.
  records=[];k=0
  for rnd in range(-WARMUPS,REPS):
   sqlorder=list(QUERIES);random.Random(26000+rnd).shuffle(sqlorder)
   for qi in sqlorder:
    arms=list(ARMS);random.Random(260000+qi*100+rnd).shuffle(arms)
    for mat,u in arms:
     k+=1;qid=f"stage26_q{qi:02d}_m{mat}u{u}_r{rnd}_n{k}"
     sql=substitute(sqls[qi],mat)+f" SETTINGS use_statistics={u},max_threads=4,max_memory_usage=9000000000"
     t0=time.perf_counter()
     q(ct,sql,qid=qid,fmt="Null",timeout=320)
     records.append({"version":ver,"q":qi,"materialize":mat,"use_statistics":u,"round":rnd,"warmup":int(rnd<0),
                     "query_id":qid,"client_ms":round(1000*(time.perf_counter()-t0),3)})
  q(ct,"SYSTEM FLUSH LOGS")
  raw=q(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
          "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'stage26_') FORMAT JSONEachRow")
  (root/"query_log.jsonl").write_text(raw)
  lmap={x["query_id"]:x for line in raw.splitlines() if line.strip() for x in [json.loads(line)]}
  if len(lmap)!=len(records):raise RuntimeError(f"Missing query profile {len(lmap)} vs {len(records)}")
  for x in records:
   log=lmap[x["query_id"]];x.update({k:log.get(k) for k in ["query_duration_ms","read_rows","read_bytes","memory_usage"]})
   ev=log.get("ProfileEvents",{}) or {}
   for e in ["QueryPlanOptimizeMicroseconds","LoadedStatisticsMicroseconds","SelectedMarks","FileOpen"]:
    x[e]=ev.get(e,0)
  with (root/"all_runs.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
  conditions=[]
  for qi in QUERIES:
   for m,u in ARMS:
    vv=[x for x in records if x["q"]==qi and x["materialize"]==m and x["use_statistics"]==u and x["round"]>=0]
    z={"version":ver,"q":qi,"materialize":m,"use_statistics":u,"n":len(vv)}
    for e in ["query_duration_ms","read_rows","read_bytes","memory_usage",
              "QueryPlanOptimizeMicroseconds","LoadedStatisticsMicroseconds","SelectedMarks","FileOpen"]:
     z[e+"_median"]=statistics.median(x[e] for x in vv)
    conditions.append(z)
    print("STAGE26_RESULT "+json.dumps(z),flush=True)
  with (root/"condition_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(conditions[0]));w.writeheader();w.writerows(conditions)
  return {"version":ver,"source_nation":sample,"rows":counts,"ingest":ingest,"conditions":conditions,"answers":answers}
 finally:
  subprocess.run(["docker","rm","-f",ct],capture_output=True,timeout=80)
def main():
 sqls,defs=setup_source()
 reports=[]
 for ver in VERSIONS:
  print("START_VERSION "+ver,flush=True)
  run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=380)
  reports.append(main_for_version(ver,sqls,defs))
 for qi in QUERIES:
  if len({r["answers"][(qi,m,u)] for r in reports for m,u in ARMS})!=1:
   raise RuntimeError(f"cross-release query results differ for q{qi}")
 allrows=[x for r in reports for x in r["conditions"]]
 with (ROOT/"all_condition_medians.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(allrows[0]));w.writeheader();w.writerows(allrows)
 compact={"valid":True,"source":{"repository":"ClickHouse/tpc-h-openhouse","commit":PIN,
    "schema":"clickhouse-cloud/setup/init.sql","source":"sf10 Parquet on public-pme.s3.amazonaws.com",
    "derivation":"all dimension rows, orders and lineitem restricted to orderkey <= 3,000,000",
    "not_standard_tpch_benchmark":True},"row_counts":reports[0]["rows"],
    "versions":VERSIONS,"queries":QUERIES,"query_results_identical":True,
    "total_executions":len(VERSIONS)*len(QUERIES)*len(ARMS)*(REPS+WARMUPS),
    "measured":len(VERSIONS)*len(QUERIES)*len(ARMS)*REPS}
 (ROOT/"FINAL_STATUS.json").write_text(json.dumps(compact,indent=2)+"\n")
 print("STAGE26_FINAL "+json.dumps(compact),flush=True)
if __name__=="__main__":main()
