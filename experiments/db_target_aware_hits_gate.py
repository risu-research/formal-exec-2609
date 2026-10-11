#!/usr/bin/env python3
"""Stage35. Target-aware upgrade certification on independent official Hits.
Same runner, 26.7 -> 26.8 on preserved volume, rebuilt comparators; 21 SQL.
Policy mismatch is a valid BLOCK for a policy rollout, false for a preserve-
state target. Preexisting Stage25 two-1.5x-or-1.10x-average gate unchanged.
"""
import csv,hashlib,json,pathlib,random,statistics,subprocess,urllib.request
import db_clickbench_hits_independent as H
S=H.S
R=pathlib.Path("s35")
H.R=R;S.ROOT=R;S.base.ROOT=R
S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="s35_clickhouse";S.VOL="s35_upgrade_volume"
STATES={"old_fresh":("old_m0","old_m1"),
 "old_attached":("old_m0","old_m1"),
 "new_inherited":("old_m0","old_m1"),
 "new_fresh":("new_m0","new_m1"),
 "new_attached":("new_m0","new_m1")}
ARMS=(("off",0,1),("cache_on",1,1),("cache_off",1,0))
REPS=3
def log(*z):print("S35",*z,flush=True)
def q(s,qid=None,fmt=None):return S.sql(S.CT,s,qid=qid,fmt=fmt,timeout=240)
def store(n,data):
 p=R/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
def js(n,obj):store(n,json.dumps(obj,indent=2,sort_keys=True)+"\n")
def create(t,cols):H.create(t,cols)
def fill(t,m):
 q("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw SETTINGS "
   "materialize_statistics_on_insert="+str(m)+",max_threads=1,"
   "max_insert_threads=1,min_insert_block_size_rows=750152,"
   "min_insert_block_size_bytes=1,max_memory_usage=10000000000")
 n=int(q("SELECT count() FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' FORMAT TSV").strip())
 if n<32:raise RuntimeError("Insufficient fragmentation "+str(n))
 log("INSERT",t,m,n)
def att(t):
 q("DETACH TABLE ssb."+t+" SYNC");q("ATTACH TABLE ssb."+t)
def fingerprint(t):
 return q("SELECT count(),sum(CounterID),sum(UserID),sum(WatchID),min(EventDate),max(EventDate) FROM ssb."+t+" FORMAT TSV")
def physical(phase,tables,prior=None):
 result={}
 for t in tables:
  inv=q("SELECT name,rows,marks,path FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  paths="\n".join(x.split("\t")[-1] for x in inv.splitlines())+"\n"
  cs=S.run(["docker","exec","-i",S.CT,"bash","-c",
    'set -e; while IFS= read -r p; do sha256sum "$p/checksums.txt"; done'],
    inp=paths.encode(),timeout=180).stdout
  result[t]={"parts":len(inv.splitlines()),"parts_sha":hashlib.sha256(inv.encode()).hexdigest(),
             "checksum_sha":hashlib.sha256(cs).hexdigest(),"fingerprint":fingerprint(t).strip()}
  store(phase+"/inventory_"+t+".tsv",inv)
 if len({v["fingerprint"] for v in result.values()})!=1:raise RuntimeError("Logical mismatch "+phase)
 if prior and result!=prior:raise RuntimeError("Physical change "+phase)
 js(phase+"/physical.json",result)
 log("PHYSICAL",phase,json.dumps({t:v["parts"] for t,v in result.items()}))
 return result
def measure(phase,tables,qs,answers):
 for qi in H.INDEX:
  h=[]
  for t in tables:
   for label,u,c in ARMS:
    text=q(H.query(qs[qi-1],t,u,c)+" FORMAT TSV")
    d=hashlib.sha256(text.encode()).hexdigest();h.append(d)
    if qi in answers and answers[qi]!=d:raise RuntimeError("Answer changed q"+str(qi))
  if len(set(h))!=1:raise RuntimeError("Result mismatch q"+str(qi))
  answers[qi]=h[0]
 rows=[]
 for rnd in range(-1,REPS):
  tasks=[(t,qi,lab,u,c) for t in tables for qi in H.INDEX for lab,u,c in ARMS]
  random.Random(350000+rnd).shuffle(tasks)
  for i,(t,qi,lab,u,c) in enumerate(tasks):
   qid="s35_"+phase+"_"+t+"_q"+str(qi)+"_"+lab+"_r"+str(rnd)+"_"+str(i)
   q(H.query(qs[qi-1],t,u,c),qid=qid,fmt="Null")
   rows.append({"phase":phase,"table":t,"q":qi,"arm":lab,"round":rnd,"query_id":qid})
  log("ROUND",phase,rnd)
 q("SYSTEM FLUSH LOGS")
 raw=q("SELECT query_id,query_duration_ms,read_rows,ProfileEvents FROM system.query_log "
  "WHERE type='QueryFinish' AND startsWith(query_id,'s35_"+phase+"_') FORMAT JSONEachRow")
 store(phase+"/query_log.jsonl",raw)
 logrows={j["query_id"]:j for line in raw.splitlines() if line for j in [json.loads(line)]}
 if len(logrows)!=len(rows):raise RuntimeError("Missing query logs")
 for row in rows:
  o=logrows[row["query_id"]];ev=o.get("ProfileEvents") or {}
  row.update(ms=o["query_duration_ms"],read_rows=o["read_rows"],
             load_ms=ev.get("LoadedStatisticsMicroseconds",0)/1000,
             file_open=ev.get("FileOpen",0),selected_marks=ev.get("SelectedMarks",0))
 with (R/phase/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 med=[]
 for t in tables:
  for qi in H.INDEX:
   for lab,u,c in ARMS:
    v=[x for x in rows if x["table"]==t and x["q"]==qi and x["arm"]==lab and x["round"]>=0]
    med.append({"phase":phase,"table":t,"q":qi,"arm":lab,
      "ms":statistics.median(x["ms"] for x in v),
      "load_ms":statistics.median(x["load_ms"] for x in v),
      "read_rows":statistics.median(x["read_rows"] for x in v)})
 with (R/phase/"medians.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(med[0]));w.writeheader();w.writerows(med)
 return med
def gate(rows):
 ix={(x["phase"],x["table"],x["q"],x["arm"]):x for x in rows}
 contrasts=(("preserve_existing","old_attached","old_m1","new_inherited","old_m1"),
  ("rebuild_same_m1","old_fresh","old_m1","new_fresh","new_m1"),
  ("rebuild_same_m0","old_fresh","old_m0","new_fresh","new_m0"),
  ("policy_rollout_m0_to_m1","old_fresh","old_m0","new_fresh","new_m1"),
  ("within_26_8_attach","new_fresh","new_m1","new_attached","new_m1"))
 ans=[]
 for target,op,ot,np,nt in contrasts:
  for arm,u,c in ARMS:
   a=[ix[op,ot,k,arm]["ms"] for k in H.INDEX]
   b=[ix[np,nt,k,arm]["ms"] for k in H.INDEX]
   ratio=statistics.mean(b)/statistics.mean(a)
   slow=sum(y>=1.5*x for x,y in zip(a,b))
   rec={"target":target,"arm":arm,"ratio":ratio,"n_slow":slow,
        "gate":"BLOCK" if slow>=2 or ratio>1.10 else "APPROVE",
        "old_mean_ms":statistics.mean(a),"new_mean_ms":statistics.mean(b)}
   ans.append(rec);log("GATE",json.dumps(rec))
 with (R/"gates.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(ans[0]));w.writeheader();w.writerows(ans)
 return ans
def stage(name,tables,queries,ans,allrows,expected=None):
 snap=physical(name,tables,expected)
 allrows+=measure(name,tables,queries,ans)
 return snap
def main():
 R.mkdir(exist_ok=True)
 for v in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+v],timeout=540)
 queries=H.fetch("versions/queries/hits.sql","b824dbb42b27f50e51f2d4e896aed93dbbfba423").splitlines()
 cols=H.fetch("versions/create/schema/hits.columns","7e0d128d744349d4affefaf326ae3e410adf0000")
 assert len(queries)==43
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 allrows=[];ans={}
 try:
  S.start(S.OLD)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  for t in ("raw","old_m0","old_m1"):create(t,cols)
  sources=[]
  for i in range(4):
   p=R/("part_"+str(i)+".zst")
   url="https://clickhouse-public-datasets.s3.amazonaws.com/hits/native/hits_100m_obfuscated_"+str(i)+".native.zst"
   urllib.request.urlretrieve(url,p)
   sources.append({"shard":i,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
   dec=subprocess.Popen(["zstd","-dc",str(p)],stdout=subprocess.PIPE)
   proc=subprocess.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
    "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],stdin=dec.stdout,timeout=600)
   dec.stdout.close()
   if proc.returncode or dec.wait()!=0:raise RuntimeError("Source inaccessible "+str(i))
   p.unlink();log("SHARD",i)
  js("sources.json",{"commit":"539ebec26a79ef80df8de5115ec927d797a94013","files":sources,"official_queries":H.INDEX})
  for m in (0,1):fill("old_m"+str(m),m)
  old=stage("old_fresh",STATES["old_fresh"],queries,ans,allrows)
  for t in STATES["old_fresh"]:att(t)
  stage("old_attached",STATES["old_attached"],queries,ans,allrows,old)
  S.run(["docker","stop","-t","90",S.CT],timeout=140);S.run(["docker","rm",S.CT])
  S.start(S.NEW)
  for t in ("raw","old_m0","old_m1"):q("SYSTEM STOP MERGES ssb."+t)
  stage("new_inherited",STATES["new_inherited"],queries,ans,allrows,old)
  for t in ("new_m0","new_m1"):create(t,cols)
  for m in (0,1):fill("new_m"+str(m),m)
  fresh=stage("new_fresh",STATES["new_fresh"],queries,ans,allrows)
  for t in STATES["new_fresh"]:att(t)
  stage("new_attached",STATES["new_attached"],queries,ans,allrows,fresh)
  outcome=gate(allrows)
  with (R/"all_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(allrows[0]));w.writeheader();w.writerows(allrows)
  js("FINAL_STATUS.json",{"valid":True,"queries":len(H.INDEX),"source_shards":4,
    "measured":5*2*len(H.INDEX)*3*REPS,"versions":[S.OLD,S.NEW],
    "source_rows":int(q("SELECT count() FROM ssb.raw FORMAT TSV")),
    "checksums_unchanged":True,"answers_unchanged":True,
    "stress_note":"ClickBench production-derived 4 shards; auto-stats on insert is nondefault benchmark setting",
    "gates":outcome})
  log("FINAL",json.dumps({"valid":True,"gates":outcome}))
 finally:S.run(["docker","rm","-f",S.CT],check=False)
if __name__=="__main__":main()
