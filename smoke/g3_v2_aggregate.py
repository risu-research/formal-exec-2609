#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
AMENDMENT_SHA='33c53b01bd437eca666adaf8f812df33d65a2bdb'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--selector',required=True);ap.add_argument('--authority-dir',required=True);ap.add_argument('--verify-dir',required=True);ap.add_argument('--controls',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 se=json.loads(Path(a.selector).read_text());ad=Path(a.authority_dir);vd=Path(a.verify_dir);ct=json.loads(Path(a.controls).read_text())
 if se.get('schema')!='g3-amendment08-selector-v1' or se.get('amendment08_freeze_sha')!=AMENDMENT_SHA:raise RuntimeError('selector authority mismatch')
 if (se.get('total_candidates'),se.get('lane_counts'),se.get('total_flips'),se.get('critical_total'),se.get('noncritical_total'),se.get('no_critical'))!=(237,{'nc1':102,'nc2':135},48027,5960,42067,0):raise RuntimeError('selector census mismatch')
 if (se.get('historical_stored_rejected'),se.get('historical_stored_not_rejected'))!=(177,60):raise RuntimeError('historical RED mismatch')
 sm={(r['lane'],r['candidate_index']):r for r in se['records']}
 af=sorted(ad.glob('AUTH_*.json'));vf=sorted(vd.glob('VERIFY_*.json'))
 if len(af)!=64:raise RuntimeError(f'authority partition count {len(af)} != 64')
 if len(vf)!=16:raise RuntimeError(f'verifier shard count {len(vf)} != 16')
 auth={};apoints=0
 for p in af:
  x=json.loads(p.read_text())
  if x.get('schema')!='g3-amendment08-authority-replay-subshard-v1' or x.get('amendment08_freeze_sha')!=AMENDMENT_SHA or not x.get('all_pass'):raise RuntimeError('authority partition RED '+p.name)
  apoints+=x['replay_points']
  for r in x['rows']:
   k=(r['lane'],r['candidate_index'])
   if k in auth:raise RuntimeError('duplicate authority candidate')
   s=sm[k]
   if (r['artifact'],r['cell'],r['query_sha256'],r['v2_selected_bit'])!=(s['artifact'],s['cell'],s['query_sha256'],s['first_critical_bit']):raise RuntimeError('authority identity mismatch')
   if not(r['canonical_divergent'] and r['v2_rejected'] and r['representative_noncritical_divergent']):raise RuntimeError('authority replay failure')
   auth[k]=r
 if len(auth)!=237 or apoints!=948:raise RuntimeError('authority population/replay-point mismatch')
 ver={};cq=ym=0
 for p in vf:
  x=json.loads(p.read_text())
  if x.get('schema')!='g3-amendment08-independent-verifier-shard-v1' or x.get('amendment08_freeze_sha')!=AMENDMENT_SHA or not x.get('all_pass'):raise RuntimeError('verifier shard RED '+p.name)
  cq+=x['cvc5_queries'];ym+=x['yosys_mutants']
  for r in x['rows']:
   k=(r['lane'],r['candidate_index'])
   if k in ver:raise RuntimeError('duplicate verifier candidate')
   s=sm[k]
   if (r['artifact'],r['cell'],r['query_sha256'],r['v2_selected_bit'])!=(s['artifact'],s['cell'],s['query_sha256'],s['first_critical_bit']):raise RuntimeError('verifier identity mismatch')
   if not(r['stored_query_hash_verified'] and r['stored_query_z3_parsed'] and r['yosys_mutant_valid'] and r['unbound_result']=='sat' and r['canonical_result']=='sat' and r['v2_result']=='unsat' and r['noncritical_result']=='sat'):raise RuntimeError('verifier semantic/checker failure')
   ver[k]=r
 if len(ver)!=237 or cq!=1185 or ym!=237:raise RuntimeError(f'verifier population mismatch cvc5={cq} yosys={ym}')
 if set(sm)!=set(auth) or set(sm)!=set(ver):raise RuntimeError('cross-lane candidate population mismatch')
 ahrej=sum(r['historical_stored_rejected'] for r in auth.values());vhrej=sum(r['historical_stored_result']=='unsat' for r in ver.values())
 if ahrej!=177 or vhrej!=177:raise RuntimeError('historical V1 RED not independently reproduced')
 if not ct.get('pass') or ct.get('schema')!='g3-amendment08-unchanged-controls-v1' or ct.get('amendment08_freeze_sha')!=AMENDMENT_SHA:raise RuntimeError('unchanged controls RED')
 checks={'amendment08_frozen':True,'candidate_population_237':True,'nc1_102':sum(k[0]=='nc1' for k in sm)==102,'nc2_135':sum(k[0]=='nc2' for k in sm)==135,'all_48027_flips_classified':se['total_flips']==48027,'all_237_have_critical_bit':se['no_critical']==0,'historical_v1_red_reproduced_177_60':ahrej==177 and vhrej==177,'authority_replay_948_pass':apoints==948,'v2_authority_rejected_237':sum(r['v2_rejected'] for r in auth.values())==237,'cvc5_and_yosys_independent_verifier_complete':cq==1185 and ym==237,'v2_cvc5_unsat_237':sum(r['v2_result']=='unsat' for r in ver.values())==237,'all_mutants_yosys_valid':sum(r['yosys_mutant_valid'] for r in ver.values())==237,'amendment07_calibration_and_support':bool(ct['calibration']['pass'] and ct['calibration']['rows']==16 and ct['calibration']['unsupported_reachable_total']==0),'nc3':bool(ct['nc3']['pass']),'nc4':bool(ct['nc4']['pass']),'nc6':bool(ct['nc6']['pass'])}
 out={'schema':'g3-amendment08-prospective-v2-final-v1','authority':'G3_NEGATIVE_CONTROL_V2_AUTHORITY','amendment08_freeze_sha':AMENDMENT_SHA,'checks':checks,'pass':all(checks.values()),'selector':{'sha256':sha(a.selector),'total_flips':48027,'critical':5960,'noncritical':42067},'authority':{'partitions':64,'candidates':237,'replay_points':948,'historical_rejected':ahrej,'historical_not_rejected':237-ahrej,'v2_rejected':237},'independent_verifier':{'shards':16,'candidates':237,'cvc5_queries':cq,'yosys_mutants':ym,'historical_rejected':vhrej,'historical_not_rejected':237-vhrej,'v2_unsat':237},'controls':ct,'history_statement':'V1 lexicographic corruption remains RED 177/237 rejected; Amendment08 V2 is a separate prospective repair.'}
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({'pass':out['pass'],'checks':checks},sort_keys=True))
 if not out['pass']:raise SystemExit(1)
if __name__=='__main__':main()
