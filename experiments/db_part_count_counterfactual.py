#!/usr/bin/env python3
"""Stage 27: within-release SSB SF1 part-count counterfactual.
Identical native input, schema, statistics policy, sorting key and query texts.
Only requested insert block size differs; report OBSERVED active parts, never
substitute requested for observed. use_statistics=0 is a negative control.
"""
import csv, hashlib, json, math, pathlib, random, statistics, time
import db_sf1_probe as base

VERSION = "26.8.1.2041"
ROOT = pathlib.Path("s27").resolve()
base.ROOT = ROOT
base.VERSIONS = [VERSION, VERSION]  # extract generator from the already-pulled image
QIDS = (2, 3, 4)                 # official SSB Q1.2 Q1.3 Q2.1
TARGETS = (8, 48, 192)
REPS = 8
WARM = 2
EVENTS = ("QueryPlanOptimizeMicroseconds", "LoadedStatisticsMicroseconds",
          "SelectedMarks", "FileOpen", "UserTimeMicroseconds")
FP = ("SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
      "sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)),"
      "min(LO_ORDERDATE),max(LO_ORDERDATE) FROM ssb.{} FORMAT TSV")
PARTS = ("SELECT count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM "
         "system.parts WHERE active AND database='ssb' AND table='{}' FORMAT TSV")
def save(name,content):
 p=ROOT/name
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(content)
def jwrite(name,content):save(name,json.dumps(content,sort_keys=True,indent=2)+"\n")
def query(ct,s,qid=None,fmt=None,timeout=120):
 p=base.client(ct,s,qid=qid,fmt=fmt,check=False,timeout=timeout)
 if p.returncode:raise RuntimeError("SQL "+s[:120]+" FAILED "+p.stderr.decode(errors="replace")[-1700:])
 return p.stdout.decode(errors="replace")
def median(a):return statistics.median(a)
def boot():
 ct="s27_clickhouse"
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144",
          "clickhouse/clickhouse-server:"+VERSION],timeout=110)
 for _ in range(90):
  if not base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=9).returncode:break
  time.sleep(1)
 else:raise RuntimeError("server startup failed")
 base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 return ct
def run():
 ROOT.mkdir(parents=True,exist_ok=True)
 print("STAGE27_START release="+VERSION+" targets="+str(TARGETS),flush=True)
 base.run(["docker","pull","clickhouse/clickhouse-server:"+VERSION],timeout=460)
 sqls=base.pinned_sources()
 native=base.make_data()
 src=json.loads((ROOT/"SOURCE_IDENTITY.json").read_text())
 print("STAGE27_NATIVE_READY "+json.dumps(src),flush=True)
 ct=boot()
 try:
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  ddl=lambda table:("CREATE TABLE ssb."+table+" ("+cols+
          ") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
  query(ct,ddl("raw_source"))
  query(ct,"SYSTEM STOP MERGES ssb.raw_source")
  with open(native,"rb") as f:
   p=base.run(["docker","exec","-i",ct,"clickhouse-client","--query",
        "INSERT INTO ssb.raw_source SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
        stdin=f,check=False,timeout=700)
   if p.returncode:raise RuntimeError("source ingest: "+p.stderr.decode(errors="replace")[-1400:])
  native.unlink()  # free disk
  source_fp=query(ct,FP.format("raw_source")).strip()
  print("STAGE27_SOURCE_LOADED "+source_fp,flush=True)
  if not source_fp.startswith("6001215\t"):raise RuntimeError("Unexpected SSB SF1 rows "+source_fp)
  save("source_fingerprint.tsv",source_fp+"\n")
  save("version_settings.tsv",query(ct,"SELECT name,value FROM system.settings WHERE name IN "
         "('materialize_statistics_on_insert','use_statistics','min_insert_block_size_rows',"
         "'min_insert_block_size_bytes','max_threads') ORDER BY name FORMAT TSV"))
  measured_parts={}
  answers={}
  for target in TARGETS:
   table="pc_"+str(target)
   query(ct,ddl(table))
   query(ct,"SYSTEM STOP MERGES ssb."+table)
   # One scan per target. Squashing controls physical insert-block sizes.
   block=math.ceil(6001215/target)
   t=time.monotonic()
   query(ct,("INSERT INTO ssb."+table+" SELECT * FROM ssb.raw_source SETTINGS "
       "materialize_statistics_on_insert=1, max_threads=1, max_insert_threads=1,"
       "min_insert_block_size_rows="+str(block)+",min_insert_block_size_bytes=1,"
       "max_memory_usage=9000000000"),timeout=900)
   ingest_s=round(time.monotonic()-t,2)
   fp=query(ct,FP.format(table)).strip()
   if fp!=source_fp:raise RuntimeError("Not equal logical data: "+table+" "+fp)
   stat=query(ct,PARTS.format(table)).strip()
   n,rows,disk,marks=[int(x) for x in stat.split("\t")]
   if rows!=6001215:raise RuntimeError("Part rows mismatch "+table+" "+stat)
   measured_parts[target]=n
   audit={"target":target,"actual_parts":n,"rows":rows,"bytes_on_disk":disk,
          "marks":marks,"insert_block_rows":block,"insert_seconds":ingest_s,
          "fingerprint":fp}
   jwrite(table+"/physical.json",audit)
   print("STAGE27_PARTS "+json.dumps(audit),flush=True)
   # Verify identical answers across all physical arms, with stats on/off.
   for qi in QIDS:
    sql=sqls[qi-1].replace("FROM lineorder_flat","FROM "+table)
    if sql==sqls[qi-1]:raise RuntimeError("Failed to rewrite official SSB SQL")
    for use in (0,1):
     setting=" SETTINGS use_statistics="+str(use)+",optimize_move_to_prewhere=1,max_threads=2"
     ans=query(ct,sql+setting+" FORMAT TSV",timeout=110)
     answers[(target,qi,use)]=hashlib.sha256(ans.encode()).hexdigest()
     for kind,prefix in (("plan","EXPLAIN PLAN "),("indexes","EXPLAIN indexes=1 ")):
      save(f"{table}/q{qi:02d}_u{use}_{kind}.txt",query(ct,prefix+sql+setting))
  for qi in QIDS:
   if len({answers[(t,qi,u)] for t in TARGETS for u in (0,1)})!=1:
    raise RuntimeError("Wrong answers differ q"+str(qi))
  jwrite("answer_hashes.json",{f"p{p}_q{q}_u{u}":v for (p,q,u),v in answers.items()})
  if max(measured_parts.values())<3*min(measured_parts.values()):
   print("STAGE27_CAUTION part separation below 3x: "+str(measured_parts),flush=True)
  # Balanced order: randomize 18 cells each round, share warmup designation.
  records=[]
  for r in range(-WARM,REPS):
   cells=[(p,q,u) for p in TARGETS for q in QIDS for u in (0,1)]
   random.Random(270000+r).shuffle(cells)
   for i,(p,qi,u) in enumerate(cells):
    qid=f"s27_p{p}_q{qi:02d}_u{u}_r{r}_i{i}"
    sql=sqls[qi-1].replace("FROM lineorder_flat","FROM pc_"+str(p))
    query(ct,sql+" SETTINGS use_statistics="+str(u)+
          ",optimize_move_to_prewhere=1,max_threads=2,max_memory_usage=9000000000",
          qid=qid,fmt="Null",timeout=110)
    records.append({"target":p,"parts":measured_parts[p],"q":qi,"stats":u,
                    "round":r,"warmup":int(r<0),"query_id":qid})
  query(ct,"SYSTEM FLUSH LOGS")
  out=query(ct,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
      "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s27_') FORMAT JSONEachRow")
  save("query_log.jsonl",out)
  lmap={x["query_id"]:x for line in out.splitlines() if line.strip() for x in [json.loads(line)]}
  if len(lmap)!=len(records):raise RuntimeError(f"Missing receipts {len(lmap)} vs {len(records)}")
  for rec in records:
   log=lmap[rec["query_id"]]
   rec.update({key:log[key] for key in ("query_duration_ms","read_rows","read_bytes","memory_usage")})
   ev=log.get("ProfileEvents",{}) or {}
   rec.update({key:ev.get(key,0) for key in EVENTS})
  with (ROOT/"all_receipts.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
  med=[]
  for p in TARGETS:
   for qi in QIDS:
    for use in (0,1):
     rs=[r for r in records if r["target"]==p and r["q"]==qi and r["stats"]==use and r["round"]>=0]
     z={"target":p,"actual_parts":measured_parts[p],"q":qi,"use_statistics":use,"n":len(rs)}
     for col in ("query_duration_ms","QueryPlanOptimizeMicroseconds",
                "LoadedStatisticsMicroseconds","read_rows","SelectedMarks","FileOpen"):
      z[col+"_median"]=median([r[col] for r in rs])
     med.append(z)
     print("STAGE27_CONDITION "+json.dumps(z),flush=True)
  with (ROOT/"condition_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
  effects=[]
  for p in TARGETS:
   for qi in QIDS:
    on=next(x for x in med if x["target"]==p and x["q"]==qi and x["use_statistics"]==1)
    off=next(x for x in med if x["target"]==p and x["q"]==qi and x["use_statistics"]==0)
    effects.append({"target":p,"actual_parts":measured_parts[p],"q":qi,
       "extra_wall_ms":on["query_duration_ms_median"]-off["query_duration_ms_median"],
       "extra_plan_ms":(on["QueryPlanOptimizeMicroseconds_median"]-
                        off["QueryPlanOptimizeMicroseconds_median"])/1000,
       "stats_load_ms":on["LoadedStatisticsMicroseconds_median"]/1000,
       "stats_disabled_load_ms":off["LoadedStatisticsMicroseconds_median"]/1000,
       "on_read_rows":on["read_rows_median"],"off_read_rows":off["read_rows_median"],
       "on_marks":on["SelectedMarks_median"],"off_marks":off["SelectedMarks_median"]})
  with (ROOT/"part_count_effects.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(effects[0]));w.writeheader();w.writerows(effects)
  part_ratio=max(measured_parts.values())/min(measured_parts.values())
  result={"valid":True,"kind":"single-release within-input part-count counterfactual",
    "version":VERSION,"sf":1,"dataset_rows":6001215,"source":src,
    "source_fingerprint":source_fp,"actual_parts":measured_parts,
    "part_count_separation_ratio":part_ratio,
    "part_count_intervention_valid":part_ratio>=3,
    "all_query_answers_identical":True,
    "warmups":len(TARGETS)*len(QIDS)*2*WARM,
    "measured_runs":len(TARGETS)*len(QIDS)*2*REPS,
    "negative_control":"use_statistics=0",
    "limitation":"No cross-release attribution; different numbers of parts can alter local statistics and read path"}
  jwrite("FINAL_STATUS.json",result)
  for z in effects:print("STAGE27_EFFECT "+json.dumps(z),flush=True)
  print("STAGE27_FINAL "+json.dumps(result),flush=True)
 finally:
  base.run(["docker","rm","-f",ct],check=False,timeout=50)
if __name__=="__main__":run()
