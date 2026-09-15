#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

AMENDMENT_SHA='33c53b01bd437eca666adaf8f812df33d65a2bdb'
SOURCE_RUN=34804261148
SOURCE_HEAD='ccd83335a9b81bc447d9740129668619c4e77267'
SOURCE_AGG_DIGEST='sha256:adce6b557ecdd0d5d7e21a370cc0e1090c07e2dfbc312dcfca3f619d83576881'
EXPECTED_CORPUS={
'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def wr(p,o):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sealed-root',required=True);ap.add_argument('--raw',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.sealed_root);rawp=Path(a.raw);x=json.loads(rawp.read_text())
 if x.get('schema')!='g3-independent-bitparallel-fixed-replay-v1': raise RuntimeError('unexpected raw selector schema')
 if x.get('source_red_run')!=SOURCE_RUN or x.get('source_red_head')!=SOURCE_HEAD or x.get('source_red_artifact_digest')!=SOURCE_AGG_DIGEST: raise RuntimeError('source RED provenance drift')
 if {k:sha(root/'input'/'frozen'/'base'/(k+'.json')) for k in EXPECTED_CORPUS}!=EXPECTED_CORPUS: raise RuntimeError('frozen corpus drift')
 for i in range(16):
  sj=root/'shards'/f'SHARD_{i:02d}.json'; rc=root/'shards'/f'SHARD_RC_{i}.txt'; cm=root/'shards'/f'RUNNER_COMMIT_{i}.txt'
  if not sj.is_file() or not rc.is_file() or rc.read_text().strip()!='0' or not cm.is_file() or cm.read_text().strip()!=SOURCE_HEAD: raise RuntimeError(f'shard {i} provenance drift')
  s=json.loads(sj.read_text())
  if s.get('schema')!='g3-negative-shard-v2' or s.get('shard_index')!=i or s.get('error_count')!=0: raise RuntimeError(f'bad shard {i}')
 if x.get('total_candidates')!=237 or x.get('total_flips')!=48027: raise RuntimeError('selector population drift')
 if (x.get('critical_total'),x.get('divergent_total'),x.get('has_critical'),x.get('no_critical'))!=(5960,42067,237,0): raise RuntimeError('sensitivity census drift')
 if (x.get('stored_rejected'),x.get('stored_not_rejected'))!=(177,60): raise RuntimeError('historical V1 result not reproduced')
 rec=x.get('records',[])
 if len(rec)!=237: raise RuntimeError('record cardinality drift')
 keys=[(r['lane'],r['candidate_index']) for r in rec]
 if len(set(keys))!=237: raise RuntimeError('duplicate selector key')
 if sorted(i for l,i in keys if l=='nc1')!=list(range(102)) or sorted(i for l,i in keys if l=='nc2')!=list(range(135)): raise RuntimeError('lane enumeration drift')
 for r in rec:
  crit=r.get('critical_single_bits',[])
  if not crit or r.get('first_critical_bit')!=crit[0] or r.get('critical_count')!=len(crit): raise RuntimeError('first-critical selection drift')
  if crit!=sorted(crit): raise RuntimeError('critical list not lexicographically ordered')
  if r.get('first_noncritical_bit') is None: raise RuntimeError('missing retained noncritical control')
 out={'schema':'g3-amendment08-selector-v1','authority':'PROSPECTIVE_AMENDMENT08_AUTHORITY','amendment08_freeze_sha':AMENDMENT_SHA,
      'source_v1_red':{'run':SOURCE_RUN,'head':SOURCE_HEAD,'aggregate_artifact_digest':SOURCE_AGG_DIGEST,'verdict':'RED','stored_rejected':177,'stored_not_rejected':60},
      'raw_sensitivity_sha256':sha(rawp),'total_candidates':237,'lane_counts':{'nc1':102,'nc2':135},'total_flips':48027,
      'critical_total':5960,'noncritical_total':42067,'no_critical':0,'historical_stored_rejected':177,'historical_stored_not_rejected':60,
      'selection_rule':'lexicographically first critical bit after exhaustive complete-assignment sensitivity evaluation','records':rec}
 wr(a.out,out);print(json.dumps({k:out[k] for k in ('total_candidates','total_flips','critical_total','noncritical_total','no_critical','historical_stored_rejected','historical_stored_not_rejected')},sort_keys=True))
if __name__=='__main__':main()
