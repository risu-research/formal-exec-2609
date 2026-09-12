#!/usr/bin/env python3
"""Independent SMT2 theorem check for ProofScope TNF.

For every clocked assumption identified in flattened JSON, prove:
  transition(s,n) -> (Yosys_u(n) == (D_EN(s) => D_CHECK(s)))
by asking Z3 for the XOR and requiring UNSAT.

The validator is module-generic: the SMT namespace is derived from the single
flattened JSON top module rather than being hard-coded to the case study.
"""
import argparse, json, re, subprocess, tempfile
from pathlib import Path


def patterns(module):
    m=re.escape(module)
    return (
        re.compile(rf'^\(define-fun \|{m}_u (\d+)\| .*'),
        re.compile(rf'\|{m}#(\d+)\| state'),
        re.compile(rf'^  \(= (.+) \(\|{m}#(\d+)\| next_state\)\) ;')
    )


def parse_smt(path,module):
    U_RE,Q_RE,T_RE=patterns(module)
    u,trans={},{}
    for line in Path(path).read_text().splitlines():
        mm=U_RE.match(line)
        if mm: u[int(mm.group(1))]=line
        mm=T_RE.match(line)
        if mm: trans[int(mm.group(2))]=mm.group(1)
    return u,trans,Q_RE


def json_top_and_phase_indices(json_path):
    d=json.load(open(json_path))
    if len(d.get('modules',{}))!=1: raise RuntimeError('expected one flattened top module')
    module,m=next(iter(d['modules'].items()))
    q=set()
    for c in m['cells'].values():
        if c['type']=='$dff': q.update(b for b in c['connections']['Q'] if isinstance(b,int))
    out=[]; total=0
    for idx,c in enumerate(c for c in m['cells'].values() if c['type']=='$assume'):
        total += 1
        aq=c['connections']['A'][0] in q; eq=c['connections']['EN'][0] in q
        if aq and eq: out.append(idx)
        elif aq != eq: raise RuntimeError(f'mixed formal at {idx}')
    return module,out,total


def validate(json_path,smt_path):
    module,clocked,total=json_top_and_phase_indices(json_path)
    text=Path(smt_path).read_text(); u,trans,Q_RE=parse_smt(smt_path,module); results=[]
    expected=set(range(total))
    if set(u)!=expected:
        raise RuntimeError(f'SMT assumption index set mismatch: expected {sorted(expected)}, got {sorted(u)}')
    for idx in clocked:
        line=u[idx]; qs=Q_RE.findall(line)
        if len(qs)!=2: raise RuntimeError(f'assume {idx}: expected CHECK/EN refs, got {qs}')
        qc,qe=map(int,qs)
        if qc not in trans or qe not in trans: raise RuntimeError(f'assume {idx}: missing transition update')
        dc,de=trans[qc],trans[qe]
        dc=dc.replace(' state',' ps_s'); de=de.replace(' state',' ps_s')
        query=f'''\n(declare-const ps_s |{module}_s|)\n(declare-const ps_n |{module}_s|)\n(assert (|{module}_t| ps_s ps_n))\n(assert (xor (|{module}_u {idx}| ps_n)\n  (or (= ((_ extract 0 0) {dc}) #b1)\n      (not (= ((_ extract 0 0) {de}) #b1)))))\n(check-sat)\n'''
        with tempfile.NamedTemporaryFile('w',suffix='.smt2',delete=False) as f:
            f.write(text); f.write(query); name=f.name
        p=subprocess.run(['z3',name],capture_output=True,text=True,timeout=30)
        ans=p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''
        results.append({'index':idx,'result':ans,'check_q':qc,'en_q':qe})
        if ans!='unsat': raise RuntimeError(f'TNF theorem failed for assumption {idx}: {ans} {p.stderr}')
    return module,total,results


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('smt2'); ns=ap.parse_args()
    module,total,r=validate(ns.json,ns.smt2)
    print(json.dumps({'schema':'proofscope-smt2-tnf-validation-v2','module':module,'assumptions_total':total,'clocked_validated':len(r),'all_unsat':True,'results':r},indent=2))
if __name__=='__main__': main()
