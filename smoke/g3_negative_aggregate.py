#!/usr/bin/env python3
"""Fail-closed Amendment-06 aggregate gate for all frozen G3 negative candidates."""
from __future__ import annotations
import argparse, hashlib, json, subprocess
from pathlib import Path
import z3
import g3_negative_controls_v2 as v2

g=v2.g
EXP={
 'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
 'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}

def wr(p,o):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def expected(base,lane):
 kind='$assume' if lane=='nc1' else '$assert';out=[];idx=0
 for lab in g.L:
  o=json.loads((Path(base)/(lab+'.json')).read_text())
  for cell in g.cells(o,kind):
   ident=lab+'__'+hashlib.sha256(cell.encode()).hexdigest()[:16]
   out.append({'candidate_index':idx,'artifact':lab,'cell':cell,'ident':ident});idx+=1
 return out
def cvc5_check(bin_,p):
 q=subprocess.run([bin_,str(p)],capture_output=True,text=True,timeout=120)
 got=next((x.strip().upper() for x in q.stdout.splitlines() if x.strip()),'NO_OUTPUT')
 return got,q.returncode,q.stderr[-2000:]
def yosys_check(p):
 q=subprocess.run(['yosys','-q','-p',f'read_json {p}; stat'],capture_output=True,text=True,timeout=120)
 return q.returncode,q.stderr[-2000:]
def recomputed_query_hash(basep,mutp,challenge,tmp):
 a=g.sem(basep,challenge);b=g.sem(mutp,challenge);dq=z3.simplify(z3.Xor(a,b));s=z3.Solver();s.add(dq)
 Path(tmp).write_text(s.to_smt2());return sha(tmp),a,b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--base-dir',required=True);ap.add_argument('--shards-dir',required=True)
 ap.add_argument('--old-source',required=True);ap.add_argument('--new-source',required=True);ap.add_argument('--out',required=True)
 ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--cvc5',required=True);a=ap.parse_args()
 base=Path(a.base_dir);shards=Path(a.shards_dir);root=Path(a.out);root.mkdir(parents=True,exist_ok=True)
 got={k:sha(base/(k+'.json')) for k in g.L}
 if got!=EXP:raise RuntimeError('corpus drift '+repr(got))
 exp={'nc1':expected(base,'nc1'),'nc2':expected(base,'nc2')}
 if len(exp['nc1'])!=102 or len(exp['nc2'])!=135:raise RuntimeError('candidate cardinality drift')
 files=sorted(shards.glob('SHARD_*.json'))
 if len(files)!=a.shard_count:raise RuntimeError(f'shard count {len(files)} != {a.shard_count}')
 allrec={'nc1':{},'nc2':{}};shard_manifest=[];seen_shards=[]
 for f in files:
  x=json.loads(f.read_text());si=x['shard_index'];seen_shards.append(si)
  if x.get('schema')!='g3-negative-shard-v1' or x.get('shard_count')!=a.shard_count:raise RuntimeError('bad shard metadata '+str(f))
  if x.get('corpus_sha256')!=EXP:raise RuntimeError('shard corpus drift '+str(f))
  if x.get('lane_totals')!={'nc1':102,'nc2':135}:raise RuntimeError('shard lane total drift '+str(f))
  shard_manifest.append({'shard_index':si,'file':f.name,'sha256':sha(f)})
  for lane in ('nc1','nc2'):
   for r in x['records'][lane]:
    idx=r['candidate_index']
    if idx%a.shard_count!=si:raise RuntimeError('wrong shard assignment')
    if idx in allrec[lane]:raise RuntimeError('duplicate candidate')
    allrec[lane][idx]=r
 if sorted(seen_shards)!=list(range(a.shard_count)):raise RuntimeError('shard index coverage failure')

 cross=[];yv=[];lane_results={};tmp=root/'recomputed.smt2'
 for lane in ('nc1','nc2'):
  if sorted(allrec[lane])!=list(range(len(exp[lane]))):raise RuntimeError(lane+' candidate coverage failure')
  checked=[];challenge=(lane=='nc2')
  for e in exp[lane]:
   r=allrec[lane][e['candidate_index']]
   for k in ('candidate_index','artifact','cell','ident'):
    if r.get(k)!=e[k]:raise RuntimeError(lane+' enumeration mismatch '+repr((e,r)))
   if 'error' in r:raise RuntimeError(lane+' shard error '+r['error'])
   bp=base/(r['artifact']+'.json');mp=shards/r['mutant_relpath'];qp=shards/r['query_relpath']
   if not mp.is_file() or not qp.is_file():raise RuntimeError('missing candidate evidence')
   if r['baseline_sha256']!=sha(bp) or r['mutant_sha256']!=sha(mp) or r['query_sha256']!=sha(qp):raise RuntimeError('candidate hash mismatch')
   qh,bf,mf=recomputed_query_hash(bp,mp,challenge,tmp)
   if qh!=r['query_sha256']:raise RuntimeError('semantic query reconstruction mismatch')
   gotc,rc,err=cvc5_check(a.cvc5,qp);match=(rc==0 and gotc==r['result'])
   cross.append({'lane':lane,'candidate_index':r['candidate_index'],'query_sha256':r['query_sha256'],'expected':r['result'],'cvc5':gotc,'returncode':rc,'match':match,'stderr':err})
   if not match:raise RuntimeError('cvc5 disagreement')
   yrc,yerr=yosys_check(mp);yv.append({'lane':lane,'candidate_index':r['candidate_index'],'mutant_sha256':r['mutant_sha256'],'returncode':yrc,'valid':yrc==0,'stderr':yerr})
   if yrc!=0:raise RuntimeError('invalid Yosys mutant')
   if r['result']=='SAT':
    w=r.get('canonical_witness');corr=r.get('one_bit_corruption')
    if not isinstance(w,dict) or not isinstance(corr,dict):raise RuntimeError('missing SAT evidence')
    rp=g.replay(bf,mf,w);br=g.replay(bf,mf,corr['witness'])
    if not rp['divergent'] or br['divergent'] or not corr.get('rejected'):raise RuntimeError('SAT replay/corruption failure')
    r=dict(r);r['aggregate_replay']=rp;r['aggregate_corruption_replay']=br
   elif r['result']!='UNSAT':raise RuntimeError('non SAT/UNSAT candidate')
   checked.append(r)
  sats=[x for x in checked if x['result']=='SAT']
  if not sats:raise RuntimeError(lane+' has no SAT candidate')
  selected=sats[0]
  lane_results[lane]={'schema':'g3-'+lane+'-negative-exhaustive-v1','attempt_count':len(checked),'sat_count':len(sats),'unsat_count':len(checked)-len(sats),'selected_index':selected['candidate_index'],'selected':selected,'attempts':checked}
  wr(root/lane/(lane.upper()+'_RESULT.json'),lane_results[lane])

 n3=g.nc3(root);n4=g.nc4(root,base,a.old_source,a.new_source)
 q3=Path(n3['query']['path']);gotc,rc,err=cvc5_check(a.cvc5,q3);m=(rc==0 and gotc==n3['semantic_result'])
 cross.append({'lane':'nc3','query_sha256':n3['query']['sha256'],'expected':n3['semantic_result'],'cvc5':gotc,'returncode':rc,'match':m,'stderr':err})
 if not m:raise RuntimeError('NC3 cvc5 disagreement')
 s=lane_results['nc1']['selected'];n5={'schema':'g3-nc5-witness-corruption-v1','source_candidate':{'artifact':s['artifact'],'cell':s['cell'],'candidate_index':s['candidate_index'],'query_sha256':s['query_sha256']},'bit':s['one_bit_corruption']['bit'],'corrupted_witness':s['one_bit_corruption']['witness'],'replay':s['one_bit_corruption']['replay'],'rejected':s['one_bit_corruption']['rejected']};wr(root/'nc5'/'NC5_RESULT.json',n5)
 wr(root/'CROSS_SOLVER.json',{'schema':'g3-negative-cross-solver-v1','count':len(cross),'failures':sum(not x['match'] for x in cross),'rows':cross})
 wr(root/'YOSYS_MUTANT_VALIDATION.json',{'schema':'g3-negative-yosys-validation-v1','count':len(yv),'failures':sum(not x['valid'] for x in yv),'rows':yv})
 wr(root/'SHARD_MANIFEST.json',{'schema':'g3-negative-shard-manifest-v1','shard_count':a.shard_count,'shards':sorted(shard_manifest,key=lambda x:x['shard_index']),'candidate_counts':{'nc1':102,'nc2':135},'candidate_total':237})
 pre={'schema':'g3-negative-controls-pre-nc6-v1','corpus_sha256':got,
      'nc1':{'pass':lane_results['nc1']['selected']['replay']['divergent'] and lane_results['nc1']['selected']['one_bit_corruption']['rejected'],'selected':lane_results['nc1']['selected'],'attempt_count':102,'sat_count':lane_results['nc1']['sat_count']},
      'nc2':{'pass':lane_results['nc2']['selected']['replay']['divergent'] and lane_results['nc2']['selected']['one_bit_corruption']['rejected'],'selected':lane_results['nc2']['selected'],'attempt_count':135,'sat_count':lane_results['nc2']['sat_count']},
      'nc3':{'pass':n3['validator_rejected_mutant'] and n3['replay']['divergent'] and n3['one_bit_corruption']['rejected']},
      'nc4':{'pass':n4['rejected'],'selected':n4['selected']},'nc5':{'pass':n5['rejected'],'bit':n5['bit']},
      'query_count':len(cross),'exhaustive_candidate_total':237}
 wr(root/'NEGATIVE_PRE_NC6_SUMMARY.json',pre);print(json.dumps({'nc1_sat':lane_results['nc1']['sat_count'],'nc2_sat':lane_results['nc2']['sat_count'],'queries':len(cross),'yosys_mutants':len(yv),'pre_pass':all(pre[x]['pass'] for x in ('nc1','nc2','nc3','nc4','nc5'))},sort_keys=True))

if __name__=='__main__':main()
