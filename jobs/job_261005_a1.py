#!/usr/bin/env python3
import csv, datetime as dt, json, re, shutil, subprocess, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
import requests, yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT=Path.cwd(); OUT=ROOT/"job-out"; ADB=ROOT/"advisory-database"; OUT.mkdir(exist_ok=True)
UA="public-empirical-runner/1.0"
FALLBACK=[
{"cycle":"2.7","releaseDate":"2010-07-03","eol":"2020-01-01"},
{"cycle":"3.0","releaseDate":"2008-12-03","eol":"2009-06-27"},
{"cycle":"3.1","releaseDate":"2009-06-27","eol":"2012-04-09"},
{"cycle":"3.2","releaseDate":"2011-02-20","eol":"2016-02-20"},
{"cycle":"3.3","releaseDate":"2012-09-29","eol":"2017-09-29"},
{"cycle":"3.4","releaseDate":"2014-03-16","eol":"2019-03-18"},
{"cycle":"3.5","releaseDate":"2015-09-13","eol":"2020-09-13"},
{"cycle":"3.6","releaseDate":"2016-12-23","eol":"2021-12-23"},
{"cycle":"3.7","releaseDate":"2018-06-27","eol":"2023-06-27"},
{"cycle":"3.8","releaseDate":"2019-10-14","eol":"2024-10-07"},
{"cycle":"3.9","releaseDate":"2020-10-05","eol":"2025-10-31"},
{"cycle":"3.10","releaseDate":"2021-10-04","eol":"2026-10-01"},
{"cycle":"3.11","releaseDate":"2022-10-24","eol":"2027-10-31"},
{"cycle":"3.12","releaseDate":"2023-10-02","eol":"2028-10-31"},
{"cycle":"3.13","releaseDate":"2024-10-07","eol":"2029-10-31"},
{"cycle":"3.14","releaseDate":"2025-10-07","eol":"2030-10-31"}]

def log(s): print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {s}",flush=True)
def norm(s): return re.sub(r"[-_.]+","-",str(s).strip()).lower()
def ver(s):
    try:return Version(str(s))
    except:return None
def pdt(x):
    if not x:return None
    try:
        z=dt.datetime.fromisoformat(str(x).replace("Z","+00:00"))
        return (z if z.tzinfo else z.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
    except:return None
def getj(url,n=5,to=30):
    e=None
    for i in range(n):
        try:
            r=requests.get(url,headers={"User-Agent":UA,"Accept":"application/json"},timeout=to)
            if r.status_code==200:return r.json(),None
            if r.status_code==404:return None,"404"
            e=f"HTTP {r.status_code}"
            if r.status_code not in (429,500,502,503,504):return None,e
        except Exception as x:e=f"{type(x).__name__}:{x}"
        time.sleep(min(8,.5*(2**i)))
    return None,e or "unknown"

def lifecycle():
    d,e=getj("https://endoflife.date/api/python.json",3,20); src="endoflife.date"
    if not isinstance(d,list):d=FALLBACK;src="fallback"
    z=[]
    for x in d:
        c=str(x.get("cycle",""))
        if not re.fullmatch(r"(?:2\.7|3\.\d+)",c):continue
        try:r=dt.date.fromisoformat(x["releaseDate"])
        except:continue
        try:q=dt.date.fromisoformat(x["eol"]) if isinstance(x.get("eol"),str) else None
        except:q=None
        z.append({"cycle":c,"releaseDate":r,"eol":q})
    z.sort(key=lambda x:tuple(map(int,x["cycle"].split("."))))
    (OUT/"lifecycle.json").write_text(json.dumps({"source":src,"error":e,"cycles":[{"cycle":x["cycle"],"releaseDate":str(x["releaseDate"]),"eol":str(x["eol"]) if x["eol"] else None} for x in z]},indent=2))
    return z

def clone():
    if ADB.exists():shutil.rmtree(ADB)
    subprocess.run(["git","clone","--depth","1","https://github.com/pypa/advisory-database.git",str(ADB)],check=True)
    return subprocess.check_output(["git","-C",str(ADB),"rev-parse","HEAD"],text=True).strip()

def load():
    a=[];bad=[]
    for p in sorted((ADB/"vulns").glob("**/PYSEC-*.yaml")):
        try:a.append({"path":str(p.relative_to(ADB)),"obj":yaml.safe_load(p.read_text()) or {}})
        except Exception as e:bad.append({"path":str(p.relative_to(ADB)),"error":str(e)})
    (OUT/"parse_errors.json").write_text(json.dumps(bad,indent=2));return a,bad

class DSU:
    def __init__(self,n):self.p=list(range(n))
    def f(self,x):
        while self.p[x]!=x:self.p[x]=self.p[self.p[x]];x=self.p[x]
        return x
    def u(self,a,b):
        a,b=self.f(a),self.f(b)
        if a!=b:self.p[b]=a

def groups(rec):
    act=[];wd=[]
    for r in rec:(wd if r["obj"].get("withdrawn") else act).append(r)
    d=DSU(len(act));own={}
    for i,r in enumerate(act):
        ps=[norm((a.get("package")or{}).get("name")) for a in (r["obj"].get("affected")or[]) if (a.get("package")or{}).get("ecosystem")=="PyPI" and (a.get("package")or{}).get("name")]
        pk=ps[0] if ps else norm(Path(r["path"]).parent.name)
        for al in [str(x).upper() for x in (r["obj"].get("aliases")or[])]:
            k=(pk,al)
            if k in own:d.u(i,own[k])
            else:own[k]=i
    g=defaultdict(list)
    for i,r in enumerate(act):g[d.f(i)].append(r)
    return list(g.values()),wd

def events(g):
    out=[];seen=set();ids=sorted({str(r["obj"].get("id","")) for r in g});als=sorted({str(a) for r in g for a in (r["obj"].get("aliases")or[])})
    gid=ids[0] if ids else ""
    for r in g:
        o=r["obj"]
        for af in o.get("affected")or[]:
            pd=af.get("package")or{}
            if pd.get("ecosystem")!="PyPI" or not pd.get("name"):continue
            pkg=str(pd["name"])
            for rg in af.get("ranges")or[]:
                if rg.get("type")!="ECOSYSTEM":continue
                intro="0"
                for ev in rg.get("events")or[]:
                    if "introduced" in ev:intro=str(ev["introduced"])
                    elif "fixed" in ev:
                        fv=ver(ev["fixed"]);iv=None if intro=="0" else ver(intro)
                        if fv is None or (intro!="0" and iv is None):continue
                        k=(norm(pkg),str(iv) if iv else "0",str(fv))
                        if k in seen:continue
                        seen.add(k);out.append({"group_id":gid,"member_ids":ids,"aliases":als,"package":pkg,"package_norm":norm(pkg),"introduced":str(iv) if iv else "0","fixed":str(fv),"source_path":r["path"],"published":o.get("published")})
    return out

def fetch(pkg):
    d,e=getj(f"https://pypi.org/pypi/{quote(pkg,safe='')}/json",6,35);return pkg,d,e
def relmap(meta):
    m=defaultdict(list);bad=[]
    for raw,fs in (meta.get("releases")or{}).items():
        v=ver(raw)
        if v is None:bad.append(raw)
        else:m[v].append((raw,fs or []))
    return m,bad
def files(m,v):
    z=[]
    for _,fs in m.get(v)or[]:z+=fs
    return z
def firstup(fs):
    z=[pdt(f.get("upload_time_iso_8601")or f.get("upload_time")) for f in fs];z=[x for x in z if x]
    return min(z) if z else None
def accept(spec,v):
    if spec is None or not str(spec).strip():return True,False
    try:return SpecifierSet(str(spec)).contains(v,prereleases=True),False
    except:return None,True
def rsets(fs,cy,drop_yank=False):
    lo=set();hi=set();bad=0;yc=0
    for f in fs:
        if f.get("yanked"):
            yc+=1
            if drop_yank:continue
        for c in cy:
            a,ia=accept(f.get("requires_python"),Version(c["cycle"]+".0"));b,ib=accept(f.get("requires_python"),Version(c["cycle"]+".999"))
            if ia or ib:bad+=1;continue
            if a:lo.add(c["cycle"])
            if b:hi.add(c["cycle"])
    return {"yes":lo&hi,"amb":lo^hi,"bad":bad,"all_yanked":bool(fs) and yc==len(fs),"n":len(fs)}
def life(cy,d):
    rel={x["cycle"] for x in cy if x["releaseDate"]<=d}
    sup={x["cycle"] for x in cy if x["releaseDate"]<=d and (x["eol"] is None or d<=x["eol"])}
    return rel,sup
def secure(m,fixed):
    fv=Version(fixed);z=[]
    for v in m:
        if v.is_prerelease or v<fv:continue
        u=firstup(files(m,v))
        if u:z.append((v,u))
    return min(z,key=lambda x:(x[0],x[1]))[0] if z else None

def pred(m,intros,fixed,t):
    fv=Version(fixed);z=[]
    for v in m:
        if v.is_prerelease or v>=fv:continue
        if not any(i=="0" or v>=Version(i) for i in intros):continue
        u=firstup(files(m,v))
        if u and u<=t:z.append((v,u))
    return max(z,key=lambda x:(x[0],x[1]))[0] if z else None

def analyze(e,meta,cy):
    o=dict(e);o.update({"status":"ok","reason":"","predecessor":"","fix_upload":"","pred_runtime_set":"","fix_runtime_set":"","lost_runtime_set":"","supported_pred_set":"","lost_supported_set":"","lost_eol_set":"","gained_runtime_set":"","ambiguous_runtime_set":"","rcl_all":"","rcl_supported":"","stranding":False,"eol_aligned_only":False,"fix_all_yanked":False,"current_lost_supported_set":"","current_stranding":False})
    if meta is None:o["status"]="excluded";o["reason"]="pypi_metadata_unavailable";return o
    m,bv=relmap(meta);sv=secure(m,e["fixed"])
    if sv is None:o["status"]="excluded";o["reason"]="no_stable_secure_release";return o
    ff=files(m,sv);tf=firstup(ff)
    if not tf:o["status"]="excluded";o["reason"]="secure_upload_time_missing";return o
    pv=pred(m,e.get("introduced_list",[e["introduced"]]),e["fixed"],tf)
    if pv is None:o["status"]="excluded";o["reason"]="predecessor_not_found";return o
    pf=files(m,pv);ps=rsets(pf,cy);fs=rsets(ff,cy);pc=rsets(pf,cy,True);fc=rsets(ff,cy,True)
    rel,sup=life(cy,tf.date());amb=(ps["amb"]|fs["amb"])&rel
    mature90={x["cycle"] for x in cy if x["releaseDate"]<=tf.date()-dt.timedelta(days=90) and (x["eol"] is None or tf.date()<=x["eol"])}
    P=(ps["yes"]&rel)-amb;F=(fs["yes"]&rel)-amb;lost=P-F;gain=F-P;Ps=P&sup;Ls=Ps-F;Le=lost-sup;Ls90=(P&mature90)-F
    P2=(pc["yes"]&rel)-amb;F2=(fc["yes"]&rel)-amb;CL=(P2&sup)-F2
    key=lambda x:tuple(map(int,x.split(".")));fmt=lambda s:";".join(sorted(s,key=key))
    o.update({"secure_release":str(sv),"predecessor":str(pv),"fix_upload":tf.isoformat(),"pred_runtime_set":fmt(P),"fix_runtime_set":fmt(F),"lost_runtime_set":fmt(lost),"supported_pred_set":fmt(Ps),"lost_supported_set":fmt(Ls),"lost_supported_90_set":fmt(Ls90),"lost_eol_set":fmt(Le),"gained_runtime_set":fmt(gain),"ambiguous_runtime_set":fmt(amb),"rcl_all":len(lost)/len(P) if P else None,"rcl_supported":len(Ls)/len(Ps) if Ps else None,"stranding":bool(Ls),"stranding_90":bool(Ls90),"eol_aligned_only":bool(lost) and not bool(Ls),"fix_all_yanked":fs["all_yanked"],"current_lost_supported_set":fmt(CL),"current_stranding":bool(CL),"pred_invalid_specs":ps["bad"],"fix_invalid_specs":fs["bad"],"pred_files":ps["n"],"fix_files":fs["n"],"bad_pypi_versions":len(bv)})
    if not P:o["status"]="excluded";o["reason"]="no_stable_minor_runtime_predecessor"
    return o

def csvw(p,rows):
    if not rows:p.write_text("");return
    cols=[];seen=set()
    for r in rows:
        for k in r:
            if k not in seen:seen.add(k);cols.append(k)
    with p.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for r in rows:
            q={k:(json.dumps(v,sort_keys=True) if isinstance(v,(list,dict)) else v) for k,v in r.items()};w.writerow(q)

def main():
    sha=clone();log("ADB "+sha);rec,bad=load();gs,wd=groups(rec);log(f"records={len(rec)} withdrawn={len(wd)} groups={len(gs)}")
    ev=[];nofix=0
    for g in gs:
        z=events(g);nofix+=not bool(z);ev+=z
    u={}
    for x in ev:
        k=(x["package_norm"],x["fixed"])
        if k not in u:
            y=dict(x);y["member_ids"]=set(x["member_ids"]);y["aliases"]=set(x["aliases"]);y["introduced_list"]={x["introduced"]};y["source_paths"]={x["source_path"]};u[k]=y
        else:
            u[k]["member_ids"].update(x["member_ids"]);u[k]["aliases"].update(x["aliases"]);u[k]["introduced_list"].add(x["introduced"]);u[k]["source_paths"].add(x["source_path"])
    ev=[]
    for y in u.values():
        y["member_ids"]=sorted(y["member_ids"]);y["aliases"]=sorted(y["aliases"]);y["introduced_list"]=sorted(y["introduced_list"]);y["source_paths"]=sorted(y["source_paths"]);y["introduced"]=";".join(y["introduced_list"]);ev.append(y)
    ev.sort(key=lambda x:(x["package_norm"],Version(x["fixed"])))
    cy=lifecycle();pk=sorted({x["package"] for x in ev},key=norm);log(f"events={len(ev)} packages={len(pk)}")
    md={};fe={}
    with ThreadPoolExecutor(max_workers=12) as ex:
        fs={ex.submit(fetch,p):p for p in pk}
        for i,f in enumerate(as_completed(fs),1):
            p,d,e=f.result();md[norm(p)]=d
            if e:fe[norm(p)]=e
            if i%200==0 or i==len(pk):log(f"fetch {i}/{len(pk)} errors={len(fe)}")
    rows=[analyze(x,md.get(x["package_norm"]),cy) for x in ev]
    ok0=[r for r in rows if r["status"]=="ok"];primary={}
    for r in ok0:
        k=(r["package_norm"],r["secure_release"],r["predecessor"])
        if k not in primary:
            q=dict(r);q["member_ids_all"]=set(r.get("member_ids")or[]);q["thresholds"]={r["fixed"]};primary[k]=q
        else:
            primary[k]["member_ids_all"].update(r.get("member_ids")or[]);primary[k]["thresholds"].add(r["fixed"])
    ok=[]
    for q in primary.values():
        q["member_ids_all"]=sorted(q["member_ids_all"]);q["thresholds"]=sorted(q["thresholds"]);ok.append(q)
    st=[r for r in ok if r["stranding"]];st90=[r for r in ok if r["stranding_90"]];co=[r for r in ok if r["lost_runtime_set"]];pr=[r for r in ok if not r["lost_runtime_set"]];eo=[r for r in ok if r["eol_aligned_only"]];ex=[r for r in ok if r["gained_runtime_set"]]
    yrs=defaultdict(lambda:{"ok":0,"stranding":0,"eol_only":0,"contracted":0})
    for r in ok:
        y=r["fix_upload"][:4];yrs[y]["ok"]+=1;yrs[y]["stranding"]+=int(r["stranding"]);yrs[y]["eol_only"]+=int(r["eol_aligned_only"]);yrs[y]["contracted"]+=int(bool(r["lost_runtime_set"]))
    reasons=Counter(r["reason"] for r in rows if r["status"]!="ok")
    S={"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"advisory_database_commit":sha,"raw_pysec_records":len(rec),"yaml_parse_errors":len(bad),"withdrawn_records_removed":len(wd),"active_records":len(rec)-len(wd),"alias_dedup_groups":len(gs),"alias_merged_groups":sum(len(g)>1 for g in gs),"groups_without_parseable_ecosystem_fix":nofix,"deduplicated_fixed_events":len(ev),"fixed_event_packages":len(pk),"pypi_fetch_errors":len(fe),"analyzable_fixed_threshold_events":len(ok0),"analyzable_repair_events":len(ok),"excluded_threshold_events":len(rows)-len(ok0),"exclusion_reasons":dict(reasons),"preserved_events_rcl0":len(pr),"contracted_events_rcl_gt0":len(co),"supported_runtime_stranding_events":len(st),"supported_runtime_stranding_events_90d":len(st90),"eol_aligned_only_contraction_events":len(eo),"expanded_events":len(ex),"currently_yanked_sensitive_stranding_events":sum(r["current_stranding"] for r in ok),"fixed_versions_all_files_currently_yanked":sum(r["fix_all_yanked"] for r in ok),"preservation_rate":len(pr)/len(ok) if ok else None,"contraction_rate":len(co)/len(ok) if ok else None,"supported_stranding_rate":len(st)/len(ok) if ok else None,"supported_stranding_rate_90d":len(st90)/len(ok) if ok else None,"eol_aligned_only_rate":len(eo)/len(ok) if ok else None,"by_fix_year":dict(sorted(yrs.items())),"definition":{"unit":"unique stable repair release (package, first stable secure release, stable vulnerable predecessor)","predecessor":"highest stable PEP440 release in an associated introduced..fixed interval uploaded before the secure release","runtime_support":"resolver-visible Requires-Python; missing field means unrestricted","minor_runtime_rule":"x.y.0 and x.y.999 must agree, otherwise minor is patch-sensitive/ambiguous","historical_yank_rule":"primary RCL ignores current yanked state because historical yank timestamps are unavailable; current-yank sensitivity is separate","stranding":"predecessor supported runtime, runtime released and not EOL at fix upload, fixed release does not support it"}}
    (OUT/"summary.json").write_text(json.dumps(S,indent=2,sort_keys=True));(OUT/"fetch_errors.json").write_text(json.dumps(fe,indent=2,sort_keys=True))
    csvw(OUT/"events.csv",rows);csvw(OUT/"repair_events.csv",ok);csvw(OUT/"stranding_events.csv",st);csvw(OUT/"stranding_events_90d.csv",st90);csvw(OUT/"contraction_events.csv",co);csvw(OUT/"excluded_events.csv",[r for r in rows if r["status"]!="ok"])
    (OUT/"run_manifest.json").write_text(json.dumps({"advisory_commit":sha,"python":sys.version,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat()},indent=2))
    log("SUMMARY "+json.dumps(S,sort_keys=True))

if __name__=="__main__":main()
