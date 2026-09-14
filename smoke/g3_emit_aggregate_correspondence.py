#!/usr/bin/env python3
"""Emit one aggregate solver-facing Yosys↔TNF correspondence miter.

Requires every governed assumption in the flattened artifact to be clocked.  The
query asserts one transition and XORs the conjunction of Yosys assumption
predicates on next-state against the conjunction of transition-normalized
D_EN => D_CHECK formulas on predecessor state.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from validate_tnf_smt2 import parse_smt, json_top_and_phase_indices


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('smt2'); ap.add_argument('out'); ns=ap.parse_args()
    module,clocked,total=json_top_and_phase_indices(ns.json)
    if len(clocked)!=total:
        raise RuntimeError(f'aggregate G3 correspondence requires all assumptions clocked: {len(clocked)}/{total}')
    text=Path(ns.smt2).read_text(); u,trans,Q_RE=parse_smt(ns.smt2,module)
    if set(u)!=set(range(total)):
        raise RuntimeError(f'assumption index mismatch: expected {list(range(total))}, got {sorted(u)}')
    yu=[]; tn=[]; rows=[]
    for idx in clocked:
        qs=Q_RE.findall(u[idx])
        if len(qs)!=2: raise RuntimeError(f'assume {idx}: expected CHECK/EN refs, got {qs}')
        qc,qe=map(int,qs)
        if qc not in trans or qe not in trans: raise RuntimeError(f'assume {idx}: missing transition update')
        dc=trans[qc].replace(' state',' ps_s'); de=trans[qe].replace(' state',' ps_s')
        yu.append(f'(|{module}_u {idx}| ps_n)')
        tn.append(f'(or (= ((_ extract 0 0) {dc}) #b1) (not (= ((_ extract 0 0) {de}) #b1)))')
        rows.append({'index':idx,'check_q':qc,'en_q':qe})
    ya='true' if not yu else (yu[0] if len(yu)==1 else '(and\n    '+'\n    '.join(yu)+')')
    ta='true' if not tn else (tn[0] if len(tn)==1 else '(and\n    '+'\n    '.join(tn)+')')
    query=f'''\n(declare-const ps_s |{module}_s|)\n(declare-const ps_n |{module}_s|)\n(assert (|{module}_t| ps_s ps_n))\n(assert (xor\n  {ya}\n  {ta}))\n(check-sat)\n'''
    p=Path(ns.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text+query)
    print(json.dumps({'schema':'g3-yosys-tnf-aggregate-miter-v1','module':module,'assumptions_total':total,'clocked':len(clocked),'rows':rows,'query_sha256':sha(p)},sort_keys=True))
if __name__=='__main__': main()
