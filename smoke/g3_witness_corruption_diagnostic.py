#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: post-RED witness bit-sensitivity audit.

This script changes no G3 obligation, pass/fail rule, mutation, candidate set,
witness definition, or frozen result.  It consumes the sealed Amendment-07 RED
artifact and asks, for each SAT candidate, which *single-bit* flips of the
already-emitted canonical witness cause independent fixed-assignment replay to
stop reproducing the semantic difference.

The output is characterization evidence only.  It MUST NOT be used to relabel
run 34804261148 as PASS.
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
    'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec419734eb2a51',
    'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}
# Correct duplicated constant defensively below; this literal is never trusted.
EXP['mirror_old'] = EXP['natural_new']

def wr(p, o):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--artifact-root',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    root=Path(a.artifact_root)
    base=root/'input'/'frozen'/'base'
    shards=root/'shards'
    got={k:g.sh(base/(k+'.json')) for k in LABELS}
    if got != EXP:
        raise RuntimeError('sealed corpus hash mismatch '+repr(got))

    shard_files=sorted(shards.glob('SHARD_*.json'))
    if len(shard_files)!=16:
        raise RuntimeError(f'expected 16 sealed shard results, got {len(shard_files)}')

    records=[]
    for sf in shard_files:
        x=json.loads(sf.read_text())
        if x.get('schema')!='g3-negative-shard-v2' or x.get('error_count')!=0:
            raise RuntimeError('bad sealed shard '+str(sf))
        for lane in ('nc1','nc2'):
            challenge=(lane=='nc2')
            for r in x['records'][lane]:
                if r.get('result')!='SAT':
                    continue
                bp=base/(r['artifact']+'.json')
                mp=shards/r['mutant_relpath']
                if not mp.is_file():
                    raise RuntimeError('missing sealed mutant '+str(mp))
                bf=g.sem(bp,challenge); mf=g.sem(mp,challenge)
                w=r['canonical_witness']
                canonical=g.replay(bf,mf,w)
                if not canonical['divergent']:
                    raise RuntimeError('sealed canonical witness does not replay')
                bits=sorted(w)
                flips=[]
                for bit in bits:
                    q=dict(w); q[bit]=1-q[bit]
                    rp=g.replay(bf,mf,q)
                    flips.append({'bit':bit,'replay':rp,'rejected':not rp['divergent']})
                critical=[z['bit'] for z in flips if z['rejected']]
                lex=bits[0]
                stored=r.get('one_bit_corruption',{})
                records.append({
                    'lane':lane,
                    'candidate_index':r['candidate_index'],
                    'artifact':r['artifact'],
                    'cell':r['cell'],
                    'query_sha256':r['query_sha256'],
                    'witness_bits':len(bits),
                    'canonical_replay':canonical,
                    'lexicographic_first_bit':lex,
                    'stored_corruption_bit':stored.get('bit'),
                    'stored_corruption_rejected':stored.get('rejected'),
                    'critical_single_bits':critical,
                    'critical_single_bit_count':len(critical),
                    'has_single_bit_rejecting_corruption':bool(critical),
                    'first_critical_bit':critical[0] if critical else None,
                    'all_single_bit_flips':flips,
                })

    records.sort(key=lambda r:(r['lane'],r['candidate_index']))
    lane_summary={}
    for lane in ('nc1','nc2'):
        rr=[r for r in records if r['lane']==lane]
        lane_summary[lane]={
            'sat_candidates':len(rr),
            'stored_first_bit_rejected':sum(bool(r['stored_corruption_rejected']) for r in rr),
            'stored_first_bit_not_rejected':sum(not bool(r['stored_corruption_rejected']) for r in rr),
            'has_any_rejecting_single_bit':sum(r['has_single_bit_rejecting_corruption'] for r in rr),
            'has_no_rejecting_single_bit':sum(not r['has_single_bit_rejecting_corruption'] for r in rr),
            'first_sat_candidate':rr[0] if rr else None,
        }
    out={
        'schema':'g3-post-red-witness-bit-sensitivity-diagnostic-v1',
        'authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY',
        'source_red_run':34804261148,
        'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267',
        'corpus_sha256':got,
        'total_sat_candidates':len(records),
        'stored_first_bit_rejected_total':sum(bool(r['stored_corruption_rejected']) for r in records),
        'stored_first_bit_not_rejected_total':sum(not bool(r['stored_corruption_rejected']) for r in records),
        'has_any_rejecting_single_bit_total':sum(r['has_single_bit_rejecting_corruption'] for r in records),
        'has_no_rejecting_single_bit_total':sum(not r['has_single_bit_rejecting_corruption'] for r in records),
        'lane_summary':lane_summary,
        'records':records,
    }
    wr(a.out,out)
    print(json.dumps({k:out[k] for k in (
        'total_sat_candidates','stored_first_bit_rejected_total',
        'stored_first_bit_not_rejected_total','has_any_rejecting_single_bit_total',
        'has_no_rejecting_single_bit_total')},sort_keys=True))

if __name__=='__main__':
    main()
