#!/usr/bin/env python3
"""Stage 38: Natural Merge Recovery & Decision Lifetime.
26.7->26.8 preserving 6,001,215 source rows per table; append an identical
3,000,607-row frozen SF1 subset before (legacy) vs after (new) upgrade.
Only allow normal background MergeTree merges. No OPTIMIZE or forced merges.
Read-only checkpoints require zero running merges before query benchmark.
"""
import csv,json,pathlib,time,hashlib
import db_mixed_history_upgrade as P
R=pathlib.Path("s38");R.mkdir(exist_ok=True)
S=P.S;S.ROOT=R;S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="s38_ch";S.VOL="s38_vol"
P.R=R;P.T=("old50","new50")
TABLES=P.T
def log(*args):print("S38",*args,flush=True)
def q(s):return P.sql(s)
def parts():
 lines=q("SELECT table,count(),sum(rows),max(level) FROM system.parts WHERE active "
   "AND database='ssb' AND table IN ('old50','new50') GROUP BY table ORDER BY table FORMAT TSV").splitlines()
 d={x.split("\t")[0]:[int(y) for y in x.split("\t")[1:]] for x in lines}
 if len(d)!=2:raise RuntimeError("Missing paired table "+str(d))
 return d
def running():
 return int(q("SELECT count() FROM system.merges WHERE database='ssb' AND table IN "
               "('old50','new50') FORMAT TSV").strip())
def frozen():
 for t in TABLES:q("SYSTEM STOP MERGES ssb."+t)
 for k in range(100):
  if running()==0:return
  time.sleep(1)
 raise RuntimeError("Active merges did not quiesce after STOP MERGES")
def thaw():
 # Persisted Stage37 freeze must be removed. Restores the normal high merge cap.
 for t in TABLES:
  q("ALTER TABLE ssb."+t+" MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool=161061273600")
  q("SYSTEM START MERGES ssb."+t)
 for t in TABLES:
  create=q("SHOW CREATE TABLE ssb."+t)
  if "161061273600" not in create:raise RuntimeError("Merge cap not restored "+t)
 log("NATURAL_MERGE_THAW")
def watch(label,limit,goal,trace):
 start=time.monotonic();last=-99
 while time.monotonic()-start<limit:
  d=parts();act=running()
  elapsed=round(time.monotonic()-start,2)
  z={"phase":label,"elapsed_s":elapsed,"old_parts":d["old50"][0],
     "new_parts":d["new50"][0],"active_merges":act}
  trace.append(z)
  if elapsed-last>=12:log("WATCH",json.dumps(z));last=elapsed
  if all(d[t][0]<=goal for t in TABLES):return True
  time.sleep(2)
 return False
def stable_snapshot(label):
 frozen()
 a=P.snap(label)
 if {a[t]["fingerprint"] for t in TABLES}.__len__()!=1:
  raise RuntimeError("Unequal logical content at "+label)
 if any(a[t]["rows"]!=9001822 for t in TABLES):raise RuntimeError("Row loss during merges")
 return a
def assess(medians,labels):
 ix={(v["phase"],v["table"],v["q"],v["arm"]):v for v in medians}
 results=[]
 def f(q):return P.group(q)
 for label in labels:
  for arm,_,_ in P.A:
   for mix,weights in P.W.items():
    for test,bphase,btable,tphase,ttable in (
      ("actual_upgraded_deployment","before","old50",label,"new50"),
      ("legacy_part_path","before","old50",label,"old50"),
      ("within_26_8_policy_history","after","old50",label,"new50")):
     a=[ix[bphase,btable,i,arm] for i in P.Q]
     b=[ix[tphase,ttable,i,arm] for i in P.Q]
     x=P.weighted(a,weights);y=P.weighted(b,weights)
     n=sum(v["ms"]>=1.5*u["ms"] for u,v in zip(a,b))
     row={"checkpoint":label,"comparison":test,"policy":arm,"workload":mix,
       "ratio":y/x,"q_ge_1_5":n,"gate":"BLOCK" if n>=2 or y/x>1.10 else "APPROVE",
       "old_ms":x,"new_ms":y}
     results.append(row)
     if mix=="equal":log("GATE",json.dumps(row))
 with (R/"decision_timeline.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
 return results
def main():
 for version in (S.OLD,S.NEW):
  S.run(["docker","pull","clickhouse/clickhouse-server:"+version],timeout=500)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 logs=[];truth={};trace=[];stages=[]
 try:
  S.start(S.OLD)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw","delta50")+TABLES:P.create(t,cols)
  with open(native,"rb") as f:
   x=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
    stdin=f,check=False,timeout=950)
  if x.returncode:raise RuntimeError("Native SF1 upload failed "+x.stderr.decode(errors="replace")[-1400:])
  native.unlink()
  n=6001215//2
  q("INSERT INTO ssb.delta50 SELECT * FROM ssb.raw ORDER BY "
    f"LO_ORDERKEY,LO_LINENUMBER LIMIT {n} SETTINGS materialize_statistics_on_insert=0,max_threads=2")
  if int(q("SELECT count() FROM ssb.delta50 FORMAT TSV").strip())!=n:
   raise RuntimeError("Delta count differs")
  for t in TABLES:P.insert(t,"raw",0)
  old_default=q("SELECT name,value FROM system.settings WHERE name IN "
    "('materialize_statistics_on_insert','materialize_statistics_on_merge','auto_statistics_types') "
    "ORDER BY name FORMAT TSV")
  P.save("defaults_old.tsv",old_default);log("OLD_DEFAULT",repr(old_default))
  P.insert("old50","delta50")
  a=P.snap("before_upgrade")
  logs_before=[]
  P.measure("before",("old50",),qs,truth,logs_before)
  logs+=logs_before
  S.run(["docker","stop","-t","90",S.CT],timeout=150)
  S.run(["docker","rm",S.CT],timeout=50)
  S.start(S.NEW)
  for t in ("raw","delta50")+TABLES:q("SYSTEM STOP MERGES ssb."+t)
  inherited=P.snap("after_binary_upgrade")
  if a!=inherited:raise RuntimeError("Original physical parts not inherited")
  new_default=q("SELECT name,value FROM system.settings WHERE name IN "
    "('materialize_statistics_on_insert','materialize_statistics_on_merge','auto_statistics_types') "
    "ORDER BY name FORMAT TSV")
  P.save("defaults_new.tsv",new_default);log("NEW_DEFAULT",repr(new_default))
  P.insert("new50","delta50")
  baseline=stable_snapshot("post_insert")
  if baseline["old50"]["fingerprint"]!=baseline["new50"]["fingerprint"]:
   raise RuntimeError("Paired data mismatch")
  P.measure("after",TABLES,qs,truth,logs)
  stable_after=P.snap("post_insert_after_benchmark")
  if stable_after!=baseline:raise RuntimeError("Unexpected baseline merges")
  labels=["after"]
  assess(logs,labels)
  thaw()
  midpoint=watch("natural_to_half",270,550,trace)
  mid=stable_snapshot("midpoint")
  if all(mid[t]["parts"]<baseline[t]["parts"] for t in TABLES):
   P.measure("mid",TABLES,qs,truth,logs)
   if P.snap("midpoint_after_benchmark")!=mid:raise RuntimeError("Midpoint changed while frozen")
   labels.append("mid")
   assess(logs,labels)
  else:log("MIDPOINT_SKIPPED","At least one arm did not merge naturally")
  for t in TABLES:q("SYSTEM START MERGES ssb."+t)
  low=watch("natural_to_tenth",270,110,trace)
  final=stable_snapshot("terminal")
  if not any(final[t]["parts"]<baseline[t]["parts"] for t in TABLES):
   raise RuntimeError("No actual background merges; invalid trial")
  P.measure("final",TABLES,qs,truth,logs)
  if P.snap("terminal_after_benchmark")!=final:raise RuntimeError("Terminal changed while frozen")
  labels.append("final")
  matrix=assess(logs,labels)
  q("SYSTEM FLUSH LOGS")
  history=q("SELECT event_time,table,event_type,part_name,rows,merged_from FROM system.part_log "
    "WHERE database='ssb' AND table IN ('old50','new50') AND event_type='MergeParts' "
    "ORDER BY event_time FORMAT JSONEachRow")
  P.save("merge_part_log.jsonl",history)
  P.dump("merge_trace.json",trace)
  with (R/"all_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(logs[0]));w.writeheader();w.writerows(logs)
  P.dump("FINAL_STATUS.json",{"valid":True,"natural_merges_observed":True,
    "part_count_start":{k:baseline[k]["parts"] for k in TABLES},
    "part_count_end":{k:final[k]["parts"] for k in TABLES},
    "checkpoints":labels,"mid_target_reached":midpoint,"low_target_reached":low,
    "old_defaults":old_default,"new_defaults":new_default,
    "before":baseline,"mid":mid,"after":final,"gates":matrix,
    "limitations":["Half-SF1 delta rows replayed from old dataset.",
      "One GitHub runner with 3 timed repetitions per SQL.",
      "Pauses isolate storage state effects from active merge resource contention.",
      "Natural merges not forced by OPTIMIZE; both treatments permitted background scheduling."]})
  log("FINAL_SUCCESS",json.dumps({"checkpoints":labels,
    "initial":{k:baseline[k]["parts"] for k in TABLES},
    "terminal":{k:final[k]["parts"] for k in TABLES}}))
 finally:
  S.run(["docker","rm","-f",S.CT],check=False,timeout=100)
if __name__=="__main__":main()
