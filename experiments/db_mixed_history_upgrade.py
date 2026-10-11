#!/usr/bin/env python3
"""Stage37. True mixed-history SF1 replay: disjoint original fact rows (3:1:2
hash cohorts), legacy 26.7 control vs 26.8 upgraded-in-place, original data
parts preserved. Compare 26.8 default new-part statistics to compatible m0.
No artificial row duplication; 0/25/50% new-row fractions; 13 official SQL.
"""
import csv,hashlib,json,pathlib,random,statistics
from collections import defaultdict
import db_inplace_upgrade_replay as S
P=pathlib.Path("s37")
S.ROOT=P;S.base.ROOT=P;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="s37_ch";S.VOL="s37_mixed_history"
N=6001215;REPS=4;QIDS=tuple(range(1,14))
ARMS=(("off",0,1),("cache_on",1,1),("cache_off",1,0))
FP=("SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),"
"sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)),"
"min(LO_ORDERDATE),max(LO_ORDERDATE) FROM ssb.{} FORMAT TSV")
def say(*x):print("S37",*x,flush=True)
def sql(s,qid=None,fmt=None):return S.sql(S.CT,s,qid=qid,fmt=fmt,timeout=750)
def save(n,s):
 p=P/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
def js(n,z):save(n,json.dumps(z,indent=2,sort_keys=True)+"\n")
def predicate(c):
 k="cityHash64(LO_ORDERKEY,LO_LINENUMBER) % 6"
 return (k+" < 3",k+" = 3",k+" >= 4")[c]
def get_setting():
 return sql("SELECT value FROM system.settings WHERE name='materialize_statistics_on_insert' FORMAT TSV").strip()
def create(t,cols):
 sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree PARTITION BY tuple() ORDER BY (LO_ORDERDATE,LO_ORDERKEY) SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
 sql("SYSTEM STOP MERGES ssb."+t)
def phys(tag,tables):
 result={}
 for t in tables:
  data=sql("SELECT name,rows,marks,path FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  lines=[s.split("\t") for s in data.splitlines() if s]
  paths="\n".join(x[-1] for x in lines)+"\n"
  cmd=["docker","exec","-i",S.CT,"bash","-c",
   'set -e; while IFS= read -r p; do sha256sum "$p/checksums.txt"; if [ -f "$p/statistics.packed" ]; then echo stats; else echo none; fi; done']
  raw=S.run(cmd,inp=paths.encode(),timeout=250).stdout.decode()
  res={"parts":len(lines),"rows":sum(int(x[1]) for x in lines),
   "marks":sum(int(x[2]) for x in lines),
   "statistics_packed_parts":raw.count("\nstats\n"),"no_statistics_parts":raw.count("\nnone\n"),
   "part_inventory_sha256":hashlib.sha256(data.encode()).hexdigest(),
   "part_files_sha256":hashlib.sha256(raw.encode()).hexdigest(),
   "fingerprint":sql(FP.format(t)).strip()}
  result[t]=res
  save(tag+"/parts_"+t+".tsv",data)
  save(tag+"/checksums_"+t+".txt",raw)
 js(tag+"/physical.json",result)
 say("STATE",tag,json.dumps({t:{k:v for k,v in result[t].items() if k in ("parts","rows","statistics_packed_parts")} for t in tables}))
 return result
def append(t,c,override=None):
 extra=("" if override is None else "materialize_statistics_on_insert="+str(override)+",")
 sql("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw WHERE "+predicate(c)+
  " SETTINGS "+extra+"max_threads=1,max_insert_threads=1,"
  "min_insert_block_size_rows=750152,min_insert_block_size_bytes=1,max_memory_usage=9000000000")
 say("APPEND",t,c,override)
def qs(q,t,u,c):
 return q.replace("FROM lineorder_flat","FROM "+t)+f" SETTINGS use_statistics={u},use_statistics_cache={c},max_threads=2,optimize_move_to_prewhere=1,use_query_condition_cache=0"
def family(q):return "Q1" if q<=3 else "Q2" if q<=6 else "Q3" if q<=10 else "Q4"
def verify(tag,tables,queries,reference):
 digest={}
 for qi in QIDS:
  hs=[]
  for t in tables:
   for arm,u,c in ARMS:
    h=hashlib.sha256(sql(qs(queries[qi-1],t,u,c)+" FORMAT TSV").encode()).hexdigest()
    hs.append(h);digest[f"q{qi}_{t}_{arm}"]=h
  if len(set(hs))!=1:raise RuntimeError("Unequal SQL result "+tag+" q"+str(qi))
  if qi in reference and reference[qi]!=hs[0]:raise RuntimeError("Cross-release result difference "+tag+" q"+str(qi))
  reference[qi]=hs[0]
 js(tag+"/answer_hashes.json",digest)
def measure(tag,tables,queries,meds):
 items=[]
 for rnd in range(-1,REPS):
  tasks=[(t,q,arm,u,c) for t in tables for q in QIDS for arm,u,c in ARMS]
  random.Random(370000+rnd).shuffle(tasks)
  for j,(t,qi,arm,u,c) in enumerate(tasks):
   qid=f"s37_{tag}_{t}_q{qi}_{arm}_r{rnd}_{j}"
   sql(qs(queries[qi-1],t,u,c),qid=qid,fmt="Null")
   items.append({"phase":tag,"table":t,"q":qi,"arm":arm,"round":rnd,"id":qid})
  say("ROUND",tag,rnd)
 sql("SYSTEM FLUSH LOGS")
 logs=sql("SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s37_"+tag+"_') FORMAT JSONEachRow")
 save(tag+"/query_log.jsonl",logs)
 d={z["query_id"]:z for line in logs.splitlines() if line for z in [json.loads(line)]}
 if len(d)!=len(items):raise RuntimeError("Missing receipts "+tag+" "+str(len(d))+"/"+str(len(items)))
 for x in items:
  z=d[x["id"]];e=z.get("ProfileEvents") or {}
  x.update(ms=z["query_duration_ms"],read_rows=z["read_rows"],stats_us=e.get("LoadedStatisticsMicroseconds",0),file_open=e.get("FileOpen",0))
 with (P/tag/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(items[0]));w.writeheader();w.writerows(items)
 for t in tables:
  for qi in QIDS:
   for arm,u,c in ARMS:
    rows=[x for x in items if x["table"]==t and x["q"]==qi and x["arm"]==arm and x["round"]>=0]
    if len(rows)!=REPS:raise RuntimeError("Wrong repetitions "+tag)
    z={"phase":tag,"table":t,"q":qi,"family":family(qi),"arm":arm}
    for key in ("ms","read_rows","stats_us","file_open"):z[key]=statistics.median(x[key] for x in rows)
    meds.append(z)
 say("MEASURED",tag,len([x for x in items if x["round"]>=0]))
def mean(rows,weights):
 groups=defaultdict(list)
 for r in rows:groups[r["family"]].append(r["ms"])
 return sum(w*statistics.mean(groups[f]) for f,w in weights.items())
def gate(medians):
 d={(x["phase"],x["table"],x["q"],x["arm"]):x for x in medians}
 gates=[];queries=[]
 for share in (0,25,50):
  for target in ("mixed","compat"):
   for arm,u,c in ARMS:
    a=[d[f"old_{share}","control",q,arm] for q in QIDS]
    b=[d[f"new_{share}",target,q,arm] for q in QIDS]
    reg=sum(y["ms"]>=1.5*x["ms"] for x,y in zip(a,b))
    for scenario,w in S.SCENARIOS.items():
     v=mean(a,w);vv=mean(b,w);ratio=vv/v
     gate_row={"new_row_share_pct":share,"comparison":"upgrade_"+target,"arm":arm,
       "scenario":scenario,"old_ms":v,"new_ms":vv,"ratio":ratio,"slow_queries":reg,
       "gate":"BLOCK" if reg>=2 or ratio>1.10 else "APPROVE"}
     gates.append(gate_row)
     if scenario=="equal_per_query":say("GATE",json.dumps(gate_row))
    for qi,x,y in zip(QIDS,a,b):
     queries.append({"share":share,"target":target,"arm":arm,"q":qi,
      "old_ms":x["ms"],"new_ms":y["ms"],"old_rows":x["read_rows"],"new_rows":y["read_rows"]})
 for share in (25,50):
  for arm,u,c in ARMS:
   a=[d[f"new_{share}","compat",q,arm] for q in QIDS]
   b=[d[f"new_{share}","mixed",q,arm] for q in QIDS]
   reg=sum(y["ms"]>=1.5*x["ms"] for x,y in zip(a,b))
   for scenario,w in S.SCENARIOS.items():
    v=mean(a,w);vv=mean(b,w);ratio=vv/v
    gates.append({"new_row_share_pct":share,"comparison":"same_version_mixed_vs_compat",
     "arm":arm,"scenario":scenario,"old_ms":v,"new_ms":vv,"ratio":ratio,
     "slow_queries":reg,"gate":"BLOCK" if reg>=2 or ratio>1.10 else "APPROVE"})
 for filename,rows in (("gates.csv",gates),("perquery.csv",queries)):
  with (P/filename).open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 return gates
def main():
 P.mkdir(exist_ok=True)
 for ver in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=480)
 queries=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False);S.run(["docker","volume","create",S.VOL])
 med=[];oldrefs={}
 try:
  S.start(S.OLD)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  v1=get_setting();save("default_old.txt",v1)
  if v1!="0":raise RuntimeError("Wrong old default "+v1)
  cols=(P/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw","control","mixed","compat"):create(t,cols)
  with open(native,"rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
    stdin=f,check=False,timeout=940)
  if p.returncode:raise RuntimeError("Native failed "+p.stderr.decode(errors="replace")[-800:])
  native.unlink()
  cohortcounts=[int(sql("SELECT count() FROM ssb.raw WHERE "+predicate(c)+" FORMAT TSV").strip()) for c in range(3)]
  if sum(cohortcounts)!=N:raise RuntimeError("Cohort partition incomplete")
  js("cohorts.json",{"sizes":cohortcounts,"predicates":[predicate(c) for c in range(3)]})
  for t in ("control","mixed","compat"):append(t,0)
  before=phys("before_upgrade",("mixed","compat"))
  for share,c in ((0,None),(25,1),(50,2)):
   if c is not None:append("control",c)
   base=phys("old_"+str(share),("control",))
   ref={};verify("old_"+str(share),("control",),queries,ref)
   oldrefs[share]=ref
   measure("old_"+str(share),("control",),queries,med)
  S.run(["docker","stop","-t","90",S.CT],timeout=140)
  S.run(["docker","rm",S.CT],timeout=60)
  S.start(S.NEW)
  for t in ("raw","control","mixed","compat"):sql("SYSTEM STOP MERGES ssb."+t)
  v2=get_setting();save("default_new.txt",v2)
  if v2!="1":raise RuntimeError("Wrong new default "+v2)
  now=phys("after_upgrade",("mixed","compat"))
  if before!=now:raise RuntimeError("Legacy part bytes were modified by upgrade")
  for share,c in ((0,None),(25,1),(50,2)):
   if c is not None:
    append("mixed",c)
    append("compat",c,0)
   p="new_"+str(share)
   status=phys(p,("mixed","compat"))
   if len({x["fingerprint"] for x in status.values()})!=1:
    raise RuntimeError("Logical data mismatch after growth "+p)
   ref=dict(oldrefs[share])
   verify(p,("mixed","compat"),queries,ref)
   measure(p,("mixed","compat"),queries,med)
  gates=gate(med)
  with (P/"all_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
  js("FINAL_STATUS.json",{"valid":True,"releases":[S.OLD,S.NEW],
    "dataset_rows":N,"cohort_counts":cohortcounts,
    "measured":len(med)*REPS,"warmup":len(med),
    "legacy_parts_verified":True,"matched_answers_every_share":True,
    "default_materialization_old":int(v1),"default_materialization_new":int(v2),
    "gates":gates,"limits":"Sequential single-runner, disjoint hash cohorts and SF1 only; no longitudinal customer trace."})
  say("FINAL_SUCCESS",len(med)*REPS)
 finally:S.run(["docker","rm","-f",S.CT],check=False)
if __name__=="__main__":main()
