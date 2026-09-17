"""Source-bound historical ZipCPU formal-IR extraction; unsupported visited cells fail closed.
Exact G2-compatible re-derivation, retained unchanged as an independent path from source replay.
"""
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path
import hashlib,json,re

@dataclass(frozen=True)
class E:
 s:str
 w:int=1
 kind:str='bv'
def sha(x):return hashlib.sha256(x.encode()).hexdigest()
def sym(key):return 'v_'+sha(key)[:24]
def bv(v,w=1):return f'(_ bv{int(v)} {int(w)})'
def boolof(e):return e.s if e.kind=='bool' else f'(not (= {e.s} {bv(0,e.w)}))'
def bvof(e,w=None,signed=False):
 s=f'(ite {e.s} #b1 #b0)' if e.kind=='bool' else e.s; ew=1 if e.kind=='bool' else e.w
 if w is None or ew==w:return s
 if ew<w:return f"((_ {'sign_extend' if signed else 'zero_extend'} {w-ew}) {s})"
 return f'((_ extract {w-1} 0) {s})'
class IR:
 def __init__(self,p):
  raw=Path(p).read_bytes();self.sha256=hashlib.sha256(raw).hexdigest();self.m=json.loads(raw)['modules']['pipemem'];self.names=defaultdict(list);self.portbits={};self.drivers={};self.qbits={};self.cache={};self.bitcache={};self.varmeta={};self.private=set()
  for pn,p in self.m['ports'].items():
   for j,b in enumerate(p['bits']):
    if isinstance(b,int):self.portbits[b]=(pn,j,len(p['bits']),p['direction'])
  for name,nn in self.m['netnames'].items():
   for j,b in enumerate(nn['bits']):
    if isinstance(b,int):self.names[b].append((name,j,len(nn['bits']),nn.get('attributes',{}).get('src','')))
  for cn,c in self.m['cells'].items():
   t=c['type'];con=c['connections'];dirs=c.get('port_directions',{})
   if t in ('$dff','$adff','$sdff','$dffe','$sdffe'):
    for j,b in enumerate(con.get('Q',[])):
     if isinstance(b,int):self.qbits[b]=(cn,c,j)
   for port,bits in con.items():
    if dirs.get(port)=='output':
     for j,b in enumerate(bits):
      if isinstance(b,int):self.drivers[b]=(cn,c,port,j)
 def var(self,key,meta={},private=False):
  s=sym(key);self.varmeta.setdefault(s,{**meta,'key':key,'symbol':s})
  if private:self.private.add(s)
  return E(s)
 def leaf(self,b):
  if b in self.portbits:
   n,j,w,d=self.portbits[b];return self.var(f'PORT::{n}[{j}]',dict(category='PORT',name=n,bit=j,width=w,direction=d,native_bit=b))
  cand=[]
  for n,j,w,src in self.names.get(b,[]):
   nn=n.replace('\\','');cand.append((1 if nn.startswith('$') or '.$' in nn else 0,len(nn),nn,j,w,src))
  if cand:
   _,_,n,j,w,src=min(cand);return self.var(f'NET::{n}[{j}]',dict(category='NET',name=n,bit=j,width=w,src=src,native_bit=b))
  return self.var(f'ANON::{b}',dict(category='ANON',bit_id=b))
 def bit(self,b,formal_boundary=False,ctx=''):
  if isinstance(b,str):
   if b in '01' and len(b)==1:return E('#b'+b)
   if b in ('x','z'):return self.var('PRIVATE_X::'+sha(ctx),dict(category='PRIVATE_X',origin=ctx),True)
   raise ValueError('unknown bit '+str(b))
  k=(b,formal_boundary)
  if k in self.bitcache:return self.bitcache[k]
  if b in self.qbits:
   cn,c,j=self.qbits[b];names=' '.join(x[0] for x in self.names.get(b,[]));fm='$formal$' in cn or '$formal$' in names;past='$past$' in cn or '$past$' in names
   if formal_boundary and fm:out=self.bit(c['connections']['D'][j],False,cn+f':D{j}')
   elif past:
    de=self.bit(c['connections']['D'][j],False,cn+f':PAST_D{j}');seen={}
    def sub(m):
     s=m.group(0)
     if s not in self.private:return s
     if s not in seen:seen[s]='PX'+str(len(seen))
     return seen[s]
    norm=re.sub(r'\bv_[0-9a-f]{24}\b',sub,de.s)
    out=self.var('PAST::'+sha(norm)[:24],dict(category='PAST',update_sha256=sha(norm),native_bit=b))
   else:out=self.leaf(b)
   self.bitcache[k]=out;return out
  if b not in self.drivers:out=self.leaf(b);self.bitcache[k]=out;return out
  cn,c,p,j=self.drivers[b];v=self.cell(cn,c)
  out=E(f'(ite {v.s} #b1 #b0)') if v.kind=='bool' else (v if v.w==1 else E(f'((_ extract {j} {j}) {v.s})'))
  self.bitcache[k]=out;return out
 def sig(self,bits,bound=False,ctx=''):
  xs=[self.bit(b,bound,ctx+f':{i}') for i,b in enumerate(bits)]
  if not xs:return E('#b0')
  return xs[0] if len(xs)==1 else E('(concat '+' '.join(x.s for x in reversed(xs))+')',len(xs))
 def cell(self,cn,c):
  if cn in self.cache:return self.cache[cn]
  t,con,par=c['type'],c.get('connections',{}),c.get('parameters',{})
  def S(p):return self.sig(con[p],False,cn+':'+p)
  def P(n,default='0'):
   v=par.get(n,default)
   try:return int(v,2) if isinstance(v,str) and set(v)<=set('01') else int(v)
   except:return 0
  yw=len(con.get('Y',[])) or len(con.get('Q',[])) or 1
  if t=='$mux':out=E(f"(ite {boolof(S('S'))} {bvof(S('B'),yw)} {bvof(S('A'),yw)})",yw)
  elif t in ('$logic_and','$logic_or'):
   op='and' if t=='$logic_and' else 'or';out=E(f"(ite ({op} {boolof(S('A'))} {boolof(S('B'))}) #b1 #b0)")
  elif t in ('$logic_not','$reduce_bool'):
   q=f"(not {boolof(S('A'))})" if t=='$logic_not' else boolof(S('A'));out=E(f'(ite {q} #b1 #b0)')
  elif t in ('$reduce_or','$reduce_and','$reduce_xor','$reduce_xnor'):
   a=S('A');bits=[f'(= ((_ extract {i} {i}) {bvof(a)}) #b1)' for i in range(a.w)]
   if t=='$reduce_or':q='false' if not bits else '(or '+' '.join(bits)+')'
   elif t=='$reduce_and':q='true' if not bits else '(and '+' '.join(bits)+')'
   else:
    q='false'
    for x in bits:q=f'(xor {q} {x})'
    if t=='$reduce_xnor':q=f'(not {q})'
   out=E(f'(ite {q} #b1 #b0)')
  elif t in ('$eq','$ne','$eqx','$nex'):
   a,b=S('A'),S('B');w=max(a.w,b.w);q=f'(= {bvof(a,w)} {bvof(b,w)})';q=f'(not {q})' if t in ('$ne','$nex') else q;out=E(f'(ite {q} #b1 #b0)')
  elif t in ('$lt','$le','$gt','$ge'):
   if P('A_SIGNED') or P('B_SIGNED'):raise ValueError('unsupported signed')
   a,b=S('A'),S('B');w=max(a.w,b.w);op={'$lt':'bvult','$le':'bvule','$gt':'bvugt','$ge':'bvuge'}[t];out=E(f'(ite ({op} {bvof(a,w)} {bvof(b,w)}) #b1 #b0)')
  elif t in ('$add','$sub','$mul'):
   op={'$add':'bvadd','$sub':'bvsub','$mul':'bvmul'}[t];out=E(f'({op} {bvof(S("A"),yw)} {bvof(S("B"),yw)})',yw)
  elif t in ('$and','$or','$xor','$xnor'):
   a,b=bvof(S('A'),yw),bvof(S('B'),yw)
   op={'$and':'bvand','$or':'bvor','$xor':'bvxor'}.get(t)
   out=E(f'(bvnot (bvxor {a} {b}))',yw) if t=='$xnor' else E(f'({op} {a} {b})',yw)
  elif t in ('$not','$pos','$neg'):
   a=bvof(S('A'),yw);out=E(a,yw) if t=='$pos' else E(f'({"bvnot" if t=="$not" else "bvneg"} {a})',yw)
  elif t=='$initstate':out=self.var('INITSTATE',dict(category='INITSTATE'))
  elif t in ('$dff','$adff','$sdff','$dffe','$sdffe'):
   xs=[self.leaf(b) if isinstance(b,int) else self.bit(b) for b in con.get('Q',[])];out=xs[0] if len(xs)==1 else E('(concat '+' '.join(x.s for x in reversed(xs))+')',len(xs))
  else:raise ValueError('unsupported reached cell '+t+' '+cn)
  self.cache[cn]=out;return out
 def formal(self,typ):
  out=[]
  for cn,c in self.m['cells'].items():
   if c['type']!=typ:continue
   con=c['connections'];a0=con['A'][0];e0=con['EN'][0];aq=isinstance(a0,int) and a0 in self.qbits;eq=isinstance(e0,int) and e0 in self.qbits
   if aq!=eq:raise ValueError('mixed formal registration')
   a=self.sig(con['A'],aq,cn+':A');en=self.sig(con['EN'],eq,cn+':EN')
   out.append(dict(index=len(out),cell=cn,src=c.get('attributes',{}).get('src',''),phase='TRANSITION' if aq else 'STATE',formula=f'(or (not {boolof(en)}) {boolof(a)})'))
  return out
 def build(self):
  A=self.formal('$assume');G=self.formal('$assert');acon='(and '+' '.join(x['formula'] for x in A)+')';gcon='(and '+' '.join(x['formula'] for x in G)+')';raw=f'(and {acon} (not {gcon}))';priv=sorted(self.private);F=raw if not priv else '(exists ('+' '.join(f'({x} (_ BitVec 1))' for x in priv)+f') {raw})'
  return {'A':A,'G':G,'challenge':F,'raw_challenge':raw,'public':{s:m for s,m in self.varmeta.items() if s not in self.private},'private':priv}
