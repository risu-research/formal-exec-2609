#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: cross-check exhaustive SMT classification with original G3 Z3 replay.

For each sealed SAT candidate in one Amendment-07 shard, replay exactly four fixed
assignments through the original authority semantic implementation:
  1. canonical witness -> must still diverge;
  2. frozen stored lex-first corruption -> must match the sealed RED outcome;
  3. independently classified first critical bit -> must reject divergence;
  4. independently classified first non-critical bit -> must preserve divergence.
No candidate, mutant, witness, or authority criterion is changed.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import g3_negative_controls_v3 as v3

g = v3.g
LABELS = ('natural_old','natural_new','mirror_old','mirror_new')
EXP = {
    'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
    'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
    'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec419734eb2a51' if False else '81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
    'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}

def wr(p, o):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')

def flip(w, bit):
    if bit not in w: raise RuntimeError('flip bit absent from witness: '+bit)
    q=dict(w); q[bit]=1-int(q[bit]); return q

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--sealed-root',required=True)
    ap.add_argument('--bitparallel',required=True)
    ap.add_argument('--shard',type=int,required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    if not 0<=a.shard<16: raise RuntimeError('bad shard')
    root=Path(a.sealed_root); base=root/'input'/'frozen'/'base'; shards=root/'shards'
    got={k:g.sh(base/(k+'.json')) for k in LABELS}
    if got!=EXP: raise RuntimeError('sealed corpus hash mismatch')
    sx=json.loads((shards/f'SHARD_{a.shard:02d}.json').read_text())
    if sx.get('schema')!='g3-negative-shard-v2' or sx.get('shard_index')!=a.shard or sx.get('error_count')!=0:
        raise RuntimeError('bad sealed shard')
    bp=json.loads(Path(a.bitparallel).read_text())
    if bp.get('schema')!='g3-independent-bitparallel-fixed-replay-v1': raise RuntimeError('bad bitparallel schema')
    if bp.get('source_red_run')!=34804261148 or bp.get('source_red_head')!='ccd83335a9b81bc447d9740129668619c4e77267':
        raise RuntimeError('bitparallel source provenance drift')
    if bp.get('total_candidates')!=237 or bp.get('total_flips')!=48027 or bp.get('no_critical')!=0:
        raise RuntimeError('bitparallel population drift')
    bpmap={(r['lane'],r['candidate_index']):r for r in bp['records']}
    if len(bpmap)!=237: raise RuntimeError('bitparallel duplicate keys')

    outrows=[]
    for lane in ('nc1','nc2'):
        challenge=(lane=='nc2')
        for r in sx['records'][lane]:
            if r.get('result')!='SAT': raise RuntimeError('sealed candidate not SAT')
            if r.get('lane')!=lane: raise RuntimeError('sealed lane field mismatch')
            key=(lane,r['candidate_index'])
            if key not in bpmap: raise RuntimeError('candidate absent from bitparallel evidence')
            b=bpmap[key]
            if b['artifact']!=r['artifact'] or b['cell']!=r['cell'] or b['query_sha256']!=r['query_sha256']:
                raise RuntimeError('candidate identity mismatch')
            w=r['canonical_witness']
            if b['witness_bits']!=len(w): raise RuntimeError('witness width mismatch')
            bp_old=base/(r['artifact']+'.json'); mp=shards/r['mutant_relpath']
            if not mp.is_file(): raise RuntimeError('mutant missing')
            bf=g.sem(bp_old,challenge); mf=g.sem(mp,challenge)

            canonical=g.replay(bf,mf,w)
            if not canonical['divergent']: raise RuntimeError('canonical authority replay failed')

            stored=r['one_bit_corruption']['bit']
            if stored!=b['stored_bit']: raise RuntimeError('stored bit mismatch')
            stored_replay=g.replay(bf,mf,flip(w,stored))
            stored_rejected=not stored_replay['divergent']
            if stored_rejected!=bool(r['one_bit_corruption']['rejected']): raise RuntimeError('sealed stored replay mismatch')
            if stored_rejected!=bool(b['stored_rejected']): raise RuntimeError('bitparallel stored replay mismatch')

            critical=b['first_critical_bit']
            if critical is None: raise RuntimeError('candidate lacks critical bit')
            critical_replay=g.replay(bf,mf,flip(w,critical))
            if critical_replay['divergent']: raise RuntimeError('bitparallel critical bit did not reject in authority replay')

            noncritical=b['first_noncritical_bit']
            if noncritical is None: raise RuntimeError('candidate lacks noncritical bit')
            noncritical_replay=g.replay(bf,mf,flip(w,noncritical))
            if not noncritical_replay['divergent']: raise RuntimeError('bitparallel noncritical bit rejected in authority replay')

            outrows.append({
              'lane':lane,'candidate_index':r['candidate_index'],'artifact':r['artifact'],'cell':r['cell'],
              'query_sha256':r['query_sha256'],'witness_bits':len(w),
              'canonical_divergent':True,
              'stored_bit':stored,'stored_rejected':stored_rejected,
              'first_critical_bit':critical,'critical_rejected':True,
              'first_noncritical_bit':noncritical,'noncritical_divergent':True,
              'authority_crosscheck_pass':True,
            })
    outrows.sort(key=lambda r:(r['lane'],r['candidate_index']))
    out={
      'schema':'g3-authority-z3-replay-crosscheck-shard-v1',
      'authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY',
      'source_red_run':34804261148,
      'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267',
      'bitparallel_run':34931813214,
      'bitparallel_head':'d93a1bc6ba6e2e2c0f7836166e3f59d93e62071c',
      'shard':a.shard,'candidates':len(outrows),'replay_points':4*len(outrows),
      'all_pass':all(r['authority_crosscheck_pass'] for r in outrows),
      'rows':outrows,
    }
    wr(a.out,out)
    print(json.dumps({k:out[k] for k in ('shard','candidates','replay_points','all_pass')},sort_keys=True))

if __name__=='__main__': main()
