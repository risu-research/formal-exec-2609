#!/usr/bin/env python3
"""Cross-revision real bit-vector property-cone translation. Arbitrary sampled-clock states, not reachability."""
import json,re,hashlib,pathlib,argparse,collections
REG={'f_init','f_past_valid','wptr','rptr','commit_ptr','wr_pkts','rd_pkts','rd_word_q','out_word','out_valid','rd_inflight','f_pop_cnt','f_push_cnt'}
def bv(i,n):return f'(_ bv{i%(1<<n)} {n})'
def vec(bits):return bits[0] if len(bits)==1 else '(concat '+' '.join(reversed(bits))+')'
class IR:
 def __init__(self,side,root):
  self.side=side;p=root/side;raw=(p/'formal.json').read_bytes();self.sha=hashlib.sha256(raw).hexdigest(); inv=json.loads((p/'inventory.json').read_text());assert self.sha==inv['formal']['sha256']
  self.m=json.loads(raw)['modules']['axis_pkt_fifo'];self.cells=self.m['cells'];self.prod={};self.alias=collections.defaultdict(list)
  for n,v in self.m['netnames'].items():
   for i,b in enumerate(v['bits']):
    if isinstance(b,int):self.alias[b].append((n,i))
  for n,c in self.cells.items():
   for port,d in c['port_directions'].items():
    if d=='output':
     for i,b in enumerate(c['connections'][port]):
      if isinstance(b,int):
       assert b not in self.prod,(side,b,n)
       self.prod[b]=(n,port,i)
  pats=[]
  for n,c in self.cells.items():
   if c['type']!='$dff':continue
   m=re.search(r'\$past\$[^\n]*?\$([0-9]+)\$0',str(self.alias[c['connections']['Q'][0]]))
   if m:pats.append((int(m.group(1)),n))
  self.past={n:i for i,(_,n) in enumerate(sorted(pats))}
  self.used={};self.defs=[];self.done=set();self.props={};self.types=collections.Counter();self.undef=collections.Counter();self.active=None
 @staticmethod
 def sym(k):return '|K_'+'_'.join(map(str,k))+'|'
 def leaf(self,b):
  if b not in self.prod:
   a=sorted((n,i) for n,i in self.alias[b] if n in self.m['ports']);assert a,('no input name',self.side,b)
   k=('input',a[0][0],a[0][1])
  else:
   n,port,i=self.prod[b];c=self.cells[n];assert c['type'] in ('$dff','$anyconst')
   candidates=sorted((name,j) for name,j in self.alias[b] if name in REG or name.startswith('f_track_'))
   if c['type']=='$anyconst':
    assert candidates;name,j=candidates[0];k=('const',name,j)
   elif candidates:
    name,j=sorted(candidates,key=lambda t:(t[0] not in REG,t[0],t[1]))[0];k=('state',name,j)
   else:
    assert n in self.past,('unnamed non-past register',self.side,n)
    k=('past',self.past[n],i)
  self.used[k]=b;return self.sym(k)
 def bit(self,b):
  if isinstance(b,str):
   assert b in ('0','1','x'),('unhandled undefined signal',self.side,b)
   if b!='x':return '#b'+b
   assert self.active is not None
   k=('undef',self.active,self.undef[self.active]);self.undef[self.active]+=1;self.used[k]=b;return self.sym(k)
  if b not in self.prod:return self.leaf(b)
  n,port,i=self.prod[b];typ=self.cells[n]['type']
  if typ in ('$dff','$anyconst'):return self.leaf(b)
  assert typ not in ('$mem_v2','$adff','$sdff'),('unexpected opaque logic',self.side,n,typ)
  self.define(n);return f'((_ extract {i} {i}) |V_{self.side}_{n}|)'
 def bits(self,seq):return vec([self.bit(x) for x in seq])
 def define(self,n):
  if n in self.done:return
  self.done.add(n);c=self.cells[n];t=c['type'];self.types[t]+=1;cc=c['connections'];p=c.get('parameters',{});w=len(cc['Y'])
  a=self.bits(cc['A']) if 'A' in cc else None;b=self.bits(cc['B']) if 'B' in cc else None;aw=len(cc.get('A',[]));bw=len(cc.get('B',[]))
  sg=lambda name:int(p.get(name,'0'),2) if isinstance(p.get(name,'0'),str) else int(p.get(name,0))
  sa=sg('A_SIGNED');sb=sg('B_SIGNED')
  def ext(x,src,dest,signed):
   assert src and dest
   if src==dest:return x
   if src>dest:return f'((_ extract {dest-1} 0) {x})'
   return f'((_ {"sign_extend" if signed else "zero_extend"} {dest-src}) {x})'
  if t=='$mux':
   assert w==aw==bw and len(cc['S'])==1
   e=f'(ite (= {self.bits(cc["S"])} #b1) {b} {a})'
  elif t in ('$add','$sub'):
   e=f'({"bvadd" if t=="$add" else "bvsub"} {ext(a,aw,w,sa)} {ext(b,bw,w,sb)})'
  elif t in ('$eq','$ne','$le','$lt','$ge','$gt'):
   assert w==1;size=max(aw,bw);signed=bool(sa and sb);x=ext(a,aw,size,signed);y=ext(b,bw,size,signed)
   if t=='$eq':test=f'(= {x} {y})'
   elif t=='$ne':test=f'(not (= {x} {y}))'
   else:test=f'(bv{"s" if signed else "u"}{t[1:]} {x} {y})'
   e=f'(ite {test} #b1 #b0)'
  elif t in ('$logic_not','$reduce_bool','$reduce_or'):
   assert w==1
   e=f'(ite (= {a} {bv(0,aw)}) {"#b1" if t=="$logic_not" else "#b0"} {"#b0" if t=="$logic_not" else "#b1"})'
  elif t in ('$logic_and','$logic_or'):
   assert w==1
   e=f'(ite ({"and" if t=="$logic_and" else "or"} (not (= {a} {bv(0,aw)})) (not (= {b} {bv(0,bw)}))) #b1 #b0)'
  elif t in ('$and','$or','$xor'):
   op={'$and':'bvand','$or':'bvor','$xor':'bvxor'}[t];e=f'({op} {ext(a,aw,w,sa)} {ext(b,bw,w,sb)})'
  elif t=='$not':e=f'(bvnot {ext(a,aw,w,sa)})'
  else:raise NotImplementedError((self.side,n,t))
  self.defs.append(f'(define-fun |V_{self.side}_{n}| () (_ BitVec {w}) {e})')
 def prop(self,n):
  c=self.cells[n];assert c['type'] in ('$assume','$assert');outputs=[]
  for port in ('A','EN'):
   assert len(c['connections'][port])==1
   bit=c['connections'][port][0];assert isinstance(bit,int) and bit in self.prod
   stage=self.cells[self.prod[bit][0]]
   if stage['type']=='$dff':
    assert len(stage['connections']['D'])==1
    bit=stage['connections']['D'][0]
   outputs.append(self.bit(bit))
  A,EN=outputs;return f'(or (= {EN} #b0) (= {A} #b1))'
 def compile(self):
  A=sorted(n for n,c in self.cells.items() if c['type']=='$assume');G=sorted(n for n,c in self.cells.items() if c['type']=='$assert')
  for n in A+G:
   self.active=('reset_assume_'+str(A.index(n)) if n.startswith('$assume$') else n)
   self.props[n]=self.prop(n)
  self.active=None;return A,G

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=pathlib.Path,required=True);p.add_argument('--out',type=pathlib.Path,required=True);args=p.parse_args()
 a,b=(IR(k,args.root) for k in ('old','new'));aa,ag=a.compile();ba,bg=b.compile()
 assert ag==bg and len(ag)==18
 special=lambda x:x.startswith('$assume$')
 assert sum(map(special,aa))==sum(map(special,ba))==2
 assert sorted(x for x in aa if not special(x))==sorted(x for x in ba if not special(x) and x!='m_pkt_fits')
 common=[(aa[i],ba[i]) for i in range(2)]+[(x,x) for x in aa if not special(x)]
 assert all(special(x) and special(y) for x,y in common[:2]) and len(common)==4 and 'm_pkt_fits' in ba
 assert len(a.past)==len(b.past)==12
 for n,j in a.past.items():
  other=next(k for k,v in b.past.items() if v==j)
  assert len(a.cells[n]['connections']['Q'])==len(b.cells[other]['connections']['Q'])
 assert set(a.used)<=set(b.used),set(a.used)-set(b.used)
 assert set(b.used)-set(a.used)=={('undef','m_pkt_fits',0)},set(b.used)-set(a.used)
 declaration=[f'(declare-fun {a.sym(k)} () (_ BitVec 1))' for k in sorted(b.used,key=str)]
 conj=lambda ps:'(and '+' '.join(ps)+')'
 A0=conj([a.props[x] for x,y in common]);A1=conj([b.props[y] for x,y in common]+[b.props['m_pkt_fits']])
 G0=conj([a.props[x] for x in ag]);G1=conj([b.props[x] for x in bg])
 F0=f'(and {A0} (not {G0}))';F1=f'(and {A1} (not {G1}))'
 header='\n'.join(['(set-logic QF_BV)']+declaration+a.defs+b.defs)+'\n'
 checks={'loss':f'(and {F0} (not {F1}))','gain':f'(and {F1} (not {F0}))','shared_property_difference':'(or '+' '.join([f'(xor {a.props[x]} {b.props[y]})' for x,y in common]+[f'(xor {a.props[x]} {b.props[x]})' for x in ag])+')','loss_with_contract_true':f'(and {F0} (not {F1}) {b.props["m_pkt_fits"]})','loss_with_all_assertions_true':f'(and {F0} (not {F1}) {G0})','mutant_delete_new_assumption':f'(and {F0} (not {b.props["m_pkt_fits"]}))'}
 args.out.mkdir(parents=True,exist_ok=True)
 for n,v in checks.items():
  suffix=f'(assert {v})\n(check-sat)\n'
  if n=='loss':suffix+='(get-value ('+' '.join(a.sym(k) for k in [('state','wptr',i) for i in range(4)]+[('state','rptr',i) for i in range(4)]+[('state','commit_ptr',i) for i in range(4)])+'))\n'
  (args.out/(n+'.smt2')).write_text(header+suffix)
 meta={'scope':'complete combinational formal property cones of two separately loaded, hashed Yosys JSON IRs, same symbolic sampled-clock state/input/anyconst/past bits; NO initialized reachability or temporal induction','old_ir_sha256':a.sha,'new_ir_sha256':b.sha,'symbolic_bits':len(b.used),'common_assumptions':len(common),'common_assertions':len(ag),'past_registers':len(a.past),'old_cone_cells':dict(a.types),'new_cone_cells':dict(b.types),'query_sha256':{n:hashlib.sha256((args.out/(n+'.smt2')).read_bytes()).hexdigest() for n in checks}}
 (args.out/'meta.json').write_text(json.dumps(meta,sort_keys=True,indent=2)+'\n');print(json.dumps(meta,sort_keys=True))
if __name__=='__main__':main()
