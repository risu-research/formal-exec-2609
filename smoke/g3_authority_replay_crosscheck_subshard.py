#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: exact authority replay cross-check with deterministic subsharding.

Scheduling only. Each candidate remains in its original sealed 16-way shard and is
assigned to one of four subshards by (candidate_index // 16) mod 4. Across 16x4 jobs,
all 237 frozen candidates occur exactly once. Scientific checks are identical to the
existing four-point authority replay diagnostic.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import g3_negative_controls_v3 as v3

g=v3.g
LABELS=('natural_old','natural_new','mirror_old','mirror_new')
EXP={
'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'}

def flip(w,b):
 q=dict(w);q[b]=1-int(q[b]);return q

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sealed-root',required=True);ap.add_argument('--bitparallel',required=True);ap.add_argument('--shard',type=int,required=True);ap.add_argument('--subshard',type=int,required=True);ap.add_argument('--subshard-count',type=int,default=4);ap.add_argument('--out',required=True);a=ap.parse_args()
 if not 0<=a.shard<16 or not 0<=a.subshard<a.subshard_count:raise RuntimeError('bad partition')
 root=Path(a.sealed_root);base=root/'input'/'frozen'/'base';shards=root/'shards'
 if {k:g.sh(base/(k+'.json')) for k in LABELS}!=EXP:raise RuntimeError('corpus hash mismatch')
 sx=json.loads((shards/f'SHARD_{a.shard:02d}.json').read_text())
 if sx.get('schema')!='g3-negative-shard-v2' or sx.get('shard_index')!=a.shard or sx.get('error_count')!=0:raise RuntimeError('bad sealed shard')
 bp=json.loads(Path(a.bitparallel).read_text())
 if bp.get('schema')!='g3-independent-bitparallel-fixed-replay-v1' or bp.get('source_red_run')!=34804261148 or bp.get('source_red_head')!='ccd83335a9b81bc447d9740129668619c4e77267':raise RuntimeError('bitparallel provenance drift')
 if bp.get('total_candidates')!=237 or bp.get('total_flips')!=48027 or bp.get('no_critical')!=0:raise RuntimeError('bitparallel population drift')
 bm={(r['lane'],r['candidate_index']):r for r in bp['records']}
 rows=[]
 for lane in ('nc1','nc2'):
  challenge=lane=='nc2'
  for r in sx['records'][lane]:
   i=r['candidate_index']
   if ((i//16)%a.subshard_count)!=a.subshard:continue
   if r.get('result')!='SAT' or r.get('lane')!=lane:raise RuntimeError('sealed record drift')
   b=bm[(lane,i)]
   if (b['artifact'],b['cell'],b['query_sha256'])!=(r['artifact'],r['cell'],r['query_sha256']):raise RuntimeError('identity mismatch')
   w=r['canonical_witness'];old=base/(r['artifact']+'.json');mut=shards/r['mutant_relpath'];bf=g.sem(old,challenge);mf=g.sem(mut,challenge)
   canonical=g.replay(bf,mf,w)
   if not canonical['divergent']:raise RuntimeError('canonical replay fail')
   stored=r['one_bit_corruption']['bit'];sr=g.replay(bf,mf,flip(w,stored));srej=not sr['divergent']
   if srej!=bool(r['one_bit_corruption']['rejected']) or srej!=bool(b['stored_rejected']):raise RuntimeError('stored replay disagreement')
   critical=b['first_critical_bit'];noncritical=b['first_noncritical_bit']
   if critical is None or noncritical is None:raise RuntimeError('missing diagnostic bit')
   cr=g.replay(bf,mf,flip(w,critical));nr=g.replay(bf,mf,flip(w,noncritical))
   if cr['divergent']:raise RuntimeError('critical bit failed authority rejection')
   if not nr['divergent']:raise RuntimeError('noncritical bit failed authority preservation')
   rows.append({'lane':lane,'candidate_index':i,'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],'witness_bits':len(w),'canonical_divergent':True,'stored_bit':stored,'stored_rejected':srej,'first_critical_bit':critical,'critical_rejected':True,'first_noncritical_bit':noncritical,'noncritical_divergent':True,'pass':True})
 rows.sort(key=lambda r:(r['lane'],r['candidate_index']))
 out={'schema':'g3-authority-z3-replay-crosscheck-subshard-v1','authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY','source_red_run':34804261148,'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267','bitparallel_run':34931813214,'bitparallel_head':'d93a1bc6ba6e2e2c0f7836166e3f59d93e62071c','original_shard':a.shard,'subshard':a.subshard,'subshard_count':a.subshard_count,'candidates':len(rows),'replay_points':4*len(rows),'all_pass':all(r['pass'] for r in rows),'rows':rows}
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({k:out[k] for k in ('original_shard','subshard','candidates','replay_points','all_pass')},sort_keys=True))
if __name__=='__main__':main()
