#!/usr/bin/env python3
"""Native four-cell B-side control. No modification of generated Yosys functions."""
import zipfile,hashlib,re,json,os
from pathlib import Path
zp=Path(os.environ.get('G4_ZIP','work/authority.zip'))
Z=zipfile.ZipFile(zp)
O=Path(os.environ.get('G4_OUT','work/queries'));O.mkdir(parents=True,exist_ok=True)
sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha(zp.read_bytes())=='5b0068501754052c134a6f56f04642e63fccb4f38c859c97ba4d6f3fa50788eb'
def parse(s):
 names=dict(re.findall(r'^; yosys-smt2-register (.*?) (\d+)$',s,re.M))
 inputs=dict(re.findall(r'^; yosys-smt2-input (.*?) (\d+)$',s,re.M))
 outputs=dict(re.findall(r'^; yosys-smt2-output (.*?) (\d+)$',s,re.M))
 mem=dict((x,(int(a),int(b))) for x,a,b in re.findall(r'^; yosys-smt2-memory (\S+) (\d+) (\d+) ',s,re.M))
 def key(name):
  if name.startswith('$formal$'):
   m=re.search(r':(\d+)\$\d+_(CHECK|EN)$',name);assert m,name;return ('formal',m.group(1),m.group(2))
  if name.startswith('$past$'):
   m=re.search(r':(\d+)\$(\d+)\$(\d+)$',name);assert m,name
   return ('past',m.group(1),str(sum(1 for n in names if n.startswith('$past$') and re.search(r':'+m.group(1)+r'\$\d+\$\d+$',n) and int(re.search(r':\d+\$(\d+)\$',n).group(1)) < int(m.group(2)))),m.group(3))
  return ('rtl',name)
 semantic={}
 for name,width in names.items():
  k=key(name)
  if k in semantic:raise RuntimeError((k,name,semantic[k]))
  semantic[k]=(name,int(width))
 return semantic,inputs,outputs,mem
records=[]
for o in (0,1):
 a=Z.read(f'ir/O{o}_B0.smt2');b=Z.read(f'ir/O{o}_B1.smt2');A=a.decode();B=b.decode()
 ra,ia,oa,ma=parse(A);rb,ib,ob,mb=parse(B)
 assert ra.keys()==rb.keys() and ia==ib and oa==ob and ma==mb
 assert len(ra)==58+o and len(oa)==5 and len(ia)==6 and len(ma)==1
 source=Z.read(f'factorial/O{o}_B0/rtl/axis_pkt_fifo.sv').decode().splitlines()
 cover_regs={k for k in ra if k[0]=='formal' and 'cover' in source[int(k[1])-1]}
 assert len(cover_regs)==6,(o,cover_regs)
 assert all(ra[x][1]==rb[x][1] for x in ra)
 ax=dict(re.findall(r'^\(define-fun \|axis_pkt_fifo_a (\d+)\|.*?; (\S+)$',A,re.M))
 bx=dict(re.findall(r'^\(define-fun \|axis_pkt_fifo_a (\d+)\|.*?; (\S+)$',B,re.M))
 ux=dict(re.findall(r'^\(define-fun \|axis_pkt_fifo_u (\d+)\|.*?; (\S+)$',A,re.M))
 vx=dict(re.findall(r'^\(define-fun \|axis_pkt_fifo_u (\d+)\|.*?; (\S+)$',B,re.M))
 assert ax==bx and len(ax)==18 and len(ux)==len(vx)==4+o
 assert [re.sub(r'O[01]_B[01]/rtl/axis_pkt_fifo.sv:(\d+)\$\d+',r'SOURCE_LINE_\1',ux[str(i)]) for i in range(4+o)] == [re.sub(r'O[01]_B[01]/rtl/axis_pkt_fifo.sv:(\d+)\$\d+',r'SOURCE_LINE_\1',vx[str(i)]) for i in range(4+o)]
 H=A.replace('axis_pkt_fifo','base_axis_pkt_fifo')+'\n'+B.replace('axis_pkt_fifo','mut_axis_pkt_fifo')+'\n'
 def eq_fn(fn,typ='n',idx='0'):
  return f'(= (|base_axis_pkt_fifo_{typ} {fn[0].replace("axis_pkt_fifo","base_axis_pkt_fifo")}| base{idx}) (|mut_axis_pkt_fifo_{typ} {fn[1].replace("axis_pkt_fifo","mut_axis_pkt_fifo")}| mut{idx}))'
 reg_eq=[eq_fn((ra[k][0],rb[k][0])) for k in sorted(ra) if k not in cover_regs]
 mem_eq=[eq_fn((k,k),'m') for k in ma]
 inp_eq=[eq_fn((k,k)) for k in ia]
 aux_eq=[eq_fn((n,n)) for n in ('f_track_data','f_track_last','f_track_idx')]+[eq_fn(('mem','mem'),'m:R0D')]
 R=reg_eq+mem_eq+aux_eq
 def form_eq(typ,count):return [f'(= (|base_axis_pkt_fifo_{typ} {i}| base0) (|mut_axis_pkt_fifo_{typ} {i}| mut0))' for i in range(count)]
 def bad(items):assert items;return '(assert (or '+' '.join('(not '+v+')' for v in items)+'))\n'
 def assertions(items):return ''.join('(assert '+t+')\n' for t in items)
 states='(declare-const base0 |base_axis_pkt_fifo_s|)\n(declare-const mut0 |mut_axis_pkt_fifo_s|)\n'
 base=states+assertions(R+inp_eq)
 def make(label,suffix,expected):
  p=O/f'O{o}_{label}.smt2';p.write_text(H+suffix+'(check-sat)\n');records.append({'name':p.name,'expected':expected,'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size})
 make('aligned_nonempty',base,'sat')
 make('whole_assertion_mismatch',base+bad(form_eq('a',18)),'unsat')
 make('whole_assumption_mismatch',base+bad(form_eq('u',4+o)),'unsat')
 other=[k for k in oa if k!='pkt_count'];assert len(other)==4
 make('other_outputs_mismatch',base+bad([eq_fn((k,k)) for k in other]),'unsat')
 make('count_output_difference',base+assertions([f'(not {eq_fn(("pkt_count","pkt_count"))})']),'sat')
 make('concrete_count_delta',base+assertions(['(= (|base_axis_pkt_fifo_n wr_pkts| base0) #x1)','(= (|base_axis_pkt_fifo_n rd_pkts| base0) #x0)','(= (|base_axis_pkt_fifo_n pkt_count| base0) #x1)','(= (|mut_axis_pkt_fifo_n pkt_count| mut0) #x2)']),'sat')
 nexts='(declare-const base1 |base_axis_pkt_fifo_s|)\n(declare-const mut1 |mut_axis_pkt_fifo_s|)\n'
 steps=base+nexts+assertions(['(|base_axis_pkt_fifo_t| base0 base1)','(|mut_axis_pkt_fifo_t| mut0 mut1)'])
 def nxt(s):return s.replace('base0','base1').replace('mut0','mut1')
 make('step_nonempty',steps,'sat')
 make('all_registers_and_memory_induction_failure',steps+bad([nxt(v) for v in R]),'unsat')
 make('both_initial_conditions_nonempty',base+assertions(['(|base_axis_pkt_fifo_i| base0)','(|mut_axis_pkt_fifo_i| mut0)']),'sat')
 make('negative_no_alignment_formal',states+assertions(inp_eq)+bad(form_eq('a',18)),'sat')
 covermap=dict(re.findall(r'^; yosys-smt2-cover (\d+) (\S+)$',A,re.M))
 assert len(covermap)==6 and covermap==dict(re.findall(r'^; yosys-smt2-cover (\d+) (\S+)$',B,re.M))
 coveridx=next(i for i,n in covermap.items() if n=='c_two_pkts')
 make('cover_two_pkts_mismatch',base+assertions([f'(not (= (|base_axis_pkt_fifo_c {coveridx}| base0) (|mut_axis_pkt_fifo_c {coveridx}| mut0)))']),'sat')
 noidx=[v for v in R if 'f_track_idx' not in v]
 assert len(noidx)==len(R)-1
 weaksteps=states+assertions(noidx+inp_eq)+nexts+assertions(['(|base_axis_pkt_fifo_t| base0 base1)','(|mut_axis_pkt_fifo_t| mut0 mut1)'])
 make('negative_missing_anyconst_tracker_induction_failure',weaksteps+bad([nxt(v) for v in R]),'sat')
 nomem=[v for v in R if '_m mem|' not in v and '_m:R0D mem|' not in v]
 assert len(nomem)==len(R)-2
 weakmem=states+assertions(nomem+inp_eq)+nexts+assertions(['(|base_axis_pkt_fifo_t| base0 base1)','(|mut_axis_pkt_fifo_t| mut0 mut1)'])
 make('negative_missing_memory_induction_failure',weakmem+bad([nxt(v) for v in R]),'sat')
 print(f'G4_BUILD O{o}: regs={len(ra)} mem={len(ma)} inputs={len(ia)} outputs={len(oa)} checks=13',flush=True)
manifest={'proof_scope':'Per-O B0 vs B1 under paired state and matched common formal, memory and anyconst; cover excluded; one-step induction conditional, not all independent initial states', 'Yosys_artifact_id':10555617048, 'Yosys_artifact_sha256':sha(zp.read_bytes()), 'queries':records}
(O/'MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print('G4_GENERATED',len(records),flush=True)
