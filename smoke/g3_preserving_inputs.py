#!/usr/bin/env python3
"""Deterministically emit G3 preserving metamorphic Yosys-JSON variants."""
from __future__ import annotations
import argparse, copy, hashlib, json, re
from pathlib import Path


def load(p): return json.loads(Path(p).read_text())
def dump(p,o):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,sort_keys=True,separators=(",",":"))+"\n")
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def module(o):
    ms=o.get("modules",{})
    if len(ms)!=1: raise RuntimeError("expected one flattened module")
    return next(iter(ms.values()))
def maxbit(o):
    m=module(o); xs=[]
    for p in m.get("ports",{}).values(): xs += [x for x in p.get("bits",[]) if isinstance(x,int)]
    for n in m.get("netnames",{}).values(): xs += [x for x in n.get("bits",[]) if isinstance(x,int)]
    for c in m.get("cells",{}).values():
        for bs in c.get("connections",{}).values(): xs += [x for x in bs if isinstance(x,int)]
    return max(xs,default=1)

def cell_order(o):
    o=copy.deepcopy(o); m=module(o); m["cells"]=dict(reversed(list(m.get("cells",{}).items()))); return o
def formal_order(o):
    o=copy.deepcopy(o); m=module(o); xs=list(m.get("cells",{}).items())
    a=[x for x in xs if x[1].get("type") not in ("$assume","$assert")]
    b=[x for x in xs if x[1].get("type") in ("$assume","$assert")]
    m["cells"]=dict(a+list(reversed(b))); return o
def net_order(o):
    o=copy.deepcopy(o); m=module(o); m["netnames"]=dict(reversed(list(m.get("netnames",{}).items()))); return o
def source_line_shift(o):
    o=copy.deepcopy(o); pat=re.compile(r"(\d+)\.(\d+)-(\d+)\.(\d+)")
    def f(s):
        if not isinstance(s,str): return s
        return pat.sub(lambda x:f"{int(x.group(1))+1000}.{x.group(2)}-{int(x.group(3))+1000}.{x.group(4)}",s)
    m=module(o)
    for x in list(m.get("netnames",{}).values())+list(m.get("cells",{}).values()):
        a=x.setdefault("attributes",{})
        if "src" in a: a["src"]=f(a["src"])
    return o
def duplicate_conjunct(o):
    o=copy.deepcopy(o); cs=module(o).setdefault("cells",{})
    for c in list(cs.values()):
        if c.get("type")=="$assume":
            n="__g3_dup__"
            while n in cs: n+="x"
            cs[n]=copy.deepcopy(c); return o
    raise RuntimeError("no assumption")
def tautology_insertion(o):
    o=copy.deepcopy(o); cs=module(o).setdefault("cells",{}); n="__g3_true__"
    while n in cs: n+="x"
    cs[n]={"type":"$assume","parameters":{},"port_directions":{"A":"input","EN":"input"},"connections":{"A":["1"],"EN":["1"]},"attributes":{"src":"g3:1.1-1.1"}}
    return o
def double_negation(o):
    o=copy.deepcopy(o); cs=module(o).setdefault("cells",{}); target=None
    for c in cs.values():
        if c.get("type")=="$assume" and len(c.get("connections",{}).get("A",[]))==1:
            target=c; break
    if target is None: raise RuntimeError("no scalar assumption")
    orig=copy.deepcopy(target["connections"]["A"]); b1=maxbit(o)+1; b2=b1+1
    cs["__g3_not1__"]={"type":"$logic_not","port_directions":{"A":"input","Y":"output"},"connections":{"A":orig,"Y":[b1]},"parameters":{},"attributes":{}}
    cs["__g3_not2__"]={"type":"$logic_not","port_directions":{"A":"input","Y":"output"},"connections":{"A":[b1],"Y":[b2]},"parameters":{},"attributes":{}}
    target["connections"]["A"]=[b2]; return o
def history_alias_rename(o):
    o=copy.deepcopy(o); m=module(o)
    for key in ("cells","netnames"):
        out={}
        for n,v in m.get(key,{}).items():
            nn=n.replace("$past$","$past$g3alias$") if "$past$" in n else n
            if nn in out: raise RuntimeError("rename collision")
            out[nn]=v
        m[key]=out
    return o
def private_aux_alpha(o):
    o=copy.deepcopy(o); m=module(o); out={}; k=0
    for n,c in m.get("cells",{}).items():
        generated=n.startswith("$") or ".$" in n or n.startswith("__")
        formal=c.get("type") in ("$assume","$assert")
        if generated and not formal:
            nn=f"__g3_alpha_{k:05d}__"; k+=1
            while nn in out or nn in m.get("cells",{}): nn += "x"
        else: nn=n
        if nn in out: raise RuntimeError("alpha rename collision")
        out[nn]=c
    if k==0: raise RuntimeError("no generated private cell")
    m["cells"]=out; return o
def commutative_operands(o):
    o=copy.deepcopy(o); changed=0
    for c in module(o).get("cells",{}).values():
        t=c.get("type"); q=c.get("connections",{}); p=c.get("parameters",{})
        if t not in ("$and","$or","$xor","$xnor","$logic_and","$logic_or"): continue
        if "A" not in q or "B" not in q: continue
        if len(q["A"])!=len(q["B"]): continue
        if str(p.get("A_SIGNED","0"))!=str(p.get("B_SIGNED","0")): continue
        q["A"],q["B"]=q["B"],q["A"]; changed+=1
    if changed==0: raise RuntimeError("no applicable commutative operands")
    return o

TRANSFORMS=[
 ("cell_order",cell_order),("formal_order",formal_order),("net_order",net_order),
 ("source_line_shift",source_line_shift),("private_aux_alpha",private_aux_alpha),
 ("commutative_operands",commutative_operands),("duplicate_conjunct",duplicate_conjunct),
 ("tautology_insertion",tautology_insertion),("double_negation",double_negation),
 ("history_alias_rename",history_alias_rename)]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--label",required=True); ap.add_argument("--out",required=True); ns=ap.parse_args()
    base=load(ns.input); root=Path(ns.out)/ns.label; rec=[]
    for name,fn in TRANSFORMS:
        p=root/f"{name}.json"; dump(p,fn(base)); rec.append({"family":name,"path":str(p),"sha256":sha(p)})
    manifest={"schema":"g3-preserving-inputs-v1","label":ns.label,"baseline":str(ns.input),"baseline_sha256":sha(ns.input),"families":rec}
    mp=root/"manifest.json"; mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"label":ns.label,"count":len(rec),"manifest_sha256":sha(mp)},sort_keys=True))
if __name__=="__main__": main()
