#!/usr/bin/env python3
from pathlib import Path
import json,re,hashlib,zipfile,os
D=Path(os.environ.get('G1_OUT','/mnt/data/DATE_G1_UNBOUNDED_20260918'));D.mkdir(exist_ok=True,parents=True)
Z=zipfile.ZipFile(Path(os.environ.get('G1_INPUT','/mnt/data/DATE_G1_RELATION_CLOSURE_20260918/input_archives'))/'source_same_ir.zip')
A=Z.read('new/bench/formal/source_ir.smt2');B=Z.read('patched/bench/formal/source_ir.smt2'); a=A.decode();b=B.decode()
ra=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',a,re.M));rb=dict(re.findall(r'^; yosys-smt2-register (.+?) (\d+)$',b,re.M));core=sorted(x for x in ra.keys()&rb.keys() if not x.startswith('$') and not x.startswith('fwb.$') and ra[x]==rb[x]);assert len(core)==20
ia=dict(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',a,re.M));ib=dict(re.findall(r'^; yosys-smt2-input (.+?) (\d+)$',b,re.M));assert ia==ib and len(ia)==12
outputs=dict(re.findall(r'^; yosys-smt2-output (.+?) (\d+)$',a,re.M));assert outputs==dict(re.findall(r'^; yosys-smt2-output (.+?) (\d+)$',b,re.M)) and len(outputs)==14
new=Z.read('new/rtl/core/pipemem.v').decode();pat=Z.read('patched/rtl/core/pipemem.v').decode()
word='(i_addr[(AW+1):2] == o_wb_addr)';word2='(i_addr[(AW+1):2] == o_wb_addr+1)';full='(i_addr == o_wb_addr)';full2='(i_addr == o_wb_addr+1)'
assert new.count(word)==1 and new.count(word2)==1 and pat==new.replace(word,full).replace(word2,full2)
assert Z.read('new/rtl/ex/fwb_master.v')==Z.read('patched/rtl/ex/fwb_master.v')
H=a.replace('pipemem','new_pipemem')+'\n'+b.replace('pipemem','patched_pipemem')+'\n';manifest={'source':'source_same_ir.zip','new_sha256':hashlib.sha256(A).hexdigest(),'patched_sha256':hashlib.sha256(B).hexdigest(),'new_rtl_sha256':hashlib.sha256(new.encode()).hexdigest(),'patched_rtl_sha256':hashlib.sha256(pat.encode()).hexdigest(),'source_change':'one old-address assumption expression restored, exactly two compare occurrences','checks':[]}
def eq(x,k,typ='n'):return f'(= (|new_pipemem_{typ} {x}| n{k}) (|patched_pipemem_{typ} {x}| p{k}))'
def write(name,t,expected):
 p=D/(name+'.smt2');p.write_text(H+t+'(check-sat)\n');manifest['checks'].append({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'expected':expected,'bytes':p.stat().st_size})
st='(declare-fun n0 () |new_pipemem_s|)\n(declare-fun p0 () |patched_pipemem_s|)\n'
base=st+''.join(f'(assert {q})\n' for q in [eq(x,0) for x in core]+[eq('fifo_oreg',0,'m')]+[eq(x,0) for x in ia])
write('counterfactual_R_nonvacuous',base,'sat')
write('counterfactual_B_14',base+'(assert (or '+' '.join('(not '+eq(x,0)+')' for x in outputs)+'))\n','unsat')
step=base+'(declare-fun n1 () |new_pipemem_s|)\n(declare-fun p1 () |patched_pipemem_s|)\n'+''.join(f'(assert {eq(x,1)})\n' for x in ia)+'(assert (|new_pipemem_t| n0 n1))\n(assert (|patched_pipemem_t| p0 p1))\n'
write('counterfactual_step_nonvacuous',step,'sat')
write('counterfactual_R_inductive',step+'(assert (or '+' '.join('(not '+q+')' for q in [eq(x,1) for x in core]+[eq('fifo_oreg',1,'m')])+'))\n','unsat')
write('counterfactual_assumption_difference_possible',base+'(assert (not (= (|new_pipemem_u| n0) (|patched_pipemem_u| p0))))\n','sat')
(D/'COUNTERFACTUAL_MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n');print('BUILT',len(manifest['checks']),'queries',flush=True)
