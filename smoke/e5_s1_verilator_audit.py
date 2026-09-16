#!/usr/bin/env python3
"""Audit a frozen six-state SMT witness against independent Verilator output."""
import hashlib, json, sys
from pathlib import Path
assert len(sys.argv)==3, 'usage: audit.py NATIVE_DIR ORIGINAL_RTL'
o,src=Path(sys.argv[1]),Path(sys.argv[2])
vec=json.loads((o/'witness_vectors.json').read_text())
assert len(vec)==6 and [r['state'] for r in vec]==list(range(6))
assert (o/'native_replay_tb.sv').is_file() and src.is_file()
log=(o/'verilator_simulation.log').read_text()
pins=('o_busy','o_wb_stb_gbl','o_wb_stb_lcl','o_wb_cyc_gbl','o_wb_cyc_lcl','f_cyc')
actual={}
for line in log.splitlines():
    if not line.startswith('TRACE '):continue
    toks=line.split()
    assert len(toks)==8,'wrong native TRACE field count'
    idx=int(toks[1]);assert idx in range(6) and idx not in actual
    assert all(v in ('0','1') for v in toks[2:]),'native X/Z or unknown bit'
    actual[idx]=dict(zip(pins,map(int,toks[2:])))
comparisons=[]
for i in range(6):
    for pin in pins:
        comparisons.append({'state':i,'pin':pin,'expected_smt':vec[i]['expected'][pin],
                            'native_verilator':actual.get(i,{}).get(pin),
                            'match':actual.get(i,{}).get(pin)==vec[i]['expected'][pin]})
match=len(actual)==6 and all(r['match'] for r in comparisons)
first_forbidden=[i for i in range(1,6) if actual.get(i-1,{}).get('f_cyc')==1
                 and vec[i-1]['inputs']['i_lock']==0 and vec[i]['inputs']['i_lock']==1]
target=(len(actual)==6 and actual[2]['f_cyc']==1 and vec[2]['inputs']['i_lock']==0
        and actual[3]['f_cyc']==1 and vec[3]['inputs']['i_lock']==1
        and actual[4]['o_busy']==0 and actual[4]['o_wb_stb_gbl']==0
        and actual[4]['o_wb_stb_lcl']==0
        and (actual[4]['o_wb_cyc_gbl'] or actual[4]['o_wb_cyc_lcl'])
        and actual[4]['f_cyc']==0 and actual[5]['o_wb_cyc_gbl']==0
        and actual[5]['o_wb_cyc_lcl']==0)
verdict={'schema':'e5-s1-native-verilator-amendment02-v1',
         'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
         'testbench_sha256':hashlib.sha256((o/'native_replay_tb.sv').read_bytes()).hexdigest(),
         'witness_vectors_sha256':hashlib.sha256((o/'witness_vectors.json').read_bytes()).hexdigest(),
         'observed_state_count':len(actual),'all_36_pin_checks_match':match,
         'historical_observed_trace_native':target,
         'first_rejected_restored_assumption_cycle':first_forbidden[0] if first_forbidden else None,
         'comparisons':comparisons,
         'limitations':['post-hoc six-state witness, not blind discovery','Verilator two-state execution',
                        'native simulations do not prove formal helper, original property failure, or silicon bug']}
(o/'VERILATOR_REPLAY_VERDICT.json').write_text(json.dumps(verdict,indent=2,sort_keys=True)+'\n')
(o/'SHA256SUMS_VERILATOR').write_text(''.join(
    hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(o.iterdir())
    if p.is_file() and p.name not in ('SHA256SUMS_VERILATOR','verilator_runner_summary.log')))
print(json.dumps({k:verdict[k] for k in ('observed_state_count','all_36_pin_checks_match',
                                      'historical_observed_trace_native',
                                      'first_rejected_restored_assumption_cycle')},sort_keys=True),flush=True)
if not (match and target and first_forbidden):sys.exit(2)
