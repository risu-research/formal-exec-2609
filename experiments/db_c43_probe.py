#!/usr/bin/env python3
"""Small command/physical-invariance probe; not an outcome benchmark."""
import pathlib,subprocess,json,hashlib,time
R=pathlib.Path("c43p");R.mkdir(exist_ok=True)
def run(a,inp=None,check=True,timeout=110):
 p=subprocess.run(a,input=inp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if check and p.returncode:raise RuntimeError(str(a)+" "+p.stderr.decode()[-1600:])
 return p
def q(s,check=True):
 p=run(["docker","exec","c43p","clickhouse-client","--database=default","--query",s],check=check)
 return {"ok":p.returncode==0,"result":p.stdout.decode(errors="replace").strip(),"error":p.stderr.decode(errors="replace")[-700:]}
def sha(t):
 raw=q("SELECT name,path,rows,marks FROM system.parts WHERE database='default' AND table='"+t+"' AND active ORDER BY name FORMAT TSV")["result"]
 entries=[line.split("\t") for line in raw.splitlines() if line]
 script="""set -eu
while IFS="$(printf '\\t')" read -r name dir; do
  echo "PART:$name"
  find "$dir" -type f ! -name 'statistics.packed' ! -name 'checksums.txt' ! -name 'count.txt' -print0 | sort -z | xargs -0 -r sha256sum | sed "s|$dir/||g" || exit 1
  if test -f "$dir/statistics.packed"; then echo "STAT:$(sha256sum "$dir/statistics.packed" | cut -d' ' -f1)"; else echo "STAT:none"; fi
done
"""
 inp="".join("\t".join(e[:2])+"\n" for e in entries).encode()
 out=run(["docker","exec","-i","c43p","bash","-c",script],inp=inp,timeout=120).stdout.decode()
 content="\n".join(s for s in out.splitlines() if not s.startswith("STAT:") and not s.startswith("PART:"))
 stat="\n".join(s for s in out.splitlines() if s.startswith("STAT:"))
 return {"parts":[{"name":e[0],"rows":e[2],"marks":e[3]} for e in entries],
   "data_digest":hashlib.sha256(content.encode()).hexdigest(),"stat_digest":hashlib.sha256(stat.encode()).hexdigest(),
   "stat_parts":stat.count("STAT:")-stat.count("STAT:none"),"all_manifest":out}
def main():
 run(["docker","pull","clickhouse/clickhouse-server:26.8.1.2041"],timeout=400)
 run(["docker","rm","-f","c43p"],check=False)
 run(["docker","run","-d","--name","c43p","clickhouse/clickhouse-server:26.8.1.2041"])
 try:
  for _ in range(45):
   if q("SELECT 1",check=False)["ok"]:break
   time.sleep(1)
  q("CREATE TABLE probe (x UInt32,y UInt32,z String) ENGINE=MergeTree ORDER BY x SETTINGS min_bytes_for_wide_part=0,max_bytes_to_merge_at_max_space_in_pool=0")
  print("C43P_NO_BACKGROUND_MERGES_WITH_MUTATIONS_ALLOWED",flush=True)
  q("INSERT INTO probe SELECT number,toUInt32(number%41),toString(number) FROM numbers(160000) SETTINGS materialize_statistics_on_insert=0")
  baseline=sha("probe")
  checks={"version":q("SELECT version()"),"settings":q("SELECT name,value FROM system.settings WHERE name LIKE '%statistics%' ORDER BY name FORMAT TSV")}
  checks["before"]=baseline
  print("C43P_START_MATERIALIZE",flush=True)
  checks["materialize"]=q("ALTER TABLE probe MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2",check=False)
  print("C43P_FINISHED_MATERIALIZE",checks["materialize"]["ok"],checks["materialize"]["error"],flush=True)
  checks["after_materialize"]=sha("probe")
  checks["clear"]=q("ALTER TABLE probe CLEAR STATISTICS ALL",check=False)
  checks["after_clear"]=sha("probe")
  checks["restore"]=q("ALTER TABLE probe MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2",check=False)
  checks["after_restore"]=sha("probe")
  checks["fingerprint"]=q("SELECT count(),sum(x),sum(y) FROM probe FORMAT TSV")
  checks["mutations"]=q("SELECT mutation_id,command,is_done,latest_fail_reason FROM system.mutations WHERE database='default' AND table='probe' FORMAT JSONEachRow",check=False)
  checks["cache_on"]=q("SELECT count() FROM probe WHERE y=2 SETTINGS use_statistics=1,use_statistics_cache=1",check=False)
  checks["cache_off"]=q("SELECT count() FROM probe WHERE y=2 SETTINGS use_statistics=1,use_statistics_cache=0",check=False)
  pathlib.Path("c43p/probe.json").write_text(json.dumps(checks,indent=2))
  summary={key:{"ok":checks[key]["ok"],"err":checks[key]["error"]} for key in ["materialize","clear","restore"]}
  for key in ["before","after_materialize","after_clear","after_restore"]:
   summary[key]={k:checks[key][k] for k in ["parts","data_digest","stat_digest","stat_parts"]}
  print("C43P_FINAL",json.dumps(summary),flush=True)
 finally:
  run(["docker","rm","-f","c43p"],check=False)
if __name__=="__main__":main()
