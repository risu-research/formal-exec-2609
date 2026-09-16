#!/usr/bin/env python3
"""E5 S1 V3: incremental native-SMT reachability from authenticated V2 monitor-free models.

This is an efficiency rewrite of V2's predeclared output-only, helper-constrained,
post-hoc observed-cycle, and contradiction controls. No source instrumentation,
new behavior target, or historical-property-failure claim. Unlike the V2 monolithic
query, do not demand an irrelevant successor of the last tested state.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else Path('v3')
HORIZON=40
ARMS=('E_removed','E_restored')
ORIGINAL='d511239e19be8fcc7f340a64554ea93699637e62'
SOURCES={'E_removed':'7238c0ff1c735c8dd467ec0ba9c3acc6a91ddd7bd1cc8268a3d4cffca0d61abc','E_restored':'1cf80859d6c885ca20eeab9bec9364c2f8d3416d4d06d6bd9946da0712307c76'}
MODELS={'E_removed':'d9995945ae635a579032c469cdb1b5e0df0e20567de8cb204525f753240462be','E_restored':'bb2c006e8cd6a81aad9c96e9961e704f1579f1d8e03a91b308b60c0d3eb8ac10'}
COMMENTED='//always @(posedge i_clk)\n//\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n//\t\t`ASSUME(!i_lock);\n'
RESTORED='\talways @(posedge i_clk)\n\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n\t\t`ASSUME(!i_lock);\n'
PINS=('i_lock','o_wb_stb_gbl','o_wb_stb_lcl','o_wb_cyc_gbl','o_wb_cyc_lcl','o_busy')
HELPERS=('f_cyc','f_outstanding')
def sha(b):return hashlib.sha256(b).hexdigest()
def v(s,n):return f'(|pipemem_n {n}| {s})'
def not_(e):return f'(not {e})'
def output_event(s):
 return f'(and {not_(v(s,"o_busy"))} {not_(v(s,"o_wb_stb_gbl"))} {not_(v(s,"o_wb_stb_lcl"))} (or {v(s,"o_wb_cyc_gbl")} {v(s,"o_wb_cyc_lcl")}))'
def helper_event(s):
 return f'(and {output_event(s)} (= {v(s,"f_outstanding")} #b00000) {not_(v(s,"f_cyc"))})'
def declarations(i):
 s=f'|q{i}|'
 out=[f'(declare-fun {s} () |pipemem_s|)',f'(assert (|pipemem_h| {s}))',f'(assert (|pipemem_u| {s}))']
 if i==0:out += [f'(assert (|pipemem_is| {s}))',f'(assert (|pipemem_i| {s}))']
 else:out.append(f'(assert (|pipemem_t| |q{i-1}| {s}))')
 return out
def mode_query(mode,s):
 if mode=='pin_any':return helper_event(s)
 if mode=='output_only_any':return output_event(s)
 if mode=='observed_cycle':
  return '(and '+' '.join((v('|q2|','f_cyc'),not_(v('|q2|','i_lock')),v('|q3|','f_cyc'),v('|q3|','i_lock'),helper_event('|q4|'),not_(v('|q5|','o_wb_cyc_gbl'))))+')'
 if mode=='negative_control':return '(and '+output_event('|q4|')+' '+not_(v('|q4|','o_wb_cyc_gbl'))+' '+v('|q4|','o_wb_cyc_gbl')+')'
 raise ValueError(mode)
def make_query(model,mode):
 # q0..q39 for 40-state bound; q0..q5 for the post-hoc six-state trace.
 n=HORIZON if mode in ('pin_any','output_only_any') else (6 if mode=='observed_cycle' else 5)
 lines=['(set-logic ALL)','(set-option :produce-models false)',model]
 for i in range(n):
  lines.extend(declarations(i))
  if mode in ('pin_any','output_only_any'):
   lines+=['(push 1)','(assert '+mode_query(mode,f'|q{i}|')+')','(check-sat)','(pop 1)']
 if mode not in ('pin_any','output_only_any'):
  lines+=['(assert '+mode_query(mode,'|q4|')+')','(check-sat)']
 lines+=['(exit)']
 return '\n'.join(lines)+'\n',n if mode in ('pin_any','output_only_any') else 1

def main():
 assert (ROOT/'SHA256SUMS').is_file(), 'Missing prior SHA256SUMS'
 old=(ROOT/'E_removed/pipemem.v').read_bytes();new=(ROOT/'E_restored/pipemem.v').read_bytes()
 assert sha(old)==SOURCES['E_removed'] and sha(new)==SOURCES['E_restored']
 assert old.decode().count(COMMENTED)==new.decode().count(RESTORED)==1
 assert old.decode().replace(COMMENTED,RESTORED,1)==new.decode(), 'Both versions must differ ONLY by assumption'
 assert (ROOT/'E_removed/actual-task.ys').read_bytes()==(ROOT/'E_restored/actual-task.ys').read_bytes()
 manifest=json.loads((ROOT/'query-manifest.json').read_text())
 assert manifest['schema']=='e5-s1-monitor-free-query-v2' and manifest['horizon']==HORIZON
 result={'schema':'e5-s1-incremental-monitor-free-v3','prior_run':35122112448,'prior_artifact_id':10457767655,'prior_digest':'sha256:9662c2a052d58312cca8d6906a36ca5aa9b2554b6e345344da6349ae60e0674f','source_commit':ORIGINAL,'horizon_states':HORIZON,'models':{}}
 for arm in ARMS:
  path=ROOT/arm;data=(path/'pipemem.smt2').read_bytes()
  assert sha(data)==MODELS[arm] and manifest['cases'][arm]['model_sha256']==MODELS[arm]
  model=data.decode()
  assert 'yosys-smt2-cover ' not in model and 'f_s1_onset' not in model and 'f_s1_endpoint' not in model
  for stem in ('pipemem_i','pipemem_is','pipemem_t','pipemem_u','pipemem_h'):
   assert '|'+stem+'|' in model,stem
  for name in PINS+HELPERS:assert '|pipemem_n '+name+'|' in model,name
  modes={}
  for mode in ('pin_any','output_only_any','observed_cycle','negative_control'):
   smt,n=make_query(model,mode)
   assert '(get-value' not in smt and '(assert (not (|pipemem_is| |q1|)))' not in smt
   p=ROOT/'incremental'/f'{arm}_{mode}.smt2';p.parent.mkdir(exist_ok=True)
   p.write_text(smt)
   modes[mode]={'sha256':sha(smt.encode()),'expected_checks':n,'posthoc':mode=='observed_cycle'}
  result['models'][arm]={'source_sha256':sha((path/'pipemem.v').read_bytes()),'model_sha256':sha(data),'modes':modes}
 (ROOT/'incremental/QUERY_MANIFEST.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
 print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
