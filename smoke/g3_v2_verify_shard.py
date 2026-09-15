#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path
import z3
import g3_negative_controls_v3 as v3

g=v3.g
AMENDMENT_SHA='33c53b01bd437eca666adaf8f812df33d65a2bdb';SOURCE_HEAD='ccd83335a9b81bc447d9740129668619c4e77267'
EXP={'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74','natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def flip(w,b):q=dict(w);q[b]=1-int(q[b]);return q
def bind(src,w):
 s=src.read_text();m='(check-sat)'
 if s.count(m)!=1:raise RuntimeError('query check-sat cardinality')
 a='\n'.join(f'(assert (= {k} #b{int(w[k])}))' for k in sorted(w));return s.replace(m,a+'\n'+m,1)
def solve(cvc5,text,timeout=60):
 with tempfile.NamedTemporaryFile('w',suffix='.smt2',delete=False) as f:f.write(text);n=f.name
 try:
  p=subprocess.run([cvc5,n],capture_output=True,text=True,timeout=timeout);first=(p.stdout.strip().splitlines() or [''])[0].strip()
  if p.returncode!=0 or first not in ('sat','unsat'):raise RuntimeError(f'cvc5 failure rc={p.returncode} first={first!r} '+(p.stdout+p.stderr)[-1000:])
  return first
 finally:Path(n).unlink(missing_ok=True)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sealed-root',required=True);ap.add_argument('--selector',required=True);ap.add_argument('--cvc5',required=True);ap.add_argument('--shard',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 if not 0<=a.shard<16:raise RuntimeError('bad shard')
 root=Path(a.sealed_root);base=root/'input'/'frozen'/'base';sd=root/'shards';sx=json.loads((sd/f'SHARD_{a.shard:02d}.json').read_text())
 if {k:sha(base/(k+'.json')) for k in EXP}!=EXP or (sd/f'RUNNER_COMMIT_{a.shard}.txt').read_text().strip()!=SOURCE_HEAD:raise RuntimeError('source provenance drift')
 if sx.get('schema')!='g3-negative-shard-v2' or sx.get('shard_index')!=a.shard or sx.get('error_count')!=0:raise RuntimeError('bad sealed shard')
 se=json.loads(Path(a.selector).read_text())
 if se.get('schema')!='g3-amendment08-selector-v1' or se.get('amendment08_freeze_sha')!=AMENDMENT_SHA:raise RuntimeError('selector drift')
 sm={(r['lane'],r['candidate_index']):r for r in se['records']};rows=[]
 for lane in ('nc1','nc2'):
  challenge=lane=='nc2'
  for r in sx['records'][lane]:
   if r.get('result')!='SAT':raise RuntimeError('frozen candidate no longer SAT')
   s=sm[(lane,r['candidate_index'])]
   if (s['artifact'],s['cell'],s['query_sha256'])!=(r['artifact'],r['cell'],r['query_sha256']):raise RuntimeError('identity mismatch')
   qp=sd/r['query_relpath'];mp=sd/r['mutant_relpath'];bp=base/(r['artifact']+'.json')
   if sha(qp)!=r['query_sha256'] or sha(mp)!=r['mutant_sha256'] or sha(bp)!=r['baseline_sha256']:raise RuntimeError('evidence hash drift')
   dq=z3.simplify(z3.Xor(g.sem(bp,challenge),g.sem(mp,challenge)));so=z3.Solver();so.add(dq)
   with tempfile.NamedTemporaryFile('w',suffix='.smt2',delete=False) as f:f.write(so.to_smt2());tmp=f.name
   try:rebuilt=sha(tmp)
   finally:Path(tmp).unlink(missing_ok=True)
   if rebuilt!=r['query_sha256']:raise RuntimeError('semantic query reconstruction mismatch')
   yr=subprocess.run(['yosys','-q','-p',f'read_json {mp}; stat'],capture_output=True,text=True,timeout=120)
   if yr.returncode!=0:raise RuntimeError('Yosys mutant validation failure '+yr.stderr[-1000:])
   unbound=solve(a.cvc5,qp.read_text());w=r['canonical_witness'];stored=r['one_bit_corruption']['bit'];critical=s['first_critical_bit'];noncritical=s['first_noncritical_bit']
   can=solve(a.cvc5,bind(qp,w));st=solve(a.cvc5,bind(qp,flip(w,stored)));cr=solve(a.cvc5,bind(qp,flip(w,critical)));nr=solve(a.cvc5,bind(qp,flip(w,noncritical)))
   expected_st='unsat' if r['one_bit_corruption']['rejected'] else 'sat'
   if (unbound,can,st,cr,nr)!=('sat','sat',expected_st,'unsat','sat'):raise RuntimeError('cvc5 independent result disagreement')
   rows.append({'lane':lane,'candidate_index':r['candidate_index'],'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],'mutant_sha256':r['mutant_sha256'],'query_reconstructed':True,'yosys_mutant_valid':True,'unbound_result':unbound,'canonical_result':can,'historical_stored_result':st,'historical_expected':expected_st,'v2_selected_bit':critical,'v2_result':cr,'representative_noncritical_bit':noncritical,'noncritical_result':nr,'pass':True})
 rows.sort(key=lambda r:(r['lane'],r['candidate_index']))
 out={'schema':'g3-amendment08-independent-verifier-shard-v1','authority':'PROSPECTIVE_AMENDMENT08_AUTHORITY','amendment08_freeze_sha':AMENDMENT_SHA,'shard':a.shard,'candidates':len(rows),'cvc5_queries':5*len(rows),'yosys_mutants':len(rows),'historical_rejected':sum(r['historical_stored_result']=='unsat' for r in rows),'historical_not_rejected':sum(r['historical_stored_result']=='sat' for r in rows),'v2_rejected':sum(r['v2_result']=='unsat' for r in rows),'all_pass':all(r['pass'] for r in rows),'rows':rows}
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({k:out[k] for k in ('shard','candidates','cvc5_queries','yosys_mutants','historical_rejected','historical_not_rejected','v2_rejected','all_pass')},sort_keys=True))
if __name__=='__main__':main()
