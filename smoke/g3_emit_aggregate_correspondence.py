#!/usr/bin/env python3
"""Emit a full aggregate Yosys solver-semantics ↔ TNF correspondence miter.

The theorem covers both STATE and TRANSITION assumptions. A two-transition frame
is intentional: generated $past registers that the TNF normalizer identifies by
update-function fingerprint are compared only after Yosys itself has established
their next-state values from a common predecessor. No assumption is dropped.

The TNF theorem variable set is snapshotted immediately after constructing the
whole normalized conjunction. Helper discovery of alternate history-register
representatives is observational only and is forbidden from widening that set.
If a nondeterministic X/Z literal is genuinely present in the snapshotted TNF
formula, this emitter fails closed rather than pretending to bind it.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import z3
from tnf_scope import IR


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def norm_name(n): return n.lstrip('\\').replace('\\','/')

def named_accessors(text,module):
    prefix=f'(define-fun |{module}_n '
    out={}
    for line in text.splitlines():
        if not line.startswith(prefix): continue
        rest=line[len(prefix):]
        marker=f'| ((state |{module}_s|)) '
        if marker not in rest: continue
        name=rest.split(marker,1)[0]
        out[name]=name
    return out

def accessor_for_bit(ir,b,accessors,state='g3_s1'):
    candidates=[]
    for n,j,w in ir.names.get(b,[]):
        for nn in (n,norm_name(n)):
            if nn in accessors:
                generated=nn.startswith('$') or '.$' in nn
                candidates.append((1 if generated else 0,len(nn),nn,j,w))
    if not candidates: return None
    _,_,name,j,w=min(candidates)
    call=f'(|{ir.module_name}_n {name}| {state})'
    if w==1:
        if j!=0: raise RuntimeError(f'1-bit accessor {name} has bit index {j}')
        expr=f'(ite {call} #b1 #b0)'
    else:
        expr=f'((_ extract {j} {j}) {call})'
    return {'name':name,'bit_index':j,'width':w,'expr':expr}

def past_representatives(ir,accessors,required_past_keys):
    """Find raw Yosys representatives only for history identities in the theorem.

    Evaluating candidate D-cones may populate IR caches/vars. Those helper-created
    variables are intentionally outside required_past_keys and never become theorem
    variables merely because discovery observed them.
    """
    out={}
    remaining=set(required_past_keys)
    for b,(cn,c,j) in ir.qbits.items():
        if not remaining: break
        qnames=' '.join(n for n,_,_ in ir.names.get(b,[]))
        if '$past$' not in cn and '$past$' not in qnames: continue
        de=z3.simplify(ir.bit(c['connections']['D'][j],False,cn+f':PAST_D{j}'))
        fp=hashlib.sha256(de.sexpr().encode()).hexdigest()[:24]
        key='PAST_'+fp
        if key not in required_past_keys: continue
        acc=accessor_for_bit(ir,b,accessors)
        if acc is not None:
            out.setdefault(key,[]).append((b,acc))
            remaining.discard(key)
    return out

def map_variables(ir,accessors,required_vars):
    required_keys=set(required_vars)
    required_past={k for k in required_keys if k.startswith('PAST_')}
    past=past_representatives(ir,accessors,required_past)
    helper_only=sorted(set(ir.vars)-required_keys)
    rows=[]
    for key,v in sorted(required_vars.items()):
        if key=='INITSTATE':
            rows.append({'key':key,'symbol':v.sexpr(),'kind':'initstate','expr':f'(ite (|{ir.module_name}_is| g3_s1) #b1 #b0)'})
            continue
        if key.startswith('X_'):
            raise RuntimeError(f'genuine TNF nondeterministic literal cannot be independently bound: {key}')
        if key.startswith('PAST_'):
            xs=past.get(key,[])
            if not xs: raise RuntimeError(f'no Yosys accessor for required semantic history identity {key}')
            b,acc=min(xs,key=lambda x:(x[1]['name'],x[1]['bit_index']))
            rows.append({'key':key,'symbol':v.sexpr(),'kind':'past_update_identity','json_bit':b,'accessor':acc['name'],'accessor_bit':acc['bit_index'],'expr':acc['expr'],'representatives':len(xs)})
            continue
        bits=sorted(set(b for b in ir.names if ir._leaf_key(b)==key))
        if len(bits)!=1:
            raise RuntimeError(f'TNF leaf {key} maps to {len(bits)} JSON bits: {bits}')
        b=bits[0]; acc=accessor_for_bit(ir,b,accessors)
        if acc is None: raise RuntimeError(f'no Yosys -wires accessor for TNF leaf {key} / bit {b}')
        rows.append({'key':key,'symbol':v.sexpr(),'kind':'state_leaf','json_bit':b,'accessor':acc['name'],'accessor_bit':acc['bit_index'],'expr':acc['expr']})
    return rows,helper_only

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('json'); ap.add_argument('smt2'); ap.add_argument('out'); ns=ap.parse_args()
    text=Path(ns.smt2).read_text(); ir=IR(ns.json); assumptions=ir.assumptions()
    if ir.unsupported: raise RuntimeError(f'unsupported cells: {sorted(ir.unsupported)}')
    if ir.mixed_formal: raise RuntimeError(f'mixed formal encodings: {ir.mixed_formal}')

    # Freeze exactly the variables created by the governed TNF conjunction before
    # any helper traversal can observe unrelated design logic.
    tnf=z3.And(*[x['f'] for x in assumptions]) if assumptions else z3.BoolVal(True)
    required_vars=dict(ir.vars)
    if any(k.startswith('X_') for k in required_vars):
        raise RuntimeError('governed TNF formula itself contains nondeterministic X/Z literals: '+','.join(sorted(k for k in required_vars if k.startswith('X_'))))

    accessors=named_accessors(text,ir.module_name)
    if not accessors: raise RuntimeError('no Yosys -wires accessors found')
    links,helper_only=map_variables(ir,accessors,required_vars)
    if set(required_vars) != {r['key'] for r in links}:
        raise RuntimeError('required TNF variable binding is not total')

    state_count=sum(x['phase']=='STATE' for x in assumptions)
    transition_count=sum(x['phase']=='TRANSITION' for x in assumptions)
    raw=[]; phase_rows=[]
    for idx,x in enumerate(assumptions):
        st='g3_s1' if x['phase']=='STATE' else 'g3_s2'
        raw.append(f'(|{ir.module_name}_u {idx}| {st})')
        phase_rows.append({'index':idx,'phase':x['phase'],'src':x['src'],'raw_state':st})
    raw_conj='true' if not raw else (raw[0] if len(raw)==1 else '(and\n    '+'\n    '.join(raw)+')')
    decls='\n'.join(f'(declare-fun {r["symbol"]} () (_ BitVec 1))' for r in links)
    binds='\n'.join(f'(assert (= {r["symbol"]} {r["expr"]}))' for r in links)
    query=f'''\n; G3 two-frame aggregate correspondence theorem\n(declare-const g3_s0 |{ir.module_name}_s|)\n(declare-const g3_s1 |{ir.module_name}_s|)\n(declare-const g3_s2 |{ir.module_name}_s|)\n(assert (|{ir.module_name}_t| g3_s0 g3_s1))\n(assert (|{ir.module_name}_t| g3_s1 g3_s2))\n{decls}\n{binds}\n(assert (xor\n  {raw_conj}\n  {tnf.sexpr()}))\n(check-sat)\n'''
    p=Path(ns.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text+query)
    meta={
      'schema':'g3-yosys-tnf-aggregate-miter-v3',
      'frame_domain':'two_transitions_common_predecessor_for_history_identity',
      'module':ir.module_name,
      'assumptions_total':len(assumptions),
      'state_assumptions':state_count,
      'transition_assumptions':transition_count,
      'required_tnf_variables':len(required_vars),
      'tnf_variables_bound':len(links),
      'helper_only_variables_excluded_from_theorem':helper_only,
      'variable_bindings':links,
      'assumption_phases':phase_rows,
      'query_sha256':sha(p)
    }
    print(json.dumps(meta,sort_keys=True))
if __name__=='__main__': main()
