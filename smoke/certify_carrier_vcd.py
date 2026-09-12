#!/usr/bin/env python3
"""Independent post-checker for ProofScope carrier VCD witnesses.

Reads the concrete traces produced by yosys-smtbmc and reconstructs only the
small semantic vocabulary needed for the case-study claims.  It does not call
Z3/Yosys and therefore provides a simple trace-level certificate independent of
the scope classifier.
"""
import argparse, json
from pathlib import Path

SIGNALS = {
    'cyc','f_cyc','f_past_valid','i_addr','i_lock','i_pipe_stb',
    'i_reset','i_wb_stall','o_wb_addr'
}


def parse_vcd(path):
    scopes=[]; code_to_name={}; widths={}; vals={}; snaps=[]; now=None
    for raw in Path(path).read_text(errors='replace').splitlines():
        line=raw.strip()
        if line.startswith('$scope'):
            scopes.append(line.split()[2]); continue
        if line.startswith('$upscope'):
            if scopes: scopes.pop()
            continue
        if line.startswith('$var'):
            p=line.split(); width=int(p[2]); code=p[3]; name=p[4]
            full='.'.join(scopes+[name])
            # Prefer top-level pipemem signals over same-named child signals.
            if name in SIGNALS and len(scopes)==1:
                code_to_name[code]=name; widths[name]=width
            continue
        if line.startswith('#'):
            if now is not None: snaps.append((now,vals.copy()))
            now=int(line[1:]); continue
        if line.startswith('b'):
            p=line.split()
            if len(p)==2 and p[1] in code_to_name: vals[code_to_name[p[1]]]=p[0][1:]
        elif line and line[0] in '01xz' and line[1:] in code_to_name:
            vals[code_to_name[line[1:]]]=line[0]
    if now is not None: snaps.append((now,vals.copy()))
    # SMTBMC uses 10ns full-step boundaries; retain those only.
    return [(t,v) for t,v in snaps if t % 10 == 0]


def intval(v,name):
    s=v[name]
    if any(c not in '01' for c in s): raise RuntimeError(f'{name} not concrete: {s}')
    return int(s,2)

def bit(v,name): return bool(intval(v,name))

def guard(v):
    return bit(v,'f_past_valid') and bit(v,'f_cyc') and not bit(v,'i_wb_stall') and bit(v,'i_pipe_stb')

def old_addr(v):
    ia,wb=intval(v,'i_addr'),intval(v,'o_wb_addr')
    return ia==wb or ia==wb+1

def new_addr(v):
    ia,wb=intval(v,'i_addr'),intval(v,'o_wb_addr')
    wa=ia >> 2
    return wa==wb or wa==wb+1

def compact(t,v):
    return {
      'time_ns':t,'step':t//10,
      'f_past_valid':intval(v,'f_past_valid'),'f_cyc':intval(v,'f_cyc'),
      'i_pipe_stb':intval(v,'i_pipe_stb'),'i_wb_stall':intval(v,'i_wb_stall'),
      'i_addr_hex':hex(intval(v,'i_addr')),'i_addr_word_hex':hex(intval(v,'i_addr')>>2),
      'o_wb_addr_hex':hex(intval(v,'o_wb_addr')),'i_lock':intval(v,'i_lock'),'i_reset':intval(v,'i_reset')
    }

def cert(mode,path):
    s=parse_vcd(path)
    if mode in ('old-new','new-old'):
        want=lambda v: guard(v) and ((old_addr(v) and not new_addr(v)) if mode=='old-new' else (new_addr(v) and not old_addr(v)))
        hits=[(t,v) for t,v in s if want(v)]
        if not hits: raise SystemExit(f'no {mode} distinguishing witness in {path}')
        t,v=hits[0]
        return {
          'schema':'proofscope-carrier-witness-v1','mode':mode,
          'earliest_distinguishing_snapshot':compact(t,v),
          'guard':guard(v),'old_address_contract':old_addr(v),'new_address_contract':new_addr(v),
          'prior_distinguishing_snapshots':sum(1 for tt,vv in s if tt<t and want(vv)),
          'certificate':'PASS'
        }
    # lock: current i_lock rises although prior cycle had f_cyc and !i_lock.
    hits=[]
    for i in range(1,len(s)):
        t,v=s[i]; pt,p=s[i-1]
        if bit(v,'f_past_valid') and bit(p,'f_cyc') and not bit(p,'i_lock') and bit(v,'i_lock'):
            hits.append((i,t,v,pt,p))
    if not hits: raise SystemExit(f'no removed-lock witness in {path}')
    i,t,v,pt,p=hits[0]
    return {
      'schema':'proofscope-carrier-witness-v1','mode':'lock',
      'earliest_distinguishing_snapshot':compact(t,v),
      'previous_snapshot':compact(pt,p),
      'historical_lock_condition':True,
      'historical_lock_conclusion_not_i_lock':False,
      'prior_distinguishing_snapshots':len([1 for j in range(1,i) if bit(s[j][1],'f_past_valid') and bit(s[j-1][1],'f_cyc') and not bit(s[j-1][1],'i_lock') and bit(s[j][1],'i_lock')]),
      'certificate':'PASS'
    }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('mode',choices=['old-new','new-old','lock']); ap.add_argument('vcd')
    ns=ap.parse_args(); print(json.dumps(cert(ns.mode,ns.vcd),indent=2,sort_keys=True))
if __name__=='__main__': main()
