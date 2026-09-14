#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: shard-local bit-sensitivity replay over sealed RED evidence."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import g3_negative_controls_v3 as v3

g=v3.g
LABELS=('natural_old','natural_new','mirror_old','mirror_new')
EXP={
 'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
 'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}
def wr(p,o):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--artifact-root',required=True);ap.add_argument('--shard',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 if not 0<=a.shard<16: raise RuntimeError('bad shard')
 root=Path(a.artifact_root);base=root/'input'/'frozen'/'base';shards=root/'shards'
 got={k:g.sh(base/(k+'.json')) for k in LABELS}
 if got!=EXP: raise RuntimeError('sealed corpus hash mismatch')
 sf=shards/f'SHARD_{a.shard:02d}.json';x=json.loads(sf.read_text())
 if x.get('schema')!='g3-negative-shard-v2' or x.get('shard_index')!=a.shard or x.get('shard_count')!=16 or x.get('error_count')!=0: raise RuntimeError('bad sealed shard')
 records=[]
 for lane in ('nc1','nc2'):
  challenge=lane=='nc2'
  for r in x['records'][lane]:
   if r.get('result')!='SAT': continue
   bp=base/(r['artifact']+'.json');mp=shards/r['mutant_relpath']
   if not mp.is_file(): raise RuntimeError('missing mutant')
   bf=g.sem(bp,challenge);mf=g.sem(mp,challenge);w=r['canonical_witness']
   canonical=g.replay(bf,mf,w)
   if not canonical['divergent']: raise RuntimeError('canonical replay failed')
   flips=[]
   for bit in sorted(w):
    q=dict(w);q[bit]=1-q[bit];rp=g.replay(bf,mf,q)
    flips.append({'bit':bit,'rejected':not rp['divergent'],'replay':rp})
   critical=[z['bit'] for z in flips if z['rejected']]
   co=r.get('one_bit_corruption',{})
   records.append({'lane':lane,'candidate_index':r['candidate_index'],'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],'witness_bits':len(w),'stored_corruption_bit':co.get('bit'),'stored_corruption_rejected':co.get('rejected'),'critical_single_bits':critical,'critical_single_bit_count':len(critical),'has_any_rejecting_single_bit':bool(critical),'first_critical_bit':critical[0] if critical else None,'all_single_bit_flips':flips})
 records.sort(key=lambda r:(r['lane'],r['candidate_index']))
 out={'schema':'g3-post-red-witness-bit-sensitivity-shard-v1','authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY','source_red_run':34804261148,'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267','shard':a.shard,'corpus_sha256':got,'sat_candidates':len(records),'stored_first_bit_rejected':sum(bool(r['stored_corruption_rejected']) for r in records),'stored_first_bit_not_rejected':sum(not bool(r['stored_corruption_rejected']) for r in records),'has_any_rejecting_single_bit':sum(r['has_any_rejecting_single_bit'] for r in records),'has_no_rejecting_single_bit':sum(not r['has_any_rejecting_single_bit'] for r in records),'records':records}
 wr(a.out,out);print(json.dumps({k:out[k] for k in ('shard','sat_candidates','stored_first_bit_rejected','stored_first_bit_not_rejected','has_any_rejecting_single_bit','has_no_rejecting_single_bit')},sort_keys=True))
if __name__=='__main__':main()
