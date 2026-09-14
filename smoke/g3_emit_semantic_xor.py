#!/usr/bin/env python3
"""Emit an SMT-LIB XOR query for two TNF conjunctions without deciding it."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import z3
from tnf_scope import IR


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def conjunction(path):
    ir=IR(path); aa=ir.assumptions()
    if ir.unsupported: raise RuntimeError(f"unsupported cells: {sorted(ir.unsupported)}")
    if ir.mixed_formal: raise RuntimeError(f"mixed formal cells: {ir.mixed_formal}")
    f=z3.And(*[x['f'] for x in aa]) if aa else z3.BoolVal(True)
    return z3.simplify(f),len(aa),ir.sha256

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('baseline'); ap.add_argument('transformed'); ap.add_argument('out'); ns=ap.parse_args()
    a,na,ha=conjunction(ns.baseline); b,nb,hb=conjunction(ns.transformed)
    s=z3.Solver(); s.add(z3.Xor(a,b))
    p=Path(ns.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(s.to_smt2())
    print(json.dumps({'schema':'g3-tnf-semantic-xor-v1','baseline_sha256':ha,'transformed_sha256':hb,'baseline_assumptions':na,'transformed_assumptions':nb,'query_sha256':sha(p)},sort_keys=True))
if __name__=='__main__': main()
