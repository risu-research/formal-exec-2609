#!/usr/bin/env python3
import csv,hashlib,json,pathlib,random,statistics
from collections import defaultdict
import db_inplace_upgrade_replay as S
R=pathlib.Path("s37");R.mkdir(exist_ok=True)
S.ROOT=R;S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="s37_ch";S.VOL="s37_vol"
T=("base0","old25","old50","new25","new50")
Q=range(1,14);REP=3
A=(("cache_on",1,1),("cache_off",1,0),("stats_off",0,1))
def log(*x):print("S37",*x,flush=True)
def sql(s,qid=None,fmt=None):return S.sql(S.CT,s,qid=qid,fmt=fmt,timeout=500)
def save(n,x):
 p=R/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(x)
def dump(n,x):save(n,json.dumps(x,sort_keys=True,indent=2)+"\n")
def create(t,cols):
 sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY) SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
 sql("SYSTEM STOP MERGES ssb."+t)
def insert(t,src,mode=None):
 settings=("materialize_statistics_on_insert="+str(mode)+"," if mode is not None else "")+"max_threads=1,max_insert_threads=1,min_insert_block_size_rows=750152,min_insert_block_size_bytes=1"
 sql("INSERT INTO ssb."+t+" SELECT * FROM ssb."+src+" SETTINGS "+settings)
 log("INSERT",t,src,mode)
def fp(t):
 return sql("SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) FROM ssb."+t+" FORMAT TSV").strip()
def snap(name):
 d={}
 for t in T:
  inventory=sql("SELECT name,rows,marks,path FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  x=[row.split("\t") for row in inventory.splitlines() if row]
  paths="\n".join(i[3] for i in x)+"\n"
  script='set -e; while IFS= read -r p; do sha256sum "$p/checksums.txt"; if test -f "$p/statistics.packed"; then echo STAT; fi; done'
  raw=S.run(["docker","exec","-i",S.CT,"bash","-c",script],inp=paths.encode(),timeout=240).stdout
  d[t]={"parts":len(x),"rows":sum(int(i[1]) for i in x),"marks":sum(int(i[2]) for i in x),
        "inventory":hashlib.sha256(inventory.encode()).hexdigest(),
        "checksums":hashlib.sha256(raw).hexdigest(),"stat_files":raw.count(b"STAT"),"fingerprint":fp(t)}
  save(name+"/inventory_"+t+".tsv",inventory)
 dump(name+"/physical.json",d)
 log("PHYSICAL",name,[(k,v["parts"],v["rows"],v["stat_files"]) for k,v in d.items()])
 return d
def qstr(q,t,u,c):
 return q.replace("FROM lineorder_flat","FROM "+t)+f" SETTINGS use_statistics={u},use_statistics_cache={c},max_threads=2,use_query_condition_cache=0"
def measure(label,tables,queries,truth,med):
 hashes={}
 for qi in Q:
  for t in tables:
   for arm,u,c in A:
    digest=hashlib.sha256(sql(qstr(queries[qi-1],t,u,c)+" FORMAT TSV").encode()).hexdigest()
    ref=(qi,25 if t.endswith("25") else 50 if t.endswith("50") else 0)
    if ref in truth and truth[ref]!=digest:raise RuntimeError("Different query answers "+str(ref))
    truth[ref]=digest;hashes[str(qi)+"_"+t+"_"+arm]=digest
 dump(label+"/answers.json",hashes)
 receipts=[]
 for rnd in range(-1,REP):
  tasks=[(t,qi,a,u,c) for t in tables for qi in Q for a,u,c in A]
  random.Random(370000+rnd).shuffle(tasks)
  for i,(t,qi,a,u,c) in enumerate(tasks):
   qid=f"s37_{label}_{t}_q{qi}_{a}_r{rnd}_i{i}"
   sql(qstr(queries[qi-1],t,u,c),qid=qid,fmt="Null")
   receipts.append({"phase":label,"table":t,"q":qi,"policy":a,"round":rnd,"qid":qid})
  log("ROUND",label,rnd)
 sql("SYSTEM FLUSH LOGS")
 raw=sql("SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s37_"+label+"_') FORMAT JSONEachRow")
 save(label+"/query_log.jsonl",raw)
 j={z["query_id"]:z for line in raw.splitlines() if line for z in [json.loads(line)]}
 if len(j)!=len(receipts):raise RuntimeError("Missing log receipts")
 for rec in receipts:
  v=j[rec["qid"]];evt=v.get("ProfileEvents") or {}
  rec.update(ms=v["query_duration_ms"],rows=v["read_rows"],load_us=evt.get("LoadedStatisticsMicroseconds",0),opens=evt.get("FileOpen",0))
 with (R/label/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(receipts[0]));w.writeheader();w.writerows(receipts)
 for t in tables:
  for qi in Q:
   for arm,_,_ in A:
    v=[x for x in receipts if x["table"]==t and x["q"]==qi and x["policy"]==arm and x["round"]>=0]
    med.append({"phase":label,"table":t,"q":qi,"arm":arm,
       "ms":statistics.median(x["ms"] for x in v),
       "rows":statistics.median(x["rows"] for x in v),
       "load_ms":statistics.median(x["load_us"] for x in v)/1000,
       "opens":statistics.median(x["opens"] for x in v)})
def group(q):return "Q1" if q<=3 else "Q2" if q<=6 else "Q3" if q<=10 else "Q4"
W={"equal":{"Q1":.25,"Q2":.25,"Q3":.25,"Q4":.25},
   "Q1-heavy":{"Q1":.6,"Q2":.15,"Q3":.15,"Q4":.1},
   "Q2-heavy":{"Q1":.1,"Q2":.6,"Q3":.2,"Q4":.1},
   "Q34-heavy":{"Q1":.1,"Q2":.1,"Q3":.45,"Q4":.35}}
def weighted(x,w):
 d=defaultdict(list)
 for z in x:d[group(z["q"])].append(z["ms"])
 return sum(weight*statistics.mean(d[fam]) for fam,weight in w.items())
def gates(med):
 idx={(v["phase"],v["table"],v["q"],v["arm"]):v for v in med}
 scenarios=(("0pct","base0","base0"),("old25","old25","old25"),("new25","old25","new25"),
   ("old50","old50","old50"),("new50","old50","new50"))
 rows=[]
 for name,src,dst in scenarios:
  for arm,_,_ in A:
   a=[idx["before",src,i,arm] for i in Q];b=[idx["after",dst,i,arm] for i in Q]
   n=sum(y["ms"]>=1.5*x["ms"] for x,y in zip(a,b))
   for wk,weight in W.items():
    p=weighted(a,weight);q=weighted(b,weight)
    z={"case":name,"policy":arm,"weights":wk,"ratio":q/p,"old_ms":p,"new_ms":q,
      "q_ge_1_5":n,"gate":"BLOCK" if n>=2 or q/p>1.10 else "APPROVE"}
    rows.append(z)
    if wk=="equal":log("GATE",json.dumps(z))
 with (R/"gate_matrix.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 return rows
def main():
 for v in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=500)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False);S.run(["docker","volume","create",S.VOL])
 med=[];truth={}
 try:
  S.start(S.OLD)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw","delta25","delta50")+T:create(t,cols)
  with open(native,"rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query","INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],stdin=f,timeout=950,check=False)
  if p.returncode:raise RuntimeError("Native upload failed")
  native.unlink()
  for frac in (25,50):
   n=6001215*frac//100
   sql(f"INSERT INTO ssb.delta{frac} SELECT * FROM ssb.raw ORDER BY LO_ORDERKEY,LO_LINENUMBER LIMIT {n} SETTINGS materialize_statistics_on_insert=0,max_threads=2")
   if int(sql("SELECT count() FROM ssb.delta"+str(frac)).strip())!=n:raise RuntimeError("Bad delta")
   log("DELTA",frac,n)
  for t in T:insert(t,"raw",0)
  defaults=sql("SELECT name,value FROM system.settings WHERE name IN ('materialize_statistics_on_insert','materialize_statistics_on_insert_max_table_size','auto_statistics_types') ORDER BY name FORMAT TSV")
  save("defaults_old.tsv",defaults);log("OLD_DEFAULT",json.dumps(defaults))
  insert("old25","delta25");insert("old50","delta50")
  before=snap("before")
  measure("before",("base0","old25","old50"),qs,truth,med)
  S.run(["docker","stop","-t","90",S.CT],timeout=140);S.run(["docker","rm",S.CT])
  S.start(S.NEW)
  for t in ("raw","delta25","delta50")+T:sql("SYSTEM STOP MERGES ssb."+t)
  defaults=sql("SELECT name,value FROM system.settings WHERE name IN ('materialize_statistics_on_insert','materialize_statistics_on_insert_max_table_size','auto_statistics_types') ORDER BY name FORMAT TSV")
  save("defaults_new.tsv",defaults);log("NEW_DEFAULT",json.dumps(defaults))
  inherited=snap("inherited")
  if before!=inherited:raise RuntimeError("Physical part identity changed across version upgrade")
  insert("new25","delta25");insert("new50","delta50")
  after=snap("after")
  for t in ("base0","old25","old50"):
   if before[t]!=after[t]:raise RuntimeError("Inherited parts changed "+t)
  for frac in (25,50):
   if after["old"+str(frac)]["fingerprint"]!=after["new"+str(frac)]["fingerprint"]:
    raise RuntimeError("New data not identical "+str(frac))
  measure("after",T,qs,truth,med)
  result=gates(med)
  with (R/"all_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
  dump("FINAL_STATUS.json",{"valid":True,"measured":(3+5)*13*3*REP,"physical_before":before,
     "physical_after":after,"unmodified_original_parts":True,
     "same_logical_data_for_old_and_new_append":True,"old_defaults":(R/"defaults_old.tsv").read_text(),
     "new_defaults":(R/"defaults_new.tsv").read_text(),"gates":result,
     "limit":"25 and 50 percent new records are replayed deterministic subsets of existing SF1; no natural arrival drift; no background merges."})
  log("FINAL_SUCCESS")
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=90)
if __name__=="__main__":main()
