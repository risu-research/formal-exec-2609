#!/usr/bin/env python3
"""Stage32: 13-query full-factorial upgrade gate over cache and table lifecycle.
Single runner: 26.7 fresh -> same-version DETACH/ATTACH -> in-place 26.8
-> fresh 26.8 insert -> DETACH/ATTACH. Existing Stage25 thresholds unchanged.
"""
import csv,json,pathlib,statistics
from collections import defaultdict
import db_statistics_cache_discriminator as B

S=B.S;L=B.L
ROOT=pathlib.Path("s32")
S.ROOT=ROOT;S.base.ROOT=ROOT;S.base.VERSIONS=[S.OLD,S.OLD]
S.VOL="s32_full_factorial";S.CT="s32_clickhouse"
L.ROOT=ROOT;B.ROOT=ROOT
B.QIDS=tuple(range(1,14));B.REPS=4;B.WARM=1
SCENARIOS=S.SCENARIOS
STAGES={"old_fresh":("old_m0","old_m1"),
        "old_reattached":("old_m0","old_m1"),
        "new_inherited":("old_m0","old_m1"),
        "new_fresh":("new_m0","new_m1"),
        "new_reattached":("new_m0","new_m1")}
def log(*x):print("S32",*x,flush=True)
def query(txt,timeout=180):return S.sql(S.CT,txt,timeout=timeout)
def put(t,src,m):
 q=(f"INSERT INTO ssb.{t} SELECT * FROM ssb.{src} SETTINGS "
    f"materialize_statistics_on_insert={m},max_threads=1,max_insert_threads=1,"
    "min_insert_block_size_rows=750152,min_insert_block_size_bytes=1,"
    "max_memory_usage=9000000000")
 query(q,timeout=660)
 parts=query(f"SELECT count(),sum(rows) FROM system.parts WHERE active AND "
            f"database='ssb' AND table='{t}' FORMAT TSV").strip()
 if parts!="734\t6001215":raise RuntimeError(f"Incorrect parts {t} {parts}")
 log("INSERT",t,m,parts)
def integrity(phase,tables,expected=None):
 x=L.snapshot(phase,tables)
 if any(v["parts"]!=734 or v["rows"]!=6001215 for v in x.values()):
  raise RuntimeError("734-part gradient violated in "+phase)
 if len({v["fingerprint"] for v in x.values()})!=1:
  raise RuntimeError("Data fingerprint mismatch "+phase)
 if expected is not None:L.unchanged(expected,x,tables,phase)
 return x
def stage(label,tables,sqls,known,allmed,expected=None):
 x=integrity(label,tables,expected)
 B.verify(label,sqls,tables,known)
 n=B.measure(label,sqls,tables,allmed)
 log("STAGE",label,"measured",n)
 return x,n
def family(q):return "Q1" if q<=3 else "Q2" if q<=6 else "Q3" if q<=10 else "Q4"
def weighted(rows,weights):
 by=defaultdict(list)
 for row in rows:by[family(row["q"])].append(row["query_duration_ms_median"])
 return sum(w*statistics.mean(by[f]) for f,w in weights.items())
def comparison_rows(allmed):
 idx={(x["phase"],x["table"],x["q"],x["case"]):x for x in allmed}
 # Controlled contrasts: Original Stage25 materialization-policy mismatch;
 # matched materialization/cache; inherited/fresh and detach alternatives.
 trials=(
  ("stage25_like_fresh_cache_on","old_fresh","old_m0","new_fresh","new_m1","stats_cache_on"),
  ("stage25_like_fresh_cache_off","old_fresh","old_m0","new_fresh","new_m1","stats_cache_off"),
  ("materialization_matched_fresh_cache_on","old_fresh","old_m1","new_fresh","new_m1","stats_cache_on"),
  ("materialization_matched_fresh_cache_off","old_fresh","old_m1","new_fresh","new_m1","stats_cache_off"),
  ("nonmaterialized_both_cache_on","old_fresh","old_m0","new_fresh","new_m0","stats_cache_on"),
  ("nonmaterialized_both_cache_off","old_fresh","old_m0","new_fresh","new_m0","stats_cache_off"),
  ("matched_use_statistics_off","old_fresh","old_m1","new_fresh","new_m1","stats_off"),
  ("inplace_old_fresh_to_26_8_cache_on","old_fresh","old_m1","new_inherited","old_m1","stats_cache_on"),
  ("inplace_old_fresh_to_26_8_cache_off","old_fresh","old_m1","new_inherited","old_m1","stats_cache_off"),
  ("inplace_old_reattached_to_26_8_cache_on","old_reattached","old_m1","new_inherited","old_m1","stats_cache_on"),
  ("inplace_old_reattached_to_26_8_cache_off","old_reattached","old_m1","new_inherited","old_m1","stats_cache_off"),
  ("matched_both_reattached_cache_on","old_reattached","old_m1","new_reattached","new_m1","stats_cache_on"),
  ("matched_both_reattached_cache_off","old_reattached","old_m1","new_reattached","new_m1","stats_cache_off"),
  ("within26_7_detach_cache_on","old_fresh","old_m1","old_reattached","old_m1","stats_cache_on"),
  ("within26_7_detach_cache_off","old_fresh","old_m1","old_reattached","old_m1","stats_cache_off"),
  ("within26_8_detach_cache_on","new_fresh","new_m1","new_reattached","new_m1","stats_cache_on"),
  ("within26_8_detach_cache_off","new_fresh","new_m1","new_reattached","new_m1","stats_cache_off")
 )
 rows=[];qrows=[]
 for name,prev,pt,nextphase,nt,p in trials:
  a=[idx[(prev,pt,q,p)] for q in B.QIDS]
  b=[idx[(nextphase,nt,q,p)] for q in B.QIDS]
  slow=0
  for j,qi in enumerate(B.QIDS):
   y=b[j]["query_duration_ms_median"];x=a[j]["query_duration_ms_median"]
   r=y/max(x,1)
   if r>=1.5:slow+=1
   qrows.append({"comparison":name,"q":qi,"ratio":r,"old_ms":x,"new_ms":y,
     "old_read_rows":a[j]["read_rows_median"],"new_read_rows":b[j]["read_rows_median"]})
  for scenario,weights in SCENARIOS.items():
   x=weighted(a,weights);y=weighted(b,weights)
   ratio=y/x
   gate="BLOCK" if slow>=2 or ratio>1.10 else "APPROVE"
   z={"comparison":name,"scenario":scenario,"old_ms":x,"new_ms":y,
      "ratio":ratio,"q_ge_1_5x":slow,"gate":gate}
   rows.append(z)
   if scenario=="equal_per_query":log("GATE",json.dumps(z))
 for name,data in (("stage25_gate_matrix.csv",rows),("per_query_ratios.csv",qrows)):
  with (ROOT/name).open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
 return rows
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for ver in (S.OLD,S.NEW):
  S.run(["docker","pull","clickhouse/clickhouse-server:"+ver],timeout=490)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 allmed=[];known={};total=0
 try:
  S.start(S.OLD)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  opts=query("SELECT name,value FROM system.settings WHERE name IN "
       "('use_statistics','use_statistics_cache','materialize_statistics_on_insert') "
       "ORDER BY name FORMAT TSV")
  L.save("old_settings.tsv",opts)
  if "use_statistics_cache\t" not in opts:raise RuntimeError("26.7 cache flag absent")
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  for t in ("raw","old_m0","old_m1"):L.create(t,cols)
  with open(native,"rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
       "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],
       stdin=f,check=False,timeout=960)
  if p.returncode:raise RuntimeError("Native load failed "+p.stderr.decode(errors="replace")[-1300:])
  native.unlink()
  for m in (0,1):put(f"old_m{m}","raw",m)
  a,n=stage("old_fresh",STAGES["old_fresh"],qs,known,allmed);total+=n
  for t in STAGES["old_fresh"]:L.detach(t)
  _,n=stage("old_reattached",STAGES["old_reattached"],qs,known,allmed,a);total+=n
  S.run(["docker","stop","-t","90",S.CT],timeout=140)
  S.run(["docker","rm",S.CT],timeout=60)
  S.start(S.NEW)
  for t in ("raw","old_m0","old_m1"):query("SYSTEM STOP MERGES ssb."+t)
  _,n=stage("new_inherited",STAGES["new_inherited"],qs,known,allmed,a);total+=n
  L.save("new_settings.tsv",query("SELECT name,value FROM system.settings WHERE name IN "
    "('use_statistics','use_statistics_cache','materialize_statistics_on_insert') ORDER BY name FORMAT TSV"))
  for t in ("new_m0","new_m1"):L.create(t,cols)
  for m in (0,1):put(f"new_m{m}","raw",m)
  fresh,n=stage("new_fresh",STAGES["new_fresh"],qs,known,allmed);total+=n
  for t in STAGES["new_fresh"]:L.detach(t)
  _,n=stage("new_reattached",STAGES["new_reattached"],qs,known,allmed,fresh);total+=n
  gates=comparison_rows(allmed)
  with (ROOT/"all_medians.csv").open("w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(allmed[0]));w.writeheader();w.writerows(allmed)
  out={"valid":True,"releases":[S.OLD,S.NEW],"measured":total,
       "warmups":total//B.REPS*B.WARM,"query_count":13,"parts":734,
       "repetitions":B.REPS,"same_query_answers":True,
       "checksums_invariant":True,
       "gate_thresholds":{"q_ratio":1.5,"q_count":2,"mean_ratio":1.10},
       "gates":{key:{r["scenario"]:r["gate"] for r in gates if r["comparison"]==key}
        for key in sorted(set(v["comparison"] for v in gates))}}
  B.dump("FINAL_STATUS.json",out)
  log("FINAL",json.dumps({"measured":total,"gates":out["gates"]}))
 finally:
  S.run(["docker","rm","-f",S.CT],check=False,timeout=90)
if __name__=="__main__":main()
