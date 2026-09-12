#!/usr/bin/env python3
"""Emit concrete TNF semantic witnesses for directional formal-scope deltas.

This is deliberately separate from temporal carrier realization.  It certifies a
model of the symbolic scope difference itself and projects the model onto only
variables occurring in target assumptions that are violated by that model.
"""
import argparse, json, re
import z3
from tnf_scope import IR, _check


def vars_in(e):
    out={}
    def walk(x):
        if z3.is_const(x) and x.decl().kind()==z3.Z3_OP_UNINTERPRETED:
            out[str(x)]=x; return
        for c in x.children(): walk(c)
    walk(e); return out


def model_value(m,v):
    x=m.eval(v,model_completion=True)
    if z3.is_bv(x): return int(x.as_long())
    if z3.is_bool(x): return bool(z3.is_true(x))
    return str(x)


def pack(assign):
    bits={}; scalars={}
    pat=re.compile(r'^(.*)\[(\d+)\]$')
    for k,v in assign.items():
        mm=pat.match(k)
        if mm and v in (0,1): bits.setdefault(mm.group(1),{})[int(mm.group(2))]=v
        else: scalars[k]=v
    buses={}
    for name,b in bits.items():
        idx=sorted(b)
        value=sum((b[i]&1)<<i for i in idx)
        buses[name]={'bits':{str(i):b[i] for i in idx},'value_hex':hex(value),'observed_width':max(idx)+1}
    return {'buses':buses,'scalars':scalars}


def direction(source,target,label,timeout_ms):
    SF=z3.And(*[x['f'] for x in source]) if source else z3.BoolVal(True)
    TF=z3.And(*[x['f'] for x in target]) if target else z3.BoolVal(True)
    s=z3.Solver(); s.add(SF,z3.Not(TF)); r=_check(s,timeout_ms)
    if r==z3.unsat: return {'direction':label,'sat':False}
    m=s.model()
    violated=[]; vv={}
    for i,x in enumerate(target):
        val=m.eval(x['f'],model_completion=True)
        if z3.is_false(val):
            violated.append({'index':i,'src':x['src'],'phase':x['phase']})
            vv.update(vars_in(x['f']))
    assign={k:model_value(m,v) for k,v in sorted(vv.items())}
    if not z3.is_true(m.eval(SF,model_completion=True)) or not z3.is_false(m.eval(TF,model_completion=True)):
        raise RuntimeError('internal witness verification failed')
    return {
      'direction':label,'sat':True,'source_conjunction':True,'target_conjunction':False,
      'violated_target_assumptions':violated,'projection':pack(assign),
      'projected_variable_count':len(assign)
    }


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('old'); ap.add_argument('new'); ap.add_argument('--timeout-ms',type=int,default=10000)
    ns=ap.parse_args(); old,new=IR(ns.old),IR(ns.new); A,B=old.assumptions(),new.assumptions()
    if old.unsupported or new.unsupported: raise RuntimeError(f'unsupported cells: {old.unsupported|new.unsupported}')
    out={
      'schema':'formal-scope-semantic-witness-v1',
      'old_sha256':old.sha256,'new_sha256':new.sha256,
      'old_only':direction(A,B,'old_and_not_new',ns.timeout_ms),
      'new_only':direction(B,A,'new_and_not_old',ns.timeout_ms),
      'scope':'symbolic_TNF_only_not_carrier_realization'
    }
    print(json.dumps(out,indent=2,sort_keys=True))
if __name__=='__main__': main()
