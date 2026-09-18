#!/usr/bin/env python3
"""Re-elaborated source counterfactual. Native 2-state symbolic F, not RTL failure."""
import argparse,hashlib,json,pathlib,re
P=pathlib.Path
OSH='148a851add9dd29671b1f493cd01af54ab87ad6fa5a97a2511ebd8cfbca46475'
NSH='ad012d4f8672e7de8bede05c4825c4573585fa49c09efc2f56f5483e02d3d0bb'
SBY='84a723e9a63e85a29ffb6b8c7598c114781053c6f30e258a1c2c1e0350f788d0'
assump=re.compile(r'\balways\s*@\s*\(posedge\s+clk\)\s*begin\s*if\s*\(rst_n\)\s*m_pkt_fits:\s*assume\s*\(\(wptr\s*-\s*commit_ptr\)\s*<\s*DEPTH\[ADDR_WIDTH:0\]\);\s*end')
cover=re.compile(r'\bc_near_full:\s*cover\s*\(rst_n\s*&&\s*\(\(wptr\s*-\s*commit_ptr\)\s*==\s*\(DEPTH\[ADDR_WIDTH:0\]\s*-\s*1\'b1\)\)\);')
def sha(b):return hashlib.sha256(b).hexdigest()
def comments(s):return re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*',lambda m:m.group(0) if m.group(0).startswith('"') else ' ',s)
def tok(s):return re.findall(r"(?:[0-9]+'[a-zA-Z][a-zA-Z0-9_xXzZ?]+|[a-zA-Z_$][\w$]*|\d+|\S)",comments(s))
def part(s):
 a,b=re.split(r'(?m)^`ifdef FORMAL$',s,maxsplit=1);f,c=re.split(r'(?m)^`endif // FORMAL$',b,maxsplit=1)
 return a+c,f
def edit(s,pattern):
 t,n=pattern.subn(lambda m:re.sub(r'[^\n]',' ',m.group(0)),s);assert n==1 and t.count('\n')==s.count('\n');return t
def prepare(a):
 o=(a.old/'rtl/axis_pkt_fifo.sv').read_bytes();n=(a.new/'rtl/axis_pkt_fifo.sv').read_bytes();so=(a.old/'formal/axis_pkt_fifo_bmc.sby').read_bytes();sn=(a.new/'formal/axis_pkt_fifo_bmc.sby').read_bytes()
 assert (sha(o),sha(n),sha(so),sha(sn))==(OSH,NSH,SBY,SBY)
 ot,nt=o.decode(),n.decode();assert tok(part(ot)[0])==tok(part(nt)[0]);assert len(assump.findall(nt))==1 and len(assump.findall(ot))==0 and len(cover.findall(nt))==1 and len(cover.findall(ot))==0
 variants={'old':ot,'new':nt,'hybrid':edit(nt,assump),'no_cover':edit(nt,cover),'restore_both':edit(edit(nt,assump),cover)}
 assert tok(part(variants['restore_both'])[1])==tok(part(ot)[1]);assert tok(part(variants['hybrid'])[1])!=tok(part(ot)[1]);assert 'c_near_full' in variants['hybrid']
 for k,s in variants.items():
  d=a.out/'variants'/k;(d/'rtl').mkdir(parents=True,exist_ok=True);(d/'formal').mkdir(parents=True,exist_ok=True)
  (d/'rtl/axis_pkt_fifo.sv').write_bytes(s.encode());(d/'formal/axis_pkt_fifo_bmc.sby').write_bytes(so)
  assert tok(part(s)[0])==tok(part(ot)[0])
 bad=nt.replace(') < DEPTH[ADDR_WIDTH:0]);',') <= DEPTH[ADDR_WIDTH:0]);',1)
 bad2=nt.replace('`endif // FORMAL',"always @(posedge clk) assert (1'b0);\n`endif // FORMAL",1)
 assert bad!=nt and len(assump.findall(bad))==0 and bad2!=nt and tok(part(bad2)[1])!=tok(part(nt)[1])
 manifest={'original_source_sha256':{'old':OSH,'new':NSH},'original_sby_sha256':SBY,'source_sha256':{k:sha(s.encode()) for k,s in variants.items()},'negative_source_checks':{'wrong_threshold_rejected':True,'inserted_assertion_rejected':True},'edit_semantics':'exact source-only edit preserves line count and unchanged original SBY'}
 (a.out/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n');print('R1_EXACT_SOURCE_INTERVENTIONS_PASS',json.dumps(manifest,sort_keys=True),flush=True)
def meta(s):
 p={}
 for kind in ('assert','assume','cover'):
  reset=0
  for idx,name in re.findall(r'^; yosys-smt2-'+kind+r' (\d+) (\S+)',s,re.M):
   if name.startswith('$assume$'):name='reset_assume_'+str(reset);reset+=1
   assert (kind,name) not in p;(p[kind,name])=int(idx)
 past=sorted(re.findall(r'^; yosys-smt2-register (\$past\$[^ ]+) (\d+)$',s,re.M),key=lambda x:int(re.search(r'\$(\d+)\$0$',x[0]).group(1)))
 return p,past
def analyze(a):
 root=a.out;manifest=json.loads((root/'SOURCE_MANIFEST.json').read_text());native={k:(root/'native'/f'{k}.smt2').read_text() for k in manifest['source_sha256']};props={k:meta(s)[0] for k,s in native.items()};pasts={k:meta(s)[1] for k,s in native.items()}
 for k,p in props.items():
  expected=(18,4 if k in ('old','hybrid','restore_both') else 5,6 if k in ('old','no_cover','restore_both') else 7)
  got=tuple(sum(kind==q for kind,name in p) for q in ('assert','assume','cover'))
  assert got==expected,(k,got,expected)
 assert {x for x in props['old'] if x[0]=='assert'}=={x for x in props['hybrid'] if x[0]=='assert'}=={x for x in props['new'] if x[0]=='assert'}
 assert {x for x in props['old'] if x[0]=='assume'}=={x for x in props['hybrid'] if x[0]=='assume'}
 assert {x for x in props['new'] if x[0]=='assume'}-{x for x in props['old'] if x[0]=='assume'}=={('assume','m_pkt_fits')}
 assert len(pasts['old'])==len(pasts['new'])==len(pasts['hybrid'])==12
 assert [int(w) for n,w in pasts['old']]==[int(w) for n,w in pasts['new']]==[int(w) for n,w in pasts['hybrid']]
 prior=(a.prior/'official_loss.smt2').read_text();assert sha(prior.encode())==a.expected_prior_sha and prior.strip().endswith('(check-sat)')
 prefix=prior.rsplit('\n(assert (and ',1)[0]+'\n';assert '(check-sat)' not in prefix
 hyb=native['hybrid'].replace('|axis_pkt_fifo','|axis_pkt_fifo_hyb');decl='(declare-fun |s_hyb| () |axis_pkt_fifo_hyb_s|)\n(declare-fun |t_hyb| () |axis_pkt_fifo_hyb_s|)\n(assert (|axis_pkt_fifo_hyb_t| |s_hyb| |t_hyb|))\n'
 new_map=re.findall(r'^\(assert \(= \(\|axis_pkt_fifo_new_n (.+?)\| \|s_new\|\) (.+)\)\)$',prefix,re.M);assert len(new_map)>=30
 pnew=[x for x,w in pasts['new']];phyb=[x for x,w in pasts['hybrid']];lines=[];past_count=0
 for name,expr in new_map:
  hname=name
  if name.startswith('$past$'):hname=phyb[pnew.index(name)];past_count+=1
  assert f'|axis_pkt_fifo_hyb_n {hname}|' in hyb,(name,hname)
  lines.append(f'(assert (= (|axis_pkt_fifo_hyb_n {hname}| |s_hyb|) {expr}))')
 assert past_count==12
 base=prefix+hyb+'\n'+decl+'\n'.join(lines)+'\n'
 def atom(k,kind,name):
  letter={'assert':'a','assume':'u','cover':'c'}[kind];step='s' if kind=='assume' and name.startswith('reset_assume_') else 't'
  return f'(|axis_pkt_fifo_{k}_{letter} {props[k][kind,name]}| |{step}_{k}|)'
 def f(k):
  assumptions=' '.join(atom(k,'assume',n) for kind,n in props[k] if kind=='assume');guarantees=' '.join(atom(k,'assert',n) for kind,n in props[k] if kind=='assert')
  return f'(and (and {assumptions}) (not (and {guarantees})))'
 fo,fn,fh=f('old'),f('new'),f('hyb');con=atom('new','assume','m_pkt_fits')
 differences=[f'(xor {atom("new",k,n)} {atom("hyb",k,n)})' for k,n in sorted(props['hybrid']) if k in ('assert','assume')]
 queries={'historical_loss':(f'(and {fo} (not {fn}))','sat'),'historical_gain':(f'(and {fn} (not {fo}))','unsat'),'hybrid_loss':(f'(and {fo} (not {fh}))','unsat'),'hybrid_gain':(f'(and {fh} (not {fo}))','unsat'),'hybrid_common_22_mismatch':('(or '+' '.join(differences)+')','unsat'),'historical_loss_contract_false':(f'(and {fo} (not {fn}) (not {con}))','sat'),'contract_true_old_challenge':(f'(and {fo} {con})','sat'),'contract_false_old_challenge':(f'(and {fo} (not {con}))','sat'),'negative_inverted_assertion':(f'(xor {atom("new","assert","a_occ_le_depth")} (not {atom("hyb","assert","a_occ_le_depth")}))','sat')}
 (root/'queries').mkdir(exist_ok=True);qman={}
 for name,(pred,expected) in queries.items():
  text=base+f'(assert {pred})\n(check-sat)\n';(root/'queries'/f'{name}.smt2').write_text(text);qman[name]={'sha256':sha(text.encode()),'expected':expected}
 (root/'QUERIES.json').write_text(json.dumps(qman,indent=2,sort_keys=True)+'\n')
 (root/'ANALYSIS_META.json').write_text(json.dumps({'native_sha256':{k:sha(s.encode()) for k,s in native.items()},'matched_net_count':len(new_map),'matched_past_regs':list(zip(pnew,phyb)),'counts':{k:{b:sum(x==b for x,n in p) for b in ('assert','assume','cover')} for k,p in props.items()},'queries':qman,'scope':'symbolic single-clock matched state, not reset-reachable assertion failure; both solvers share Yosys frontend'},indent=2,sort_keys=True)+'\n')
 print('R1_NATIVE_REELAB_TRIPLE_QUERIES_PASS',len(new_map),len(qman),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','analyze'));p.add_argument('--old',type=P);p.add_argument('--new',type=P);p.add_argument('--out',type=P,required=True);p.add_argument('--prior',type=P);p.add_argument('--expected-prior-sha',default='');a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 if a.mode=='prepare':assert a.old and a.new;prepare(a)
 else:assert a.prior and a.expected_prior_sha;analyze(a)
