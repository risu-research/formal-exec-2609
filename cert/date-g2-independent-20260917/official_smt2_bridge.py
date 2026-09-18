#!/usr/bin/env python3
"""Compare official Yosys write_smt2 with separately generated JSON property cones.

Full arbitrary-state sampled-clock property equivalence; not an unbounded reachability proof.
"""
import pathlib,hashlib,importlib.util,re,json,collections,argparse
ap=argparse.ArgumentParser();ap.add_argument('--root',type=pathlib.Path,required=True);ap.add_argument('--official',type=pathlib.Path,required=True);ap.add_argument('--translator',type=pathlib.Path,required=True);ap.add_argument('--out',type=pathlib.Path,required=True);args=ap.parse_args()
args.out.mkdir(parents=True,exist_ok=True)
mod_path=args.translator
spec=importlib.util.spec_from_file_location('G2OriginalTranslator',mod_path)
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
model={sd:t.IR(sd,args.root) for sd in ('old','new')}
names={sd:model[sd].compile() for sd in model}
old,new=model['old'],model['new']
assert set(new.used)-set(old.used)=={('undef','m_pkt_fits',0)}
assert set(old.used)<set(new.used)
old_names={n for n in names['old'][1]};assert old_names=={n for n in names['new'][1]} and len(old_names)==18

def extract_official(sd):
 text=(args.official/f'{sd}.smt2').read_text()
 renamed='axis_pkt_fifo_'+sd
 # Standalone official Yosys output, only deterministic alpha-renaming for joint SMT use.
 text=text.replace('|axis_pkt_fifo','|'+renamed)
 smt_sha=hashlib.sha256((args.official/f'{sd}.smt2').read_bytes()).hexdigest()
 orig=(args.official/f'{sd}.smt2').read_text()
 props={}
 for kind,prefix in (('assert','a'),('assume','u')):
  for idx,name in re.findall(r'^; yosys-smt2-'+kind+r' (\d+) (\S+)',orig,re.M):
   idx=int(idx)
   if name.startswith('$assume$'):name='reset_assume_'+str(len([p for p in props if p[0]=='assume' and p[1].startswith('reset_assume_')]))
   key=(kind,name)
   assert key not in props,(sd,key)
   # Reset assumptions originate from always @(*) and are sampled in predecessor.
   # Clocked property registers are sampled in successor. This distinction is tested.
   props[key]=f'(|{renamed}_{prefix} {idx}| |{"s" if kind=="assume" and name.startswith("reset_assume_") else "t"}_{sd}|)'
 official_past=re.findall(r'^; yosys-smt2-register (\$past\$[^ ]+) (\d+)$',orig,re.M)
 official_past=sorted(official_past,key=lambda x:int(re.search(r'\$past\$[^\n]*?\$(\d+)\$0',x[0]).group(1)))
 assert len(official_past)==len(model[sd].past)==12
 for i,(name,w) in enumerate(official_past):
  reg=next(k for k,j in model[sd].past.items() if j==i)
  assert int(w)==len(model[sd].cells[reg]['connections']['Q']),(sd,i,w,reg)
 return text,props,[n for n,w in official_past],smt_sha
O={sd:extract_official(sd) for sd in ('old','new')}
for sd in ('old','new'):
 assert len(O[sd][1])==len(names[sd][0])+len(names[sd][1])

def shared_prelude():
 decl=[f'(declare-fun {old.sym(k)} () (_ BitVec 1))' for k in sorted(new.used,key=str)]
 smt=['(set-logic ALL)']+[O[sd][0] for sd in ('old','new')]+decl+old.defs+new.defs
 for sd in ('old','new'):
  smt+=[f'(declare-fun |s_{sd}| () |axis_pkt_fifo_{sd}_s|)',f'(declare-fun |t_{sd}| () |axis_pkt_fifo_{sd}_s|)']
  smt +=[f'(assert (|axis_pkt_fifo_{sd}_t| |s_{sd}| |t_{sd}|))']
  # Pair exclusively source-named independent leaves and width-checked past regs.
  combos=collections.defaultdict(dict)
  for key,bit in model[sd].used.items():
   cat=key[0]
   if cat=='undef':continue  # leave original JSON x-bits unconstrained
   if cat=='past':name=O[sd][2][key[1]];index=key[2]
   else:name=key[1];index=key[2]
   assert cat in ('state','input','const','past'),key
   assert index not in combos[name],(sd,name,index)
   combos[name][index]=key
  for name,bitkeys in combos.items():
   assert sorted(bitkeys)==list(range(len(bitkeys))),(sd,name,bitkeys)
   alias=f'(|axis_pkt_fifo_{sd}_n {name}| |s_{sd}|)'
   if len(bitkeys)==1:expr=f'(= {old.sym(bitkeys[0])} #b1)'
   else:expr='(concat '+' '.join(old.sym(bitkeys[i]) for i in sorted(bitkeys,reverse=True))+')'
   smt.append(f'(assert (= {alias} {expr}))')
 return '\n'.join(smt)+'\n'

def cp(sd,kind,name):
 m=model[sd];lst=names[sd][0] if kind=='assume' else names[sd][1]
 if kind=='assume' and name.startswith('reset_assume_'):
  original=[n for n in lst if n.startswith('$assume$')][int(name.rsplit('_',1)[1])]
 else:original=name
 assert original in m.props,(sd,kind,name,original)
 return m.props[original]

pr=shared_prelude()
for sd in ('old','new'):
 pairs=[(kind,name) for kind,name in O[sd][1]]
 mismatches=[f'(xor {O[sd][1][(kind,name)]} {cp(sd,kind,name)})' for kind,name in pairs]
 (args.out/f'{sd}_official_vs_json_all.smt2').write_text(pr+'(assert (or '+' '.join(mismatches)+'))\n(check-sat)\n')
 for kind,name in pairs:
  (args.out/f'{sd}_{kind}_{name}.smt2').write_text(pr+f'(assert (xor {O[sd][1][(kind,name)]} {cp(sd,kind,name)}))\n(check-sat)\n')
# Independently reconstruct whole-F from official backend ONLY, without JSON predicates.
assumptions={sd:[v for (kind,name),v in O[sd][1].items() if kind=='assume'] for sd in O}
assertions={sd:[v for (kind,name),v in O[sd][1].items() if kind=='assert'] for sd in O}
F={sd:'(and (and '+' '.join(assumptions[sd])+') (not (and '+' '.join(assertions[sd])+')))' for sd in O}
queries={'official_loss':f'(and {F["old"]} (not {F["new"]}))',
         'official_gain':f'(and {F["new"]} (not {F["old"]}))',
         'official_common_properties_mismatch':'(or '+' '.join([f'(xor {O["old"][1][kind,name]} {O["new"][1][kind,name]})' for kind,name in O['old'][1] if (kind,name) in O['new'][1]])+')',
         'official_loss_contract_true':f'(and {F["old"]} (not {F["new"]}) {O["new"][1][("assume","m_pkt_fits")]})',
         'official_new_contract_false':f'(not {O["new"][1][("assume","m_pkt_fits")]})'}
for label,pred in queries.items():
 (args.out/f'{label}.smt2').write_text(pr+f'(assert {pred})\n(check-sat)\n')
# Nontrivial negative controls: exact threshold off-by-one and wrong reset time.
kb=lambda n:'(concat '+' '.join(old.sym(('state',n,i)) for i in range(3,-1,-1))+')'
mutated=f'(or (= {old.sym(("input","rst_n",0))} #b0) (bvult (bvsub {kb("wptr")} {kb("rptr")}) #b1000))'
(args.out/'negative_mutated_occ_boundary.smt2').write_text(pr+f'(assert (xor {O["old"][1][("assert","a_occ_le_depth")]} {mutated}))\n(assert (= {kb("wptr")} #b1000))\n(assert (= {kb("rptr")} #b0000))\n(assert (= {old.sym(("input","rst_n",0))} #b1))\n(check-sat)\n')
reset_name='reset_assume_0';reset_wrong=O['old'][1][('assume',reset_name)].replace('|s_old|','|t_old|')
(args.out/'negative_wrong_reset_sampling.smt2').write_text(pr+f'(assert (xor {reset_wrong} {cp("old","assume",reset_name)}))\n(check-sat)\n')
meta={'official_sha256':{sd:O[sd][3] for sd in O},'original_json_sha256':{sd:model[sd].sha for sd in model},'translator_sha256':hashlib.sha256(mod_path.read_bytes()).hexdigest(),'mapping':{sd:{'past_names':O[sd][2],'property_pairs':len(O[sd][1]),'mapped_leaf_bit_count':len([k for k in model[sd].used if k[0]!='undef']),'undefined_bit_count':len([k for k in model[sd].used if k[0]=='undef'])} for sd in O},'undef_semantics':'unconstrained in JSON route; official SMT2 uses its own Yosys semantics','scope':'all old 22/new 23 property predicates and independently generated whole-F; arbitrary sampled-clock states only; no initialized reachability in this bridge'}
(args.out/'bridge_meta.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n')
print(json.dumps(meta,indent=2,sort_keys=True))