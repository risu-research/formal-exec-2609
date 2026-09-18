#!/usr/bin/env python3
"""Compare five historical source-reelaborated native Yosys models with frozen official claims.
Symbolic two-state common-state domain; this is NOT a reset-reachable assertion-failure claim.
"""
import pathlib,re,hashlib,json,argparse
P=pathlib.Path
H=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def meta(s):
 p={}
 for kind in ('assert','assume','cover'):
  reset=0
  for index,name in re.findall(r'^; yosys-smt2-'+kind+r' (\d+) (\S+)',s,re.M):
   if name.startswith('$assume$'):name='reset_assume_'+str(reset);reset+=1
   assert (kind,name) not in p
   p[kind,name]=int(index)
 past=sorted(re.findall(r'^; yosys-smt2-register (\$past\$[^ ]+) (\d+)$',s,re.M),key=lambda r:int(re.search(r'\$(\d+)\$0$',r[0]).group(1)))
 return p,past
ap=argparse.ArgumentParser();ap.add_argument('--source',type=P,required=True);ap.add_argument('--bridge',type=P,required=True);ap.add_argument('--official',type=P,required=True);ap.add_argument('--output',type=P,required=True);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
source=json.loads((a.source/'SOURCE_MANIFEST.json').read_text())
assert source['original_source_sha256']=={'old':'148a851add9dd29671b1f493cd01af54ab87ad6fa5a97a2511ebd8cfbca46475','new':'ad012d4f8672e7de8bede05c4825c4573585fa49c09efc2f56f5483e02d3d0bb'}
assert source['original_sby_sha256']=='84a723e9a63e85a29ffb6b8c7598c114781053c6f30e258a1c2c1e0350f788d0'
for k,v in source['source_sha256'].items():assert H(a.source/'variants'/k/'rtl/axis_pkt_fifo.sv')==v
oldsha='41a3d4eac9450eab902dfdc46ddc6482adde8bda3e6e9009429ea32dd5b39173';newsha='3158f8608207c1f5aed5cc077a477cfe89749740cb2e6bb7b7128d275b176b91'
for sd,digest in [('old',oldsha),('new',newsha)]:assert H(a.official/f'{sd}.smt2')==digest
assert H(a.bridge/'official_loss.smt2')=='56f9a37955c2e70b727def892017ebaab5c79b6f36646f6d2b9f42d20a2de38c'
original={sd:(a.official/f'{sd}.smt2').read_text() for sd in ('old','new')};native={k:(a.source/'native'/f'{k}.smt2').read_text() for k in source['source_sha256']}
orig={sd:meta(original[sd]) for sd in original};fresh={k:meta(native[k]) for k in native}
for k in native:
 p,past=fresh[k];expected=(18,4 if k in ('old','hybrid','restore_both') else 5,6 if k in ('old','no_cover','restore_both') else 7)
 counts=tuple(sum(x==kind for x,name in p) for kind in ('assert','assume','cover'))
 assert counts==expected,(k,counts,expected)
 assert len(past)==12
for sd in ('old','new'):
 assert fresh[sd][0]==orig[sd][0],sd
 assert [int(w) for n,w in fresh[sd][1]]==[int(w) for n,w in orig[sd][1]],sd
for k in ('hybrid','no_cover','restore_both'):
 assert [int(w) for n,w in fresh[k][1]]==[int(w) for n,w in fresh['new'][1]],k
assert set((k,n) for k,n in fresh['new'][0] if k=='assume')-set((k,n) for k,n in fresh['hybrid'][0] if k=='assume')=={('assume','m_pkt_fits')}
for k in ('hybrid','no_cover','restore_both'):
 assert {p for p in fresh[k][0] if p[0]=='assert'}=={p for p in fresh['old'][0] if p[0]=='assert'}
prior=(a.bridge/'official_loss.smt2').read_text();prefix=prior.rsplit('\n(assert (and ',1)[0]+'\n';assert '(check-sat)' not in prefix
sym={'old':'r1_old','new':'r1_new','hybrid':'hyb','no_cover':'noco','restore_both':'rest'}
mapping={}
for sd in ('old','new'):
 rows=re.findall(r'^\(assert \(= \(\|axis_pkt_fifo_'+sd+r'_n (.+?)\| \|s_'+sd+r'\|\) (.+)\)\)$',prefix,re.M)
 assert len(rows)>=30
 mapping[sd]=rows
for sd in ('old','new'):
 names=[n for n,w in orig[sd][1]];matched=[n for n,e in mapping[sd] if n.startswith('$past$')]
 assert set(names)==set(matched),(sd,len(matched))
# Original wire identities are preserved by SHA-pinned source and checked property inventories.
# All 12 temporal registers are mapped by stable Yosys source-expression ordinal AND width.
extra={}
for k,text in native.items():
 sd='old' if k=='old' else 'new';tag=sym[k]
 renamed=text.replace('|axis_pkt_fifo','|axis_pkt_fifo_'+tag)
 orig_past=[name for name,width in orig[sd][1]];cur_past=[name for name,width in fresh[k][1]]
 chunks=[renamed,f'(declare-fun |s_{tag}| () |axis_pkt_fifo_{tag}_s|)',f'(declare-fun |t_{tag}| () |axis_pkt_fifo_{tag}_s|)',f'(assert (|axis_pkt_fifo_{tag}_t| |s_{tag}| |t_{tag}|))']
 used_past=0
 for name,expr in mapping[sd]:
  mapped=cur_past[orig_past.index(name)] if name.startswith('$past$') else name
  if name.startswith('$past$'):used_past+=1
  assert f'|axis_pkt_fifo_{tag}_n {mapped}|' in renamed,(k,name,mapped)
  chunks.append(f'(assert (= (|axis_pkt_fifo_{tag}_n {mapped}| |s_{tag}|) {expr}))')
 assert used_past==12
 extra[k]='\n'.join(chunks)+'\n'
def atm(k,kind,name):
 tag=sym[k];idx=fresh[k][0][kind,name];letter={'assert':'a','assume':'u','cover':'c'}[kind]
 step='s' if kind=='assume' and name.startswith('reset_assume_') else 't'
 return f'(|axis_pkt_fifo_{tag}_{letter} {idx}| |{step}_{tag}|)'
def whole(k):
 props=fresh[k][0]
 ass=' '.join(atm(k,'assume',n) for kind,n in props if kind=='assume')
 gs=' '.join(atm(k,'assert',n) for kind,n in props if kind=='assert')
 return f'(and (and {ass}) (not (and {gs})))'
def original_atom(k,kind,name):
 idx=orig[k][0][kind,name];letter={'assert':'a','assume':'u','cover':'c'}[kind]
 step='s' if kind=='assume' and name.startswith('reset_assume_') else 't'
 return f'(|axis_pkt_fifo_{k}_{letter} {idx}| |{step}_{k}|)'
def original_F(k):
 p=orig[k][0]
 return '(and (and '+' '.join(original_atom(k,'assume',n) for kind,n in p if kind=='assume')+') (not (and '+' '.join(original_atom(k,'assert',n) for kind,n in p if kind=='assert')+')))'
fo,fn=original_F('old'),original_F('new');q={}
def add(name,what,expected,ks):
 assert name not in q
 text=prefix+''.join(extra[k] for k in ks)+f'(assert {what})\n(check-sat)\n'
 dest=a.output/f'{name}.smt2';dest.write_text(text)
 q[name]={'expected':expected,'sha256':H(dest),'reelaborated_sources':ks}
add('historical_loss',f'(and {fo} (not {fn}))','sat',[])
add('historical_gain',f'(and {fn} (not {fo}))','unsat',[])
for k in ('hybrid','restore_both'):
 f=whole(k)
 add(k+'_loss',f'(and {fo} (not {f}))','unsat',[k]);add(k+'_gain',f'(and {f} (not {fo}))','unsat',[k])
for k in ('old','new'):
 xs=[f'(xor {original_atom(k,kind,n)} {atm(k,kind,n)})' for kind,n in orig[k][0] if kind in ('assert','assume')]
 assert len(xs)==(22 if k=='old' else 23)
 add('reelab_'+k+'_all_original_conditions_mismatch','(or '+' '.join(xs)+')','unsat',[k])
for k in ('hybrid','no_cover'):
 xs=[f'(xor {original_atom("new",kind,n)} {atm(k,kind,n)})' for kind,n in fresh[k][0] if kind in ('assert','assume') and (kind,n) in orig['new'][0]]
 assert len(xs)==(22 if k=='hybrid' else 23)
 add(k+'_shared_conditions_mismatch','(or '+' '.join(xs)+')','unsat',[k])
add('no_cover_whole_F_mismatch',f'(xor {fn} {whole("no_cover")})','unsat',['no_cover'])
contract=original_atom('new','assume','m_pkt_fits')
add('old_challenge_contract_false',f'(and {fo} (not {contract}))','sat',[])
add('old_challenge_contract_true',f'(and {fo} {contract})','sat',[])
add('original_loss_is_contract_false',f'(and {fo} (not {fn}) {contract})','unsat',[])
bits=lambda name:'(concat '+' '.join(f'|K_state_{name}_{i}|' for i in range(3,-1,-1))+')'
mut=f'(or (= |K_input_rst_n_0| #b0) (bvult (bvsub {bits("wptr")} {bits("rptr")}) #b1000))'
add('negative_off_by_one_boundary',f'(and (xor {original_atom("old","assert","a_occ_le_depth")} {mut}) (= {bits("wptr")} #b1000) (= {bits("rptr")} #b0000) (= |K_input_rst_n_0| #b1))','sat',[])
(a.output/'QUERIES.json').write_text(json.dumps(q,indent=2,sort_keys=True)+'\n')
(a.output/'PROVENANCE.json').write_text(json.dumps({'original_source_sha256':source['original_source_sha256'],'fresh_native_sha256':{k:H(a.source/'native'/f'{k}.smt2') for k in native},'original_official_sha256':{'old':oldsha,'new':newsha},'original_bridge_sha256':H(a.bridge/'official_loss.smt2'),'same_source_property_inventory':True,'matched_prior_native_wires':{sd:len(rows) for sd,rows in mapping.items()},'queries':len(q),'limitation':'shared Yosys frontend; matched arbitrary two-state symbolic states, not general actual-RTL safety or all four-state traces'},indent=2,sort_keys=True)+'\n')
print('R1_V2_FIVE_FRESH_NATIVE_REELAB_CROSSCHECK_QUERIES_READY',len(q),flush=True)
