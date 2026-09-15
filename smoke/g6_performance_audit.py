#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, os, resource, statistics, subprocess, time
from pathlib import Path
import z3

EXPECTED_R2_SHA256="00cc74ac08a15a785eaf7caa601181c16d8a2ee152b735b3e87ae9a3bb981af1"
REPETITIONS=5

def pct(xs,p):
    xs=sorted(xs)
    if not xs:return None
    rank=max(1,math.ceil(p*len(xs)))
    return xs[rank-1]

def expected_queries(r2):
    out={}
    def add(q):
        if not q:return
        path=q.get("query")
        if path:
            rel=path[path.index("queries/"):] if "queries/" in path else Path(path).name
            out[rel]=q["z3"]
        f=q.get("fixed_replay")
        if f and f.get("query"):
            path=f["query"]; rel=path[path.index("queries/"):] if "queries/" in path else Path(path).name
            out[rel]=f["cvc5"]["result"]
    for row in r2["rows"]:
        for axis in ("environment","guarantee"):
            d=row[axis]
            add(d.get("old_only")); add(d.get("new_only")); add(d.get("separator_replay"))
    return out

def run_z3(text):
    s=z3.Solver(); s.from_string(text); r=s.check()
    if r==z3.sat:return "sat"
    if r==z3.unsat:return "unsat"
    return "unknown"

def run_cvc5(binary,path):
    p=subprocess.run([binary,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
    if p.returncode!=0:raise RuntimeError(f"cvc5 rc {p.returncode}: {(p.stdout+p.stderr)[-800:]}")
    return (p.stdout.strip().splitlines() or [""])[0].strip()

def measure(fn):
    t=time.perf_counter_ns(); r=fn(); return r,(time.perf_counter_ns()-t)/1e6

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--r2",required=True);ap.add_argument("--query-root",required=True);ap.add_argument("--cvc5",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    import hashlib
    b=Path(a.r2).read_bytes(); sha=hashlib.sha256(b).hexdigest()
    if sha!=EXPECTED_R2_SHA256:raise RuntimeError(f"R2 result hash mismatch {sha}")
    r2=json.loads(b); expected=expected_queries(r2); root=Path(a.query_root)
    files=sorted(root.rglob("*.smt2"))
    actual_rel={"queries/"+str(p.relative_to(root)).replace(os.sep,"/"):p for p in files}
    if set(actual_rel)!=set(expected):
        raise RuntimeError(f"query set mismatch missing={sorted(set(expected)-set(actual_rel))} extra={sorted(set(actual_rel)-set(expected))}")
    rows=[]
    for rel in sorted(expected):
        p=actual_rel[rel];text=p.read_text(); exp=expected[rel]
        # warm-up, never reported
        if run_z3(text)!=exp:raise RuntimeError(f"Z3 warm-up mismatch {rel}")
        if run_cvc5(a.cvc5,p)!=exp:raise RuntimeError(f"cvc5 warm-up mismatch {rel}")
        ztimes=[];ctimes=[]
        for _ in range(REPETITIONS):
            zr,zt=measure(lambda:run_z3(text)); cr,ct=measure(lambda:run_cvc5(a.cvc5,p))
            if zr!=exp or cr!=exp:raise RuntimeError(f"repeat outcome mismatch {rel}: {zr}/{cr}/{exp}")
            ztimes.append(zt);ctimes.append(ct)
        rows.append({"query":rel,"expected":exp,"bytes":len(text.encode()),"declare_fun_count":text.count("(declare-fun"),"assert_count":text.count("(assert"),
                     "fixed_binding_count":sum(1 for line in text.splitlines() if line.strip().startswith("(assert (= ")),
                     "z3_ms":ztimes,"cvc5_ms":ctimes,"z3_median_ms":statistics.median(ztimes),"cvc5_median_ms":statistics.median(ctimes)})
    zm=[r["z3_median_ms"] for r in rows];cm=[r["cvc5_median_ms"] for r in rows]
    result={"schema":"g6-performance-audit-v1","r2_sha256":sha,"query_count":len(rows),"repetitions":REPETITIONS,
            "z3_version":z3.get_version_string(),"cvc5_version":"1.3.4 pinned by workflow",
            "aggregate":{"z3":{"p50_ms":pct(zm,.50),"p95_ms":pct(zm,.95),"max_ms":max(zm)},"cvc5":{"p50_ms":pct(cm,.50),"p95_ms":pct(cm,.95),"max_ms":max(cm)},
                         "query_bytes":{"p50":pct([r['bytes'] for r in rows],.50),"p95":pct([r['bytes'] for r in rows],.95),"max":max(r['bytes'] for r in rows)},
                         "assert_count":{"p50":pct([r['assert_count'] for r in rows],.50),"p95":pct([r['assert_count'] for r in rows],.95),"max":max(r['assert_count'] for r in rows)},
                         "fixed_query_count":sum(r['fixed_binding_count']>0 for r in rows)},
            "resource":{"python_self_maxrss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"children_maxrss_kib":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss},"queries":rows}
    op=out/"g6-performance.json";op.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"query_count":len(rows),"aggregate":result["aggregate"],"resource":result["resource"]},sort_keys=True))
if __name__=="__main__":main()
