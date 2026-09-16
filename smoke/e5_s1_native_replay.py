#!/usr/bin/env python3
"""Project a frozen monitor-free SAT witness into unmodified RTL simulation.

Evidence only. Use first SAT model; never search alternate models or repair
stimulus after observing native outputs. Native RTL has no FORMAL or monitors.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

assert len(sys.argv)==3, 'usage: python3 e5_s1_native_replay.py EVIDENCE_DIR OUTPUT_DIR'
root,out=map(Path,sys.argv[1:]);out.mkdir(parents=True,exist_ok=True)
manifest=json.loads((root/'incremental/QUERY_MANIFEST.json').read_text())
assert manifest['schema']=='e5-s1-incremental-monitor-free-v3'
assert manifest['prior_artifact_id']==10457767655
assert manifest['prior_digest']=='sha256:9662c2a052d58312cca8d6906a36ca5aa9b2554b6e345344da6349ae60e0674f'
assert manifest['source_commit']=='d511239e19be8fcc7f340a64554ea93699637e62'
for arm in ('E_removed','E_restored'):
    assert hashlib.sha256((root/arm/'pipemem.v').read_bytes()).hexdigest()==manifest['models'][arm]['source_sha256']
    assert hashlib.sha256((root/arm/'pipemem.smt2').read_bytes()).hexdigest()==manifest['models'][arm]['model_sha256']
src=root/'incremental/E_removed_observed_cycle.smt2'
text=src.read_text()
assert hashlib.sha256(src.read_bytes()).hexdigest()==manifest['models']['E_removed']['modes']['observed_cycle']['sha256']
assert text.count('(check-sat)')==1 and text.rstrip().endswith('(exit)')
assert text.count('(set-option :produce-models false)')==1
inputs={'i_clk':1,'i_reset':1,'i_pipe_stb':1,'i_lock':1,'i_op':3,'i_addr':32,'i_data':32,'i_oreg':5,'i_wb_ack':1,'i_wb_stall':1,'i_wb_err':1,'i_wb_data':32}
outputs={'o_busy':1,'o_wb_stb_gbl':1,'o_wb_stb_lcl':1,'o_wb_cyc_gbl':1,'o_wb_cyc_lcl':1,'f_cyc':1,'f_outstanding':5}
terms=[f'(|pipemem_n {name}| |q{i}|)' for i in range(6) for name in (*inputs,*outputs)]
query=text.replace('(set-option :produce-models false)','(set-option :produce-models true)')
query=query[:query.rfind('(exit)')]+'(get-value (\n'+'\n'.join(' '+t for t in terms)+'\n))\n(exit)\n'
qfile=out/'observed_cycle_witness_query.smt2';qfile.write_text(query)
p=subprocess.run(['z3','-smt2',str(qfile)],capture_output=True,text=True,timeout=120)
(out/'z3_witness_stdout.log').write_text(p.stdout);(out/'z3_witness_stderr.log').write_text(p.stderr)
if p.returncode or not p.stdout.startswith('sat\n') or '(error ' in p.stdout:
    raise RuntimeError('witness model extraction FAILED; preserve logs, no alternate target or model')
values={}
pat=re.compile(r'\(\(\|pipemem_n ([a-zA-Z_0-9]+)\| \|q([0-5])\|\)\s+(true|false|#b[01]+|#x[0-9a-fA-F]+|\(_ bv\d+ \d+\))\s*\)')
for m in pat.finditer(p.stdout):
    name,qi,raw=m.groups();i=int(qi)
    if raw in ('true','false'):v=int(raw=='true')
    elif raw.startswith('#b'):v=int(raw[2:],2)
    elif raw.startswith('#x'):v=int(raw[2:],16)
    else:v=int(re.match(r'\(_ bv(\d+)',raw).group(1))
    assert (i,name) not in values
    values[i,name]=v
if set(values)!=set((i,name) for i in range(6) for name in (*inputs,*outputs)):
    missing=set((i,name) for i in range(6) for name in (*inputs,*outputs))-set(values)
    raise RuntimeError(f'noncomplete model: {missing}')
for (i,name),v in values.items():
    width=inputs.get(name,outputs.get(name));assert width is not None and 0<=v<2**width
vectors=[{'state':i,'inputs':{k:values[i,k] for k in inputs},'expected':{k:values[i,k] for k in outputs}} for i in range(6)]
(out/'witness_vectors.json').write_text(json.dumps(vectors,indent=2,sort_keys=True)+'\n')
removed=(root/'E_removed/pipemem.v').read_text();restored=(root/'E_restored/pipemem.v').read_text()
a='\talways @(posedge i_clk)\n\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n\t\t`ASSUME(!i_lock);'
b='//always @(posedge i_clk)\n//\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n//\t\t`ASSUME(!i_lock);'
assert restored.count(a)==1 and restored.replace(a,b)==removed,'source arm delta exceeds frozen formal assumption'
tb=['`timescale 1ns/1ps','module tb;','reg i_clk=0;']
for name,width in inputs.items():
    if name!='i_clk':tb.append('reg '+(f'[{width-1}:0] ' if width>1 else '')+name+';')
for name in outputs:
    if name not in ('f_cyc','f_outstanding'):tb.append('wire '+name+';')
ports=['i_clk',*[k for k in inputs if k!='i_clk'],*[k for k in outputs if k not in ('f_cyc','f_outstanding')]]
tb.append('pipemem dut('+', '.join(f'.{k}({k})' for k in ports)+');')
tb.append('initial begin')
def assignments(state):return [f"  {n} = {w}'d{values[state,n]};" for n,w in inputs.items() if n!='i_clk']
def sample(i):return '  $display("TRACE %0d %b %b %b %b %b %b", '+str(i)+', o_busy, o_wb_stb_gbl, o_wb_stb_lcl, o_wb_cyc_gbl, o_wb_cyc_lcl, dut.cyc);'
tb+=assignments(0);tb+=['  #2;',sample(0)]
for i in range(1,6):
    tb+=['  #2; i_clk = 1;','  #1; i_clk = 0;'];tb+=assignments(i);tb+=['  #2;',sample(i)]
tb+=['  $finish;','end','endmodule']
(out/'native_replay_tb.sv').write_text('\n'.join(tb)+'\n')
ver=subprocess.run(['iverilog','-V'],capture_output=True,text=True,timeout=20)
(out/'iverilog_version.txt').write_text(ver.stdout+'\n'+ver.stderr)
if ver.returncode:raise RuntimeError('iverilog unavailable')
compile=subprocess.run(['iverilog','-g2012','-s','tb','-o',str(out/'native_replay.vvp'),str(root/'E_removed/pipemem.v'),str(out/'native_replay_tb.sv')],capture_output=True,text=True,timeout=60)
(out/'compile.log').write_text(compile.stdout+'\n'+compile.stderr)
if compile.returncode:raise RuntimeError('native simulator COMPILE_RED; preserve evidence')
sim=subprocess.run(['vvp',str(out/'native_replay.vvp')],capture_output=True,text=True,timeout=40)
(out/'simulation.log').write_text(sim.stdout+'\n'+sim.stderr)
if sim.returncode:raise RuntimeError('native simulator RUN_RED; preserve evidence')
observed={}
for line in sim.stdout.splitlines():
    if not line.startswith('TRACE '):continue
    toks=line.split();assert len(toks)==8
    idx=int(toks[1]);assert 0<=idx<6 and idx not in observed
    if not all(t in ('0','1') for t in toks[2:]):raise RuntimeError('X or Z in native output; preserve evidence')
    observed[idx]=dict(zip(['o_busy','o_wb_stb_gbl','o_wb_stb_lcl','o_wb_cyc_gbl','o_wb_cyc_lcl','f_cyc'],map(int,toks[2:])))
comparisons=[]
for i in range(6):
    for name,value in observed.get(i,{}).items():comparisons.append({'q':i,'port':name,'rtl':value,'smt':values[i,name],'match':value==values[i,name]})
all_matches=len(observed)==6 and len(comparisons)==36 and all(x['match'] for x in comparisons)
first_rejected=[i for i in range(1,6) if observed.get(i-1,{}).get('f_cyc')==1 and values[i-1,'i_lock']==0 and values[i,'i_lock']==1]
native_target=(len(observed)==6 and observed[2]['f_cyc']==1 and values[2,'i_lock']==0 and observed[3]['f_cyc']==1 and values[3,'i_lock']==1 and observed[4]['o_busy']==0 and observed[4]['o_wb_stb_gbl']==0 and observed[4]['o_wb_stb_lcl']==0 and (observed[4]['o_wb_cyc_gbl'] or observed[4]['o_wb_cyc_lcl']) and observed[4]['f_cyc']==0 and observed[5]['o_wb_cyc_gbl']==0)
result={'schema':'e5-s1-native-rtl-witness-replay-v1','formal_source_commit':manifest['source_commit'],'original_artifact':manifest['prior_artifact_id'],'original_digest':manifest['prior_digest'],'witness_query_sha256':hashlib.sha256(query.encode()).hexdigest(),'witness_vector_sha256':hashlib.sha256((out/'witness_vectors.json').read_bytes()).hexdigest(),'native_outputs_match_smt':all_matches,'native_observed_cycle_target':native_target,'first_rejected_formal_assumption_cycle':first_rejected[0] if first_rejected else None,'comparisons':comparisons,'limitations':['single post-hoc SMT witness','no native helper f_outstanding assertion','no original-property violation established','no alternative witness if native mismatch','Yosys state sampling alignment is an explicit tested convention']}
(out/'REPLAY_VERDICT.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:result[k] for k in ('native_outputs_match_smt','native_observed_cycle_target','first_rejected_formal_assumption_cycle')},sort_keys=True),flush=True)
(out/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(q.read_bytes()).hexdigest()}  {q.name}\n' for q in sorted(out.iterdir()) if q.is_file() and q.name!='SHA256SUMS'))
if not(all_matches and native_target and first_rejected):sys.exit(2)
