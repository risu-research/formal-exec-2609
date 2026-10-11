#!/usr/bin/env python3
"""Stage30. True ClickHouse in-place replay: 26.7 -> 26.7 sham -> 26.8.
Named Docker volume is mounted at /var/lib/clickhouse across image swaps.
Four existing MergeTree tables persist unchanged, no export/reinsert during
transition. All gate thresholds are copied exactly from Stage25.
"""
import csv,hashlib,json,pathlib,random,statistics,subprocess,time
from collections import defaultdict
import db_sf1_probe as base

OLD="26.7.1.1315"
NEW="26.8.1.2041"
VOL="s30_upgrade_data"
CT="s30_clickhouse"
ROOT=pathlib.Path("s30")
base.ROOT=ROOT
base.VERSIONS=[OLD,OLD]
STATES=("legacy_many","many","middle","single")
QIDS=list(range(1,14))
REPS=3
WARM=1
FIELDS=("query_duration_ms","read_rows","read_bytes","memory_usage")
EVENTS=("LoadedStatisticsMicroseconds","QueryPlanOptimizeMicroseconds","FileOpen","SelectedMarks")
SCENARIOS={"equal_per_query":{"Q1":.25,"Q2":.25,"Q3":.25,"Q4":.25},
           "Q1_heavy":{"Q1":.60,"Q2":.15,"Q3":.15,"Q4":.10},
           "Q2_heavy":{"Q1":.10,"Q2":.60,"Q3":.20,"Q4":.10},
           "Q34_heavy":{"Q1":.10,"Q2":.10,"Q3":.45,"Q4":.35}}
FP=("SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
    "sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)),"
    "min(LO_ORDERDATE),max(LO_ORDERDATE) FROM ssb.{} FORMAT TSV")
def save(rel,text):
 p=ROOT/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
def jwrite(rel,x):save(rel,json.dumps(x,indent=2,sort_keys=True)+"\n")
def run(cmd,timeout=150,check=True,inp=None):
 p=subprocess.run(cmd,input=inp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if check and p.returncode:raise RuntimeError(f"COMMAND FAILED {cmd[:6]}: "+p.stderr.decode(errors="replace")[-1600:])
 return p
def sql(ct,query,qid=None,fmt=None,timeout=180):
 p=base.client(ct,query,qid=qid,fmt=fmt,check=False,timeout=timeout)
 if p.returncode:raise RuntimeError("SQL FAILED "+query[:120]+" ERR "+p.stderr.decode(errors="replace")[-1700:])
 return p.stdout.decode(errors="replace")
def start(ver):
 # Never remove the named data volume between versions.
 run(["docker","rm","-f",CT],check=False)
 run(["docker","run","-d","--name",CT,"--ulimit","nofile=262144:262144",
      "--mount",f"type=volume,source={VOL},target=/var/lib/clickhouse",
      "clickhouse/clickhouse-server:"+ver],timeout=100)
 for _ in range(110):
  p=run(["docker","exec",CT,"clickhouse-client","--query","SELECT 1"],check=False,timeout=12)
  if p.returncode==0:break
  time.sleep(1)
 else:raise RuntimeError("ClickHouse did not boot "+ver)
 actual=sql(CT,"SELECT version() FORMAT TSV").strip()
 print("S30_STARTED "+json.dumps({"image":ver,"server_version":actual}),flush=True)
 if actual!=ver:raise RuntimeError(f"Wrong version {actual} != {ver}")
 return CT
def freeze():
 for table in ("raw_source",)+STATES:
  sql(CT,"SYSTEM STOP MERGES ssb."+table)
def fp(t):return sql(CT,FP.format(t)).strip()
def physical():
 out={}
 for t in STATES:
  text=sql(CT,"SELECT count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts "
    "WHERE active AND database='ssb' AND table='"+t+"' FORMAT TSV").strip()
  parts,rows,bytes_,marks=map(int,text.split("\t"))
  out[t]={"parts":parts,"rows":rows,"bytes":bytes_,"marks":marks,"fingerprint":fp(t)}
 return out
def inventory():
 # Explicit part names and dimensions, independent of code/version.
 data=sql(CT,"SELECT table,name,partition,rows,marks,bytes_on_disk FROM system.parts "
               "WHERE active AND database='ssb' ORDER BY table,name FORMAT TSV")
 return {"count":len(data.splitlines()),"sha256":hashlib.sha256(data.encode()).hexdigest(),"lines":data}
def part_checksums():
 # Audit only active part checksums; obsolete inactive parts may be cleaned
 # after a restart, so scanning the whole store would generate false alarms.
 paths=sql(CT,"SELECT path FROM system.parts WHERE active AND database='ssb' ORDER BY table,name FORMAT TSV")
 cmd=["docker","exec","-i",CT,"bash","-c",
      "set -e; while IFS= read -r p; do sha256sum \"${p%/}/checksums.txt\"; done"]
 data=run(cmd,timeout=180,inp=paths.encode()).stdout
 return {"files":len(data.splitlines()),"digest_sha256":hashlib.sha256(data).hexdigest()}
def capture(phase):
 summary={"phase":phase,"server_version":sql(CT,"SELECT version() FORMAT TSV").strip(),
          "physical":physical(),"inventory":inventory(),"part_checksum_files":part_checksums(),
          "defaults":sql(CT,"SELECT name,value FROM system.settings WHERE name IN "
             "('materialize_statistics_on_insert','use_statistics','optimize_move_to_prewhere') ORDER BY name FORMAT TSV")}
 save(phase+"/part_inventory.tsv",summary["inventory"].pop("lines"))
 save(phase+"/session_settings.tsv",summary.pop("defaults"))
 jwrite(phase+"/physical.json",summary)
 print("S30_PHYSICAL "+json.dumps({"phase":phase,"parts":{k:v["parts"] for k,v in summary["physical"].items()},
       "part_checksums":summary["part_checksum_files"]}),flush=True)
 return summary
def establish(sqls,native):
 run(["docker","volume","rm",VOL],check=False) # ONLY this dedicated test volume.
 run(["docker","volume","create",VOL])
 start(OLD)
 run(["docker","exec",CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
 cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
 for t in ("raw_source",)+STATES:
  sql(CT,"CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY)")
  sql(CT,"SYSTEM STOP MERGES ssb."+t)
 with open(native,"rb") as f:
  out=base.run(["docker","exec","-i",CT,"clickhouse-client","--query",
           "INSERT INTO ssb.raw_source SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
           stdin=f,timeout=850,check=False)
  if out.returncode:raise RuntimeError("Native load failed: "+out.stderr.decode(errors="replace")[-1200:])
 native.unlink()
 for dest,flag,compact in (("legacy_many",0,False),("many",1,False),("middle",1,True),("single",1,False)):
  opt=("min_insert_block_size_rows=750152,min_insert_block_size_bytes=1," if not compact else "")
  stmt=f"INSERT INTO ssb.{dest} SELECT * FROM ssb.raw_source SETTINGS materialize_statistics_on_insert={flag},max_threads=1,max_insert_threads=1,{opt}max_memory_usage=9000000000"
  sql(CT,stmt,timeout=650)
  print("S30_INGEST "+json.dumps({"table":dest,"m":flag,"rows_parts":sql(CT,"SELECT count(),sum(rows) FROM system.parts WHERE active AND database='ssb' AND table='"+dest+"' FORMAT TSV").strip()}),flush=True)
 sql(CT,"SYSTEM START MERGES ssb.single")
 t0=time.monotonic()
 sql(CT,"OPTIMIZE TABLE ssb.single FINAL SETTINGS optimize_throw_if_noop=1",timeout=720)
 sql(CT,"SYSTEM STOP MERGES ssb.single")
 print("S30_FINAL_MERGE_SEC "+str(round(time.monotonic()-t0,3)),flush=True)
 p=physical()
 assert all(x["fingerprint"]==next(iter(p.values()))["fingerprint"] for x in p.values()),"Input mismatch"
 assert all(x["rows"]==6001215 for x in p.values()),"Row count mismatch"
 if not(p["legacy_many"]["parts"]>=400 and p["many"]["parts"]>=400 and
    5<=p["middle"]["parts"]<=40 and p["single"]["parts"]==1):
  raise RuntimeError("Unexpected pre-upgrade part states "+str(p))
 return p
def replace(sqltxt,t):
 assert "FROM lineorder_flat" in sqltxt
 return sqltxt.replace("FROM lineorder_flat","FROM "+t)
def answers(phase,queries):
 h={}
 for qi in QIDS:
  for t in STATES:
   for u in (0,1):
    query=replace(queries[qi-1],t)+f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0"
    h[f"q{qi}_{t}_u{u}"]=hashlib.sha256(sql(CT,query+" FORMAT TSV").encode()).hexdigest()
  if len({h[f"q{qi}_{t}_u{u}"] for t in STATES for u in (0,1)})!=1:
   raise RuntimeError("Different logical result in "+phase+" q"+str(qi))
 jwrite(phase+"/answer_hashes.json",h)
 return h
def measure(phase,queries,parts):
 records=[]
 for rnd in range(-WARM,REPS):
  cells=[(t,qi,u) for t in STATES for qi in QIDS for u in (0,1)]
  random.Random(300000+rnd).shuffle(cells)
  for i,(t,qi,u) in enumerate(cells):
   query=replace(queries[qi-1],t)+f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,max_threads=2,use_query_condition_cache=0,max_memory_usage=9000000000"
   qid=f"s30_{phase}_{t}_q{qi:02d}_u{u}_r{rnd}_i{i}"
   sql(CT,query,qid=qid,fmt="Null",timeout=185)
   records.append({"phase":phase,"state":t,"q":qi,"use_statistics":u,"round":rnd,
                   "query_id":qid,"parts":parts[t]["parts"],"warmup":int(rnd<0)})
  print("S30_ROUND "+phase+" "+str(rnd),flush=True)
 sql(CT,"SYSTEM FLUSH LOGS")
 raw=sql(CT,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
   f"FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s30_{phase}_') FORMAT JSONEachRow")
 save(phase+"/query_log.jsonl",raw)
 logs={obj["query_id"]:obj for line in raw.splitlines() if line.strip() for obj in [json.loads(line)]}
 if len(logs)!=len(records):raise RuntimeError("Missing receipts "+phase+f" {len(logs)}/{len(records)}")
 for r in records:
  obj=logs[r["query_id"]];event=obj.get("ProfileEvents",{}) or {}
  for name in FIELDS:r[name]=obj[name]
  for name in EVENTS:r[name]=event.get(name,0)
 with (ROOT/phase/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
 med=[]
 for t in STATES:
  for qi in QIDS:
   for u in (0,1):
    rr=[r for r in records if r["state"]==t and r["q"]==qi and r["use_statistics"]==u and r["round"]>=0]
    if len(rr)!=REPS:raise RuntimeError("Missing repeats")
    row={"phase":phase,"table":t,"q":qi,"use_statistics":u,"n":REPS,
         "family":"Q1" if qi<=3 else "Q2" if qi<=6 else "Q3" if qi<=10 else "Q4"}
    for field in FIELDS+EVENTS:
     row[field+"_median"]=statistics.median(float(r[field]) for r in rr)
    med.append(row)
 with (ROOT/phase/"medians.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
 for qi in (2,3,4):
  for t in ("legacy_many","many","middle","single"):
   a=next(z for z in med if z["table"]==t and z["q"]==qi and z["use_statistics"]==1)
   b=next(z for z in med if z["table"]==t and z["q"]==qi and z["use_statistics"]==0)
   print("S30_SPOT "+json.dumps({"phase":phase,"state":t,"q":qi,
     "wall_on":a["query_duration_ms_median"],"wall_off":b["query_duration_ms_median"],
     "stats_load_ms":a["LoadedStatisticsMicroseconds_median"]/1000,
     "stats_file_opens":a["FileOpen_median"]-b["FileOpen_median"]}),flush=True)
 return med
def plans(phase,queries):
 for qi in (2,3,4,7,11):
  for t in STATES:
   query=replace(queries[qi-1],t)+" SETTINGS use_statistics=1,optimize_move_to_prewhere=1"
   for label,prefix in (("plan","EXPLAIN PLAN "),("indexes","EXPLAIN indexes=1 ")):
    save(f"{phase}/plans/q{qi}_{t}_{label}.txt",sql(CT,prefix+query))
def restart(ver):
 run(["docker","stop","-t","90",CT],timeout=140)
 run(["docker","rm",CT])
 start(ver)
 freeze()
def weighted(rows,scenario):
 by=defaultdict(list)
 for x in rows:by[x["family"]].append(x["query_duration_ms_median"])
 return sum(weight*statistics.mean(by[family]) for family,weight in scenario.items())
def gate(old,new):
 basev=weighted(old,SCENARIOS["equal_per_query"])
 # threshold must hold for each workload scenario separately
 count=sum(a["query_duration_ms_median"]>=1.5*b["query_duration_ms_median"]
   for a,b in zip(sorted(new,key=lambda z:z["q"]),sorted(old,key=lambda z:z["q"])))
 return {"large_regressions":count,"large_regression_trigger":count>=2,
         "old_equal_mean_ms":basev,"new_equal_mean_ms":weighted(new,SCENARIOS["equal_per_query"])}
def gate_matrix(medians):
 idx={(x["phase"],x["table"],x["q"],x["use_statistics"]):x for x in medians}
 phases=["before","sham_restart","upgraded"]
 reports=[]
 comparisons=(("sham_restart","before",1,1),
   ("version_same_policy_ON","sham_restart",1,1),
   ("version_same_policy_OFF","sham_restart",0,0),
   ("switch_OFF_to_ON","sham_restart",0,1))
 # Field "comparison" is descriptive; after state is upgraded unless sham restart.
 for state in STATES:
  for typ,oldphase,ou,nu in comparisons:
   newphase="sham_restart" if typ=="sham_restart" else "upgraded"
   old=[idx[(oldphase,state,q,ou)] for q in QIDS]
   new=[idx[(newphase,state,q,nu)] for q in QIDS]
   reg=sum(new[i]["query_duration_ms_median"]>=1.5*old[i]["query_duration_ms_median"] for i in range(13))
   for name,weights in SCENARIOS.items():
    avg_old=weighted(old,weights);avg_new=weighted(new,weights);ratio=avg_new/avg_old
    blocked=reg>=2 or ratio>1.10
    reports.append({"table":state,"comparison":typ,"from_phase":oldphase,"to_phase":newphase,
      "old_statistics":ou,"new_statistics":nu,"scenario":name,
      "old_ms":round(avg_old,5),"new_ms":round(avg_new,5),"ratio":round(ratio,6),
      "count_ge_1_5":reg,"gate":"BLOCK" if blocked else "APPROVE"})
 with (ROOT/"gate_matrix.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(reports[0]));w.writeheader();w.writerows(reports)
 for r in reports:
  if r["scenario"]=="equal_per_query":print("S30_GATE "+json.dumps(r),flush=True)
 return reports
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for v in (OLD,NEW):run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=480)
 qs=base.pinned_sources();native=base.make_data()
 allstats=[]
 try:
  establish(qs,native)
  before=capture("before");ah=answers("before",qs)
  plans("before",qs)
  allstats.extend(measure("before",qs,before["physical"]))
  print("S30_SHAM_RESTART",flush=True)
  restart(OLD)
  sham=capture("sham_restart")
  if sham["inventory"]!=before["inventory"] or sham["part_checksum_files"]!=before["part_checksum_files"]:
   raise RuntimeError("Same-version restart modified part inventory or data checksums")
  if answers("sham_restart",qs)!=ah:raise RuntimeError("Sham restart changed query answers")
  allstats.extend(measure("sham_restart",qs,sham["physical"]))
  print("S30_VERSION_UPGRADE",flush=True)
  restart(NEW)
  after=capture("upgraded")
  if after["inventory"]!=before["inventory"] or after["part_checksum_files"]!=before["part_checksum_files"]:
   raise RuntimeError("Version upgrade modified part inventory or data checksums")
  if before["physical"]!=after["physical"]:
   raise RuntimeError("Upgraded physical dimensions changed")
  if answers("upgraded",qs)!=ah:raise RuntimeError("Upgrade changed query results")
  plans("upgraded",qs)
  allstats.extend(measure("upgraded",qs,after["physical"]))
  # Require statistics materialization to remain observable on the loaded arm.
  for phase in ("before","sham_restart","upgraded"):
   x=next(r for r in allstats if r["phase"]==phase and r["table"]=="many" and r["q"]==2 and r["use_statistics"]==1)
   if x["LoadedStatisticsMicroseconds_median"]<=0:raise RuntimeError("Stats path missing in "+phase)
  matrix=gate_matrix(allstats)
  report={"valid":True,"replay":"single persistent Docker named volume, same runner, same part bytes",
   "volume":VOL,"versions":[OLD,OLD,NEW],"source":json.loads((ROOT/"SOURCE_IDENTITY.json").read_text()),
   "physical_before":before["physical"],"identical_inventory_and_checksums":True,
   "same_answers_all_states_queries_settings_phases":True,
   "n_measured":3*len(STATES)*13*2*REPS,
   "n_warmup":3*len(STATES)*13*2*WARM,
   "gates":matrix,"constraints":"No claim of separate hardware generality; same version restart controls some temporal drift, not all caching differences."}
  jwrite("FINAL_STATUS.json",report)
  print("S30_FINAL "+json.dumps({"valid":True,"measured":report["n_measured"],
    "states":{k:v["parts"] for k,v in before["physical"].items()}}),flush=True)
 finally:
  run(["docker","rm","-f",CT],check=False,timeout=80)
  # Volume is NOT removed here. Hosted runner eventually discards its disk.
if __name__=="__main__":main()
