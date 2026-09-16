#!/usr/bin/env python3
"""E5 S1 monitor-free check: exact native child, original assumption only, direct DUT outputs.

Two source arms differ only by activation of the historically removed assumption.
No injected cover/observer; 40 transitions. Observed-cycle is post-hoc replay,
not a prospectively selected discovery endpoint. An UNSAT query has no get-value.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('pin-check')
HORIZON = 40
COMMENTED = '//always @(posedge i_clk)\n//\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n//\t\t`ASSUME(!i_lock);\n'
RESTORED = '\talways @(posedge i_clk)\n\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n\t\t`ASSUME(!i_lock);\n'
PINS = ('i_lock','o_wb_stb_gbl','o_wb_stb_lcl','o_wb_cyc_gbl','o_wb_cyc_lcl','o_busy')
HELPERS = ('f_cyc','f_outstanding')

def sha(raw): return hashlib.sha256(raw).hexdigest()
def v(state,name): return f'(|pipemem_n {name}| {state})'
def no(s): return f'(not {s})'
def pins_only(s):
    return f'(and {no(v(s,"o_busy"))} {no(v(s,"o_wb_stb_gbl"))} {no(v(s,"o_wb_stb_lcl"))} (or {v(s,"o_wb_cyc_gbl")} {v(s,"o_wb_cyc_lcl")}))'
def helper_endpoint(s):
    return f'(and {pins_only(s)} (= {v(s,"f_outstanding")} #b00000) {no(v(s,"f_cyc"))})'

def build(model,mode):
    for stem in ('pipemem_i','pipemem_t','pipemem_u','pipemem_h','pipemem_is'):
        assert '|'+stem+'|' in model,stem
    for name in PINS+HELPERS:
        assert '|pipemem_n '+name+'|' in model,name
    assert 'yosys-smt2-cover ' not in model
    assert 'f_s1_onset' not in model and 'f_s1_endpoint' not in model
    lines=['(set-logic ALL)','(set-option :produce-models true)',model]
    for i in range(HORIZON+1):
        s=f'|q{i}|'
        lines.extend([f'(declare-fun {s} () |pipemem_s|)',f'(assert (|pipemem_h| {s}))'])
        if i==0:
            lines.append(f'(assert (|pipemem_is| {s}))')
            lines.append(f'(assert (|pipemem_i| {s}))')
        lines.append(f'(assert (|pipemem_u| {s}))')
        if i:
            lines.append(f'(assert (|pipemem_t| |q{i-1}| {s}))')
    if mode=='pin_any':
        query='(or '+' '.join(helper_endpoint(f'|q{i}|') for i in range(HORIZON))+')'
    elif mode=='output_only_any':
        query='(or '+' '.join(pins_only(f'|q{i}|') for i in range(HORIZON))+')'
    elif mode=='observed_cycle':
        # Post-hoc trace-replay, explicitly not an independently preselected outcome.
        query='(and '+' '.join((v('|q2|','f_cyc'),no(v('|q2|','i_lock')),v('|q3|','f_cyc'),v('|q3|','i_lock'),helper_endpoint('|q4|'),no(v('|q5|','o_wb_cyc_gbl'))))+')'
    elif mode=='negative_control':
        query='(and '+pins_only('|q4|')+' '+no(v('|q4|','o_wb_cyc_gbl'))+' '+v('|q4|','o_wb_cyc_gbl')+')'
    else: raise ValueError(mode)
    lines.extend(['(assert '+query+')','(check-sat)','(exit)'])
    return '\n'.join(lines)+'\n'

def main():
    summary={'schema':'e5-s1-monitor-free-query-v2','horizon':HORIZON,'posthoc_replay':True,'cases':{}}
    a=(ROOT/'E_removed/pipemem.v').read_text()
    b=(ROOT/'E_restored/pipemem.v').read_text()
    assert 'f_s1_onset' not in a+b and 'cover(' not in a+b
    assert a.count(COMMENTED)==b.count(RESTORED)==1 and a.replace(COMMENTED,RESTORED,1)==b
    assert (ROOT/'E_removed/actual-task.ys').read_bytes()==(ROOT/'E_restored/actual-task.ys').read_bytes()
    for arm in ('E_removed','E_restored'):
        p=ROOT/arm
        model=(p/'pipemem.smt2').read_text()
        info={'src_sha256':sha((p/'pipemem.v').read_bytes()),'model_sha256':sha(model.encode()),'queries':{}}
        for mode in ('pin_any','output_only_any','observed_cycle','negative_control'):
            q=build(model,mode)
            (p/(mode+'.smt2')).write_text(q)
            info['queries'][mode]=sha(q.encode())
        summary['cases'][arm]=info
    (ROOT/'query-manifest.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    print(json.dumps(summary,sort_keys=True))

if __name__=='__main__': main()
