#!/usr/bin/env python3
"""c43m: ordered reversible metadata interventions with untreated control.

Pre-specified phases: absent -> built -> cleared -> rebuilt.
No automatic merges, no OPTIMIZE, and no background worker mutation suspension.
The data-stream content is verified independently of mutation-versioned part names.
"""
import csv,hashlib,json,pathlib,random,statistics,subprocess,time
import db_mixed_history_upgrade as P
S=P.S
R=pathlib.Path("c43m");R.mkdir(exist_ok=True)
P.R=R;P.T=("a","b")
S.ROOT=S.base.ROOT=R;S.base.VERSIONS=[S.OLD,S.OLD]
S.CT="c43_metadata";S.VOL="c43_metadata_volume"
TABLES=("a","b")
ARMS=(("cache_off",1,0),("stats_off",0,1),("cache_on",1,1))
FAM=(range(1,4),range(4,7),range(7,11),range(11,14))
N=5

def sql(s,qid=None,fmt=None):
 return P.sql(s,qid=qid,fmt=fmt)
def dump(path,obj):
 p=R/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")
def snapshot(label):
 raw={}
 for t in TABLES:
  lines=sql("SELECT partition,min_block_number,max_block_number,level,rows,marks,path "
            "FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' "
            "ORDER BY partition,min_block_number,max_block_number,level FORMAT TSV")
  pts=[l.split("\t") for l in lines.splitlines() if l]
  keys=[]
  paths=[]
  for e in pts:
   if len(e)!=7:raise RuntimeError("Unexpected part inventory")
   key=":".join(e[:6])
   keys.append(key)
   paths.append(key+"\t"+e[6])
  script=r'''set -euo pipefail
  while IFS="$(printf '\t')" read -r key directory; do
    find "$directory" -maxdepth 1 -type f \( -name '*.bin' -o -name '*.cmrk2' -o -name '*.mrk2' -o -name '*.mrk3' -o -name '*.cidx' -o -name '*.idx' \) -print0
  done | sort -z | xargs -0 -r sha256sum'''
  proc=S.run(["docker","exec","-i",S.CT,"bash","-c",script],
            inp=("\n".join(paths)+"\n").encode(),timeout=320)
  loc={e[6].rstrip("/"):":".join(e[:6]) for e in pts}
  entries=[]
  for line in proc.stdout.decode().splitlines():
   sha,full=line.split(None,1)
   full=full.strip()
   directory=str(pathlib.PurePosixPath(full).parent)
   if directory not in loc:raise RuntimeError("Unexpected file path "+directory)
   key=loc[directory]
   entries.append(key+"|"+pathlib.PurePosixPath(full).name+"|"+sha)
  manifest="\n".join(sorted(entries))+"\n"
  if not entries:raise RuntimeError("Empty data-file manifest "+t)
  raw[t]={"parts":len(keys),"geometry":hashlib.sha256("\n".join(keys).encode()).hexdigest(),
          "data_manifest_sha256":hashlib.sha256(manifest.encode()).hexdigest(),
          "files":len(manifest.splitlines()),
          "part_keys":keys,
          "fingerprint":P.fp(t),
          "stats":int(sql("SELECT count() FROM system.parts WHERE active AND database='ssb' "
                           "AND table='"+t+"' AND statistics_compressed_bytes>0 FORMAT TSV").strip())
                    if False else None}
  # Check the actual statistics.packed presence; do not infer it from settings.
  inv=sql("SELECT path FROM system.parts WHERE active AND database='ssb' AND table='"+t+"' ORDER BY name FORMAT TSV")
  script2='set -e; while IFS= read -r d; do test -f "$d/statistics.packed" && echo 1 || echo 0; done'
  check=S.run(["docker","exec","-i",S.CT,"bash","-c",script2],inp=inv.encode(),timeout=130)
  flags=[int(k) for k in check.stdout.decode().splitlines()]
  if len(flags)!=len(pts):raise RuntimeError("Statistics inventory missing")
  raw[t]["stats"]=sum(flags)
  raw[t]["rows"]=sum(int(e[4]) for e in pts)
 dump(label+"/physical.json",raw)
 print("C43M_SNAPSHOT",label,{t:(v["parts"],v["stats"],v["data_manifest_sha256"][:12]) for t,v in raw.items()},flush=True)
 return raw

def verify(before,after,label,expected_a_stats):
 for t in TABLES:
  for key in ("geometry","data_manifest_sha256","fingerprint","rows","parts","files"):
   if before[t][key]!=after[t][key]:
    raise RuntimeError(f"Data geometry changed at {label}/{t}/{key}")
 if before["b"]["stats"]!=after["b"]["stats"]:
  raise RuntimeError("Control changed statistics")
 if expected_a_stats=="present" and after["a"]["stats"]==0:
  raise RuntimeError("No statistics created on treatment")
 if expected_a_stats=="absent" and after["a"]["stats"]!=0:
  raise RuntimeError("Statistics remained on treatment")
 print("C43M_INVARIANCE_OK",label,flush=True)

def measure(phase,qs,reps):
 # Verify SQL answers across both physical data paths and 3 settings.
 ans={}
 for qi in range(1,14):
  target=None
  for t in TABLES:
   for arm,u,c in ARMS:
    val=hashlib.sha256(sql(P.qstr(qs[qi-1],t,u,c)+" FORMAT TSV").encode()).hexdigest()
    if target is not None and val!=target:raise RuntimeError(f"Different result {phase}, q{qi}")
    target=val
    ans[f"{qi}/{t}/{arm}"]=val
 dump(phase+"/answers.json",ans)
 receipts=[]
 for rnd in range(-1,reps):
  rand=random.Random(430100+rnd)
  queries=list(range(1,14));rand.shuffle(queries)
  for qi in queries:
   combos=list(ARMS);rand.shuffle(combos)
   for k,(arm,u,c) in enumerate(combos):
    order=TABLES if (qi+k+rnd)%2==0 else TABLES[::-1]
    for t in order:
     id=f"c43m_{phase}_{len(receipts)}"
     sql(P.qstr(qs[qi-1],t,u,c),qid=id,fmt="Null")
     receipts.append({"phase":phase,"round":rnd,"q":qi,"table":t,
                      "arm":arm,"qid":id,"order":len(receipts)})
  print("C43M_ROUND",phase,rnd,flush=True)
 sql("SYSTEM FLUSH LOGS")
 raw=sql("SELECT query_id,query_duration_ms,read_rows,read_bytes,ProfileEvents "
         "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'c43m_"+phase+"_') FORMAT JSONEachRow")
 look={v["query_id"]:v for ln in raw.splitlines() if ln for v in [json.loads(ln)]}
 if len(look)!=len(receipts):raise RuntimeError("Incomplete receipts "+phase)
 for r in receipts:
  v=look[r["qid"]];ev=v.get("ProfileEvents") or {}
  r.update(ms=v["query_duration_ms"],read_rows=v["read_rows"],
           read_bytes=v["read_bytes"],
           stat_us=ev.get("LoadedStatisticsMicroseconds",0),
           opt_us=ev.get("QueryPlanOptimizeMicroseconds",0),
           file_open=ev.get("FileOpen",0))
 with (R/phase/"receipts.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(receipts[0]));w.writeheader();w.writerows(receipts)
 valid=[r for r in receipts if r["round"]>=0]
 means=[]
 for t in TABLES:
  for arm,_,_ in ARMS:
   perq={}
   for qi in range(1,14):
    y=[x for x in valid if (x["table"],x["arm"],x["q"])==(t,arm,qi)]
    assert len(y)==reps
    perq[qi]={k:statistics.median(z[k] for z in y) for k in ("ms","stat_us","opt_us","read_rows","read_bytes")}
   m=lambda k:statistics.mean(statistics.mean(perq[qi][k] for qi in fam) for fam in FAM)
   means.append({"phase":phase,"table":t,"arm":arm,
                 "ms":m("ms"),"loaded_ms":m("stat_us")/1000,
                 "optimize_ms":m("opt_us")/1000,"per_sql":perq})
 dump(phase+"/means.json",means)
 for arm,_,_ in ARMS:
  a=next(z for z in means if z["table"]=="a" and z["arm"]==arm)
  b=next(z for z in means if z["table"]=="b" and z["arm"]==arm)
  print("C43M_COMPARISON",phase,arm,
        json.dumps({"ratio":a["ms"]/b["ms"],"ms_gap":a["ms"]-b["ms"],
                    "load_gap":a["loaded_ms"]-b["loaded_ms"],
                    "opt_gap":a["optimize_ms"]-b["optimize_ms"]}),flush=True)
 return means

def intervene(command,label):
 print("C43M_INTERVENE_START",label,flush=True)
 sql(command)
 remaining=int(sql("SELECT count() FROM system.mutations WHERE database='ssb' AND table='a' AND is_done=0 FORMAT TSV").strip())
 if remaining:raise RuntimeError("Unfinished mutation "+label)
 print("C43M_INTERVENE_COMPLETE",label,flush=True)

def main():
 for version in (S.OLD,S.NEW):S.run(["docker","pull","clickhouse/clickhouse-server:"+version],timeout=500)
 qs=S.base.pinned_sources();native=S.base.make_data()
 S.run(["docker","volume","rm",S.VOL],check=False)
 S.run(["docker","volume","create",S.VOL])
 try:
  S.start(S.NEW)
  S.run(["docker","exec",S.CT,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(R/"official/create/schema/lineorder_flat.columns").read_text()
  P.create("raw",cols)
  for t in TABLES:
   sql("CREATE TABLE ssb."+t+" ("+cols+") ENGINE=MergeTree "
       "PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE,LO_ORDERKEY) "
       "SETTINGS max_bytes_to_merge_at_max_space_in_pool=0")
  with native.open("rb") as f:
   p=S.base.run(["docker","exec","-i",S.CT,"clickhouse-client","--query",
      "INSERT INTO ssb.raw SETTINGS materialize_statistics_on_insert=0 FORMAT Native"],stdin=f,check=False,timeout=950)
  if p.returncode:raise RuntimeError("Native load error "+p.stderr.decode(errors="replace")[-1200:])
  native.unlink()
  for t in TABLES:
   sql("INSERT INTO ssb."+t+" SELECT * FROM ssb.raw SETTINGS "
       "materialize_statistics_on_insert=0,max_threads=2,max_insert_threads=1,"
       "min_insert_block_size_rows=150000,min_insert_block_size_bytes=1")
  initial=snapshot("initial")
  if initial["a"]["stats"]!=0 or initial["b"]["stats"]!=0:raise RuntimeError("Initial statistics must be absent")
  if initial["a"]["fingerprint"]!=initial["b"]["fingerprint"] or initial["a"]["rows"]!=6001215:
   raise RuntimeError("Paired data mismatch")
  results={}
  results["initial"]=measure("initial",qs,N)
  post_meas=snapshot("initial_after")
  verify(initial,post_meas,"initial_unchanged","absent")
  intervene("ALTER TABLE ssb.a MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2","built")
  built=snapshot("built")
  verify(initial,built,"built","present")
  results["built"]=measure("built",qs,N)
  verify(built,snapshot("built_after"),"built_unchanged","present")
  intervene("ALTER TABLE ssb.a CLEAR STATISTICS ALL","cleared")
  cleared=snapshot("cleared")
  verify(initial,cleared,"cleared","absent")
  results["cleared"]=measure("cleared",qs,N)
  verify(cleared,snapshot("cleared_after"),"cleared_unchanged","absent")
  intervene("ALTER TABLE ssb.a MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2","rebuilt")
  rebuilt=snapshot("rebuilt")
  verify(initial,rebuilt,"rebuilt","present")
  results["rebuilt"]=measure("rebuilt",qs,N)
  verify(rebuilt,snapshot("rebuilt_after"),"rebuilt_unchanged","present")
  dump("FINAL_STATUS.json",{"valid":True,"data_file_content_invariant":True,
        "interventions":["initial","built","cleared","rebuilt"],
        "rounds_per_phase":N,"query_count":13,"conditions":list(a for a,_,_ in ARMS),
        "results":results,"initial":initial,"built":built,"cleared":cleared,"rebuilt":rebuilt,
        "limitations":["Part directory identity is rewritten by mutations; the geometry and column stream hashes are unchanged",
          "The cache-on arm is a warm/residency-dependent observation, not a randomized cache intervention",
          "All phases use a single runner; control table tracks time drift",
          "The choice of SQL execution order is deterministic with randomized query and arm orders"]})
  print("C43M_FINAL_SUCCESS",flush=True)
 finally:S.run(["docker","rm","-f",S.CT],check=False,timeout=100)
if __name__=="__main__":main()
