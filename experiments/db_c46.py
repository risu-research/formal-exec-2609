#!/usr/bin/env python3
"""C46: real sequential mixed-request replay, paired 26.7-inherited vs 26.8-rebuilt data.
All mix points, query identities, gates and physical checkpoints are pre-specified.
Sequential traces measure request latency/serial throughput, NOT concurrent QPS.
"""
import csv,hashlib,json,os,pathlib,random,statistics,subprocess,time,math
import db_c45 as C
import db_mixed_history_upgrade as P
import db_tpch_join_boundary as T

CAP=int(os.environ.get("C46_CAP","300000"))
if CAP not in (300000,1500000): raise RuntimeError("unregistered cap")
N=200 if CAP==300000 else 400
POINTS={"mo":(.25,.40),"yr":(.04,.10)} if CAP==300000 else {"mo":(.01,.025),"yr":(0,.01)}
ROUNDS=2
R=pathlib.Path("c46_"+str(CAP)).resolve();R.mkdir(parents=True,exist_ok=True)
S=P.S
P.R=R;S.ROOT=R;S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c46_ch";S.VOL="c46_volume"
T.ROOT=R
T.KEY_CAP=CAP
C.R=R
ARMS=(("prune",0,1,0),("both",1,1,0))
HIST=("i","f")
FAMILIES=(tuple(range(1,4)),tuple(range(4,7)),tuple(range(7,11)),tuple(range(11,14)))
JOIN=(3,5,10,12)
SRC="https://public-pme.s3.amazonaws.com/join_bench/tpc-h/sf10/"

def dump(p,v):
 path=R/p;path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(v,sort_keys=True,indent=2)+"\n")
def emit(*v):print("C46",*v,flush=True)
def sql(v,fmt=None):return P.sql(v,fmt=fmt)
def create_ssb(tab,cols,part):
 sql(f"CREATE TABLE ssb.{tab} ({cols}) ENGINE=MergeTree PARTITION BY {part} ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
def create_join(tab,defs):
 col,pk=defs[tab]
 sql(f"CREATE TABLE tpch.{tab}_i ({col}) ENGINE=MergeTree ORDER BY ({pk})")
def freeze(db,tables):
 for t in tables:sql(f"SYSTEM STOP MERGES {db}.{t}")
def dataset_snapshot(label):
 d={}
 for db,tabs in (("ssb",("mo_i","yr_i","mo_f","yr_f")),("tpch",tuple(t+"_"+h for t in T.TABLES for h in HIST))):
  for tab in tabs:
   inv=sql(f"SELECT partition,name,rows,marks,bytes_on_disk FROM system.parts WHERE active AND database='{db}' AND table='{tab}' ORDER BY partition,name FORMAT TSV")
   z=[line.split("\t") for line in inv.splitlines() if line]
   if not z:raise RuntimeError(f"empty parts {db}.{tab}")
   paths=sql(f"SELECT path FROM system.parts WHERE active AND database='{db}' AND table='{tab}' ORDER BY partition,name FORMAT TSV")
   script='while IFS= read -r p; do test -f "${p%/}/statistics.packed" && echo 1 || echo 0; done'
   proc=S.run(["docker","exec","-i",S.CT,"bash","-c",script],inp=paths.encode(),timeout=150)
   flags=proc.stdout.decode().splitlines()
   if len(flags)!=len(z):raise RuntimeError("stat inventory mismatch "+tab)
   d[f"{db}/{tab}"]={"parts":len(z),"rows":sum(int(x[2]) for x in z),
     "marks":sum(int(x[3]) for x in z),"stats_parts":sum(map(int,flags)),
     "part_identity_sha256":hashlib.sha256(inv.encode()).hexdigest()}
 dump(label+"/physical.json",d)
 return d
def old_snapshot(label):
 d={}
 for db,tabs in (("ssb",("mo_i","yr_i")),("tpch",tuple(t+"_i" for t in T.TABLES))):
  for tab in tabs:
   inv=sql(f"SELECT partition,name,rows,marks,bytes_on_disk FROM system.parts WHERE active AND database='{db}' AND table='{tab}' ORDER BY partition,name FORMAT TSV")
   d[f"{db}/{tab}"]=hashlib.sha256(inv.encode()).hexdigest()
 dump(label+"/inherited_identity.json",d)
 return d
def get_data():
 sq=S.base.pinned_sources()
 jq,defs=T.setup_source()
 native=S.base.make_data()
 dump("SOURCE_IDENTITY.json",{"ssb":"ClickBench pinned SF1","tpch_commit":T.PIN,"cap":CAP})
 return sq,jq,defs,native
def join_source(tab):
 suffix=T.SOURCE_PARQUET[tab]
 cond=f" WHERE o_orderkey <= {CAP}" if tab=="orders" else (f" WHERE l_orderkey <= {CAP}" if tab=="lineitem" else "")
 return f"SELECT {T.SELECTS[tab]} FROM s3('{SRC+suffix}',NOSIGN,'Parquet'){cond}"
def setup(sq,jq,defs,native):
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 S.start(S.OLD)
 sql("CREATE DATABASE IF NOT EXISTS ssb")
 sql("CREATE DATABASE IF NOT EXISTS tpch")
 cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
 P.create("raw",cols)
 for layout,part in (("mo","toYYYYMM(LO_ORDERDATE)"),("yr","toYear(LO_ORDERDATE)")):
  create_ssb(layout+"_i",cols,part)
 with native.open("rb") as f:
  p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],stdin=f,check=False,timeout=950)
 if p.returncode:raise RuntimeError("SSB native upload "+p.stderr.decode(errors="replace")[-1000:])
 native.unlink()
 for layout in ("mo","yr"):
  sql(f"INSERT INTO ssb.{layout}_i SELECT * FROM ssb.raw SETTINGS materialize_statistics_on_insert=0,max_threads=2,max_insert_threads=1,min_insert_block_size_rows=100000,min_insert_block_size_bytes=1")
  emit("OLD_SSB",layout)
 for t in T.TABLES:
  create_join(t,defs)
  stmt=f"INSERT INTO tpch.{t}_i {join_source(t)} SETTINGS materialize_statistics_on_insert=0,max_threads=4,max_insert_threads=2,max_memory_usage=9000000000"
  sql(stmt)
  emit("OLD_JOIN",t)
 if C.until_idle(("mo_i","yr_i"),"ssb",180)>180:raise RuntimeError("ssb idle")
 C.until_idle(tuple(t+"_i" for t in T.TABLES),"tpch",180)
 freeze("ssb",("mo_i","yr_i"))
 freeze("tpch",tuple(t+"_i" for t in T.TABLES))
 # No DDL changes to inherited parts after this checkpoint.
 old=old_snapshot("pre_upgrade")
 S.run(["docker","stop","-t","90",S.CT],timeout=150)
 S.run(["docker","rm",S.CT])
 S.start(S.NEW)
 freeze("ssb",("mo_i","yr_i"))
 freeze("tpch",tuple(t+"_i" for t in T.TABLES))
 after=old_snapshot("post_upgrade")
 if old!=after:raise RuntimeError("inherited physical part identity changed across upgrade")
 for layout,part in (("mo","toYYYYMM(LO_ORDERDATE)"),("yr","toYear(LO_ORDERDATE)")):
  create_ssb(layout+"_f",cols,part)
  sql(f"INSERT INTO ssb.{layout}_f SELECT * FROM ssb.raw SETTINGS materialize_statistics_on_insert=1,max_threads=2,max_insert_threads=1,min_insert_block_size_rows=100000,min_insert_block_size_bytes=1")
  emit("FRESH_SSB",layout)
 for t in T.TABLES:
  col,pk=defs[t]
  sql(f"CREATE TABLE tpch.{t}_f ({col}) ENGINE=MergeTree ORDER BY ({pk})")
  sql(f"INSERT INTO tpch.{t}_f SELECT * FROM tpch.{t}_i SETTINGS materialize_statistics_on_insert=1,max_threads=4,max_insert_threads=2,max_memory_usage=9000000000")
  emit("FRESH_JOIN",t)
 C.until_idle(("mo_f","yr_f"),"ssb",180)
 C.until_idle(tuple(t+"_f" for t in T.TABLES),"tpch",180)
 freeze("ssb",("mo_f","yr_f"))
 freeze("tpch",tuple(t+"_f" for t in T.TABLES))
 stable=dataset_snapshot("fixed")
 for layout in ("mo","yr"):
  if stable[f"ssb/{layout}_i"]["rows"]!=6001215 or stable[f"ssb/{layout}_f"]["rows"]!=6001215:raise RuntimeError("ssb count")
  if P.fp(layout+"_i")!=P.fp(layout+"_f"):raise RuntimeError("ssb fingerprint")
 expected={"nation":25,"region":5,"supplier":100000,"customer":1500000,
           "orders":75000 if CAP==300000 else 375000,
           "lineitem":299814 if CAP==300000 else 1499579}
 for t in T.TABLES:
  if stable[f"tpch/{t}_i"]["rows"]!=expected[t] or stable[f"tpch/{t}_f"]["rows"]!=expected[t]:raise RuntimeError("join population "+t)
 dump("ingest.json",{"version_old":S.OLD,"version_new":S.NEW,"physical":stable,"expected":expected})
 return stable

def statement(kind,q,layout,history,arm,sq,jq,comment=""):
 _,use,prune,cache=arm
 if kind=="ssb":
  stem=sq[q-1].replace("FROM lineorder_flat",f"FROM {layout}_{history}")
  threads=2
 else:
  import re
  stem=re.sub(r"\b("+"|".join(T.TABLES)+r")\b",lambda m:"tpch."+m.group(1)+"_"+history,jq[q])
  threads=4
 settings=f" SETTINGS use_statistics={use},use_statistics_for_part_pruning={prune},use_statistics_cache={cache},max_threads={threads},max_memory_usage=9000000000,use_query_condition_cache=0"
 if comment:settings+=",log_comment='"+comment+"'"
 return stem+settings

def verify(sq,jq):
 ans={}
 for layout in ("mo","yr"):
  for typ,qids in (("ssb",range(1,14)),("join",JOIN)):
   for q in qids:
    found=set()
    for hist in HIST:
     for arm in ARMS:
      stmt=statement(typ,q,layout,hist,arm,sq,jq)
      reply=sql(stmt+" FORMAT TSV")
      digest=hashlib.sha256(reply.encode()).hexdigest()
      ans[f"{layout}/{typ}/{q}/{hist}/{arm[0]}"]=digest
      found.add(digest)
    if len(found)!=1:raise RuntimeError("nonidentical answer "+str((layout,typ,q)))
 dump("answers.json",ans)
 emit("ANSWER_EQUALITY",len(ans))

def workload(layout,share,seed):
 nj=round(N*share);ns=N-nj
 ss=[]
 fcounts=[ns//4+(1 if i<ns%4 else 0) for i in range(4)]
 for qs,n in zip(FAMILIES,fcounts):
  ss += [("ssb",qs[i%len(qs)]) for i in range(n)]
 js=[("join",JOIN[i%4]) for i in range(nj)]
 tasks=ss+js
 rng=random.Random(seed);rng.shuffle(tasks)
 assert len(tasks)==N
 return tasks

def warmup(sq,jq):
 for typ,qs in (("ssb",range(1,14)),("join",JOIN)):
  for h in HIST:
   for arm in ARMS:
    for q in qs:
     stmt=statement(typ,q,"mo",h,arm,sq,jq)
     sql(stmt,fmt="Null")
 emit("WARMED")

def replay(sq,jq):
 meta=[];execs=[];seq=0
 for layout in ("mo","yr"):
  for p_index,p in enumerate(POINTS[layout]):
   scenario=f"{layout}_{p_index}"
   tasks=workload(layout,p,460000+CAP+p_index+(0 if layout=="mo" else 100))
   entries=[{"kind":typ,"sql":q} for typ,q in tasks]
   dump("traces/"+scenario+".json",{"layout":layout,"target_join_share":p,
         "realized_join_share":sum(t[0]=="join" for t in tasks)/N,
         "q5_count":sum(t==("join",5) for t in tasks),"tasks":entries})
   for rnd in range(ROUNDS):
    combos=[(h,a) for h in HIST for a in ARMS]
    random.Random(46000+CAP+rnd+100*p_index+(0 if layout=="mo" else 999)).shuffle(combos)
    for hist,arm in combos:
     start_seq=seq
     statements=[]
     for i,(typ,q) in enumerate(tasks):
      tag=f"C46_{CAP}_{scenario}_{rnd}_{hist}_{arm[0]}_{i}"
      statements.append(statement(typ,q,layout,hist,arm,sq,jq,tag)+";")
      meta.append({"tag":tag,"scenario":scenario,"layout":layout,"join_share":p,
       "round":rnd,"history":hist,"arm":arm[0],"index":i,"workload":typ,"q":q})
      seq+=1
     # One client session replays actual sequential requests; each receives
     # its own log_comment for lossless receipt matching.
     t0=time.monotonic()
     pipe="\n".join(statements)+"\n"
     proc=S.run(["docker","exec","-i",S.CT,"clickhouse-client","--database","ssb",
         "--multiquery","--format","Null"],inp=pipe.encode(),timeout=1400,check=False)
     wall_ms=(time.monotonic()-t0)*1000
     if proc.returncode:raise RuntimeError("replay failed "+scenario+" "+hist+" "+arm[0]+" "+proc.stderr.decode(errors="replace")[-1600:])
     execs.append({"scenario":scenario,"round":rnd,"history":hist,"arm":arm[0],
         "requests":N,"wall_ms":round(wall_ms,3),"start_index":start_seq})
     emit("REPLAY",scenario,rnd,hist,arm[0],round(wall_ms/1000,1))
 dump("stream_wall.json",execs)
 sql("SYSTEM FLUSH LOGS")
 raw=sql("SELECT log_comment,query_duration_ms,read_rows,memory_usage,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(log_comment,'C46_') FORMAT JSONEachRow")
 found={}
 for line in raw.splitlines():
  if not line:continue
  v=json.loads(line);tag=v["log_comment"]
  if tag in found:raise RuntimeError("duplicate receipt "+tag)
  found[tag]=v
 if len(found)!=len(meta):raise RuntimeError(f"missing receipts {len(found)}/{len(meta)}")
 for row in meta:
  v=found[row["tag"]];pe=v.get("ProfileEvents") or {}
  row.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],peak_memory=v.get("memory_usage",0),
             stat_us=pe.get("LoadedStatisticsMicroseconds",0),
             opt_us=pe.get("QueryPlanOptimizeMicroseconds",0),
             selected_marks=pe.get("SelectedMarks",0))
 with (R/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(meta[0]));w.writeheader();w.writerows(meta)
 return meta,execs

def pctl(vals,p):
 x=sorted(vals);pos=(len(x)-1)*p;low=int(pos);hi=min(len(x)-1,low+1)
 return x[low]+(x[hi]-x[low])*(pos-low)

def gates(records,walls):
 items=[]
 cells={}
 for r in records:cells.setdefault((r["scenario"],r["history"],r["arm"]),[]).append(r)
 for (sc,h,a),rr in cells.items():
  w=[x["wall_ms"] for x in walls if (x["scenario"],x["history"],x["arm"])==(sc,h,a)]
  m={"scenario":sc,"history":h,"arm":a,"n":len(rr),
     "join_share":rr[0]["join_share"],"sql_sum_ms":sum(x["ms"] for x in rr),
     "mean_ms":statistics.mean(x["ms"] for x in rr),
     "p95_ms":pctl([x["ms"] for x in rr],.95),
     "p99_ms":pctl([x["ms"] for x in rr],.99),
     "wall_sum_ms":sum(w),"serial_qps":len(rr)/(sum(w)/1000),
     "q5_invocations":sum(x["workload"]=="join" and x["q"]==5 for x in rr),
     "stat_load_ms":sum(x["stat_us"] for x in rr)/1000}
  items.append(m)
 ix={(v["scenario"],v["history"],v["arm"]):v for v in items}
 comparisons=[]
 for sc in sorted(set(x["scenario"] for x in items)):
  for axis,first,second in (("optimizer_inherited",("i","prune"),("i","both")),
            ("optimizer_fresh",("f","prune"),("f","both")),
            ("deployment_prune",("i","prune"),("f","prune")),
            ("deployment_both",("i","both"),("f","both"))):
   old=ix[(sc,*first)];new=ix[(sc,*second)]
   from_rr=cells[(sc,*first)];to_rr=cells[(sc,*second)]
   per={}
   for typ in ("ssb","join"):
    for q in (range(1,14) if typ=="ssb" else JOIN):
     a=[r["ms"] for r in from_rr if (r["workload"],r["q"])==(typ,q)]
     b=[r["ms"] for r in to_rr if (r["workload"],r["q"])==(typ,q)]
     if a and b:per[f"{typ}{q}"]={"n":len(a),"ratio":statistics.median(b)/max(.01,statistics.median(a))}
   count=sum(v["ratio"]>=1.5 for v in per.values() if v["n"]>=2)
   ratio=new["mean_ms"]/max(.01,old["mean_ms"])
   comparisons.append({"scenario":sc,"comparison":axis,"from":first,"to":second,
      "mean_ratio":ratio,"wall_ratio":new["wall_sum_ms"]/old["wall_sum_ms"],
      "p95_ratio":new["p95_ms"]/max(.01,old["p95_ms"]),
      "large_regressions":count,
      "gate":"BLOCK" if ratio>1.10 or count>=2 else "APPROVE",
      "per_query":per,
      "scope":"within-run observed sequential trace; SQL with <2 calls excluded from single-query guardrail"})
 dump("stream_metrics.json",items);dump("decision_matrix.json",comparisons)
 for v in comparisons:emit("GATE",json.dumps({k:v[k] for k in ("scenario","comparison","mean_ratio","large_regressions","gate")}))
 return comparisons

def main():
 for v in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=500)
 sq,jq,defs,native=get_data()
 try:
  fixed=setup(sq,jq,defs,native)
  verify(sq,jq)
  warmup(sq,jq)
  records,walls=replay(sq,jq)
  gates(records,walls)
  after=dataset_snapshot("verified")
  if after!=fixed:raise RuntimeError("physical state changed during replay")
  dump("FINAL_STATUS.json",{"valid":True,"cap":CAP,"N_per_stream":N,
      "rounds":ROUNDS,"total_receipts":len(records),"engine":S.NEW,
      "histories":{"i":"26.7-built parts carried unchanged into 26.8",
                   "f":"same final logical data rebuilt on 26.8"},
      "sequential_client":True,"same_trace_all_arms":True,
      "mix_points":POINTS,"measurement":"ClickHouse query_log elapsed per invocation and client observed serial wall",
      "limitations":["Not concurrent load, no queuing tail latency or production QPS",
      "ClickHouse statistics and physical layout jointly depend on deployment history",
      "Query frequencies prespecified from Stage45, not measured production traffic",
      "Low JOIN shares use sparse Q5 samples; individual SQL guardrail requires 2+ requests",
      "Two repeats on a single runner, independent runner needed for generalization",
      "TPC-H restricted key-prefix population is not standard TPC-H score"]})
  emit("SUCCESS",CAP,len(records))
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=120)
if __name__=="__main__": main()
