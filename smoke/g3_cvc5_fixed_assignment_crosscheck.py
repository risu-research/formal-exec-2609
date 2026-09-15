#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: pinned-cvc5 fixed-assignment cross-check over sealed G3 negative queries.

For every SAT NC1/NC2 candidate in one sealed shard, run the exact frozen symmetric-
difference SMT2 query under three complete assignments:
  canonical witness                       -> SAT
  frozen lex-first one-bit corruption    -> SAT/UNSAT exactly as sealed RED recorded
  independently classified first critical -> UNSAT

This script does not import TNF/authority replay code and does not search for or alter
any candidate, mutant, witness, or query.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path

SOURCE_RUN=34804261148
SOURCE_HEAD='ccd83335a9b81bc447d9740129668619c4e77267'
BP_RUN=34931813214
BP_HEAD='d93a1bc6ba6e2e2c0f7836166e3f59d93e62071c'

def wr(p,o):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')

def flip(w,b):
    q=dict(w); q[b]=1-int(q[b]); return q

def bind_query(src:Path,w:dict)->str:
    s=src.read_text()
    marker='(check-sat)'
    if s.count(marker)!=1: raise RuntimeError('query must contain exactly one check-sat')
    # Witness keys are the exact SMT-LIB declared symbol spellings retained by the sealed executor.
    a='\n'.join(f'(assert (= {k} #b{int(w[k])}))' for k in sorted(w))
    return s.replace(marker,a+'\n'+marker,1)

def solve(cvc5:str,text:str,timeout:int=30):
    with tempfile.NamedTemporaryFile('w',suffix='.smt2',delete=False) as f:
        f.write(text); name=f.name
    try:
        p=subprocess.run([cvc5,name],capture_output=True,text=True,timeout=timeout)
        first=(p.stdout.strip().splitlines() or [''])[0].strip()
        if p.returncode!=0: raise RuntimeError(f'cvc5 rc={p.returncode}: {(p.stdout+p.stderr)[-1000:]}')
        if first not in ('sat','unsat'): raise RuntimeError('unexpected cvc5 result '+repr(first))
        return {'result':first,'returncode':p.returncode,'stdout':p.stdout[-1000:],'stderr':p.stderr[-1000:]}
    finally:
        Path(name).unlink(missing_ok=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--sealed-root',required=True); ap.add_argument('--bitparallel',required=True); ap.add_argument('--cvc5',required=True); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    if not 0<=a.shard<16: raise RuntimeError('bad shard')
    root=Path(a.sealed_root); sx=json.loads((root/'shards'/f'SHARD_{a.shard:02d}.json').read_text())
    if sx.get('schema')!='g3-negative-shard-v2' or sx.get('shard_index')!=a.shard or sx.get('error_count')!=0: raise RuntimeError('bad sealed shard')
    bp=json.loads(Path(a.bitparallel).read_text())
    if bp.get('schema')!='g3-independent-bitparallel-fixed-replay-v1' or bp.get('source_red_run')!=SOURCE_RUN or bp.get('source_red_head')!=SOURCE_HEAD: raise RuntimeError('bad bitparallel provenance')
    if bp.get('total_candidates')!=237 or bp.get('total_flips')!=48027 or bp.get('no_critical')!=0: raise RuntimeError('bitparallel population drift')
    bm={(r['lane'],r['candidate_index']):r for r in bp['records']}
    rows=[]
    for lane in ('nc1','nc2'):
        for r in sx['records'][lane]:
            if r.get('result')!='SAT' or r.get('lane')!=lane: raise RuntimeError('sealed record drift')
            b=bm[(lane,r['candidate_index'])]
            if (b['artifact'],b['cell'],b['query_sha256'])!=(r['artifact'],r['cell'],r['query_sha256']): raise RuntimeError('candidate identity mismatch')
            qp=root/'shards'/r['query_relpath']
            if hashlib.sha256(qp.read_bytes()).hexdigest()!=r['query_sha256']: raise RuntimeError('query hash mismatch')
            w=r['canonical_witness']; stored=r['one_bit_corruption']['bit']; critical=b['first_critical_bit']
            if critical is None: raise RuntimeError('missing critical bit')
            canonical=solve(a.cvc5,bind_query(qp,w))
            stored_run=solve(a.cvc5,bind_query(qp,flip(w,stored)))
            critical_run=solve(a.cvc5,bind_query(qp,flip(w,critical)))
            expected_stored='unsat' if bool(r['one_bit_corruption']['rejected']) else 'sat'
            if canonical['result']!='sat': raise RuntimeError('canonical not SAT in cvc5')
            if stored_run['result']!=expected_stored: raise RuntimeError('stored corruption cvc5 disagreement')
            if critical_run['result']!='unsat': raise RuntimeError('critical corruption not UNSAT in cvc5')
            rows.append({'lane':lane,'candidate_index':r['candidate_index'],'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],'witness_bits':len(w),'stored_bit':stored,'stored_expected':expected_stored,'stored_result':stored_run['result'],'first_critical_bit':critical,'canonical_result':canonical['result'],'critical_result':critical_run['result'],'pass':True})
    rows.sort(key=lambda r:(r['lane'],r['candidate_index']))
    out={'schema':'g3-cvc5-fixed-assignment-crosscheck-shard-v1','authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY','source_red_run':SOURCE_RUN,'source_red_head':SOURCE_HEAD,'bitparallel_run':BP_RUN,'bitparallel_head':BP_HEAD,'shard':a.shard,'candidates':len(rows),'cvc5_queries':3*len(rows),'stored_rejected':sum(r['stored_result']=='unsat' for r in rows),'stored_not_rejected':sum(r['stored_result']=='sat' for r in rows),'critical_rejected':sum(r['critical_result']=='unsat' for r in rows),'all_pass':all(r['pass'] for r in rows),'rows':rows}
    wr(a.out,out); print(json.dumps({k:out[k] for k in ('shard','candidates','cvc5_queries','stored_rejected','stored_not_rejected','critical_rejected','all_pass')},sort_keys=True))
if __name__=='__main__': main()
