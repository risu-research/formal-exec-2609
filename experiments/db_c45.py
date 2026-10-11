#!/usr/bin/env python3
"""c45: joint same-process calibration of SSB and pinned, key-capped TPC-H joins.

Prespecified populations: SSB SF1 monthly/yearly and TPC-H source with
orderkey <= C45_CAP. No workloads or queries selected by observed speed.
Each job runs BOTH workload families inside the SAME ClickHouse server/runner.
"""
import csv,hashlib,json,os,pathlib,random,statistics,subprocess,time
import db_mixed_history_upgrade as P
import db_tpch_join_boundary as T

CAP=int(os.environ.get("C45_CAP","300000"))
if CAP not in (300000,1500000):raise RuntimeError("Unexpected population cap")
R=pathlib.Path("c45_"+str(CAP)).resolve();R.mkdir(exist_ok=True)
P.R=R
P.T=("mo","yr")
S=P.S
S.ROOT=R;S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c45_ch";S.VOL="c45_volume"
T.ROOT=R
T.KEY_CAP=CAP
SSB=tuple(range(1,14))
JOIN=(3,5,10,12)
ARMS=(("prune",0,1,0),("both",1,1,0),("cached",1,1,1),("neither",0,0,0))
ROUNDS=8
WARMUP=2
FAMILY=(range(1,4),range(4,7),range(7,11),range(11,14))
MIX=(0,.01,.02,.05,.1,.2,.3,.4,.5,.6,.75,.9,1)
VER=S.NEW

def dump(path,obj):
 p=R/path;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")
def ssb(s,qid=None,fmt=None):return P.sql(s,qid=qid,fmt=fmt)
def join(s,qid=None,fmt=None,timeout=500):return T.q(S.CT,s,qid=qid,fmt=fmt,timeout=timeout)
def qs_query(q,t,arm):
 _,use,p,c=arm
 return q.replace("FROM lineorder_flat","FROM "+t)+(
  f" SETTINGS use_statistics={use},use_statistics_for_part_pruning={p},"
  f"use_statistics_cache={c},max_threads=2,use_query_condition_cache=0")
def j_query(q,arm):
 _,use,p,c=arm
 return T.substitute(q,1)+(
  f" SETTINGS use_statistics={use},use_statistics_for_part_pruning={p},"
  f"use_statistics_cache={c},max_threads=4,max_memory_usage=9000000000,"
  "use_query_condition_cache=0")

def until_idle(tables,database="ssb",limit=150):
 start=time.monotonic();silent=0
 names=",".join("'"+t+"'" for t in tables)
 while time.monotonic()-start<limit:
  x=int(ssb("SELECT count() FROM system.merges WHERE database='"+database+
       "' AND table IN ("+names+") FORMAT TSV").strip())
  silent=silent+1 if x==0 else 0
  if silent>=3:return round(time.monotonic()-start,1)
  time.sleep(2)
 raise RuntimeError("Merges did not quiesce for "+database)

def snap_join():
 d={}
 for t in T.TABLES:
  tab=t+"_m1"
  a=ssb("SELECT name,rows,marks,path FROM system.parts WHERE active AND database='tpch' "
    "AND table='"+tab+"' ORDER BY name FORMAT TSV")
  x=[line.split("\t") for line in a.splitlines() if line]
  if not x:raise RuntimeError("No parts "+tab)
  paths="\n".join(v[-1] for v in x)+"\n"
  bash='while IFS= read -r f; do test -f "$f/statistics.packed" && echo 1 || echo 0; done'
  proc=S.run(["docker","exec","-i",S.CT,"bash","-c",bash],inp=paths.encode(),timeout=90)
  flags=[int(z) for z in proc.stdout.decode().splitlines()]
  if len(flags)!=len(x):raise RuntimeError("Stats inventory mismatch "+tab)
  d[tab]={"parts":len(x),"rows":sum(int(y[1]) for y in x),
     "marks":sum(int(y[2]) for y in x),"stat_parts":sum(flags),
     "inventory_sha256":hashlib.sha256(a.encode()).hexdigest()}
 return d

def preflight_sources():
 a=S.base.pinned_sources()
 b,defs=T.setup_source()
 return a,b,defs

def import_data(ssb_q,jq,defs):
 native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 S.start(VER)
 S.run(["docker","exec",S.CT,"clickhouse-client","--query",
   "CREATE DATABASE IF NOT EXISTS ssb; CREATE DATABASE IF NOT EXISTS tpch;"],check=False)
 # Run two separate client calls to avoid multiquery behavior dependencies.
 S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS tpch"])
 cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
 P.create("raw",cols)
 with native.open("rb") as f:
  ins=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
      "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
      stdin=f,check=False,timeout=950)
 if ins.returncode:raise RuntimeError("Native import "+ins.stderr.decode(errors="replace")[-1600:])
 native.unlink()
 ingress=[]
 for tab,partition in (("mo","toYYYYMM(LO_ORDERDATE)"),("yr","toYear(LO_ORDERDATE)")):
  ssb("CREATE TABLE ssb."+tab+" ("+cols+") ENGINE=MergeTree "
   "PARTITION BY "+partition+" ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
  before=time.monotonic()
  ssb("INSERT INTO ssb."+tab+" SELECT * FROM ssb.raw SETTINGS "
      "materialize_statistics_on_insert=1,max_threads=2,max_insert_threads=1,"
      "min_insert_block_size_rows=100000,min_insert_block_size_bytes=1")
  ingress.append({"dataset":"ssb","table":tab,"rows":6001215,
                  "seconds":round(time.monotonic()-before,2)})
  print("C45_LOADED",json.dumps(ingress[-1]),flush=True)
 source_url="https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/"
 nation=join("SELECT count() FROM s3('"+source_url+"nation_0.parquet',NOSIGN,'Parquet') FORMAT TSV",timeout=120).strip()
 if nation!="25":raise RuntimeError("TPC-H upstream source unreachable")
 for t in T.TABLES:
  columns,pk=defs[t]
  join("CREATE TABLE tpch."+t+"_m1 ("+columns+") ENGINE=MergeTree ORDER BY ("+pk+")")
  suffix=T.SOURCE_PARQUET[t]
  filt=(" WHERE o_orderkey <= "+str(CAP)) if t=="orders" else (
        " WHERE l_orderkey <= "+str(CAP)) if t=="lineitem" else ""
  start=time.monotonic()
  insert=("INSERT INTO tpch."+t+"_m1 SELECT "+T.SELECTS[t]+
      " FROM s3('"+source_url+suffix+"',NOSIGN,'Parquet')"+filt+
      " SETTINGS materialize_statistics_on_insert=1,max_threads=4,"
      "max_insert_threads=2,max_memory_usage=9000000000")
  join(insert,timeout=1200)
  n=int(join("SELECT count() FROM tpch."+t+"_m1 FORMAT TSV").strip())
  ingress.append({"dataset":"join","table":t,"rows":n,
                  "seconds":round(time.monotonic()-start,2)})
  dump("ingest.json",ingress)
  print("C45_LOADED",json.dumps(ingress[-1]),flush=True)
 t0=until_idle(("mo","yr"),"ssb")
 t1=until_idle(tuple(t+"_m1" for t in T.TABLES),"tpch")
 for t in ("mo","yr"):ssb("SYSTEM STOP MERGES ssb."+t)
 for t in T.TABLES:join("SYSTEM STOP MERGES tpch."+t+"_m1")
 pa=P.snap("fixed_ssb")
 pb=snap_join()
 dump("fixed_join.json",pb)
 if any(pa[t]["rows"]!=6001215 for t in ("mo","yr")):raise RuntimeError("SSB count disagreement")
 if pa["mo"]["fingerprint"]!=pa["yr"]["fingerprint"]:raise RuntimeError("SSB logical mismatch")
 expected={"nation":25,"region":5,"supplier":100000,"customer":1500000,
  "orders":75000 if CAP==300000 else 375000,
  "lineitem":299814 if CAP==300000 else 1499579}
 for tab,count in expected.items():
  if pb[tab+"_m1"]["rows"]!=count:
   raise RuntimeError(f"TPC-H upstream population changed for {tab}: {pb[tab+'_m1']['rows']} vs {count}")
 dump("acquisition.json",{"ingest":ingress,"idle":{ "ssb":t0,"join":t1},
     "source_nation":nation,"ssb":pa,"join":pb})
 return pa,pb

def verify_answers(sq,jq):
 hashed={}
 for q in SSB:
  expected=None
  for table in ("mo","yr"):
   for arm in ARMS:
    out=ssb(qs_query(sq[q-1],table,arm)+" FORMAT TSV")
    dh=hashlib.sha256(out.encode()).hexdigest()
    if expected is not None and expected!=dh:
     raise RuntimeError(f"SSB answer not equal q{q}, {table}, {arm[0]}")
    expected=dh
    hashed[f"S{q:02d}/{table}/{arm[0]}"]=dh
 for q in JOIN:
  expected=None
  for arm in ARMS:
   out=join(j_query(jq[q],arm)+" FORMAT TSV",timeout=500)
   dh=hashlib.sha256(out.encode()).hexdigest()
   if expected is not None and expected!=dh:
    raise RuntimeError(f"JOIN answer not equal q{q}, {arm[0]}")
   expected=dh
   hashed[f"J{q:02d}/{arm[0]}"]=dh
 dump("answers.json",hashed)

def run_queries(sq,jq):
 preplanned=[("ssb",t,q) for t in ("mo","yr") for q in SSB]+[("join","m1",q) for q in JOIN]
 records=[]
 for round_id in range(-WARMUP,ROUNDS):
  rng=random.Random(450000+round_id)
  tasks=preplanned[:]
  rng.shuffle(tasks)
  for typ,table,q in tasks:
   cell=ARMS[:]
   rng.shuffle(cell)
   for arm in cell:
    qid=f"c45_{len(records)}"
    if typ=="ssb":
     ssb(qs_query(sq[q-1],table,arm),qid=qid,fmt="Null")
    else:
     join(j_query(jq[q],arm),qid=qid,fmt="Null",timeout=500)
    records.append({"round":round_id,"workload":typ,"table":table,
                    "sql":q,"arm":arm[0],"qid":qid,"sequence":len(records)})
  print("C45_ROUND",round_id,len(records),flush=True)
 ssb("SYSTEM FLUSH LOGS")
 rows=ssb("SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
   "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c45_') FORMAT JSONEachRow")
 (R/"query_log.jsonl").write_text(rows)
 ix={q["query_id"]:q for line in rows.splitlines() if line for q in [json.loads(line)]}
 if len(ix)!=len(records):raise RuntimeError("Missing receipts "+str(len(ix))+"/"+str(len(records)))
 for item in records:
  v=ix[item["qid"]];ev=v.get("ProfileEvents") or {}
  item.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],memory=v.get("memory_usage",0),
    stat_us=ev.get("LoadedStatisticsMicroseconds",0),
    opt_us=ev.get("QueryPlanOptimizeMicroseconds",0),
    selected_marks=ev.get("SelectedMarks",0))
 with (R/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
 return records

def evaluate(records):
 filtered=[r for r in records if r["round"]>=0]
 cells={}
 for typ,table,q in [("ssb",t,q) for t in ("mo","yr") for q in SSB]+[("join","m1",q) for q in JOIN]:
  for arm in (a[0] for a in ARMS):
   chosen=[r for r in filtered if (r["workload"],r["table"],r["sql"],r["arm"])==(typ,table,q,arm)]
   if len(chosen)!=ROUNDS:raise RuntimeError("Lost measurement cell")
   cells[f"{typ}/{table}/{q}/{arm}"]={k:statistics.median(v[k] for v in chosen)
        for k in ("ms","read_rows","memory","stat_us","opt_us","selected_marks")}
 dump("medians.json",cells)
 def s_mean(table,arm):
  return statistics.mean(statistics.mean(cells[f"ssb/{table}/{q}/{arm}"]["ms"] for q in fam)
                           for fam in FAMILY)
 def j_mean(arm):
  return statistics.mean(cells[f"join/m1/{q}/{arm}"]["ms"] for q in JOIN)
 summary={}
 for table in ("mo","yr"):
  sm={arm:s_mean(table,arm) for arm in ("prune","both","cached","neither")}
  jm={arm:j_mean(arm) for arm in ("prune","both","cached","neither")}
  combinations={}
  for mode in ("both","cached"):
   ds=sm[mode]-sm["prune"];dj=jm[mode]-jm["prune"]
   threshold=ds/(ds-dj) if ds>0 and dj<0 else None
   mixs=[{"join_share":p,
           "prune_ms":(1-p)*sm["prune"]+p*jm["prune"],
           "enabled_ms":(1-p)*sm[mode]+p*jm[mode],
           "delta_ms":(1-p)*ds+p*dj} for p in MIX]
   combinations[mode]={"delta_ssb_ms":ds,"delta_join_ms":dj,
        "crossover":threshold,
        "status":"CROSSOVER" if threshold is not None and 0<threshold<1 else "NO_INTERNAL_CROSSOVER",
        "grid":mixs}
  summary[table]={"ssb_ms":sm,"join_ms":jm,"comparisons":combinations}
 dump("mixture_summary.json",summary)
 print("C45_BOUNDARY",json.dumps({t:{m:summary[t]["comparisons"][m]["crossover"] for m in ("both","cached")} for t in summary}),flush=True)
 return summary

def main():
 for v in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=500)
 sq,jq,defs=preflight_sources()
 try:
  before_a,before_b=import_data(sq,jq,defs)
  verify_answers(sq,jq)
  plans={}
  for q in (1,3,10,13):
   for t in ("mo","yr"):
    for arm in ARMS[:3]:
     plans[f"S{q}_{t}_{arm[0]}"]=ssb("EXPLAIN indexes=1 "+qs_query(sq[q-1],t,arm))
  for q in JOIN:
   for arm in ARMS[:3]:
    plans[f"J{q}_{arm[0]}"]=join("EXPLAIN PLAN "+j_query(jq[q],arm),timeout=180)
  dump("plans.json",plans)
  records=run_queries(sq,jq)
  summary=evaluate(records)
  after_a=P.snap("verified_ssb");after_b=snap_join()
  dump("verified_join.json",after_b)
  if before_a!=after_a or before_b!=after_b:
   raise RuntimeError("Physical state changed during benchmark")
  dump("FINAL_STATUS.json",{"valid":True,"version":VER,"source":{"ssb":"pinned ClickBench SF1",
    "tpch_commit":T.PIN,"tpch_orderkey_cap":CAP,"tpch_standard_score":False},
    "rows":6001215,"queries":{"ssb":13,"join":list(JOIN)},"arms":[a[0] for a in ARMS],
    "rounds":ROUNDS,"warmups":WARMUP,"receipts":len(records),
    "runner_mode":"same ClickHouse Docker container and same GitHub runner for both workloads",
    "mixture_rule":"equal SSB family weight and equal JOIN query weight, then linear mix by invocation share",
    "pre_registered_grid":MIX,"mix":summary,
    "limits":["TPC-H fact source is key-capped, not standard SF10",
      "One runner per cap; across-cap hosts can differ",
      "Frequency grid is scenario analysis, not measured customer behavior",
      "Query interleaving balances order but does not eliminate shared-cache interference",
      "Mean per-invocation latency is not a throughput benchmark",
      "No write/merge resource use included in the read-only mixture"]})
  print("C45_SUCCESS",CAP,flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=120)
if __name__=="__main__":main()
