#!/usr/bin/env python3
"""Independent settings-path diagnostic on one immutable content layout."""
import csv,json,random,statistics,pathlib,hashlib,time
import db_mixed_history_upgrade as P
S=P.S
R=pathlib.Path("c43d");R.mkdir(exist_ok=True)
P.R=R;P.T=("a","b")
S.ROOT=S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c43d_ch";S.VOL="c43d_vol"
TABLES=("a","b")
QIDS=(1,2,3,7,10,13)
ARMS=(("s0p0",0,0),("s0p1",0,1),("s1p0",1,0),("s1p1",1,1))
N=5

def sql(query,qid=None,fmt=None):return P.sql(query,qid=qid,fmt=fmt)
def dump(file,obj):
 path=R/file;path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def stmt(q,t,u,p):
 return P.qstr(q,t,u,0)+",use_statistics_for_part_pruning="+str(p)
def physical(label):
 s={}
 for t in TABLES:
  parts=sql("SELECT partition,name,path,rows,marks FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  paths="\n".join(x.split("\t")[2] for x in parts.splitlines())+"\n"
  proc=S.run(["docker","exec","-i",S.CT,"bash","-c",
          'while IFS= read -r f; do test -f "$f/statistics.packed" && echo 1 || echo 0; done'],
          inp=paths.encode(),timeout=200)
  flags=[int(x) for x in proc.stdout.decode().splitlines()]
  rows=[x.split("\t") for x in parts.splitlines()]
  if len(flags)!=len(rows):raise RuntimeError("Statistics record mismatch")
  s[t]={"parts":len(rows),"stat_parts":sum(flags),"rows":sum(int(x[3]) for x in rows),
        "inventory_sha":hashlib.sha256(parts.encode()).hexdigest(),"fingerprint":P.fp(t)}
 dump(label+"/physical.json",s)
 print("C43D_STATE",label,json.dumps(s),flush=True)
 return s
def main():
 for ver in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=500)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  settings=sql("SELECT name,value FROM system.settings WHERE name IN "
    "('use_statistics','use_statistics_for_part_pruning','use_statistics_for_min_max_aggregation',"
    "'use_statistics_cache','auto_statistics_types') ORDER BY name FORMAT TSV")
  (R/"settings.tsv").write_text(settings)
  if "use_statistics_for_part_pruning" not in settings:
   raise RuntimeError("Missing independent part-pruning control on this release")
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  P.create("raw",cols)
  for t in TABLES:
   sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree "
       "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE,LO_ORDERKEY) "
       "SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
  with native.open("rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
     "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
     stdin=f,check=False,timeout=950)
  if p.returncode:raise RuntimeError(p.stderr.decode(errors="replace")[-900:])
  native.unlink()
  for t in TABLES:
   sql("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw SETTINGS materialize_statistics_on_insert=0,"
       "max_threads=2,max_insert_threads=1,min_insert_block_size_rows=150000,min_insert_block_size_bytes=1")
  pre=physical("pre")
  if pre["a"]["rows"]!=pre["b"]["rows"]!=6001215 or pre["a"]["stat_parts"]!=0 or pre["b"]["stat_parts"]!=0:
   raise RuntimeError("Unmatched initial states")
  sql("ALTER TABLE ssb.a MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
  post=physical("post")
  if post["a"]["rows"]!=6001215 or post["a"]["stat_parts"]!=post["a"]["parts"] or post["b"]["stat_parts"]!=0:
   raise RuntimeError("Statistics-only mutation did not materialize expected state")
  answers={}
  plans={}
  for q in QIDS:
   expected=None
   for t in TABLES:
    for arm,u,p in ARMS:
     body=stmt(qs[q-1],t,u,p)
     ans=hashlib.sha256(sql(body+" FORMAT TSV").encode()).hexdigest()
     if expected is not None and ans!=expected:raise RuntimeError("Query result mismatch q"+str(q))
     expected=ans
     answers[f"{q}/{t}/{arm}"]=ans
     explained=sql("EXPLAIN indexes=1 "+body)
     plans[f"{q}/{t}/{arm}"]=explained
  dump("answers.json",answers);dump("plans.json",plans)
  records=[]
  for round_id in range(-1,N):
   rng=random.Random(430700+round_id)
   qlist=list(QIDS);rng.shuffle(qlist)
   for q in qlist:
    cells=[(t,arm,u,p) for t in TABLES for arm,u,p in ARMS]
    rng.shuffle(cells)
    for t,arm,u,p in cells:
     qid="c43d_"+str(len(records))
     sql(stmt(qs[q-1],t,u,p),qid=qid,fmt="Null")
     records.append({"round":round_id,"sql":q,"table":t,"arm":arm,"qid":qid,"sequence":len(records)})
   print("C43D_ROUND",round_id,flush=True)
  sql("SYSTEM FLUSH LOGS")
  raw=sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
          "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c43d_') FORMAT JSONEachRow")
  logs={j["query_id"]:j for row in raw.splitlines() if row for j in [json.loads(row)]}
  if len(logs)!=len(records):raise RuntimeError("Missing query results "+str(len(logs))+"/"+str(len(records)))
  for r in records:
   rec=logs[r["qid"]];ev=rec.get("ProfileEvents") or {}
   r.update(ms=rec["query_duration_ms"],read_rows=rec["read_rows"],
           selected_marks=ev.get("SelectedMarks",0),load_us=ev.get("LoadedStatisticsMicroseconds",0),
           opt_us=ev.get("QueryPlanOptimizeMicroseconds",0),file_opens=ev.get("FileOpen",0))
  with (R/"receipts.csv").open("w",newline="") as f:
   writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
  result=[]
  for q in QIDS:
   for t in TABLES:
    for arm,_,_ in ARMS:
     cells=[r for r in records if r["round"]>=0 and r["sql"]==q and r["table"]==t and r["arm"]==arm]
     if len(cells)!=N:raise RuntimeError("Not enough samples")
     result.append({"sql":q,"table":t,"arm":arm,
        **{key:statistics.median(r[key] for r in cells) for key in ("ms","read_rows","selected_marks","load_us","opt_us","file_opens")}})
  dump("medians.json",result)
  check=physical("after")
  if check!=post:raise RuntimeError("Part inventories changed during measurements")
  dump("FINAL_STATUS.json",{"valid":True,"rows":6001215,"qids":QIDS,
     "settings":settings,"repetitions":N,"pre":pre,"post":post,"measurements":len(records),
     "limitations":["Single runner","Data content verified by fingerprints and query answers, not full stream hash",
       "This diagnostic isolates query settings on fixed post-mutation state, not effects of mutation itself"]})
  print("C43D_SUCCESS",flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=100)
if __name__=="__main__":main()
