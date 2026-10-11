#!/usr/bin/env python3
"""ClickBench Hits independent pilot: 4 official Native shards, 15 official SQL.
26.8 freshly materialized stats vs same-table DETACH/ATTACH, cache controls.
Deliberate nondefault statistics stress configuration; not official full-scale.
"""
import csv,hashlib,json,pathlib,random,statistics,subprocess,urllib.request
import db_inplace_upgrade_replay as S
R=pathlib.Path("s33p")
S.ROOT=R
S.base.ROOT=R
S.base.VERSIONS=[S.NEW,S.NEW]
S.VOL="s33p_hits"
S.CT="s33p_server"
COMMIT="539ebec26a79ef80df8de5115ec927d797a94013"
INDEX=(1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,20,21,25,38,39,40)
def sql(q,qid=None,fmt=None):return S.sql(S.CT,q,qid=qid,fmt=fmt,timeout=240)
def fetch(rel,sha):
 b=urllib.request.urlopen("https://raw.githubusercontent.com/ClickHouse/ClickBench/"+COMMIT+"/"+rel,timeout=90).read()
 x=hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
 if x!=sha:raise RuntimeError("Source sha "+rel)
 return b.decode()
def create(t,c):
 sql("CREATE TABLE ssb."+t+" ("+c+") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (CounterID,EventDate,UserID,EventTime,WatchID) SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
 sql("SYSTEM STOP MERGES ssb."+t)
def query(text,t,use,cache):
 return text.replace("FROM hits","FROM "+t)+" SETTINGS use_statistics="+str(use)+",use_statistics_cache="+str(cache)+",max_threads=1,use_query_condition_cache=0"
def run_phase(tag,queries):
 measurements=[]
 for qi in INDEX:
  for use,cache,name in ((0,1,"off"),(1,1,"on"),(1,0,"nocache")):
   stmt=query(queries[qi-1],"test",use,cache)
   for r in range(4):
    qid=f"s33p_{tag}_q{qi}_{name}_r{r}"
    sql(stmt,qid=qid,fmt="Null")
    measurements.append({"phase":tag,"q":qi,"policy":name,"rep":r,"qid":qid})
 sql("SYSTEM FLUSH LOGS")
 raw=sql("SELECT query_id,query_duration_ms,ProfileEvents,read_rows FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s33p_"+tag+"_') FORMAT JSONEachRow")
 lookup={x["query_id"]:x for line in raw.splitlines() if line for x in [json.loads(line)]}
 if len(lookup)!=len(measurements):raise RuntimeError("Query receipts incomplete")
 for x in measurements:
  v=lookup[x["qid"]];ev=v.get("ProfileEvents") or {}
  x["wall_ms"]=v["query_duration_ms"];x["load_us"]=ev.get("LoadedStatisticsMicroseconds",0)
  x["file_open"]=ev.get("FileOpen",0);x["read_rows"]=v["read_rows"]
 (R/(tag+"_receipts.csv")).write_text("")
 with (R/(tag+"_receipts.csv")).open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(measurements[0]));w.writeheader();w.writerows(measurements)
 print("S33P_MEASURED",tag,len(measurements),flush=True)
 return measurements
def main():
 R.mkdir(exist_ok=True)
 S.run(["docker","pull","clickhouse/clickhouse-server:"+S.NEW],timeout=480)
 sqls=fetch("versions/queries/hits.sql","b824dbb42b27f50e51f2d4e896aed93dbbfba423").splitlines()
 cols=fetch("versions/create/schema/hits.columns","7e0d128d744349d4affefaf326ae3e410adf0000")
 if len(sqls)!=43:raise RuntimeError("Query source changed")
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  create("raw",cols);create("test",cols)
  sources=[]
  for i in range(4):
   p=R/("part_"+str(i)+".zst")
   urllib.request.urlretrieve("https://datasets.clickhouse.com/hits/native/hits_100m_obfuscated_"+str(i)+".native.zst",p)
   sources.append({"part":i,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size})
   dec=subprocess.Popen(["zstd","-dc",str(p)],stdout=subprocess.PIPE)
   r=subprocess.run(["docker","exec","-i",S.CT,"clickhouse-client","--query","INSERT INTO ssb.raw FORMAT Native"],stdin=dec.stdout,timeout=600)
   dec.stdout.close()
   if r.returncode or dec.wait()!=0:raise RuntimeError("Source shard failed "+str(i))
   p.unlink()
   print("S33P_SHARD",i,sources[-1]["bytes"],flush=True)
  sql("INSERT INTO ssb.test SELECT * FROM ssb.raw SETTINGS materialize_statistics_on_insert=1,max_threads=1,max_insert_threads=1,min_insert_block_size_rows=750152,min_insert_block_size_bytes=1")
  n=sql("SELECT count(),sum(rows) FROM system.parts WHERE active AND database='ssb' AND table='test' FORMAT TSV").strip()
  print("S33P_PARTS",n,flush=True)
  if int(n.split()[0])<32:raise RuntimeError("Insufficient parts")
  a=run_phase("fresh",sqls)
  before=sql("SELECT name,rows,marks FROM system.parts WHERE active AND database='ssb' AND table='test' ORDER BY name FORMAT TSV")
  sql("DETACH TABLE ssb.test SYNC");sql("ATTACH TABLE ssb.test")
  after=sql("SELECT name,rows,marks FROM system.parts WHERE active AND database='ssb' AND table='test' ORDER BY name FORMAT TSV")
  if before!=after:raise RuntimeError("Physical parts changed")
  b=run_phase("attached",sqls)
  with (R/"medians.csv").open("w",newline="") as f:
   w=csv.writer(f);w.writerow(["phase","q","policy","median_wall_ms","median_stats_load_ms"])
   for phase,items in (("fresh",a),("attached",b)):
    for q in INDEX:
     for policy in ("off","on","nocache"):
      xs=[v for v in items if v["q"]==q and v["policy"]==policy]
      w.writerow([phase,q,policy,statistics.median(x["wall_ms"] for x in xs),
                  statistics.median(x["load_us"] for x in xs)/1000])
  (R/"FINAL_STATUS.json").write_text(json.dumps({"valid":True,"rows":int(n.split()[1]),
   "parts":int(n.split()[0]),"queries":len(INDEX),"query_indices":INDEX,
   "measured":len(a)+len(b),"official_shards":sources,
   "control":"cache on/off and DETACH/ATTACH, same 26.8; nondefault materialized stats"},indent=2))
  print("S33P_SUCCESS",flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False)
if __name__=="__main__":main()
