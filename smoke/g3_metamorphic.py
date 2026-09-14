#!/usr/bin/env python3
"""Independent G3 metamorphic oracle over flattened Yosys JSON.

Imports only the public TNF reference implementation, never the frozen production
classifier.  It checks relation-level invariance under representation-preserving
changes and requires meaning-changing controls to be detected.

G3 diagnostic instrumentation note: TRACE lines are observational only. They do
not change transforms, expected relations, comparison criteria, or pass/fail
semantics; they exist so failed frozen obligations remain attributable in CI.
"""
from __future__ import annotations
import argparse, copy, json, re, tempfile
from pathlib import Path
from tnf_scope import analyze


def trace(msg): print(f"G3_META_TRACE {msg}", flush=True)
def load(p): return json.loads(Path(p).read_text())
def dump(p,o): Path(p).write_text(json.dumps(o,sort_keys=True,separators=(",",":")))
def mod(o):
    ms=o.get("modules",{})
    if len(ms)!=1: raise RuntimeError("expected one flattened module")
    return next(iter(ms.values()))
def maxbit(o):
    m=mod(o); xs=[]
    for p in m.get("ports",{}).values(): xs += [x for x in p.get("bits",[]) if isinstance(x,int)]
    for n in m.get("netnames",{}).values(): xs += [x for x in n.get("bits",[]) if isinstance(x,int)]
    for c in m.get("cells",{}).values():
        for bs in c.get("connections",{}).values(): xs += [x for x in bs if isinstance(x,int)]
    return max(xs,default=1)

def reorder_cells(o):
    o=copy.deepcopy(o); m=mod(o); m["cells"]=dict(reversed(list(m.get("cells",{}).items()))); return o
def reorder_formals(o):
    o=copy.deepcopy(o); m=mod(o); xs=list(m.get("cells",{}).items())
    a=[x for x in xs if x[1].get("type") not in ("$assume","$assert")]
    b=[x for x in xs if x[1].get("type") in ("$assume","$assert")]
    m["cells"]=dict(a+list(reversed(b))); return o
def reorder_nets(o):
    o=copy.deepcopy(o); m=mod(o); m["netnames"]=dict(reversed(list(m.get("netnames",{}).items()))); return o
def shift_src(o):
    o=copy.deepcopy(o); pat=re.compile(r"(\d+)\.(\d+)-(\d+)\.(\d+)")
    def f(s):
        if not isinstance(s,str): return s
        return pat.sub(lambda x:f"{int(x.group(1))+1000}.{x.group(2)}-{int(x.group(3))+1000}.{x.group(4)}",s)
    m=mod(o)
    for x in list(m.get("netnames",{}).values())+list(m.get("cells",{}).values()):
        a=x.get("attributes",{}); a["src"]=f(a["src"]) if "src" in a else a.get("src","")
    return o
def duplicate_assume(o):
    o=copy.deepcopy(o); cs=mod(o).setdefault("cells",{})
    for _,c in list(cs.items()):
        if c.get("type")=="$assume":
            n="__g3_dup__"
            while n in cs:n+="x"
            cs[n]=copy.deepcopy(c); return o
    raise RuntimeError("no assumption")
def add_true_assume(o):
    o=copy.deepcopy(o); cs=mod(o).setdefault("cells",{}); n="__g3_true__"
    while n in cs:n+="x"
    cs[n]={"type":"$assume","port_directions":{"A":"input","EN":"input"},"connections":{"A":["1"],"EN":["1"]},"attributes":{"src":"g3:1.1-1.1"}}
    return o
def double_not(o):
    o=copy.deepcopy(o); m=mod(o); cs=m.setdefault("cells",{}); target=None
    for c in cs.values():
        if c.get("type")=="$assume" and len(c.get("connections",{}).get("A",[]))==1: target=c; break
    if target is None: raise RuntimeError("no scalar assumption")
    orig=copy.deepcopy(target["connections"]["A"]); b1=maxbit(o)+1; b2=b1+1
    cs["__g3_not1__"]={"type":"$logic_not","port_directions":{"A":"input","Y":"output"},"connections":{"A":orig,"Y":[b1]},"parameters":{}}
    cs["__g3_not2__"]={"type":"$logic_not","port_directions":{"A":"input","Y":"output"},"connections":{"A":[b1],"Y":[b2]},"parameters":{}}
    target["connections"]["A"]=[b2]; return o
def rename_past(o):
    o=copy.deepcopy(o); m=mod(o)
    for key in ("cells","netnames"):
        out={}
        for n,v in m.get(key,{}).items():
            nn=n.replace("$past$","$past$g3alias$") if "$past$" in n else n
            if nn in out: raise RuntimeError("rename collision")
            out[nn]=v
        m[key]=out
    return o
def semantic_change(o):
    o=copy.deepcopy(o)
    for c in mod(o).get("cells",{}).values():
        q=c.get("connections",{})
        if c.get("type")=="$assume" and len(q.get("A",[]))==1 and len(q.get("EN",[]))==1:
            q["A"]=["0"]; q["EN"]=["1"]; return o
    raise RuntimeError("no scalar assumption")

TRANSFORMS=[("cell_order",reorder_cells),("formal_order",reorder_formals),("net_order",reorder_nets),("source_line_shift",shift_src),("duplicate_conjunct",duplicate_assume),("tautology_insertion",add_true_assume),("double_negation",double_not),("history_alias_rename",rename_past)]
def sig(r): return {"relation":r["relation"],"old_only_sat":r["old_only_sat"],"new_only_sat":r["new_only_sat"]}

def pair(label,op,np,expect,td):
    old,new=load(op),load(np); trace(f"pair={label} phase=baseline")
    base=sig(analyze(op,np))
    trace(f"pair={label} baseline={json.dumps(base,sort_keys=True)}")
    if base["relation"]!=expect: raise RuntimeError(f"{label}: expected {expect}, got {base}")
    rec=[]
    for name,fn in TRANSFORMS:
        for side in ("old","new","both"):
            trace(f"pair={label} family={name} side={side} phase=begin")
            a=fn(old) if side in ("old","both") else copy.deepcopy(old)
            b=fn(new) if side in ("new","both") else copy.deepcopy(new)
            ap=td/f"{label}-{name}-{side}-0.json"; bp=td/f"{label}-{name}-{side}-1.json"; dump(ap,a); dump(bp,b)
            got=sig(analyze(str(ap),str(bp))); ok=got==base
            trace(f"pair={label} family={name} side={side} observed={json.dumps(got,sort_keys=True)} pass={str(ok).lower()}")
            rec.append({"family":name,"side":side,"expected":base,"observed":got,"pass":ok})
            if not ok: raise RuntimeError(f"{label}/{name}/{side}: {got} != {base}")
    neg=[]
    for side in ("old","new"):
        trace(f"pair={label} negative_side={side} phase=begin")
        a=semantic_change(old) if side=="old" else copy.deepcopy(old)
        b=semantic_change(new) if side=="new" else copy.deepcopy(new)
        ap=td/f"{label}-negative-{side}-0.json"; bp=td/f"{label}-negative-{side}-1.json"; dump(ap,a); dump(bp,b)
        got=sig(analyze(str(ap),str(bp))); detected=got!=base
        trace(f"pair={label} negative_side={side} observed={json.dumps(got,sort_keys=True)} detected={str(detected).lower()}")
        neg.append({"side":side,"baseline":base,"observed":got,"detected":detected})
        if not detected: raise RuntimeError(f"{label}/negative/{side}: semantic change masked")
    return {"label":label,"baseline":base,"preserving":rec,"negative_controls":neg}

def main():
    ap=argparse.ArgumentParser();
    for x in ("natural_old","natural_new","mirror_old","mirror_new"): ap.add_argument("--"+x.replace("_","-"),dest=x,required=True)
    ap.add_argument("--out",required=True); ns=ap.parse_args()
    with tempfile.TemporaryDirectory() as x:
        td=Path(x); ps=[pair("natural",ns.natural_old,ns.natural_new,"INCOMPARABLE",td),pair("mirror",ns.mirror_old,ns.mirror_new,"CONTRACT",td)]
    out={"schema":"g3-independent-metamorphic-v1","implementation":"public_tnf_reference_not_production_g2","pairs":ps,"preserving_checks":sum(len(p["preserving"]) for p in ps),"negative_controls":sum(len(p["negative_controls"]) for p in ps),"all_pass":True}
    Path(ns.out).write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print(f"G3_METAMORPHIC_PASS preserving={out['preserving_checks']} negative={out['negative_controls']}")
if __name__=="__main__": main()
