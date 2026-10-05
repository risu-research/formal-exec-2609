#!/usr/bin/env python3
import csv, datetime as dt, json, re, shutil, subprocess, time
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
import requests, yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version, InvalidVersion

ROOT=Path.cwd(); OUT=ROOT/"job-out-a5"; ADB=ROOT/"advisory-database"
OUT.mkdir(exist_ok=True)
UA="neutral-exact-remediation-frontier/1.0"

PY=[
("2.7","2010-07-03","2020-01-01"),
("3.0","2008-12-03","2009-06-27"),
("3.1","2009-06-27","2012-04-09"),
("3.2","2011-02-20","2016-02-20"),
("3.3","2012-09-29","2017-09-29"),
("3.4","2014-03-16","2019-03-18"),
("3.5","2015-09-13","2020-09-30"),
("3.6","2016-12-23","2021-12-23"),
("3.7","2018-06-27","2023-06-27"),
("3.8","2019-10-14","2024-10-07"),
("3.9","2020-10-05","2025-10-31"),
("3.10","2021-10-04","2026-10-01"),
("3.11","2022-10-24","2027-10-31"),
("3.12","2023-10-02","2028-10-31"),
("3.13","2024-10-07","2029-10-31"),
("3.14","2025-10-07","2030-10-31"),
]
PY=[{"cycle":c,"release":dt.date.fromisoformat(r),"eol":dt.date.fromisoformat(e)} for c,r,e in PY]

def log(x): print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {x}",flush=True)
def norm(x): return re.sub(r"[-_.]+","-",str(x).strip()).lower()
def V(x):
    try:return Version(str(x))
    except:return None
def T(x):
    if not x:return None
    try:
        z=dt.datetime.fromisoformat(str(x).replace("Z","+00:00"))
        return (z if z.tzinfo else z.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
    except:return None
def getj(url,n=5):
    err=None
    for i in range(n):
        try:
            r=requests.get(url,headers={"User-Agent":UA,"Accept":"application/json"},timeout=35)
            if r.status_code==200:return r.json(),None
            if r.status_code==404:return None,"404"
            err=f"HTTP {r.status_code}"
            if r.status_code not in (429,500,502,503,504): return None,err
        except Exception as e: err=f"{type(e).__name__}:{e}"
        time.sleep(min(8,.5*(2**i)))
    return None,err
def clone():
    if ADB.exists(): shutil.rmtree(ADB)
    subprocess.run(["git","clone","--depth","1","https://github.com/pypa/advisory-database.git",str(ADB)],check=True)
    return subprocess.check_output(["git","-C",str(ADB),"rev-parse","HEAD"],text=True).strip()
def load():
    rec=[]; bad=[]
    for p in sorted((ADB/"vulns").glob("**/PYSEC-*.yaml")):
        try:
            o=yaml.safe_load(p.read_text()) or {}
            if not o.get("withdrawn"): rec.append({"path":str(p.relative_to(ADB)),"obj":o})
        except Exception as e: bad.append({"path":str(p),"error":str(e)})
    return rec,bad
def fetch(pkg):
    return pkg,*getj(f"https://pypi.org/pypi/{quote(pkg,safe='')}/json")
def relmap(meta):
    m=defaultdict(list); raw_by_v=defaultdict(list)
    for raw,fs in (meta.get("releases")or{}).items():
        v=V(raw)
        if v is None: continue
        m[v].extend(fs or []); raw_by_v[v].append(raw)
    return m,raw_by_v
def up(fs):
    xs=[T(f.get("upload_time_iso_8601") or f.get("upload_time")) for f in fs]
    xs=[x for x in xs if x]
    return min(xs) if xs else None
def acc(spec,v):
    if spec is None or not str(spec).strip(): return True
    try:return SpecifierSet(str(spec)).contains(v,prereleases=True)
    except:return None
def rset(fs, when):
    released={x["cycle"] for x in PY if x["release"]<=when.date()}
    support={x["cycle"] for x in PY if x["release"]<=when.date() and when.date()<=x["eol"]}
    yes=set(); amb=set()
    for c in PY:
        if c["cycle"] not in released: continue
        a=acc_any(fs,Version(c["cycle"]+".0"))
        b=acc_any(fs,Version(c["cycle"]+".999"))
        if a is None or b is None: amb.add(c["cycle"])
        elif a and b: yes.add(c["cycle"])
        elif a!=b: amb.add(c["cycle"])
    return yes-amb, support, amb
def acc_any(fs,v):
    vals=[]
    for f in fs:
        a=acc(f.get("requires_python"),v)
        if a is not None: vals.append(a)
    return any(vals) if vals else None

def intervals(af):
    out=[]
    for rg in af.get("ranges") or []:
        if rg.get("type")!="ECOSYSTEM": continue
        start=None
        for ev in rg.get("events") or []:
            if "introduced" in ev:
                start=V(ev["introduced"]) if str(ev["introduced"])!="0" else None
                if str(ev["introduced"])=="0": start="ZERO"
            elif "fixed" in ev and start is not None:
                end=V(ev["fixed"])
                if end is not None: out.append(("fixed",start,end))
                start=None
            elif "last_affected" in ev and start is not None:
                end=V(ev["last_affected"])
                if end is not None: out.append(("last",start,end))
                start=None
            elif "limit" in ev and start is not None:
                lim=str(ev["limit"])
                if "*" not in lim:
                    end=V(lim)
                    if end is not None: out.append(("limit",start,end))
                start=None
        if start is not None: out.append(("open",start,None))
    return out

def affected(v, af):
    # Explicit affected versions are authoritative additions.
    for raw in af.get("versions") or []:
        vv=V(raw)
        if vv is not None and vv==v: return True
    for typ,a,b in intervals(af):
        lo=True if a=="ZERO" else v>=a
        if not lo: continue
        if typ in ("fixed","limit"):
            if b is None or v<b: return True
        elif typ=="last":
            if b is None or v<=b: return True
        elif typ=="open":
            return True
    return False

def pyminor_sort(s):
    return tuple(map(int,s.split(".")))
def fmt(s): return ";".join(sorted(s,key=pyminor_sort))

def version_classifiers(pkg,v):
    d,e=getj(f"https://pypi.org/pypi/{quote(pkg,safe='')}/{quote(str(v),safe='')}/json",4)
    if not d:return set(),e
    out=set()
    for c in (d.get("info") or {}).get("classifiers") or []:
        m=re.fullmatch(r"Programming Language :: Python :: (2\.7|3\.\d+)",str(c).strip())
        if m:out.add(m.group(1))
    return out,None

def build_advisory_events(rec, metas):
    events=[]
    for rr in rec:
        o=rr["obj"]; gid=str(o.get("id",""))
        aliases=sorted(str(x) for x in (o.get("aliases") or []))
        for af in o.get("affected") or []:
            pd=af.get("package") or {}
            if pd.get("ecosystem")!="PyPI" or not pd.get("name"): continue
            pkg=str(pd["name"]); meta=metas.get(norm(pkg))
            if not meta: continue
            m,_=relmap(meta)
            fixeds=[]
            for rg in af.get("ranges") or []:
                if rg.get("type")!="ECOSYSTEM": continue
                for ev in rg.get("events") or []:
                    if "fixed" in ev:
                        fv=V(ev["fixed"])
                        if fv is not None: fixeds.append(fv)
            if not fixeds: continue
            stable=sorted(v for v in m if not v.is_prerelease and not v.is_devrelease)
            if len(stable)<2: continue
            states=[affected(v,af) for v in stable]
            # exact vulnerable -> unaffected transitions in canonical version order,
            # retained only when an explicit OSV fixed boundary lies in (pred, secure].
            for i in range(len(stable)-1):
                if not states[i]: continue
                j=i+1
                if states[j]: continue
                pred=stable[i]; sec=stable[j]
                matched_fixed=[fv for fv in fixeds if pred < fv and fv <= sec]
                if not matched_fixed: continue
                tp=up(m[pred]); ts=up(m[sec])
                if not tp or not ts: continue
                t=max(tp,ts)
                P,S,ambp=rset(m[pred],t)
                # all safe upgrade candidates that actually existed by t
                safe=[]
                for v in stable:
                    if v<pred or affected(v,af): continue
                    tv=up(m[v])
                    if tv and tv<=t: safe.append(v)
                U=set(); ambu=set()
                for v in safe:
                    rv,_,a=rset(m[v],t); U|=rv; ambu|=a
                usable=(P&S)-ambp-ambu
                lost=usable-U
                # recovery for each lost runtime: earliest later safe release >= pred that admits r
                recov={}
                for r in lost:
                    best=None; bestv=None
                    for v in stable:
                        if v<pred or affected(v,af): continue
                        tv=up(m[v])
                        if not tv or tv<=t: continue
                        rv,_,a=rset(m[v],tv)
                        if r in rv and r not in a:
                            if best is None or tv<best: best,bestv=tv,v
                    recov[r]={"release":str(bestv) if bestv else None,"time":best.isoformat() if best else None,
                              "delay_days":(best-t).days if best else None}
                events.append({
                    "group_id":gid,"aliases":aliases,"source_path":rr["path"],"package":pkg,"package_norm":norm(pkg),"matched_fixed":";".join(str(x) for x in sorted(matched_fixed)),
                    "predecessor":str(pred),"secure_release":str(sec),
                    "predecessor_upload":tp.isoformat(),"secure_upload":ts.isoformat(),"event_time":t.isoformat(),
                    "pred_runtime_set":fmt(P),"supported_pred_set":fmt(P&S),"safe_candidates_at_event":";".join(map(str,safe)),
                    "safe_union_runtime_set":fmt(U),"lost_supported_set":fmt(lost),"lost_count":len(lost),
                    "recovery":recov,"has_operational_stranding":bool(lost)
                })
    return events

def csvw(path,rows):
    if not rows: path.write_text(""); return
    cols=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); cols.append(k)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for r in rows:
            w.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(list,dict)) else v for k,v in r.items()})

def main():
    sha=clone(); rec,bad=load(); log(f"active={len(rec)} parse_errors={len(bad)}")
    pk=sorted({str((af.get("package")or{}).get("name")) for rr in rec for af in rr["obj"].get("affected")or []
               if (af.get("package")or{}).get("ecosystem")=="PyPI" and (af.get("package")or{}).get("name")},key=norm)
    metas={}; ferr={}
    with ThreadPoolExecutor(max_workers=12) as ex:
        fut={ex.submit(fetch,p):p for p in pk}
        for i,f in enumerate(as_completed(fut),1):
            p,d,e=f.result(); metas[norm(p)]=d
            if e:ferr[norm(p)]=e
            if i%200==0 or i==len(pk): log(f"fetch {i}/{len(pk)} errors={len(ferr)}")
    raw=build_advisory_events(rec,metas)
    # Collapse same concrete repair frontier across advisories.
    merged={}
    for r in raw:
        k=(r["package_norm"],r["predecessor"],r["secure_release"],r["event_time"])
        if k not in merged:
            q=dict(r);q["group_ids"]={r["group_id"]};q["all_aliases"]=set(r["aliases"]);q["source_paths"]={r["source_path"]}; merged[k]=q
        else:
            q=merged[k];q["group_ids"].add(r["group_id"]);q["all_aliases"].update(r["aliases"]);q["source_paths"].add(r["source_path"])
            # operational stranding only if union across vulnerabilities? For a concrete repair event, preserve per-vulnerability lost sets separately.
            # We keep the strongest lost set but mark IDs; duplicate same frontier typically same runtime behavior.
    rows=[]
    for q in merged.values():
        q["group_ids"]=sorted(q["group_ids"]);q["all_aliases"]=sorted(q["all_aliases"]);q["source_paths"]=sorted(q["source_paths"]);rows.append(q)
    strand=[r for r in rows if r["has_operational_stranding"]]
    # Classifier corroboration on lost predecessor runtimes only.
    corr=[]; cerr={}
    for i,r in enumerate(strand,1):
        C,e=version_classifiers(r["package"],r["predecessor"])
        if e: cerr[f'{r["package"]}:{r["predecessor"]}']=e
        lost=set(x for x in r["lost_supported_set"].split(";") if x)
        cc=lost&C
        q=dict(r);q["pred_classifier_minors"]=fmt(C);q["classifier_corroborated_lost"]=fmt(cc);q["classifier_corroborated"]=bool(cc)
        if q["classifier_corroborated"]: corr.append(q)
        if i%20==0: log(f"classifier {i}/{len(strand)}")
    # Recovery consequences.
    recov_pairs=0; recovered=0; censored=0; delays=[]
    for r in strand:
        d=r["recovery"]
        for py,x in d.items():
            recov_pairs+=1
            if x["delay_days"] is None:censored+=1
            else:recovered+=1;delays.append(x["delay_days"])
    S={
      "generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
      "advisory_database_commit":sha,"active_pysec_records":len(rec),"pypi_packages":len(pk),"pypi_fetch_errors":len(ferr),
      "raw_exact_transitions":len(raw),"unique_exact_repair_frontiers":len(rows),
      "operational_stranding_frontiers":len(strand),"operational_stranding_rate":len(strand)/len(rows) if rows else None,
      "classifier_corroborated_frontiers":len(corr),"classifier_corroborated_rate":len(corr)/len(rows) if rows else None,
      "classifier_corroborated_fraction_of_stranding":len(corr)/len(strand) if strand else None,
      "lost_runtime_pairs":recov_pairs,"recovered_runtime_pairs":recovered,"censored_runtime_pairs":censored,
      "recovery_delay_median_days":sorted(delays)[len(delays)//2] if delays else None,
      "recovery_delay_max_days":max(delays) if delays else None,
      "definition":{
        "transition":"adjacent stable PyPI versions in canonical PEP 440 order where advisory state changes affected->unaffected and at least one explicit ECOSYSTEM fixed boundary lies in (predecessor, secure]",
        "event_time":"later of predecessor and first-safe successor upload timestamps",
        "safe_path":"union of all non-vulnerable stable upgrade candidates >= predecessor already uploaded by event_time",
        "stranding":"a Python minor admitted by predecessor and upstream-supported at event_time is admitted by no safe upgrade candidate at event_time",
        "recovery":"first later non-vulnerable stable upgrade candidate admitting that minor"
      }
    }
    (OUT/"summary.json").write_text(json.dumps(S,indent=2,sort_keys=True))
    (OUT/"fetch_errors.json").write_text(json.dumps(ferr,indent=2,sort_keys=True))
    csvw(OUT/"exact_repair_frontiers.csv",rows)
    csvw(OUT/"operational_stranding_frontiers.csv",strand)
    csvw(OUT/"classifier_corroborated_frontiers.csv",corr)
    log("SUMMARY "+json.dumps(S,sort_keys=True))

if __name__=="__main__": main()
