#!/usr/bin/env python3
"""Amendment-07 sharded executor; semantics/denominator unchanged."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import z3
import g3_negative_controls_v3 as v3

g=v3.g
EXP={'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74','natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'}
def wr(p,x):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def emitq(root,lane,ident,q):
 p=Path(root)/'queries'/lane/(ident+'.smt2');p.parent.mkdir(parents=True,exist_ok=True);s=z3.Solver();s.add(q);p.write_text(s.to_smt2());return str(Path('queries')/lane/(ident+'.smt2')),g.sh(p)
def emitm(root,lane,ident,obj):
 p=Path(root)/'mutants'/lane/(ident+'.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,sort_keys=False,separators=(',',':'))+'\n');return str(Path('mutants')/lane/(ident+'.json')),g.sh(p)
def lane(root,base,name,kind,challenge,mut,si,sc):
 recs=[];idx=0;total=0
 for lab in g.L:
  bp=Path(base)/(lab+'.json');obj=json.loads(bp.read_text());baseline=g.sem(bp,challenge)
  for cell in g.cells(obj,kind):
   ci=idx;idx+=1;total+=1
   if ci%sc!=si:continue
   ident=lab+'__'+hashlib.sha256(cell.encode()).hexdigest()[:16];r={'candidate_index':ci,'artifact':lab,'cell':cell,'ident':ident,'baseline_sha256':g.sh(bp),'lane':name}
   try:
    mo=mut(obj,cell);mr,ms=emitm(root,name,ident,mo);mp=Path(root)/mr;mf=g.sem(mp,challenge);dq=z3.simplify(z3.Xor(baseline,mf));res,w=g.witness(dq);qr,qs=emitq(root,name,ident,dq);r.update(mutant_relpath=mr,mutant_sha256=ms,query_relpath=qr,query_sha256=qs,result=res)
    if res=='SAT':
     rp=g.replay(baseline,mf,w);bit,bad=g.corrupt(w);br=g.replay(baseline,mf,bad);r.update(canonical_witness=w,replay=rp,one_bit_corruption={'bit':bit,'witness':bad,'replay':br,'rejected':not br['divergent']})
   except Exception as e:r['error']=repr(e)
   recs.append(r)
 return total,recs
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--base-dir',required=True);ap.add_argument('--out',required=True);ap.add_argument('--shard',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);a=ap.parse_args();assert 0<=a.shard<a.shard_count
 root=Path(a.out);root.mkdir(parents=True,exist_ok=True);base=Path(a.base_dir);got={k:g.sh(base/(k+'.json')) for k in g.L}
 if got!=EXP:raise RuntimeError('corpus drift '+repr(got))
 t1,n1=lane(root,base,'nc1','$assume',False,g.m1,a.shard,a.shard_count);t2,n2=lane(root,base,'nc2','$assert',True,g.m2,a.shard,a.shard_count)
 out={'schema':'g3-negative-shard-v2','shard_index':a.shard,'shard_count':a.shard_count,'corpus_sha256':got,'lane_totals':{'nc1':t1,'nc2':t2},'records':{'nc1':n1,'nc2':n2},'error_count':sum('error'in x for x in n1+n2)}
 wr(root/f'SHARD_{a.shard:02d}.json',out);print(json.dumps({'shard':a.shard,'nc1_records':len(n1),'nc2_records':len(n2),'errors':out['error_count']},sort_keys=True))
 if out['error_count']:raise SystemExit('candidate execution errors')
if __name__=='__main__':main()
