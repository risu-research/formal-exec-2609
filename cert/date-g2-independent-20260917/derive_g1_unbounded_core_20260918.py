#!/usr/bin/env python3
"""Generate unrestricted one-step core-bisimulation and observational equivalence queries.
The two SMT2 headers are exact from immutable upstream archives, with only module symbols namespaced.
This proves statements about the native Yosys 2-state state machine, not Verilog front-end correctness.
"""
from pathlib import Path
import json,hashlib,re,zipfile,os
D=Path(os.environ.get('G1_OUT','/mnt/data/DATE_G1_UNBOUNDED_20260918')); D.mkdir(parents=True,exist_ok=True)
input_=Path(os.environ.get('G1_INPUT','/mnt/data/DATE_G1_RELATION_CLOSURE_20260918/input_archives'))
fz=zipfile.ZipFile(input_/'frozen_upstream.zip');sz=zipfile.ZipFile(input_/'source_same_ir.zip')
manifest={'scope':'unbounded conditional simulation of SAME RTL revision under two Yosys routes; 2-state native transition functions, 20 physical regs + 16x9 FIFO memory and 12 inputs; no environmental assumptions for core/outputs', 'queries': [],'sources':{}, 'mapping': {}}
for side,nassert in [('old',30),('new',35)]:
 a=fz.read('natural/'+('r0' if side=='old' else 'r1')+'.smt2')
 b=sz.read(side+'/bench/formal/source_ir.smt2')
 manifest['sources'][side]={'frozen_smt2':hashlib.sha256(a).hexdigest(),'source_smt2':hashlib.sha256(b).hexdigest()}
 A=a.decode();B=b.decode()
 ra=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',A,re.M));rb=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',B,re.M))
 core=sorted(x for x in ra.keys()&rb.keys() if not x.startswith('$') and not x.startswith('fwb.$') and ra[x]==rb[x]); assert len(core)==20
 ia=dict(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',A,re.M));ib=dict(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',B,re.M)); assert ia==ib and len(ia)==12
 oa=dict(re.findall(r'^; yosys-smt2-output (.+?) (\d+)$',A,re.M));ob=dict(re.findall(r'^; yosys-smt2-output (.+?) (\d+)$',B,re.M));assert oa==ob and len(oa)==14
 assert '; yosys-smt2-memory fifo_oreg 4 9 1 1 sync' in A and '; yosys-smt2-memory fifo_oreg 4 9 1 1 sync' in B
 H=A.replace('pipemem','frz_pipemem')+'\n'+B.replace('pipemem','src_pipemem')+'\n'
 manifest['mapping'][side]={'core_registers':{x:ra[x] for x in core},'input_ports':ia,'output_ports':oa,'memory':'fifo_oreg; 16 x 9 bits'}
 def write(label,body,expect):
  t=H+body+'(check-sat)\n';p=D/(side+'_'+label+'.smt2');p.write_text(t)
  manifest['queries'].append({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'expected':expect})
 def st(k):return ''.join(f'(declare-fun {p}{k} () |{nm}_pipemem_s|)\n' for p,nm in [('f','frz'),('s','src')])
 def eq(x,k,func='n'):return f'(= (|frz_pipemem_{func} {x}| f{k}) (|src_pipemem_{func} {x}| s{k}))'
 def equal(x):return '(assert '+x+')\n'
 def core_rel(k):return [eq(x,k) for x in core]+[eq('fifo_oreg',k,'m')]
 def input_rel(k):return [eq(x,k) for x in ia]
 def bad(items):return '(assert (or '+' '.join('(not '+q+')' for q in items)+'))\n'
 D0=st(0)+''.join(map(equal,core_rel(0)+input_rel(0)))
 write('induction_R_nonvacuous',D0,'sat')
 write('outputs_14_all',D0+bad([eq(x,0) for x in oa]),'unsat')
 for x in oa:write('output_'+x,D0+equal('(not '+eq(x,0)+')'),'unsat')
 D1=D0+st(1)+''.join(map(equal,input_rel(1)))+'(assert (|frz_pipemem_t| f0 f1))\n(assert (|src_pipemem_t| s0 s1))\n'
 write('induction_step_base',D1,'sat')
 write('induction_step_all_21',D1+bad(core_rel(1)),'unsat')
 write('induction_step_memory',D1+equal('(not '+eq('fifo_oreg',1,'m')+')'),'unsat')
 write('induction_step_phys20',D1+bad([eq(x,1) for x in core]),'unsat')
 Dinit=D0+'(assert (|frz_pipemem_i| f0))\n(assert (|src_pipemem_i| s0))\n'
 write('paired_init_nonvacuous',Dinit,'sat')
 write('paired_init_original_U_nonvacuous',Dinit+'(assert (|frz_pipemem_u| f0))\n(assert (|src_pipemem_u| s0))\n','sat')
 # Direct compare of the *entire* formal assertion output vector with no init or ghost relation.
 write('free_symbol_assertion_mismatch',D0+bad([f'(= (|frz_pipemem_a {i}| f0) (|src_pipemem_a {i}| s0))' for i in range(nassert)]),'sat')
(D/'CORE_MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print('BUILT',len(manifest['queries']),'queries',manifest['sources'],flush=True)
