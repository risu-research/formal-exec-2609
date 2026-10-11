#!/usr/bin/env python3
"""Stage31: fresh insert, DETACH/ATTACH, same-version restart and renewed
insert on ClickHouse 26.8; 734 parts; untouched time control; immutable
part checksums and identical query outputs required for every phase.
"""
import csv,hashlib,json,pathlib,random,statistics
import db_inplace_upgrade_replay as S
S.ROOT=pathlib.Path("s31");S.base.ROOT=S.ROOT
S.base.VERSIONS=[S.NEW,S.NEW]
S.CT="s31_ch";S.VOL="s31_data_volume"
ROOT=S.ROOT
V=S.NEW
QIDS=(2,3,4)
COLS=("query_duration_ms","read_rows","read_bytes")
EV=("LoadedStatisticsMicroseconds","QueryPlanOptimizeMicroseconds","FileOpen","SelectedMarks")
REPS=6
WARM=1
def log(*x):print("S31",*x,flush=True)
def save(name,content):
 path=ROOT/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
def dump(name,thing):save(name,json.dumps(thing,sort_keys=True,indent=2)+"\n")
def q(s,qid=None,fmt=None,timeout=180):return S.sql(S.CT,s,qid=qid,fmt=fmt,timeout=timeout)
def fingerprint(t):return S.fp(t)
def snapshot(phase,tables):
 status={}
 for t in tables:
  inventory=q("SELECT name,partition,rows,marks,bytes_on_disk,path FROM system.parts "
              "WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  lines=[r.split("\t") for r in inventory.splitlines() if r]
  paths="\n".join(r[5] for r in lines)+"\n"
  raw=S.run(["docker","exec","-i",S.CT,"bash","-c",
     "set -e; while IFS= read -r p; do sha256sum \"$p/checksums.txt\"; done"],
     inp=paths.encode(),timeout=200).stdout
  props={"parts":len(lines),"rows":sum(int(x[2]) for x in lines),
     "marks":sum(int(x[3]) for x in lines),
     "bytes":sum(int(x[4]) for x in lines),
     "inventory_sha256":hashlib.sha256(inventory.encode()).hexdigest(),
     "part_checksum_sha256":hashlib.sha256(raw).hexdigest(),
     "fingerprint":fingerprint(t)}
  status[t]=props
  save(f"{phase}/parts_{t}.tsv",inventory)
  save(f"{phase}/checksum_lines_{t}.txt",raw.decode())
  save(f"{phase}/create_{t}.sql",q("SHOW CREATE TABLE ssb."+t))
  save(f"{phase}/statistics_columns_{t}.tsv",
       q("SELECT name,type,statistics FROM system.columns WHERE database='ssb' "
         "AND table='"+t+"' ORDER BY name FORMAT TSV"))
  # Inspect actual part file basenames in sampled parts, not just SQL metadata.
  for k,x in enumerate(lines[:2]):
   path=x[5]
   filelist=S.run(["docker","exec",S.CT,"find",path,"-maxdepth","1","-type","f","-printf","%f %s\n"],
       timeout=35).stdout.decode(errors="replace")
   save(f"{phase}/disk_files_{t}_{k}.txt",filelist)
 dump(phase+"/physical.json",status)
 log("PHYSICAL",phase,json.dumps({t:status[t]["parts"] for t in tables}))
 return status
def unchanged(old,new,tables,label):
 for t in tables:
  if old[t]!=new[t]:raise RuntimeError("Physical files changed after "+label+" "+t+
         " old="+json.dumps(old[t])+" new="+json.dumps(new[t]))
 log("UNCHANGED",label,"+".join(tables))
def create(t,cols):
 q(f"CREATE TABLE ssb.{t} ({cols}) ENGINE=MergeTree PARTITION BY tuple() "
   "ORDER BY (LO_ORDERDATE,LO_ORDERKEY) SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
 q("SYSTEM STOP MERGES ssb."+t)
def insert(t,src="raw"):
 q(f"INSERT INTO ssb.{t} SELECT * FROM ssb.{src} SETTINGS "
   "materialize_statistics_on_insert=1,max_threads=1,max_insert_threads=1,"
   "min_insert_block_size_rows=750152,min_insert_block_size_bytes=1,max_memory_usage=9000000000",timeout=650)
 n=q("SELECT count() FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' FORMAT TSV").strip()
 log("INSERT",t,"parts="+n)
 if n!="734":raise RuntimeError("Actual inserted parts != 734: "+t+" "+n)
def qs(sql,t,u):
 assert "FROM lineorder_flat" in sql
 return sql.replace("FROM lineorder_flat","FROM "+t)+(
  f" SETTINGS use_statistics={u},optimize_move_to_prewhere=1,"
  "max_threads=2,use_query_condition_cache=0,max_memory_usage=9000000000")
def check_answers(phase,sqls,tables,known):
 all_hashes={}
 for qi in QIDS:
  for t in tables:
   for u in (0,1):
    h=hashlib.sha256(q(qs(sqls[qi-1],t,u)+" FORMAT TSV").encode()).hexdigest()
    all_hashes[f"q{qi}_{t}_u{u}"]=h
    if qi in known and h!=known[qi]:raise RuntimeError("Answer changed "+phase+str(qi))
  values={all_hashes[f"q{qi}_{t}_u{u}"] for t in tables for u in (0,1)}
  if len(values)!=1:raise RuntimeError("Result mismatch within phase "+phase+" q"+str(qi))
  known[qi]=next(iter(values))
 dump(f"{phase}/answer_hashes.json",all_hashes)
def plans(phase,sqls,tables):
 for qi in QIDS:
  for t in tables:
   query=qs(sqls[qi-1],t,1)
   for p,label in (("EXPLAIN PLAN ","plan"),("EXPLAIN indexes=1 ","indexes")):
    save(f"{phase}/plans/q{qi}_{t}_{label}.txt",q(p+query))
def test(phase,sqls,tables,summary):
 rows=[]
 for r in range(-WARM,REPS):
  cells=[(t,qi,u) for t in tables for qi in QIDS for u in (0,1)]
  random.Random(310000+r).shuffle(cells)
  for i,(t,qi,u) in enumerate(cells):
   qid=f"s31_{phase}_{t}_q{qi}_u{u}_r{r}_n{i}"
   q(qs(sqls[qi-1],t,u),qid=qid,fmt="Null",timeout=180)
   rows.append({"phase":phase,"table":t,"q":qi,"u":u,"round":r,
       "warmup":int(r<0),"query_id":qid})
  log("ROUND",phase,r)
 q("SYSTEM FLUSH LOGS")
 raw=q("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
    "FROM system.query_log WHERE type='QueryFinish' AND "
    f"startsWith(query_id,'s31_{phase}_') FORMAT JSONEachRow",timeout=120)
 save(phase+"/query_log.jsonl",raw)
 records={o["query_id"]:o for line in raw.splitlines() if line for o in [json.loads(line)]}
 if len(records)!=len(rows):raise RuntimeError(f"Missing query receipts {phase}: {len(records)}/{len(rows)}")
 for a in rows:
  obj=records[a["query_id"]];e=obj.get("ProfileEvents") or {}
  for key in COLS:a[key]=obj[key]
  for key in EV:a[key]=e.get(key,0)
 with (ROOT/phase/"receipts.csv").open("w",newline="") as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 out=[]
 for t in tables:
  for qi in QIDS:
   for u in (0,1):
    relevant=[x for x in rows if x["table"]==t and x["q"]==qi and x["u"]==u and x["round"]>=0]
    assert len(relevant)==REPS
    obj={"phase":phase,"table":t,"q":qi,"use_statistics":u,"n":REPS}
    for key in COLS+EV:obj[key+"_median"]=statistics.median(float(x[key]) for x in relevant)
    out.append(obj)
 with (ROOT/phase/"medians.csv").open("w",newline="") as f:
  writer=csv.DictWriter(f,fieldnames=list(out[0]));writer.writeheader();writer.writerows(out)
 summary.extend(out)
 for qi in QIDS:
  for t in tables:
   on=next(x for x in out if x["q"]==qi and x["table"]==t and x["use_statistics"]==1)
   off=next(x for x in out if x["q"]==qi and x["table"]==t and x["use_statistics"]==0)
   log("EFFECT",json.dumps({"phase":phase,"table":t,"q":qi,
    "load_ms":on["LoadedStatisticsMicroseconds_median"]/1000,
    "extra_opens":on["FileOpen_median"]-off["FileOpen_median"],
    "on_ms":on["query_duration_ms_median"],"off_ms":off["query_duration_ms_median"],
    "on_rows":on["read_rows_median"],"off_rows":off["read_rows_median"]}))
 return len([x for x in rows if x["round"]>=0])
def runphase(label,tables,sqls,known,summary):
 snap=snapshot(label,tables)
 check_answers(label,sqls,tables,known)
 plans(label,sqls,tables)
 nr=test(label,sqls,tables,summary)
 return snap,nr
def detach(t):
 q("DETACH TABLE ssb."+t+" SYNC",timeout=200)
 q("ATTACH TABLE ssb."+t,timeout=200)
 q("SYSTEM STOP MERGES ssb."+t)
 log("DETACH_ATTACH",t)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 S.run(["docker","pull","clickhouse/clickhouse-server:"+V],timeout=490)
 sqls=S.base.pinned_sources()
 native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 everything=[];known={};n_total=0
 try:
  S.start(V)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw","touch","untouched"):create(t,cols)
  with open(native,"rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
      "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
      stdin=f,check=False,timeout=950)
  if p.returncode:raise RuntimeError("Failed load "+p.stderr.decode(errors="replace")[-1200:])
  native.unlink()
  if int(q("SELECT count() FROM ssb.raw FORMAT TSV").strip())!=6001215:raise RuntimeError("Bad source")
  insert("touch");insert("untouched")
  a,n=runphase("baseline",("touch","untouched"),sqls,known,everything);n_total+=n
  if a["touch"]["fingerprint"]!=a["untouched"]["fingerprint"]:raise RuntimeError("Datasets differ")
  detach("touch")
  b,n=runphase("reattached",("touch","untouched"),sqls,known,everything);n_total+=n
  unchanged(a,b,("touch","untouched"),"reattached")
  # Use the same Docker volume and SAME 26.8 binary; imported Stage30
  # restart helper refers to unrelated Stage30 table names, so restart here.
  S.run(["docker","stop","-t","90",S.CT],timeout=140)
  S.run(["docker","rm",S.CT],timeout=50)
  S.start(V)
  for table in ("raw","touch","untouched"):
   q("SYSTEM STOP MERGES ssb."+table)
  log("RESTART_26_8_SAME_VERSION")
  c,n=runphase("restarted",("touch","untouched"),sqls,known,everything);n_total+=n
  unchanged(a,c,("touch","untouched"),"restarted")
  create("post",cols);insert("post")
  d,n=runphase("post_insert",("touch","untouched","post"),sqls,known,everything);n_total+=n
  unchanged(a,d,("touch","untouched"),"post_insert")
  detach("post")
  e,n=runphase("post_reattached",("touch","untouched","post"),sqls,known,everything);n_total+=n
  unchanged(d,e,("touch","untouched","post"),"post_reattached")
  with (ROOT/"medians_all.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(everything[0]));w.writeheader();w.writerows(everything)
  summary={"valid":True,"version":V,"physical_identity":True,"all_query_answers_match":True,
      "tested_parts":{"touch":a["touch"]["parts"],"untouched":a["untouched"]["parts"],"post":d["post"]["parts"]},
      "measured_queries":n_total,"warmups":int(n_total*WARM/REPS),
      "phases":["baseline","reattached","restarted","post_insert","post_reattached"]}
  dump("FINAL_STATUS.json",summary)
  log("FINAL",json.dumps(summary))
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=90)
if __name__=="__main__":main()
