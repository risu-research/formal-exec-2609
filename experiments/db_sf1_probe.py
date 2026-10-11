#!/usr/bin/env python3
"""Pinned ClickBench SSB SF1 comparative profile capture, not SF100 reproduction."""
import csv,hashlib,json,os,pathlib,random,statistics,subprocess,sys,time,urllib.request
COMMIT="1ce27b281d8a73aad75458b0e04a2b77705aac53"
SOURCES={"prepare-data/ssb.sh":"498817696a0ddcd20744a6494610854398db1f03","queries/ssb.sql":"243bc9feba43359d450e1f9db310338f27fc6175","create/schema/lineorder_flat.columns":"0c55a91461af95a8538533984e07afdd0d752c4d"}
VERSIONS=["26.7.1.1315","26.8.1.2041"]
QIDS=[1,2,3,4,5,6,7,8]
SETTINGS={"default":"","cache_off":"use_query_condition_cache=0","prewhere_off":"optimize_move_to_prewhere=0","skipindex_off":"use_skip_indexes_on_data_read=0","compat_26_7":"compatibility='26.7'","single_thread":"max_threads=1"}
ROOT=pathlib.Path("s22").resolve()
def run(cmd,stdin=None,check=True,timeout=600):
 p=subprocess.run(cmd,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if check and p.returncode:raise RuntimeError(f"FAIL {cmd[:8]} rc={p.returncode}: {p.stderr.decode(errors='replace')[-2000:]}")
 return p
def digest(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for chunk in iter(lambda:f.read(1<<20),b""):h.update(chunk)
 return h.hexdigest()
def pinned_sources():
 for rel,sha in SOURCES.items():
  target=ROOT/"official"/rel
  target.parent.mkdir(parents=True,exist_ok=True)
  with urllib.request.urlopen(f"https://raw.githubusercontent.com/ClickHouse/ClickBench/{COMMIT}/versions/{rel}",timeout=90) as r: b=r.read()
  actual=hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
  if actual!=sha:raise RuntimeError("Pinned source hash mismatch "+rel)
  target.write_bytes(b)
 qs=[s.strip().rstrip(";") for s in (ROOT/"official/queries/ssb.sql").read_text().splitlines() if s.strip()]
 assert len(qs)==13
 print("PINNED_SOURCES_OK",flush=True)
 return qs
def make_data():
 # Source preparation uses the EXACT original ClickBench script and SF1 endpoint.
 run(["docker","create","--name","s22_extract","--entrypoint","/bin/true","clickhouse/clickhouse-server:"+VERSIONS[0]])
 try:run(["docker","cp","s22_extract:/usr/bin/clickhouse",str(ROOT/"clickhouse")])
 finally:run(["docker","rm","-f","s22_extract"],check=False)
 os.chmod(ROOT/"clickhouse",0o755)
 (ROOT/"official/prepare-data/data").mkdir(parents=True,exist_ok=True)
 env=dict(os.environ,CLICKHOUSE=str(ROOT/"clickhouse"),SSB_SCALE="1")
 proc=subprocess.run(["bash",str(ROOT/"official/prepare-data/ssb.sh")],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1400)
 (ROOT/"data-prep.stderr.txt").write_bytes(proc.stderr)
 if proc.returncode:raise RuntimeError("Official SF1 builder failed: "+proc.stderr.decode(errors="replace")[-3500:])
 source=ROOT/"official/prepare-data/data/ssb.native.zst"
 if not source.exists() or not source.stat().st_size:raise RuntimeError("No Native source generated")
 target=ROOT/"ssb.native"
 with open(target,"wb") as out:r=subprocess.run(["zstd","-dc",str(source)],stdout=out,stderr=subprocess.PIPE,timeout=350)
 if r.returncode:raise RuntimeError(r.stderr.decode(errors="replace")[-1200:])
 info={"source_sha256":digest(target),"native_bytes":target.stat().st_size,"sf":1,"clickbench_commit":COMMIT}
 (ROOT/"SOURCE_IDENTITY.json").write_text(json.dumps(info,indent=2)+"\n")
 print("SSB_NATIVE_GENERATED",json.dumps(info),flush=True)
 source.unlink()
 return target
def client(ct,sql,qid=None,fmt=None,check=True,timeout=400):
 argv=["docker","exec","-i",ct,"clickhouse-client","--database","ssb"]
 if qid:argv+=["--query_id",qid]
 if fmt:argv+=["--format",fmt]
 return run(argv+["--query",sql],check=check,timeout=timeout)
def save(ct,folder,name,sql):
 p=client(ct,sql,check=False)
 (folder/name).write_bytes(p.stdout+(b"\nERROR: "+p.stderr if p.returncode else b""))
 return p
def compare_version(ver,native,qs):
 folder=ROOT/ver;folder.mkdir(parents=True,exist_ok=True)
 ct="s22_"+ver.replace(".","_")
 run(["docker","rm","-f",ct],check=False)
 run(["docker","run","-d","--name",ct,"--ulimit","nofile=262144:262144","clickhouse/clickhouse-server:"+ver])
 rows=[]
 try:
  for i in range(100):
   p=run(["docker","exec",ct,"clickhouse-client","--query","SELECT 1"],check=False)
   if p.returncode==0:break
   time.sleep(1)
  else:raise RuntimeError("Server startup failed "+ver)
  print("SERVER_READY",ver,flush=True)
  run(["docker","exec",ct,"clickhouse-client","--query","CREATE DATABASE IF NOT EXISTS ssb"])
  cols=(ROOT/"official/create/schema/lineorder_flat.columns").read_text()
  ddl=f"CREATE TABLE ssb.lineorder_flat ({cols}) ENGINE=MergeTree PARTITION BY toYYYYMM(LO_ORDERDATE) ORDER BY (LO_ORDERDATE, LO_ORDERKEY)"
  (folder/"ddl.sql").write_text(ddl+"\n")
  client(ct,ddl)
  with open(native,"rb") as f: p=run(["docker","exec","-i",ct,"clickhouse-client","--query","INSERT INTO ssb.lineorder_flat FORMAT Native"],stdin=f,timeout=1100)
  print("INSERT_OK",ver,flush=True)
  checks={
   "fingerprint.tsv":"SELECT count(),sum(LO_REVENUE),sum(LO_EXTENDEDPRICE),min(LO_ORDERDATE),max(LO_ORDERDATE),sum(cityHash64(LO_ORDERKEY,LO_LINENUMBER,LO_REVENUE,F_YEAR)) FROM ssb.lineorder_flat FORMAT TSV",
   "parts.tsv":"SELECT partition,count(),sum(rows),sum(bytes_on_disk),sum(marks) FROM system.parts WHERE active AND database='ssb' AND table='lineorder_flat' GROUP BY partition ORDER BY partition FORMAT TSV",
   "settings.tsv":"SELECT name,value,changed FROM system.settings ORDER BY name FORMAT TSVWithNames",
   "create.txt":"SHOW CREATE TABLE ssb.lineorder_flat"
  }
  for file,sql in checks.items():
   p=save(ct,folder,file,sql)
   if p.returncode:raise RuntimeError("Diagnostic failed: "+file+" "+p.stderr.decode(errors="replace")[-300:])
  for q in QIDS:
   s=qs[q-1]
   save(ct,folder,f"q{q:02d}_indexes.txt","EXPLAIN indexes=1 "+s)
   save(ct,folder,f"q{q:02d}_pipeline.txt","EXPLAIN PIPELINE "+s)
   p=save(ct,folder,f"q{q:02d}_answer.tsv",s+" FORMAT TSV")
   if p.returncode:raise RuntimeError(f"Result missing ver={ver} q={q}: {p.stderr.decode(errors='replace')[-700:]}")
  enabled={"default":""}
  if ver==VERSIONS[1]:
   for name,setting in SETTINGS.items():
    if not setting:continue
    p=client(ct,"SELECT 1 SETTINGS "+setting,check=False)
    if p.returncode==0:enabled[name]=setting
    else:(folder/(name+"_unsupported.txt")).write_bytes(p.stderr)
  (folder/"enabled_settings.json").write_text(json.dumps(enabled,indent=2))
  tasks=[(q,"default",r) for r in range(4) for q in QIDS]
  if ver==VERSIONS[1]:
   rng=random.Random(220826)
   for r in range(3):
    block=[(q,name,r+4) for q in QIDS for name in enabled]
    rng.shuffle(block);tasks.extend(block)
  for i,(q,name,r) in enumerate(tasks):
   setting=enabled[name]
   s=qs[q-1]+(" SETTINGS "+setting if setting else "")
   qid=f"s22_{ver.replace('.','_')}_q{q:02d}_{name}_r{r}_i{i}"
   t=time.monotonic()
   p=client(ct,s,qid=qid,fmt="Null",check=False,timeout=250)
   rows.append({"version":ver,"q":q,"treatment":name,"repeat":r,"query_id":qid,"elapsed_client_ms":round(1000*(time.monotonic()-t),2),"exit_code":p.returncode,"error":p.stderr.decode(errors="replace")[-350:]})
   if p.returncode:print("QUERY_FAILED",ver,q,name,p.stderr.decode(errors="replace")[-250:],flush=True)
  with open(folder/"runs.csv","w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
  client(ct,"SYSTEM FLUSH LOGS",check=False)
  sql="SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'s22_') ORDER BY query_id FORMAT JSONEachRow"
  p=save(ct,folder,"query_log.jsonl",sql)
  profiles={}
  for line in p.stdout.decode(errors="replace").splitlines():
   try:
    z=json.loads(line);profiles[z["query_id"]]=z
   except (ValueError,KeyError):pass
  for x in rows:
   z=profiles.get(x["query_id"],{})
   ev=z.get("ProfileEvents") or {}
   x.update(server_ms=z.get("query_duration_ms"),read_rows=z.get("read_rows"),read_bytes=z.get("read_bytes"),SelectedMarks=ev.get("SelectedMarks"),SelectedRows=ev.get("SelectedRows"))
  with open(folder/"joined.csv","w",newline="") as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
  for q in QIDS:
   seq=[x for x in rows if x["q"]==q and x["treatment"]=="default" and x["repeat"]<4 and x["server_ms"] is not None]
   if seq:print(f"BASELINE {ver} q={q} n={len(seq)} med_server_ms={statistics.median(x['server_ms'] for x in seq)} med_read_rows={statistics.median(x['read_rows'] for x in seq)} med_selected_marks={statistics.median((x['SelectedMarks'] or 0) for x in seq)}",flush=True)
  # Print per-intervention sample medians for remote audit even when artifact download is unavailable.
  if ver==VERSIONS[1]:
   for q in QIDS:
    for name in enabled:
     seq=[x for x in rows if x["q"]==q and x["treatment"]==name and x["repeat"]>=4 and x["server_ms"] is not None]
     if seq:print(f"INTERVENTION {ver} q={q} type={name} n={len(seq)} med_server_ms={statistics.median(x['server_ms'] for x in seq)} med_read_rows={statistics.median(x['read_rows'] for x in seq)} med_selected_marks={statistics.median((x['SelectedMarks'] or 0) for x in seq)}",flush=True)
  print("VERSION_COMPLETE",ver,"runs",len(rows),"profiles",len(profiles),flush=True)
  return {"fingerprint":(folder/"fingerprint.tsv").read_text().strip(),"runs":len(rows),"profiles":len(profiles),"failures":sum(x["exit_code"]!=0 for x in rows)}
 finally:run(["docker","rm","-f",ct],check=False)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 for v in VERSIONS:run(["docker","pull","clickhouse/clickhouse-server:"+v])
 sqls=pinned_sources();native=make_data()
 outcomes={v:compare_version(v,native,sqls) for v in VERSIONS}
 mismatches=[q for q in QIDS if (ROOT/VERSIONS[0]/f"q{q:02d}_answer.tsv").read_bytes()!=(ROOT/VERSIONS[1]/f"q{q:02d}_answer.tsv").read_bytes()]
 outcomes["same_fingerprint"]=outcomes[VERSIONS[0]]["fingerprint"]==outcomes[VERSIONS[1]]["fingerprint"]
 outcomes["answer_mismatches"]=mismatches
 outcomes["valid"]=outcomes["same_fingerprint"] and not mismatches and all(outcomes[v]["failures"]==0 and outcomes[v]["profiles"]>0 for v in VERSIONS)
 (ROOT/"STATUS.json").write_text(json.dumps(outcomes,indent=2)+"\n")
 print("FINAL_STATUS",json.dumps(outcomes),flush=True)
 if not outcomes["valid"]:sys.exit(3)
if __name__=="__main__":main()
