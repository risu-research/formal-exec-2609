#!/usr/bin/env python3
"""Independent SMT2 theorem check for ProofScope TNF.

For every clocked assumption identified in flattened JSON, prove:
  transition(s,n) -> (Yosys_u(n) == (D_EN(s) => D_CHECK(s)))
by asking Z3 for the XOR and requiring UNSAT.
"""
import argparse, json, re, subprocess, tempfile
from pathlib import Path

U_RE=re.compile(r'^\(define-fun \|pipemem_u (\d+)\| .*')
Q_RE=re.compile(r'\|pipemem#(\d+)\| state')
T_RE=re.compile(r'^  \(= (.+) \(\|pipemem#(\d+)\| next_state\)\) ;')

def parse_smt(path):
    u,trans={},{}
    for line in Path(path).read_text().splitlines():
        m=U_RE.match(line)
        if m: u[int(m.group(1))]=line
        m=T_RE.match(line)
        if m: trans[int(m.group(2))]=m.group(1)
    return u,trans

def phase_indices(json_path):
    d=json.load(open(json_path)); m=next(iter(d['modules'].values()))
    q=set()
    for c in m['cells'].values():
        if c['type']=='$dff': q.update(b for b in c['connections']['Q'] if isinstance(b,int))
    out=[]
    for idx,c in enumerate(c for c in m['cells'].values() if c['type']=='$assume'):
        aq=c['connections']['A'][0] in q; eq=c['connections']['EN'][0] in q
        if aq and eq: out.append(idx)
        elif aq != eq: raise RuntimeError(f'mixed formal at {idx}')
    return out

def validate(json_path,smt_path):
    text=Path(smt_path).read_text(); u,trans=parse_smt(smt_path); results=[]
    for idx in phase_indices(json_path):
        line=u[idx]; qs=Q_RE.findall(line)
        if len(qs)!=2: raise RuntimeError(f'assume {idx}: expected CHECK/EN refs, got {qs}')
        qc,qe=map(int,qs)
        if qc not in trans or qe not in trans: raise RuntimeError(f'assume {idx}: missing transition update')
        dc,de=trans[qc],trans[qe]
        query=f'''\n(declare-const ps_s |pipemem_s|)\n(declare-const ps_n |pipemem_s|)\n(assert (|pipemem_t| ps_s ps_n))\n(assert (xor (|pipemem_u {idx}| ps_n)\n  (or (= ((_ extract 0 0) {dc.replace(' state',' ps_s')}) #b1)\n      (not (= ((_ extract 0 0) {de.replace(' state',' ps_s')}) #b1)))))\n(check-sat)\n'''
        with tempfile.NamedTemporaryFile('w',suffix='.smt2',delete=False) as f:
            f.write(text); f.write(query); name=f.name
        p=subprocess.run(['z3',name],capture_output=True,text=True,timeout=30)
        ans=p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''
        results.append({'index':idx,'result':ans,'check_q':qc,'en_q':qe})
        if ans!='unsat': raise RuntimeError(f'TNF theorem failed for assumption {idx}: {ans} {p.stderr}')
    return results

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('smt2'); ns=ap.parse_args()
    r=validate(ns.json,ns.smt2)
    print(json.dumps({'schema':'proofscope-smt2-tnf-validation-v1','validated':len(r),'all_unsat':True,'results':r},indent=2))
if __name__=='__main__': main()
