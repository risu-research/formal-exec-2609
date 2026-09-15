#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, os, re, resource, shutil, statistics, subprocess, tempfile, time
from pathlib import Path
import z3

CORPUS_SHA="96217099573c869cfa2232bbb559a4f47d6561590bfe8faa459a6ed6b96a5aed"
R1_SHA="2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161"
R2_CANON_SHA="1d9a39581af5e5fec36e0011a302259c16971f008bc938f08f74bc7b7e6a98df"
REPS=5
ATOMS=[16,64,256,1024]
HORIZONS=[1,4,16,64]
TMP_RE=re.compile(r"/tmp/tmp[^\s/]*?\.smt2")

def sha_file(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

def nr_pct(xs,p):
    xs=sorted(xs); return xs[max(1,math.ceil(len(xs)*p))-1]

def stats(xs):
    return {"n":len(xs),"p50":nr_pct(xs,.50),"p95":nr_pct(xs,.95),"max":max(xs),"min":min(xs)}

def parse_maxrss(text):
    for ln in text.splitlines():
        if "Maximum resident set size (kbytes):" in ln:
            return int(ln.rsplit(":",1)[1].strip())
    return None

def timed_cmd(cmd, cwd=None):
    tf=tempfile.NamedTemporaryFile(delete=False); tf.close()
    t0=time.perf_counter_ns()
    p=subprocess.run(["/usr/bin/time","-v","-o",tf.name,*cmd],cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    ms=(time.perf_counter_ns()-t0)/1e6
    txt=Path(tf.name).read_text(errors="replace"); os.unlink(tf.name)
    if p.returncode!=0: raise RuntimeError(f"command failed {cmd}\nSTDOUT\n{p.stdout[-2000:]}\nSTDERR\n{p.stderr[-2000:]}")
    return {"wall_ms":ms,"maxrss_kib":parse_maxrss(txt),"stdout_tail":p.stdout[-1000:],"stderr_tail":p.stderr[-1000:]}

def canonicalize(x):
    if isinstance(x,dict):
        out={}
        for k,v in x.items():
            if k=="query" and isinstance(v,str) and "queries/" in v:
                out[k]="queries/"+v.split("queries/",1)[1]
            elif k=="stderr_tail" and isinstance(v,str):
                out[k]=TMP_RE.sub("/tmp/TEMP.smt2",v)
            else: out[k]=canonicalize(v)
        return out
    if isinstance(x,list): return [canonicalize(v) for v in x]
    return x

def canon_digest(p):
    o=json.loads(Path(p).read_text()); b=(json.dumps(canonicalize(o),indent=2,sort_keys=True)+"\n").encode(); return hashlib.sha256(b).hexdigest()

def query_map(root):
    root=Path(root);return {str(p.relative_to(root)).replace(os.sep,"/"):sha_file(p) for p in sorted(root.rglob("*.smt2"))}

def z3_run_text(text):
    s=z3.Solver();s.from_string(text);r=s.check();return str(r)

def cvc5_run(binary,path):
    p=subprocess.run([binary,str(path)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    if p.returncode!=0:raise RuntimeError((p.stdout+p.stderr)[-2000:])
    return (p.stdout.strip().splitlines() or [""])[0].strip()

def bench_real_queries(refroot,cvc5):
    rows=[]
    for p in sorted(Path(refroot).rglob("*.smt2")):
        rel=str(p.relative_to(refroot)).replace(os.sep,"/");text=p.read_text();
        zr=z3_run_text(text); cr=cvc5_run(cvc5,p)
        if zr!=cr: raise RuntimeError(f"solver mismatch warmup {rel}: {zr}/{cr}")
        zt=[];ct=[]
        for _ in range(REPS):
            t=time.perf_counter_ns(); r=z3_run_text(text); zt.append((time.perf_counter_ns()-t)/1e6)
            if r!=zr:raise RuntimeError("z3 instability")
            t=time.perf_counter_ns(); r=cvc5_run(cvc5,p); ct.append((time.perf_counter_ns()-t)/1e6)
            if r!=cr:raise RuntimeError("cvc5 instability")
        rows.append({"query":rel,"sha256":sha_file(p),"result":zr,"bytes":p.stat().st_size,"declare_fun_count":text.count("(declare-fun"),"assert_count":text.count("(assert"),"fixed_binding_count":sum(1 for l in text.splitlines() if l.strip().startswith("(assert (= ")),"z3_ms":zt,"cvc5_ms":ct,"z3":stats(zt),"cvc5":stats(ct)})
    if len(rows)!=32:raise RuntimeError(f"real query count {len(rows)}")
    out={}
    for solver in ("z3","cvc5"):
        meds=[r[solver]["p50"] for r in rows]; allcalls=[v for r in rows for v in r[solver+"_ms"]]
        out[solver]={"per_query_median_p50_ms":nr_pct(meds,.5),"per_query_median_p95_ms":nr_pct(meds,.95),"per_query_median_max_ms":max(meds),"all_calls":stats(allcalls)}
    return rows,out

def skeleton(A,H):
    decl=[]; trans=[]
    for t in range(H+1):
        for i in range(A):decl.append(f"(declare-fun x_{t}_{i} () Bool)")
    for t in range(H):
        for i in range(A):trans.append(f"(= x_{t+1}_{i} (xor x_{t}_{i} x_{t}_{(i+1)%A}))")
    F="(and "+" ".join(trans)+")" if trans else "true"
    return decl,F

def emit_query(A,H,family,direction,path):
    decl,F=skeleton(A,H); x="x_0_0"
    if family=="STRICT_EXPANSION":
        old=f"(and {F} (not {x}))"; new=F; expected="sat" if direction=="new_not_old" else "unsat"
    else:
        old=F; new=f"(and {F} (or {x} (not {x})) (or x_0_{A-1} (not x_0_{A-1})))"; expected="unsat"
    q=f"(and {new} (not {old}))" if direction=="new_not_old" else f"(and {old} (not {new}))"
    text="(set-logic ALL)\n"+"\n".join(decl)+f"\n(assert {q})\n(check-sat)\n"
    Path(path).write_text(text)
    return {"expected":expected,"unrolled_boolean_variables":A*(H+1),"assertion_count":1,"bytes":len(text.encode()),"query":q}

def bench_ladder(outdir,cvc5):
    rows=[]; qdir=Path(outdir)/"ladder_queries";qdir.mkdir(parents=True,exist_ok=True)
    for A in ATOMS:
      for H in HORIZONS:
       for fam in ("STRICT_EXPANSION","EQUALITY"):
        for direction in ("new_not_old","old_not_new"):
            p=qdir/f"a{A}_h{H}_{fam}_{direction}.smt2";meta=emit_query(A,H,fam,direction,p);text=p.read_text()
            z0=z3_run_text(text);c0=cvc5_run(cvc5,p)
            if z0!=meta["expected"] or c0!=meta["expected"]:raise RuntimeError(f"ladder warmup mismatch {p} {z0}/{c0}/{meta['expected']}")
            zt=[];ct=[]
            for _ in range(REPS):
                t=time.perf_counter_ns();r=z3_run_text(text);zt.append((time.perf_counter_ns()-t)/1e6)
                if r!=meta["expected"]:raise RuntimeError("z3 ladder instability")
                t=time.perf_counter_ns();r=cvc5_run(cvc5,p);ct.append((time.perf_counter_ns()-t)/1e6)
                if r!=meta["expected"]:raise RuntimeError("cvc5 ladder instability")
            rows.append({"atoms":A,"horizon":H,"family":fam,"direction":direction,"expected":meta["expected"],"unrolled_boolean_variables":meta["unrolled_boolean_variables"],"assertion_count":meta["assertion_count"],"smt2_bytes":meta["bytes"],"sha256":sha_file(p),"z3_ms":zt,"cvc5_ms":ct,"z3":stats(zt),"cvc5":stats(ct)})
    return rows

def slope(v1,t1,v2,t2):
    if t1<=0 or t2<=0 or v1<=0 or v2<=v1:return None
    return math.log(t2/t1)/math.log(v2/v1)

def eval_t2(rows,solver):
    checks=[]
    # four sweeps: each family/direction, atoms at H=64 and horizons at A=1024
    for fam in ("STRICT_EXPANSION","EQUALITY"):
      for direction in ("new_not_old","old_not_new"):
       for axis,levels,fixed_key,fixed in (("atoms",ATOMS,"horizon",64),("horizon",HORIZONS,"atoms",1024)):
        sub=[]
        for lv in levels:
            rr=next(r for r in rows if r[axis]==lv and r[fixed_key]==fixed and r["family"]==fam and r["direction"]==direction)
            sub.append(rr)
        ts=[r[solver]["p50"] for r in sub];vs=[r["unrolled_boolean_variables"] for r in sub]
        ratio=ts[-1]/ts[0] if ts[0]>0 else float("inf"); sl=slope(vs[0],ts[0],vs[-1],ts[-1]);mono=all(ts[i+1]>ts[i] for i in range(3)); fired=ratio>=10 and sl is not None and sl>1.25 and mono
        checks.append({"solver":solver,"family":fam,"direction":direction,"axis":axis,"fixed":fixed,"medians_ms":ts,"variables":vs,"ratio":ratio,"endpoint_loglog_slope":sl,"strictly_increasing":mono,"T2_fired":fired})
    return checks

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--zipcpu",required=True);ap.add_argument("--riscv-formal",required=True);ap.add_argument("--opentitan",required=True);ap.add_argument("--reference-r2",required=True);ap.add_argument("--cvc5",required=True);ap.add_argument("--out",required=True);ap.add_argument("--network-ms",type=float,default=None);ap.add_argument("--hydration-ms",type=float,default=None)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);work=out/"work";work.mkdir(exist_ok=True)
    ref=Path(a.reference_r2);refmap=query_map(ref/"queries");
    if len(refmap)!=32:raise RuntimeError("reference query count")
    stages={k:[] for k in ("P0_HISTORY_CORPUS","P1_EXTRACT_NORMALIZE","P2_CLASSIFY_QUERY_BUILD")}
    p2_dirs=[]
    for rep in range(REPS):
        rdir=work/f"rep{rep}";rdir.mkdir(exist_ok=True);p0=rdir/"p0";p1=rdir/"p1";p2=rdir/"p2"
        m=timed_cmd(["python3","smoke/g6_r0_blind_corpus_seal.py","--zipcpu",a.zipcpu,"--riscv-formal",a.riscv_formal,"--opentitan",a.opentitan,"--out",str(p0)])
        if sha_file(p0/"g6-r0-corpus-manifest.json")!=CORPUS_SHA:raise RuntimeError("P0 identity mismatch");stages["P0_HISTORY_CORPUS"].append(m)
        m=timed_cmd(["python3","smoke/g6_r1_exact_historical_inventory.py","--manifest",str(p0/"g6-r0-corpus-manifest.json"),"--zipcpu",a.zipcpu,"--riscv-formal",a.riscv_formal,"--opentitan",a.opentitan,"--out",str(p1)])
        if sha_file(p1/"g6-r1-inventory.json")!=R1_SHA:raise RuntimeError("P1 identity mismatch");stages["P1_EXTRACT_NORMALIZE"].append(m)
        m=timed_cmd(["python3","smoke/g6_r2_blind_semantic_classification.py","--r1",str(p1/"g6-r1-inventory.json"),"--cvc5",a.cvc5,"--out",str(p2)])
        if canon_digest(p2/"g6-r2-results.json")!=R2_CANON_SHA:raise RuntimeError("P2 canonical identity mismatch")
        qm=query_map(p2/"queries")
        if qm!=refmap:raise RuntimeError("P2 exact query-byte map mismatch")
        stages["P2_CLASSIFY_QUERY_BUILD"].append(m);p2_dirs.append(p2)
    stage_summary={}
    for k,rr in stages.items():
        stage_summary[k]={"wall_ms":stats([x["wall_ms"] for x in rr]),"maxrss_kib":stats([x["maxrss_kib"] for x in rr if x["maxrss_kib"] is not None]),"repetitions":rr}
    core=sum(stage_summary[k]["wall_ms"]["p95"] for k in stage_summary)
    for k in stage_summary:stage_summary[k]["p95_share_of_core"]=stage_summary[k]["wall_ms"]["p95"]/core
    t1=[k for k in stage_summary if stage_summary[k]["p95_share_of_core"]>=.5]
    real_rows,real_summary=bench_real_queries(ref/"queries",a.cvc5)
    ladder=bench_ladder(out,a.cvc5)
    t2=eval_t2(ladder,"z3")+eval_t2(ladder,"cvc5")
    t2f=[x for x in t2 if x["T2_fired"]]
    t3=[]
    for r in ladder:
        for s in ("z3","cvc5"):
            over=sum(v>=1000 for v in r[s+"_ms"])
            if over>=2:t3.append({"kind":"latency","solver":s,"point":{k:r[k] for k in ("atoms","horizon","family","direction")},"over_1000ms_reps":over})
    t4=[]
    for r in real_rows:
        for s in ("z3","cvc5"):
            if r[s]["p50"]>=100 or r[s]["max"]>=1000:t4.append({"query":r["query"],"solver":s,"p50_ms":r[s]["p50"],"max_ms":r[s]["max"]})
    rss=max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    if rss>=524288:t3.append({"kind":"process_rss","maxrss_kib":rss})
    triggers={"T1_stages":t1,"T2_checks_fired":t2f,"T3":t3,"T4":t4,"any_trigger":bool(t1 or t2f or t3 or t4)}
    result={"schema":"g8-scale-frontier-result-v1","versions":{"z3":z3.get_version_string(),"cvc5":"1.3.4 pinned workflow"},"network":{"clone_and_reference_fetch_ms":a.network_ms,"untimed_hydration_ms":a.hydration_ms,"excluded_from_T1":True},"real_pipeline":stage_summary,"real_queries":{"query_count":32,"summary":real_summary,"rows":real_rows},"controlled_ladder":{"points":16,"query_rows":len(ladder),"rows":ladder,"T2_checks":t2},"process_maxrss_kib":rss,"triggers":triggers}
    (out/"g8-scale-frontier.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    (out/"summary.json").write_text(json.dumps({"stage_summary":{k:{"wall_ms":v["wall_ms"],"maxrss_kib":v["maxrss_kib"],"p95_share_of_core":v["p95_share_of_core"]} for k,v in stage_summary.items()},"real_query_summary":real_summary,"process_maxrss_kib":rss,"triggers":triggers},indent=2,sort_keys=True)+"\n")
    print(json.dumps(json.loads((out/"summary.json").read_text()),sort_keys=True))
if __name__=="__main__":main()
