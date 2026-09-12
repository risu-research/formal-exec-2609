#!/usr/bin/env python3
"""Audit cross-revision external vocabulary before identity-aligned comparison.

The checker never silently assumes that different interface schemas share a common
semantic universe.  Exact port identity permits the simple identity alignment on
external signals.  Any mismatch is reported and can be configured to fail closed,
requiring an explicit relational alignment instead.
"""
import argparse, hashlib, json


def load(path):
    raw=open(path,'rb').read(); d=json.loads(raw)
    if len(d.get('modules',{}))!=1: raise RuntimeError('expected one flattened module')
    name,m=next(iter(d['modules'].items()))
    ports={}
    for n,p in m.get('ports',{}).items():
        ports[n]={'direction':p.get('direction'),'width':len(p.get('bits',[]))}
    return name,hashlib.sha256(raw).hexdigest(),ports

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('old'); ap.add_argument('new'); ap.add_argument('--require-external-identity',action='store_true')
    ns=ap.parse_args(); on,oh,a=load(ns.old); nn,nh,b=load(ns.new)
    names=sorted(set(a)|set(b)); diffs=[]
    for n in names:
        if a.get(n)!=b.get(n): diffs.append({'port':n,'old':a.get(n),'new':b.get(n)})
    out={'schema':'formal-scope-vocabulary-audit-v1','old_module':on,'new_module':nn,'old_sha256':oh,'new_sha256':nh,
         'external_port_count_old':len(a),'external_port_count_new':len(b),'external_identity':not diffs,'differences':diffs,
         'alignment_contract':'IDENTITY_ON_EXTERNAL_PORTS' if not diffs else 'EXPLICIT_RELATIONAL_ALIGNMENT_REQUIRED',
         'internal_state_identity_claimed':False,
         'note':'Internal-state equivalence is not inferred from names; temporal carrier validation is a separate layer.'}
    print(json.dumps(out,indent=2,sort_keys=True))
    if ns.require_external_identity and diffs: raise SystemExit('external vocabulary mismatch: explicit alignment required')
if __name__=='__main__': main()
