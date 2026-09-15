#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: exact exhaustive one-bit witness sensitivity over sealed Amendment-07 RED evidence.

This is a computational acceleration only. For each sealed SAT witness, every possible
single-bit flip is represented by one selector variable. We then classify all selector
values by two independent Z3 paths:
  (A) direct grounding/simplification of baseline and mutant formulas for every selector;
  (B) solver enumeration of all selector values satisfying divergence and non-divergence.
The two exhaustive partitions must match exactly. Stored first-bit corruption is also
replayed through the original fixed-assignment replay function and cross-checked.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import z3
from z3.z3util import get_vars
import g3_negative_controls_v3 as v3

g = v3.g
LABELS = ('natural_old','natural_new','mirror_old','mirror_new')
EXP = {
    'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
    'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
    'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
    'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}

def wr(p, o):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, indent=2, sort_keys=True) + '\n')

def truth(e):
    e = z3.simplify(e)
    if z3.is_true(e): return True
    if z3.is_false(e): return False
    raise RuntimeError('expression did not ground to Boolean constant: ' + e.sexpr()[:240])

def selectorize(e, ordered_bits, w, selector):
    vm = {v.sexpr(): v for v in get_vars(e)}
    missing = [k for k in vm if k not in w]
    if missing: raise RuntimeError('witness missing expression vars ' + repr(missing[:8]))
    subs = []
    index = {k:i for i,k in enumerate(ordered_bits)}
    for k, v in vm.items():
        if not z3.is_bv(v) or v.size() != 1: raise RuntimeError('unexpected witness sort ' + k)
        i = index[k]
        c = int(w[k])
        subs.append((v, z3.If(selector == i, z3.BitVecVal(1-c,1), z3.BitVecVal(c,1))))
    q = z3.simplify(z3.substitute(e, *subs)) if subs else z3.simplify(e)
    rem = get_vars(q)
    if any(v.sexpr() != selector.sexpr() for v in rem):
        raise RuntimeError('selectorization left non-selector vars: ' + repr([v.sexpr() for v in rem]))
    return q

def enumerate_selector(expr, selector, n, want_true):
    s = z3.Solver()
    s.add(selector >= 0, selector < n, expr if want_true else z3.Not(expr))
    out = set()
    while True:
        r = s.check()
        if r == z3.unsat: break
        if r != z3.sat: raise RuntimeError('selector enumeration returned ' + str(r))
        m = s.model(); val = m.eval(selector, model_completion=True).as_long()
        if not 0 <= val < n: raise RuntimeError('selector model out of range')
        if val in out: raise RuntimeError('selector enumeration duplicate')
        out.add(val); s.add(selector != val)
    return out

def direct_original_replay(bf, mf, w, bit):
    q = dict(w); q[bit] = 1 - q[bit]
    return g.replay(bf, mf, q)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--artifact-root', required=True)
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    if not 0 <= a.shard < 16: raise RuntimeError('bad shard')
    root = Path(a.artifact_root); base = root/'input'/'frozen'/'base'; shards = root/'shards'
    got = {k:g.sh(base/(k+'.json')) for k in LABELS}
    if got != EXP: raise RuntimeError('sealed corpus hash mismatch')
    sf = shards/f'SHARD_{a.shard:02d}.json'; x = json.loads(sf.read_text())
    if x.get('schema') != 'g3-negative-shard-v2' or x.get('shard_index') != a.shard or x.get('error_count') != 0:
        raise RuntimeError('bad sealed shard')

    records = []
    for lane in ('nc1','nc2'):
        challenge = lane == 'nc2'
        for r in x['records'][lane]:
            if r.get('result') != 'SAT': continue
            bp = base/(r['artifact']+'.json'); mp = shards/r['mutant_relpath']
            if not mp.is_file(): raise RuntimeError('missing mutant ' + str(mp))
            bf = g.sem(bp, challenge); mf = g.sem(mp, challenge)
            w = r['canonical_witness']; bits = sorted(w); n = len(bits)
            if n == 0: raise RuntimeError('empty SAT witness')
            canonical = g.replay(bf, mf, w)
            if not canonical['divergent']: raise RuntimeError('canonical replay failed')

            selector = z3.Int(f'__g3_flip_selector_{lane}_{r["candidate_index"]}')
            bs = selectorize(bf, bits, w, selector)
            ms = selectorize(mf, bits, w, selector)
            ds = z3.simplify(z3.Xor(bs, ms))

            direct = []
            direct_div = set(); direct_rej = set()
            for i, bit in enumerate(bits):
                bv = truth(z3.substitute(bs, (selector, z3.IntVal(i))))
                mv = truth(z3.substitute(ms, (selector, z3.IntVal(i))))
                dv = bv != mv
                (direct_div if dv else direct_rej).add(i)
                direct.append({'index':i,'bit':bit,'baseline':bv,'mutant':mv,'divergent':dv,'rejected':not dv})

            solver_div = enumerate_selector(ds, selector, n, True)
            solver_rej = enumerate_selector(ds, selector, n, False)
            universe = set(range(n))
            if solver_div & solver_rej: raise RuntimeError('solver partition overlap')
            if solver_div | solver_rej != universe: raise RuntimeError('solver partition incomplete')
            if direct_div != solver_div or direct_rej != solver_rej:
                raise RuntimeError('direct/solver exhaustive partition disagreement')

            co = r.get('one_bit_corruption', {})
            stored_bit = co.get('bit')
            if stored_bit not in w: raise RuntimeError('stored corruption bit absent')
            stored_idx = bits.index(stored_bit)
            original_stored = direct_original_replay(bf, mf, w, stored_bit)
            stored_rejected = not original_stored['divergent']
            if stored_rejected != bool(co.get('rejected')):
                raise RuntimeError('stored artifact replay disagreement')
            if stored_rejected != direct[stored_idx]['rejected']:
                raise RuntimeError('stored replay vs selector replay disagreement')

            critical_bits = [bits[i] for i in sorted(direct_rej)]
            first_critical = critical_bits[0] if critical_bits else None
            first_critical_original = None
            if first_critical is not None:
                first_critical_original = direct_original_replay(bf, mf, w, first_critical)
                if first_critical_original['divergent']:
                    raise RuntimeError('first critical bit failed original replay cross-check')

            records.append({
                'lane':lane,
                'candidate_index':r['candidate_index'],
                'artifact':r['artifact'],
                'cell':r['cell'],
                'query_sha256':r['query_sha256'],
                'witness_bits':n,
                'canonical_replay':canonical,
                'stored_corruption_bit':stored_bit,
                'stored_corruption_rejected':stored_rejected,
                'stored_original_replay':original_stored,
                'critical_single_bits':critical_bits,
                'critical_single_bit_count':len(critical_bits),
                'has_any_rejecting_single_bit':bool(critical_bits),
                'first_critical_bit':first_critical,
                'first_critical_original_replay':first_critical_original,
                'divergent_single_bit_count':len(direct_div),
                'direct_partition_sha256':hashlib.sha256(json.dumps(direct,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'all_single_bit_flips':direct,
                'solver_rejecting_indices':sorted(solver_rej),
                'solver_divergent_indices':sorted(solver_div),
                'exhaustive_methods_agree':True,
            })

    records.sort(key=lambda r:(r['lane'],r['candidate_index']))
    out = {
        'schema':'g3-post-red-witness-bit-sensitivity-shard-v2',
        'authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY',
        'method':'exact_selector_direct_plus_solver_enumeration_plus_original_replay_crosscheck',
        'source_red_run':34804261148,
        'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267',
        'source_red_artifact_digest':'sha256:adce6b557ecdd0d5d7e21a370cc0e1090c07e2dfbc312dcfca3f619d83576881',
        'shard':a.shard,
        'corpus_sha256':got,
        'sat_candidates':len(records),
        'total_single_bit_flips':sum(r['witness_bits'] for r in records),
        'stored_first_bit_rejected':sum(r['stored_corruption_rejected'] for r in records),
        'stored_first_bit_not_rejected':sum(not r['stored_corruption_rejected'] for r in records),
        'has_any_rejecting_single_bit':sum(r['has_any_rejecting_single_bit'] for r in records),
        'has_no_rejecting_single_bit':sum(not r['has_any_rejecting_single_bit'] for r in records),
        'all_exhaustive_methods_agree':all(r['exhaustive_methods_agree'] for r in records),
        'records':records,
    }
    wr(a.out, out)
    print(json.dumps({k:out[k] for k in ('shard','sat_candidates','total_single_bit_flips','stored_first_bit_rejected','stored_first_bit_not_rejected','has_any_rejecting_single_bit','has_no_rejecting_single_bit','all_exhaustive_methods_agree')}, sort_keys=True))

if __name__ == '__main__': main()
