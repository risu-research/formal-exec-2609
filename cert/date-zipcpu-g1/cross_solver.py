"""Fail-closed replay of source-generated full-F queries. No original source mutation in this checker."""
import sys,json,hashlib,subprocess,pathlib,time,re
from independent_ir import IR
ROOT=pathlib.Path('g1');OUT=pathlib.Path('g1-cross');OUT.mkdir(exist_ok=True)
PIN={'old':'6514165245434d493f5ba54a045b5d3ef8cb111152e499fab15609ecf54705bd','new':'4bb3f46644df37123677cd1c57e78506a87285bbf789be754c27a4e9e12d667e','patched':'6ba92a65e2a4da6c10b4467893e4e20cdb544dde68abfe414764412e46ed459b'}
QPIN={'old_new_loss':'c7b529d608dfcc27f73d387a26ccc12417c06a1598b90297e8db8ce8f86d3264','old_new_gain':'a77abeecc28a5a8db8d8088f1da4e81d8ec14485bd19d888c8f4850faaf9e512','old_patch_loss':'bae8412ba644717fb3b7098887a61730d4f9db332f1d34bd00416aa5d2e1d0a0','old_patch_gain':'ddb5cfe4d7a534b2dcd766b7193fe9e39524abe97015193cdd8ca94d86c1bcad'}
sources=ROOT/'sources';new=(sources/'new.v').read_bytes();patch=(sources/'patched.v').read_bytes()
old_clause=b'`ASSUME( (i_addr == o_wb_addr)\n\t\t\t\t||(i_addr == o_wb_addr+1));'
new_clause=b'`ASSUME( (i_addr[(AW+1):2] == o_wb_addr)\n\t\t\t\t||(i_addr[(AW+1):2] == o_wb_addr+1));'
assert new.count(new_clause)==1 and patch==new.replace(new_clause,old_clause),'unexpected changed RTL outside exactly one allowed source assumption'
assert (sources/'old.pipemem.ys').read_bytes()==(sources/'new.pipemem.ys').read_bytes()
assert (sources/'old.fwb_master.v').read_bytes()==(sources/'new.fwb_master.v').read_bytes()
objs={}
for side,expected in PIN.items():
 path=ROOT/'ir'/f'{side}.json';got=hashlib.sha256(path.read_bytes()).hexdigest()
 assert got==expected,(side,got,expected)
 obj=IR(path).build();objs[side]=obj
 assert len(obj['A'])==(26 if side=='old' else 25) and len(obj['G'])==(30 if side=='old' else 35)
 (OUT/f'{side}.metadata.json').write_text(json.dumps({'ir_sha256':got,'A':len(obj['A']),'G':len(obj['G']),'public':len(obj['public']),'private':len(obj['private'])},indent=2)+'\n')
a,b=objs['new'],objs['patched']
assert all(x['formula']==y['formula'] for x,y in zip(a['G'],b['G']))
assert all(x['formula']==y['formula'] for i,(x,y) in enumerate(zip(a['A'],b['A'])) if i!=4)
assert a['A'][4]['formula']!=b['A'][4]['formula']
(OUT/'structural.txt').write_text('ONE_SOURCE_EDIT_24_ASSUMPTIONS_AND_35_ASSERTIONS_IDENTICAL_PASS\n')
qs=[('old_new_loss','old','new','sat'),('old_new_gain','new','old','sat'),('old_patch_loss','old','patched','unsat'),('old_patch_gain','patched','old','sat')]
results=[]
for name,left,right,expected in qs:
 a,b=objs[left],objs[right];symbols=sorted(set(a['public'])|set(b['public']))
 decl='\n'.join(f'(declare-fun {s} () (_ BitVec 1))' for s in symbols)+'\n'
 script=decl+f'(assert (and {a["challenge"]} (not {b["challenge"]})))\n(check-sat)\n'
 target=OUT/f'{name}.smt2';target.write_text(script)
 digest=hashlib.sha256(script.encode()).hexdigest()
 assert digest==QPIN[name],('extracted query differs from independently reconstructed and pinned source query',name,digest)
 entry={'query':name,'sha256':digest,'expected':expected,'tool_answers':{}}
 for solver,cmd in [('z3',['z3','-smt2',str(target)]),('cvc5',['cvc5','--lang','smt2',str(target)])]:
  tic=time.monotonic()
  try:
   res=subprocess.run(cmd,text=True,capture_output=True,timeout=60)
   stdout,stderr,rc=res.stdout,res.stderr,res.returncode
   answers=re.findall(r'(?m)^(sat|unsat|unknown)\s*$',stdout)
   answer=answers[-1] if len(answers)==1 else 'NO_SINGLE_ANSWER'
  except subprocess.TimeoutExpired as e:
   stdout=str(e.stdout);stderr=str(e.stderr);rc='timeout';answer='TIMEOUT'
  (OUT/f'{name}.{solver}.stdout').write_text(stdout)
  (OUT/f'{name}.{solver}.stderr').write_text(stderr)
  entry['tool_answers'][solver]={'answer':answer,'returncode':rc,'seconds':round(time.monotonic()-tic,3)}
  print(name,solver,answer,'expected',expected,'rc',rc,flush=True)
 results.append(entry)
 (OUT/'RESULTS.json').write_text(json.dumps(results,indent=2)+'\n')
assert all(x['tool_answers'].get(s,{}).get('answer')==x['expected'] for x in results for s in ('z3','cvc5')),'CROSS_SOLVER_DIVERGENCE_OR_FAILURE'
print('G1_SOURCE_REPLAY_Z3_CVC5_WHOLE_F_FOUR_DIRECTION_PASS',flush=True)
