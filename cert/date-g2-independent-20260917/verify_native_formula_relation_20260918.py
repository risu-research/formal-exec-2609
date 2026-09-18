#!/usr/bin/env python3
"""Rebuild exact two-Yosys-frontend finite relational miters. Python stdlib only.
This checks initialized safety-assertion correspondence, NOT unbounded semantics.
"""
import argparse, hashlib, json, re
from pathlib import Path

INDEXES = {'old': (7, 8, 23), 'new': (12, 13, 28)}
H = {'frozen_old_json':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
     'frozen_new_json':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05'}
def hash_file(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def build(root, out, sides=('old','new'), depths=(2,3,5,6)):
    out.mkdir(parents=True, exist_ok=True)
    manifest={'scope':'Initialized original historical old/new source; 20 common physical registers aligned at k0, 12 original input ports at every state; original full assumption functions asserted on both at each state; 2026-09-13 frozen Yosys SMT2 vs 2026-09-18 original target Yosys SMT2','cases':[], 'mapping':{}}
    for side in sides:
        frz=root/'frozen/natural'/('r0' if side=='old' else 'r1')
        src=root/'source'/side/'bench/formal/source_ir.smt2'
        mat=root/'matrix/out'/side/'frozen_exact/ir.json'
        assert hash_file(frz.with_suffix('.json')) == H['frozen_'+side+'_json']
        assert hash_file(mat)==H['frozen_'+side+'_json'], 'historical frozen JSON cannot be reproduced literally'
        assert src.is_file()
        a=frz.with_suffix('.smt2').read_text();b=src.read_text()
        assert 'yosys-smt2-module pipemem' in a and 'yosys-smt2-module pipemem' in b
        ra=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',a,re.M));rb=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',b,re.M))
        regs=sorted(x for x in ra.keys()&rb.keys() if not x.startswith('$') and not x.startswith('fwb.$') and ra[x]==rb[x])
        ia=set(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',a,re.M));ib=set(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',b,re.M))
        inputs=sorted(n for n,w in ia&ib)
        assert len(regs)==20 and len(inputs)==12,(side,regs,inputs)
        assert sorted(ia)==sorted(ib), 'model IO drift'
        ap=a.replace('pipemem','frz_pipemem');bp=b.replace('pipemem','src_pipemem')
        header=ap+'\n'+bp+'\n'
        manifest['mapping'][side]={'frozen_smt2_sha256':hash_file(frz.with_suffix('.smt2')),'source_smt2_sha256':hash_file(src),'matched_physical_registers':regs,'matched_inputs':inputs,'differing_assertion_indices':list(INDEXES[side])}
        lines=frz.with_suffix('.smt2').read_text().splitlines();ens={}
        for idx in INDEXES[side]:
            line=next(x for x in lines if x.startswith(f'(define-fun |pipemem_a {idx}|'))
            en=re.search(r'\(not \(= \(\(_ extract 0 0\) \(\|pipemem#(\d+)\| state\)\) #b1\)\)',line)
            assert en and ('rtl/' in line), (side,idx,line)
            ens[idx]=int(en.group(1))
        past=('$past$rtl/core/pipemem.v:447$5$0' if side=='old' else '$past$rtl/core/pipemem.v:471$4$0').replace('pipemem','frz_pipemem')
        assert f'|frz_pipemem_n {past}|' in ap
        for depth in depths:
            body=''.join(f'(declare-fun f{k} () |frz_pipemem_s|)\n(declare-fun s{k} () |src_pipemem_s|)\n' for k in range(depth+1))
            body+='(assert (|frz_pipemem_is| f0))\n(assert (|src_pipemem_is| s0))\n'
            body+=''.join(f'(assert (not (|frz_pipemem_is| f{k})))\n(assert (not (|src_pipemem_is| s{k})))\n' for k in range(1,depth+1))
            body+='(assert (|frz_pipemem_i| f0))\n(assert (|src_pipemem_i| s0))\n'
            body+=''.join(f'(assert (= (|frz_pipemem_n {x}| f0) (|src_pipemem_n {x}| s0)))\n' for x in regs)
            body+=''.join(f'(assert (= (|frz_pipemem_n {x}| f{k}) (|src_pipemem_n {x}| s{k})))\n' for k in range(depth+1) for x in inputs)
            body+=''.join(f'(assert (|frz_pipemem_t| f{k} f{k+1}))\n(assert (|src_pipemem_t| s{k} s{k+1}))\n' for k in range(depth))
            body+=''.join(f'(assert (|frz_pipemem_u| f{k}))\n(assert (|src_pipemem_u| s{k}))\n' for k in range(depth+1))
            queries={
                'base':('', 'sat'),
                'all':('(assert (or '+' '.join(f'(not (= (|frz_pipemem_a {j}| f{depth}) (|src_pipemem_a {j}| s{depth})))' for j in range(30 if side=='old' else 35))+'))\n','unsat'),
                'physical':('(assert (or '+' '.join(f'(not (= (|frz_pipemem_n {x}| f{depth}) (|src_pipemem_n {x}| s{depth})))' for x in regs)+'))\n','unsat'),
                'three':('(assert (or '+' '.join(f'(not (= (|frz_pipemem_a {j}| f{depth}) (|src_pipemem_a {j}| s{depth})))' for j in INDEXES[side])+'))\n','unsat'),
                'active':(f'(assert (|frz_pipemem_n f_past_valid| f{depth}))\n(assert (|frz_pipemem_n i_pipe_stb| f{depth-1}))\n','sat'),
                'past_not_zero':(f'(assert (|frz_pipemem_n {past}| f{depth}))\n','unsat'),
            }
            if depth>=3:
                for idx,en in ens.items():
                    if idx == INDEXES[side][-1] and depth<5: continue
                    queries[f'assertion_{idx}_enabled']=(f'(assert (= (|frz_pipemem#{en}| f{depth}) #b1))\n','sat')
            for label,(challenge,expected) in queries.items():
                name=f'{side}_k{depth}_{label}.smt2';data=(header+body+challenge+'(check-sat)\n').encode();(out/name).write_bytes(data)
                manifest['cases'].append({'name':name,'side':side,'depth':depth,'kind':label,'expected':expected,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    (out/'MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print('GENERATED',len(manifest['cases']),'queries',len(manifest['mapping']),'models')
    return manifest
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--side',choices=('old','new'))
    args=ap.parse_args();build(args.root,args.output,(args.side,) if args.side else ('old','new'))
