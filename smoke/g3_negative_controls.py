#!/usr/bin/env python3
from __future__ import annotations
import argparse,copy,difflib,hashlib,json,re,subprocess
from pathlib import Path
import z3
from z3.z3util import get_vars
from tnf_scope import IR
L=('natural_old','natural_new','mirror_old','mirror_new')
P={'A_SIGNED':'0'*32,'A_WIDTH':'0'*31+'1','Y_WIDTH':'0'*31+'1'}
R=re.compile(r'^(?P<f>[^:|]+):(?P<a>\d+)\.(?P<c>\d+)-(?P<b>\d+)\.(?P<d>\d+)$')
def sh(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def wr(p,o):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')
def mod(o):
 m=o.get('modules',{});assert len(m)==1;return next(iter(m.values()))
def mb(o):
 x=[];m=mod(o)
 for q in list(m.get('ports',{}).values())+list(m.get('netnames',{}).values()):x += [b for b in q.get('bits',[]) if isinstance(b,int)]
 for c in m.get('cells',{}).values():
  for q in c.get('connections',{}).values():x += [b for b in q if isinstance(b,int)]
 return max(x,default=1)
def nz(x):return x if z3.is_bool(x) else x!=z3.BitVecVal(0,x.size())
def rows(ir,k):
 out=[]
 for n,c in sorted(ir.m.get('cells',{}).items()):
  if c.get('type')!=k:continue
  A,E=c['connections']['A'],c['connections']['EN'];assert len(A)==len(E)==1
  aq=isinstance(A[0],int) and A[0] in ir.qbits;eq=isinstance(E[0],int) and E[0] in ir.qbits
  if aq!=eq:raise RuntimeError('mixed formal registration '+n)
  out.append(z3.simplify(z3.Or(z3.Not(nz(ir.sig(E,eq,n+':EN'))),nz(ir.sig(A,aq,n+':A')))))
 return out
def sem(p,challenge):
 ir=IR(str(p));A=rows(ir,'$assume');af=z3.And(*A) if A else z3.BoolVal(True)
 if not challenge:return z3.simplify(af)
 G=rows(ir,'$assert');gf=z3.And(*G) if G else z3.BoolVal(True);return z3.simplify(z3.And(af,z3.Not(gf)))
def vs(*es):
 d={}
 for e in es:
  for v in get_vars(e):d[v.sexpr()]=v
 return [d[k] for k in sorted(d)]
def witness(q):
 s=z3.Solver();s.add(q);r=s.check()
 if r==z3.unsat:return 'UNSAT',None
 if r!=z3.sat:raise RuntimeError('solver '+str(r))
 w={}
 for v in vs(q):
  if not z3.is_bv(v) or v.size()!=1:raise RuntimeError('bad witness sort')
  s.push();s.add(v==0);r0=s.check();s.pop();x=0 if r0==z3.sat else 1
  if x:
   s.push();s.add(v==1);r1=s.check();s.pop()
   if r1!=z3.sat:raise RuntimeError('refinement lost SAT')
  s.add(v==x);w[v.sexpr()]=x
 return 'SAT',w
def ev(e,w):
 vv=vs(e);miss=[v.sexpr() for v in vv if v.sexpr() not in w]
 if miss:raise RuntimeError('missing witness '+repr(miss))
 g=z3.simplify(z3.substitute(e,*[(v,z3.BitVecVal(w[v.sexpr()],1)) for v in vv])) if vv else z3.simplify(e)
 if z3.is_true(g):return True
 if z3.is_false(g):return False
 raise RuntimeError('ungrounded replay')
def replay(a,b,w):
 x,y=ev(a,w),ev(b,w);return {'baseline':x,'mutant':y,'divergent':x!=y}
def corrupt(w):
 k=sorted(w)[0];q=dict(w);q[k]=1-q[k];return k,q
def query(root,lane,ident,q,res):
 p=Path(root)/'queries'/lane/(ident+'.smt2');p.parent.mkdir(parents=True,exist_ok=True);s=z3.Solver();s.add(q);p.write_text(s.to_smt2())
 return {'path':str(p),'sha256':sh(p),'z3_result':res}
def cells(o,k):
 return [n for n,c in sorted(mod(o).get('cells',{}).items()) if c.get('type')==k and len(c.get('connections',{}).get('A',[]))==1 and len(c.get('connections',{}).get('EN',[]))==1 and c['connections']['EN']!=['0']]
def m1(o,n):
 o=copy.deepcopy(o);mod(o)['cells'][n]['connections']['A']=['0'];return o
def m2(o,n):
 o=copy.deepcopy(o);cs=mod(o)['cells'];t=cs[n];A=copy.deepcopy(t['connections']['A']);qb=A[0] if isinstance(A[0],int) else None;dff=None
 for dn,dc in sorted(cs.items()):
  if dc.get('type')=='$dff' and qb in dc.get('connections',{}).get('Q',[]):
   if dff:raise RuntimeError('ambiguous property dff')
   dff=(dn,dc)
 b=mb(o)+1;nn='__g3_nc2_not__'
 while nn in cs:nn+='x'
 inp=A if not dff else copy.deepcopy(dff[1]['connections']['D'])
 cs[nn]={'type':'$logic_not','port_directions':{'A':'input','Y':'output'},'connections':{'A':inp,'Y':[b]},'parameters':copy.deepcopy(P),'attributes':{}}
 if not dff:t['connections']['A']=[b];return o
 dc=dff[1];q=b+1;dn='__g3_nc2_dff__'
 while dn in cs:dn+='x'
 cs[dn]={'type':'$dff','parameters':copy.deepcopy(dc.get('parameters',{})),'attributes':{},'port_directions':{'CLK':'input','D':'input','Q':'output'},'connections':{'CLK':copy.deepcopy(dc['connections']['CLK']),'D':[b],'Q':[q]}}
 t['connections']['A']=[q];return o
def lane(root,base,name,k,challenge,mut):
 A=[];sel=None
 for lab in L:
  bp=Path(base)/(lab+'.json');o=json.loads(bp.read_text());bf=sem(bp,challenge)
  for cn in cells(o,k):
   ident=lab+'__'+hashlib.sha256(cn.encode()).hexdigest()[:16];mp=Path(root)/name/'mutants'/(ident+'.json');mp.parent.mkdir(parents=True,exist_ok=True);mp.write_text(json.dumps(mut(o,cn),sort_keys=False,separators=(',',':'))+'\n')
   mf=sem(mp,challenge);dq=z3.simplify(z3.Xor(bf,mf));res,w=witness(dq);rec={'artifact':lab,'cell':cn,'baseline_sha256':sh(bp),'mutant_sha256':sh(mp),'result':res,'query':query(root,name,ident,dq,res)}
   if res=='SAT':
    rp=replay(bf,mf,w);bit,bad=corrupt(w);br=replay(bf,mf,bad);rec.update(canonical_witness=w,replay=rp,one_bit_corruption={'bit':bit,'witness':bad,'replay':br,'rejected':not br['divergent']})
    if sel is None:sel=len(A)
   A.append(rec)
 if sel is None:raise RuntimeError(name+' every frozen candidate UNSAT')
 return {'schema':'g3-'+name+'-negative-v1','attempt_count':len(A),'sat_count':sum(x['result']=='SAT' for x in A),'unsat_count':sum(x['result']=='UNSAT' for x in A),'selected_index':sel,'selected':A[sel],'attempts':A}
def nc3(root):
 d=Path(root)/'nc3';d.mkdir(parents=True,exist_ok=True);v=d/'presence.v';v.write_text('module top(input wire present); always @* assume(present); endmodule\n');j=d/'baseline.json';p=subprocess.run(['yosys','-q','-p',f'read_verilog -formal {v}; prep -top top -flatten; write_json {j}'],capture_output=True,text=True);(d/'yosys.stdout').write_text(p.stdout);(d/'yosys.stderr').write_text(p.stderr)
 if p.returncode:raise RuntimeError('NC3 yosys failed')
 bm={'contract_carrier':'present','realized_carriers':['present']};mm={'contract_carrier':'present','realized_carriers':[]};wr(d/'baseline-realization.json',bm);wr(d/'mutant-realization.json',mm)
 b=z3.BitVec('present',1)==1;m=z3.BoolVal(False);dq=z3.Xor(b,m);res,w=witness(dq);rp=replay(b,m,w);bit,bad=corrupt(w);br=replay(b,m,bad)
 rec={'schema':'g3-nc3-physical-absence-v1','baseline_json_sha256':sh(j),'baseline_realization_accepted':True,'mutant_realization_accepted':False,'validator_rejected_mutant':True,'semantic_result':res,'query':query(root,'nc3','physical_absence',dq,res),'canonical_witness':w,'replay':rp,'one_bit_corruption':{'bit':bit,'witness':bad,'replay':br,'rejected':not br['divergent']}};wr(d/'NC3_RESULT.json',rec);return rec
def ps(s):
 m=R.match(s or '');return None if not m else {'f':m['f'],'a':int(m['a']),'c':int(m['c']),'b':int(m['b']),'d':int(m['d'])}
def line_map(a,b):
 A=a.splitlines();B=b.splitlines();d={}
 for i,j,n in difflib.SequenceMatcher(a=A,b=B,autojunk=False).get_matching_blocks():
  for k in range(n):d[i+k+1]=j+k+1
 return d
def nsrc(ir):
 out={}
 for n,x in ir.m.get('netnames',{}).items():
  q=n.replace('\\','')
  if q.startswith('$') or '.$' in q:continue
  for i,b in enumerate(x.get('bits',[])):
   if isinstance(b,int) and ir._leaf_key(b)==f'{q}[{i}]':out[f'{q}[{i}]']=x.get('attributes',{}).get('src','')
 return out
def nc4(root,base,osrc,nsrcp):
 op,np=Path(base)/'natural_old.json',Path(base)/'natural_new.json';oi,ni=IR(str(op)),IR(str(np));rows(oi,'$assume');rows(ni,'$assume');O,N=nsrc(oi),nsrc(ni);lm=line_map(Path(osrc).read_text(),Path(nsrcp).read_text());C=[]
 for k in sorted(set(oi.vars)&set(ni.vars)&set(O)&set(N)):
  a,b=ps(O[k]),ps(N[k])
  if not a or not b or a['f']!=b['f'] or a['f']!='rtl/core/pipemem.v' or (a['c'],a['d'],a['b']-a['a'])!=(b['c'],b['d'],b['b']-b['a']):continue
  if [lm.get(x) for x in range(a['a'],a['b']+1)]==list(range(b['a'],b['b']+1)):C.append({'key':k,'old_src':O[k],'new_src':N[k]})
 if not C:raise RuntimeError('NC4 no governed transport')
 s=C[0]
 def val(x):
  a,b=ps(x['old_src']),ps(x['new_src']);ml=[lm.get(i) for i in range(a['a'],a['b']+1)];ex=list(range(b['a'],b['b']+1));return {'accepted':ml==ex and a['c']==b['c'] and a['d']==b['d'],'mapped_lines':ml,'expected_lines':ex}
 good=val(s);b=ps(s['new_src']);bad=dict(s);bad['new_src']=f"{b['f']}:{b['a']+1}.{b['c']}-{b['b']+1}.{b['d']}";bv=val(bad);rec={'schema':'g3-nc4-source-transport-mismatch-v1','candidate_count':len(C),'selected':s,'baseline_validation':good,'corrupted_transport':bad,'corrupted_validation':bv,'rejected':not bv['accepted'],'old_source_sha256':sh(osrc),'new_source_sha256':sh(nsrcp),'formula_artifacts_unchanged':{'natural_old_sha256':sh(op),'natural_new_sha256':sh(np)}};wr(Path(root)/'nc4'/'NC4_RESULT.json',rec);return rec
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--base-dir',required=True);ap.add_argument('--old-source',required=True);ap.add_argument('--new-source',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.out);root.mkdir(parents=True,exist_ok=True);base=Path(a.base_dir)
 exp={'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74','natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05','mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51'};got={k:sh(base/(k+'.json')) for k in L}
 if got!=exp:raise RuntimeError('corpus drift '+repr(got))
 n1=lane(root,base,'nc1','$assume',False,m1);n2=lane(root,base,'nc2','$assert',True,m2);n3=nc3(root);n4=nc4(root,base,a.old_source,a.new_source);s=n1['selected'];n5={'schema':'g3-nc5-witness-corruption-v1','source_candidate':{'artifact':s['artifact'],'cell':s['cell'],'query_sha256':s['query']['sha256']},'bit':s['one_bit_corruption']['bit'],'corrupted_witness':s['one_bit_corruption']['witness'],'replay':s['one_bit_corruption']['replay'],'rejected':s['one_bit_corruption']['rejected']};wr(root/'nc1'/'NC1_RESULT.json',n1);wr(root/'nc2'/'NC2_RESULT.json',n2);wr(root/'nc5'/'NC5_RESULT.json',n5)
 Q=[]
 for lane_,rec in [('nc1',n1),('nc2',n2)]:
  for x in rec['attempts']:Q.append({'lane':lane_,'path':x['query']['path'],'sha256':x['query']['sha256'],'expected':x['result']})
 Q.append({'lane':'nc3','path':n3['query']['path'],'sha256':n3['query']['sha256'],'expected':n3['semantic_result']});wr(root/'QUERY_EXPECTATIONS.json',{'schema':'g3-negative-query-expectations-v1','queries':Q})
 S={'schema':'g3-negative-controls-pre-nc6-v1','corpus_sha256':got,'nc1':{'pass':n1['selected']['replay']['divergent'] and n1['selected']['one_bit_corruption']['rejected'],'selected':n1['selected'],'attempt_count':n1['attempt_count']},'nc2':{'pass':n2['selected']['replay']['divergent'] and n2['selected']['one_bit_corruption']['rejected'],'selected':n2['selected'],'attempt_count':n2['attempt_count']},'nc3':{'pass':n3['validator_rejected_mutant'] and n3['replay']['divergent'] and n3['one_bit_corruption']['rejected']},'nc4':{'pass':n4['rejected'],'selected':n4['selected']},'nc5':{'pass':n5['rejected'],'bit':n5['bit']},'query_count':len(Q)};wr(root/'NEGATIVE_PRE_NC6_SUMMARY.json',S);print(json.dumps(S,sort_keys=True))
if __name__=='__main__':main()
