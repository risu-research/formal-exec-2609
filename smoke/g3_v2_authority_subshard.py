#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import g3_negative_controls_v3 as v3

g=v3.g
AMENDMENT_SHA='33c53b01bd437eca666adaf8f812df33d65a2bdb'
SOURCE_HEAD='ccd83335a9b81bc447d9740129668619c4e77267'
EXP={'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74','natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'}
def flip(w,b):q=dict(w);q[b]=1-int(q[b]);return q
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sealed-root',required=True);ap.add_argument('--selector',required=True);ap.add_argument('--shard',type=int,required=True);ap.add_argument('--subshard',type=int,required=True);ap.add_argument('--subshard-count',type=int,default=4);ap.add_argument('--out',required=True);a=ap.parse_args()
 if not 0<=a.shard<16 or not 0<=a.subshard<a.subshard_count:raise RuntimeError('bad partition')
 root=Path(a.sealed_root);base=root/'input'/'frozen'/'base';sd=root/'shards'
 if {k:sha(base/(k+'.json')) for k in EXP}!=EXP:raise RuntimeError('corpus hash mismatch')
 if (sd/f'RUNNER_COMMIT_{a.shard}.txt').read_text().strip()!=SOURCE_HEAD:raise RuntimeError('sealed source head drift')
 sx=json.loads((sd/f'SHARD_{a.shard:02d}.json').read_text())
 if sx.get('schema')!='g3-negative-shard-v2' or sx.get('shard_index')!=a.shard or sx.get('error_count')!=0:raise RuntimeError('bad sealed shard')
 se=json.loads(Path(a.selector).read_text())
 if se.get('schema')!='g3-amendment08-selector-v1' or se.get('amendment08_freeze_sha')!=AMENDMENT_SHA or se.get('total_candidates')!=237 or se.get('no_critical')!=0:raise RuntimeError('selector authority drift')
 sm={(r['lane'],r['candidate_index']):r for r in se['records']}
 rows=[]
 for lane in ('nc1','nc2'):
  challenge=lane=='nc2'
  for r in sx['records'][lane]:
   i=r['candidate_index']
   if ((i//16)%a.subshard_count)!=a.subshard:continue
   if r.get('result')!='SAT' or r.get('lane')!=lane:raise RuntimeError('sealed record drift')
   s=sm[(lane,i)]
   if (s['artifact'],s['cell'],s['query_sha256'])!=(r['artifact'],r['cell'],r['query_sha256']):raise RuntimeError('identity mismatch')
   w=r['canonical_witness'];old=base/(r['artifact']+'.json');mut=sd/r['mutant_relpath'];bf=g.sem(old,challenge);mf=g.sem(mut,challenge)
   can=g.replay(bf,mf,w)
   if not can['divergent']:raise RuntimeError('canonical replay failure')
   stored=r['one_bit_corruption']['bit'];sr=g.replay(bf,mf,flip(w,stored));stored_rej=not sr['divergent']
   if stored_rej!=bool(r['one_bit_corruption']['rejected']) or stored_rej!=bool(s['stored_rejected']):raise RuntimeError('historical V1 replay disagreement')
   critical=s['first_critical_bit'];noncritical=s['first_noncritical_bit']
   cr=g.replay(bf,mf,flip(w,critical));nr=g.replay(bf,mf,flip(w,noncritical))
   if cr['divergent']:raise RuntimeError('V2 selected critical corruption accepted by authority replay')
   if not nr['divergent']:raise RuntimeError('retained noncritical control rejected by authority replay')
   rows.append({'lane':lane,'candidate_index':i,'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],'witness_bits':len(w),'canonical_divergent':True,'historical_stored_bit':stored,'historical_stored_rejected':stored_rej,'v2_selected_bit':critical,'v2_rejected':True,'representative_noncritical_bit':noncritical,'representative_noncritical_divergent':True,'pass':True})
 rows.sort(key=lambda r:(r['lane'],r['candidate_index']))
 out={'schema':'g3-amendment08-authority-replay-subshard-v1','authority':'PROSPECTIVE_AMENDMENT08_AUTHORITY','amendment08_freeze_sha':AMENDMENT_SHA,'source_v1_head':SOURCE_HEAD,'original_shard':a.shard,'subshard':a.subshard,'subshard_count':a.subshard_count,'candidates':len(rows),'replay_points':4*len(rows),'all_pass':all(r['pass'] for r in rows),'rows':rows}
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({k:out[k] for k in ('original_shard','subshard','candidates','replay_points','all_pass')},sort_keys=True))
if __name__=='__main__':main()
