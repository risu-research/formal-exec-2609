#!/usr/bin/env python3
"""Emit standalone solver-facing XOR miters for clocked formal assumptions."""
import argparse, hashlib, json
from pathlib import Path
from validate_tnf_smt2 import parse_smt, json_top_and_phase_indices


def emit(json_path, smt_path, out_dir):
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    module,clocked,total=json_top_and_phase_indices(json_path)
    text=Path(smt_path).read_text(); u,trans,Q_RE=parse_smt(smt_path,module)
    expected=set(range(total))
    if set(u)!=expected:
        raise RuntimeError(f'SMT assumption index set mismatch: expected {sorted(expected)}, got {sorted(u)}')
    rec=[]
    for idx in clocked:
        qs=Q_RE.findall(u[idx])
        if len(qs)!=2: raise RuntimeError(f'assume {idx}: expected CHECK/EN refs, got {qs}')
        qc,qe=map(int,qs)
        if qc not in trans or qe not in trans: raise RuntimeError(f'assume {idx}: missing transition update')
        dc=trans[qc].replace(' state',' ps_s'); de=trans[qe].replace(' state',' ps_s')
        query=f'''\n(declare-const ps_s |{module}_s|)\n(declare-const ps_n |{module}_s|)\n(assert (|{module}_t| ps_s ps_n))\n(assert (xor (|{module}_u {idx}| ps_n)\n  (or (= ((_ extract 0 0) {dc}) #b1)\n      (not (= ((_ extract 0 0) {de}) #b1)))))\n(check-sat)\n'''
        p=out/f'miter-{idx:03d}.smt2'; p.write_text(text+query)
        rec.append({'index':idx,'check_q':qc,'en_q':qe,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'file':p.name})
    manifest={'schema':'solver-miter-set-v1','module':module,'assumptions_total':total,'clocked_count':len(rec),'miters':rec}
    (out/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    return manifest


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('smt2'); ap.add_argument('out'); ns=ap.parse_args()
    print(json.dumps(emit(ns.json,ns.smt2,ns.out),sort_keys=True,indent=2))
if __name__=='__main__': main()
