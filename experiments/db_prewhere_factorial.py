#!/usr/bin/env python3
"""Stage 23. Fixed-input PREWHERE factorial (2 releases x ON/OFF), ABBA waves."""
import csv
import hashlib
import json
import pathlib
import random
import statistics
import sys
import time
from collections import defaultdict
import db_sf1_probe as base

VERSIONS=base.VERSIONS
ORDER=[VERSIONS[0],VERSIONS[1],VERSIONS[1],VERSIONS[0]]
QIDS=base.QIDS
REPS=12
PREWHERE={"ON":"optimize_move_to_prewhere=1","OFF":"optimize_move_to_prewhere=0"}
ROOT=pathlib.Path("s23").resolve()
base.ROOT=ROOT
LOG_KEYS=["QueryPlanOptimizeMicroseconds","QueryPlanMicroseconds","SelectedMarks",
          "SelectedRows","SelectedBytes","ReadCompressedBytes",
          "UserTimeMicroseconds","OSCPUVirtualTimeMicroseconds"]
def die(s): raise RuntimeError(s)
def output(ct,sql,timeout=300):
 p=base.client(ct,sql,check=False,timeout=timeout)
 if p.returncode:die(f"SQL failure: {sql[:130]} {p.stderr.decode(errors='replace')[-1300:]}")
 return p.stdout.decode(errors="replace")
def save(folder,name,text):
 (folder/name).write_text(text)
def med(xs):
 vals=[float(x) for x in xs if x is not None]
 return statistics.median(vals) if vals else None
def run_wave(wave,ver,native,sqls):
 folder=ROOT/f"wave{wave}_{ver}"
 folder.mkdir(parents=True,exist_ok=True)
 ct=f"s23_{wave}_{ver.replace('.','_')}"
 base.run(["docker","rm","-f",ct],check=False)
 base.run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144",
           "clickhouse/clickhouse-server:"+ver],timeout=120)
 try:
  for i in range(100):
   ready=base.run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False,timeout=20)
   if ready.returncode==0:break
   time.sleep(1)
  else:die("Server failed start "+ver)
  base.run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"],timeout=80)
  columns=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  ddl=(f"CREATE TABLE ssb.lineorder_flat ({columns}) ENGINE=MergeTree "
       "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE, LO_ORDERKEY)")
  output(ct,ddl)
  with open(native,"rb") as f:
   base.run(["docker","exec","-i",ct,"clickhouse-client","--query",
             "INSERT INTO ssb.lineorder_flat FORMAT Native"],stdin=f,timeout=1000)
  output(ct,"SYSTEM STOP MERGES ssb.lineorder_flat")
  fingerprint=output(ct,"SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
                  "min(LO_ORDERDATE),max(LO_ORDERDATE),"
                  "sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) "
                  "FROM ssb.lineorder_flat FORMAT TSV").strip()
  if not fingerprint.startswith("6001215\t"):die("Unexpected SF1 size: "+fingerprint)
  save(folder,"fingerprint.tsv",fingerprint+"\n")
  part_sql=("SELECT partition,count(),sum(rows),sum(bytes_on_disk),sum(marks) "
            "FROM system.parts WHERE active AND database='ssb' AND table='lineorder_flat' "
            "GROUP BY partition ORDER BY partition FORMAT TSV")
  save(folder,"parts_before.tsv",output(ct,part_sql))
  save(folder,"settings.tsv",output(ct,"SELECT name,value,changed FROM system.settings "
                                    "WHERE name IN ('optimize_move_to_prewhere','use_query_condition_cache',"
                                    "'max_threads') ORDER BY name FORMAT TSV"))
  # Every comparison is a real ON or OFF setting; no default ambiguity.
  results_hash={}
  for q in QIDS:
   for flag,setting in PREWHERE.items():
    sql=sqls[q-1]+" SETTINGS "+setting
    answer=output(ct,sql+" FORMAT TSV")
    results_hash[f"q{q:02d}_{flag}"]=hashlib.sha256(answer.encode()).hexdigest()
    if wave in (0,1):  # store actual plan and indexes once per version
     for kind,prefix in [("indexes","EXPLAIN indexes=1 "),("pipeline","EXPLAIN PIPELINE "),("plan","EXPLAIN PLAN ")]:
      p=base.client(ct,prefix+sql,check=False,timeout=90)
      save(folder,f"q{q:02d}_{flag}_{kind}.txt",
           p.stdout.decode(errors="replace")+(
           "\nERROR: "+p.stderr.decode(errors="replace") if p.returncode else ""))
  save(folder,"answers_sha256.json",json.dumps(results_hash,sort_keys=True,indent=2)+"\n")
  records=[]
  seqno=0
  # Round 0 is explicit warmup, rounds 1..12 are measured.
  for round_id in range(REPS+1):
   rng=random.Random(230000+1000*wave+round_id)
   qorder=QIDS.copy()
   rng.shuffle(qorder)
   for q in qorder:
    flags=["ON","OFF"]
    # Exactly balanced treatment order for every query in each wave.
    if (round_id+q+wave)%2:flags.reverse()
    for flag in flags:
     setting=PREWHERE[flag]
     query_id=f"s23_w{wave}_q{q:02d}_{flag}_r{round_id}_n{seqno}"
     seqno+=1
     start=time.monotonic_ns()
     p=base.client(ct,sqls[q-1]+" SETTINGS "+setting,qid=query_id,fmt="Null",check=False,timeout=220)
     if p.returncode:
      die(f"Query failed wave={wave} q={q} {flag} round={round_id}: "+
          p.stderr.decode(errors="replace")[-1400:])
     records.append({"wave":wave,"version":ver,"query":q,"flag":flag,
                     "round":round_id,"warmup":int(round_id==0),
                     "query_id":query_id,
                     "client_ms":round((time.monotonic_ns()-start)/1e6,3)})
  save(folder,"run_order.json",json.dumps(records,indent=2)+"\n")
  output(ct,"SYSTEM FLUSH LOGS")
  logsql=("SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
          "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s23_') "
          "ORDER BY query_id FORMAT JSONEachRow")
  jsonl=output(ct,logsql,timeout=250)
  save(folder,"query_log.jsonl",jsonl)
  logmap={}
  for s in jsonl.splitlines():
   if not s.strip():continue
   x=json.loads(s)
   if x["query_id"] in logmap:die("Duplicate receipt query_id "+x["query_id"])
   logmap[x["query_id"]]=x
  cols=["wave","version","query","flag","round","warmup","query_id","client_ms",
        "query_duration_ms","read_rows","read_bytes","memory_usage"]+LOG_KEYS
  rows=[]
  for rec in records:
   x=logmap.get(rec["query_id"])
   if x is None:die("Missing query profile "+rec["query_id"])
   ev=x.get("ProfileEvents") or {}
   row=dict(rec)
   row.update({k:x.get(k) for k in ["query_duration_ms","read_rows","read_bytes","memory_usage"]})
   row.update({k:ev.get(k) for k in LOG_KEYS})
   rows.append(row)
  with (folder/"all_receipts.csv").open("w",newline="") as f:
   wr=csv.DictWriter(f,fieldnames=cols);wr.writeheader();wr.writerows(rows)
  save(folder,"parts_after.tsv",output(ct,part_sql))
  print(f"WAVE_COMPLETE wave={wave} release={ver} "
        f"fingerprint={fingerprint} runs={len(rows)} logs={len(logmap)}",flush=True)
  for q in QIDS:
   dd={}
   for flag in PREWHERE:
    rr=[x for x in rows if x["query"]==q and x["flag"]==flag and not x["warmup"]]
    dd[flag]={"server_ms":med(x["query_duration_ms"] for x in rr),
              "plan_us":med(x["QueryPlanOptimizeMicroseconds"] for x in rr),
              "marks":med(x["SelectedMarks"] for x in rr),
              "rows":med(x["read_rows"] for x in rr)}
   print("CONDITION "+json.dumps({"wave":wave,"version":ver,"q":q,**dd}),flush=True)
  return fingerprint,results_hash,rows
 finally:
  base.run(["docker","rm","-f",ct],check=False,timeout=40)
def make_summary(waves,rows):
 data=[r for r in rows if not r["warmup"]]
 grouped=defaultdict(list)
 for r in data:grouped[(r["wave"],r["version"],r["query"],r["flag"])].append(r)
 summary=[]
 for q in QIDS:
  for ver in VERSIONS:
   subset=[x for x in data if x["query"]==q and x["version"]==ver]
   counts={flag:sum(x["flag"]==flag for x in subset) for flag in PREWHERE}
   if counts!={"ON":24,"OFF":24}:die("Incorrect factorial balance: "+str((q,ver,counts)))
   row={"q":q,"version":ver,"n_per_treatment":24}
   for setting in PREWHERE:
    sr=[r for r in subset if r["flag"]==setting]
    for metric in ["query_duration_ms","QueryPlanOptimizeMicroseconds",
                   "read_rows","read_bytes","SelectedMarks","UserTimeMicroseconds"]:
     row[f"{setting}_{metric}"]=med(r[metric] for r in sr)
   for metric in ["query_duration_ms","QueryPlanOptimizeMicroseconds"]:
    diffs=[]
    for wave in range(len(ORDER)):
     if ORDER[wave]!=ver:continue
     for round_id in range(1,REPS+1):
      lookup={(r["flag"]):r for r in subset if r["wave"]==wave and r["round"]==round_id}
      a,b=lookup["ON"].get(metric),lookup["OFF"].get(metric)
      if a is not None and b is not None:diffs.append(float(a)-float(b))
    row[f"paired_ON_minus_OFF_{metric}"]=med(diffs)
   summary.append(row)
 with (ROOT/"factorial_summary.csv").open("w",newline="") as f:
  wr=csv.DictWriter(f,fieldnames=list(summary[0]));wr.writeheader();wr.writerows(summary)
 interactions=[]
 for q in QIDS:
  a=next(x for x in summary if x["q"]==q and x["version"]==VERSIONS[0])
  b=next(x for x in summary if x["q"]==q and x["version"]==VERSIONS[1])
  dx=b["paired_ON_minus_OFF_query_duration_ms"]-a["paired_ON_minus_OFF_query_duration_ms"]
  dp=b["paired_ON_minus_OFF_QueryPlanOptimizeMicroseconds"]-a["paired_ON_minus_OFF_QueryPlanOptimizeMicroseconds"]
  z={"q":q,"interaction_duration_ms":dx,"interaction_plan_us":dp,"interaction_plan_ms":dp/1000,
     "fraction_explained_descriptive":None if not dx else (dp/1000)/dx}
  interactions.append(z)
  print("INTERACTION "+json.dumps(z),flush=True)
 with (ROOT/"interactions.csv").open("w",newline="") as f:
  wr=csv.DictWriter(f,fieldnames=list(interactions[0]));wr.writeheader();wr.writerows(interactions)
 return summary,interactions
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 sqls=base.pinned_sources()
 native=base.make_data()
 source=json.loads((ROOT/"SOURCE_IDENTITY.json").read_text())
 outcomes=[]
 all_rows=[]
 answers=[]
 for wave,ver in enumerate(ORDER):
  fp,ans,rows=run_wave(wave,ver,native,sqls)
  outcomes.append({"wave":wave,"version":ver,"fingerprint":fp,"receipts":len(rows)})
  answers.append(ans);all_rows+=rows
 if len({x["fingerprint"] for x in outcomes})!=1:die("Input checksum differs across waves")
 if answers.count(answers[0])!=len(answers):die("Answers differ across versions/settings/waves")
 if len(all_rows)!=4*len(QIDS)*2*(REPS+1):die("Missing profiles")
 if any(r["QueryPlanOptimizeMicroseconds"] is None for r in all_rows):die("Missing plan optimization event")
 summary,interactions=make_summary(outcomes,all_rows)
 result={"valid":True,"source":source,"outcomes":outcomes,"samples":len(all_rows),
         "measured":sum(not x["warmup"] for x in all_rows),
         "query_count":len(QIDS),"rounds_per_wave":REPS,"wave_order":ORDER,
         "answers_identical":True,"all_plan_event_present":True,
         "interaction_ms_by_query":{str(z["q"]):z["interaction_duration_ms"] for z in interactions}}
 save(ROOT,"FINAL_STATUS.json",json.dumps(result,indent=2)+"\n")
 print("FINAL_STATUS "+json.dumps(result),flush=True)
if __name__=="__main__":main()
