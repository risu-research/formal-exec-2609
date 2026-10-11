#!/usr/bin/env python3
"""Stage31b - falsify statistics-loader disappearance using query-level
use_statistics_cache toggle, with fresh 734-part and reattached tables.
Same ClickHouse 26.8 binary. Immutable checksum controls in every phase.
"""
import csv,hashlib,json,pathlib,random,statistics
import db_statistics_lifecycle as L
S=L.S
ROOT=pathlib.Path("s31b")
S.ROOT=ROOT;S.base.ROOT=ROOT;S.base.VERSIONS=[S.NEW,S.NEW]
S.CT="s31b_ch";S.VOL="s31b_statscache"
L.ROOT=ROOT
QIDS=(2,3,4)
REPS=5;WARM=1
ARMS=(("stats_off",0,1),("stats_cache_on",1,1),("stats_cache_off",1,0))
EV=("LoadedStatisticsMicroseconds","FileOpen","SelectedMarks","QueryPlanOptimizeMicroseconds")
def say(*x):print("S31B",*x,flush=True)
def dump(path,obj):L.dump(path,obj)
def q(s,qid=None,fmt=None,timeout=180):return L.q(s,qid=qid,fmt=fmt,timeout=timeout)
def sql(query,table,u,c):
 return L.qs(query,table,u)+f",use_statistics_cache={c}"
def verify(phase,queries,tables,reference):
 out={}
 for qi in QIDS:
  for t in tables:
   for label,u,c in ARMS:
    ans=q(sql(queries[qi-1],t,u,c)+" FORMAT TSV",timeout=160)
    h=hashlib.sha256(ans.encode()).hexdigest()
    out[f"q{qi}_{t}_{label}"]=h
    if qi in reference and h!=reference[qi]:raise RuntimeError("Result changed "+phase)
  vals={out[f"q{qi}_{t}_{label}"] for t in tables for label,u,c in ARMS}
  if len(vals)!=1:raise RuntimeError("Same-phase answers differ "+str(qi))
  reference[qi]=next(iter(vals))
 dump(f"{phase}/answer_hashes.json",out)
def measure(phase,queries,tables,allmeds):
 recs=[]
 for rnd in range(-WARM,REPS):
  cells=[(t,qi,label,u,c) for t in tables for qi in QIDS for label,u,c in ARMS]
  random.Random(311000+rnd).shuffle(cells)
  for i,(t,qi,label,u,c) in enumerate(cells):
   qid=f"s31b_{phase}_{t}_{label}_q{qi}_r{rnd}_i{i}"
   q(sql(queries[qi-1],t,u,c),qid=qid,fmt="Null",timeout=180)
   recs.append({"phase":phase,"table":t,"q":qi,"case":label,"stats":u,"cache":c,
     "round":rnd,"query_id":qid})
  say("ROUND",phase,rnd)
 q("SYSTEM FLUSH LOGS")
 raw=q("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
  "FROM system.query_log WHERE type='QueryFinish' AND "
  f"startsWith(query_id,'s31b_{phase}_') FORMAT JSONEachRow",timeout=120)
 L.save(phase+"/query_log.jsonl",raw)
 prof={a["query_id"]:a for line in raw.splitlines() if line.strip() for a in [json.loads(line)]}
 if len(prof)!=len(recs):raise RuntimeError(f"Incomplete receipts {phase} {len(prof)}/{len(recs)}")
 for item in recs:
  z=prof[item["query_id"]];ev=z.get("ProfileEvents") or {}
  item.update({"query_duration_ms":z["query_duration_ms"],"read_rows":z["read_rows"]})
  item.update({e:ev.get(e,0) for e in EV})
 with (ROOT/phase/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(recs[0]));w.writeheader();w.writerows(recs)
 rows=[]
 for t in tables:
  for qi in QIDS:
   for label,u,c in ARMS:
    a=[r for r in recs if r["table"]==t and r["q"]==qi and r["case"]==label and r["round"]>=0]
    if len(a)!=REPS:raise RuntimeError("Wrong reps")
    z={"phase":phase,"table":t,"q":qi,"case":label,"statistics":u,"cache":c,"n":len(a)}
    for col in ("query_duration_ms","read_rows")+EV:
     z[col+"_median"]=statistics.median(r[col] for r in a)
    rows.append(z);allmeds.append(z)
    say("MEDIAN",json.dumps(z))
 with (ROOT/phase/"medians.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 return len([x for x in recs if x["round"]>=0])
def phase(label,tables,queries,reference,allmeds):
 ss=L.snapshot(label,tables)
 verify(label,queries,tables,reference)
 measured=measure(label,queries,tables,allmeds)
 return ss,measured
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 S.run(["docker","pull","clickhouse/clickhouse-server:"+S.NEW],timeout=490)
 queries=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 allmeds=[];known={};total=0
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  opts=q("SELECT name,value FROM system.settings WHERE name IN "
       "('use_statistics','use_statistics_cache','materialize_statistics_on_insert') ORDER BY name FORMAT TSV")
  L.save("system_settings.tsv",opts);say("SETTINGS",json.dumps(opts))
  if "use_statistics_cache\t" not in opts:raise RuntimeError("Cache setting absent")
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  L.create("raw",cols);L.create("first",cols)
  with open(native,"rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
    stdin=f,check=False,timeout=960)
  if p.returncode:raise RuntimeError("Native failed "+p.stderr.decode(errors="replace")[-1400:])
  native.unlink()
  L.insert("first")
  a,n=phase("fresh",("first",),queries,known,allmeds);total+=n
  L.detach("first")
  b,n=phase("reattached",("first",),queries,known,allmeds);total+=n
  L.unchanged(a,b,("first",),"reattach")
  S.run(["docker","stop","-t","90",S.CT],timeout=140);S.run(["docker","rm",S.CT])
  S.start(S.NEW)
  for t in ("raw","first"):q("SYSTEM STOP MERGES ssb."+t)
  c,n=phase("restarted",("first",),queries,known,allmeds);total+=n
  L.unchanged(a,c,("first",),"restart")
  L.create("second",cols);L.insert("second")
  d,n=phase("post_fresh",("first","second"),queries,known,allmeds);total+=n
  L.unchanged(a,d,("first",),"second insertion")
  L.detach("second")
  e,n=phase("post_reattached",("first","second"),queries,known,allmeds);total+=n
  L.unchanged(d,e,("first","second"),"second reattach")
  with (ROOT/"medians_all.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(allmeds[0]));w.writeheader();w.writerows(allmeds)
  report={"valid":True,"server_version":S.NEW,"dataset_rows":6001215,
   "query_ids":QIDS,"parts_per_table":734,"measured":total,
   "warmups":int(total*WARM/REPS),"immutable_part_files":True,
   "identical_query_answers":True,"stages":["fresh","reattached","restarted","post_fresh","post_reattached"],
   "focus":"Does use_statistics_cache=0 restore statistics loading after reattachment?"}
  dump("FINAL_STATUS.json",report);say("FINAL",json.dumps(report))
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=90)
if __name__=="__main__":main()
