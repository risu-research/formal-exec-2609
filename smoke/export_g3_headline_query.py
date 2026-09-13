#!/usr/bin/env python3
"""Reconstruct the frozen controlled-mirror GAIN query byte-for-byte.

This is a serialization-only public calculator. It does not classify the result.
The exported query is usable only if its SHA-256 equals the previously frozen G2
query digest. Any serializer or source drift therefore fails closed.
"""
from __future__ import annotations
import argparse,hashlib,json,re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

VAR_RE=re.compile(r'\bv_[0-9a-f]{24}\b')
def sha_text(s):return hashlib.sha256(s.encode()).hexdigest()
def safe_symbol(k):return 'v_'+hashlib.sha256(k.encode()).hexdigest()[:24]
def bvc(v,w=1):return f'(_ bv{int(v)} {int(w)})'
def bool_of(e):return e.s if e.kind=='bool' else f'(not (= {e.s} {bvc(0,e.w)}))'
def bv_of(e,w=None,signed=False):
    if e.kind=='bool':s=f'(ite {e.s} #b1 #b0)';ew=1
    else:s=e.s;ew=e.w
    if w is None or ew==w:return s
    if ew<w:return f"((_ {'sign_extend' if signed else 'zero_extend'} {w-ew}) {s})"
    return f'((_ extract {w-1} 0) {s})'
@dataclass(frozen=True)
class E:s:str;w:int=1;kind:str='bv'
class Unsupported(RuntimeError):pass

class IR:
    def __init__(self,path):
        self.path=str(path);raw=Path(path).read_bytes();self.sha256=hashlib.sha256(raw).hexdigest();d=json.loads(raw)
        if len(d.get('modules',{}))!=1:raise Unsupported('expected one flattened module')
        self.module_name,self.m=next(iter(d['modules'].items()));self.names=defaultdict(list);self.portbits={};self.drivers={};self.qbits={};self.cache={};self.bitcache={};self.varmeta={};self.private=set()
        for pn,p in self.m.get('ports',{}).items():
            bits=p.get('bits',[])
            for j,b in enumerate(bits):
                if isinstance(b,int):self.portbits[b]=(pn,j,len(bits),p.get('direction',''))
        for n,nn in self.m.get('netnames',{}).items():
            attrs=nn.get('attributes',{})
            for j,b in enumerate(nn.get('bits',[])):
                if isinstance(b,int):self.names[b].append((n,j,len(nn['bits']),attrs.get('src','')))
        for cn,c in self.m.get('cells',{}).items():
            t=c.get('type');dirs=c.get('port_directions',{})
            if t in ('$dff','$adff','$sdff','$dffe','$sdffe'):
                for j,b in enumerate(c.get('connections',{}).get('Q',[])):
                    if isinstance(b,int):self.qbits[b]=(cn,c,j)
            for p,bits in c.get('connections',{}).items():
                if dirs.get(p)=='output':
                    for j,b in enumerate(bits):
                        if isinstance(b,int):self.drivers[b]=(cn,c,p,j)
        if any(c.get('type') in ('$live','$fair') for c in self.m.get('cells',{}).values()):raise Unsupported('liveness/fairness')
    def _var(self,key,meta,private=False):
        sym=safe_symbol(key);self.varmeta.setdefault(sym,{**meta,'key':key,'symbol':sym})
        if private:self.private.add(sym)
        return E(sym)
    def _alpha_private(self,text):
        seen={}
        def repl(m):
            s=m.group(0)
            if s not in self.private:return s
            if s not in seen:seen[s]=f'PX{len(seen)}'
            return seen[s]
        return VAR_RE.sub(repl,text)
    def _leaf(self,b):
        if b in self.portbits:
            n,j,w,d=self.portbits[b];return self._var(f'PORT::{n}[{j}]',{'category':'PORT','name':n,'bit':j,'width':w,'direction':d,'native_bit':b})
        cand=[]
        for n,j,w,src in self.names.get(b,[]):
            nn=n.replace('\\','');gen=nn.startswith('$') or '.$' in nn;cand.append((1 if gen else 0,len(nn),nn,j,w,src))
        if cand:
            _,_,n,j,w,src=min(cand);return self._var(f'NET::{n}[{j}]',{'category':'NET','name':n,'bit':j,'width':w,'src':src,'native_bit':b})
        return self._var(f'ANON::{b}',{'category':'ANON','bit_id':b})
    def bit(self,b,formal_boundary=False,ctx=''):
        if isinstance(b,str):
            if b=='0':return E('#b0')
            if b=='1':return E('#b1')
            if b in ('x','z'):return self._var('PRIVATE_X::'+hashlib.sha256(ctx.encode()).hexdigest(),{'category':'PRIVATE_X','origin':ctx},True)
            raise Unsupported('unknown logic')
        k=(b,formal_boundary)
        if k in self.bitcache:return self.bitcache[k]
        if b in self.qbits:
            cn,c,j=self.qbits[b];qnames=' '.join(n for n,_,_,_ in self.names.get(b,[]));is_formal='$formal$' in cn or '$formal$' in qnames;is_past='$past$' in cn or '$past$' in qnames
            if formal_boundary and is_formal:out=self.bit(c['connections']['D'][j],False,cn+f':D{j}')
            elif is_past:
                de=self.bit(c['connections']['D'][j],False,cn+f':PAST_D{j}');normalized=self._alpha_private(de.s);fp=sha_text(normalized)[:24];aliases=sorted((n,jj,w,src) for n,jj,w,src in self.names.get(b,[]));out=self._var('PAST::'+fp,{'category':'PAST','update_sha256':sha_text(normalized),'native_bit':b,'native_aliases':aliases})
            else:out=self._leaf(b)
            self.bitcache[k]=out;return out
        if b not in self.drivers:out=self._leaf(b);self.bitcache[k]=out;return out
        cn,c,p,j=self.drivers[b];v=self.cell(cn,c)
        if v.kind=='bool':out=E(f'(ite {v.s} #b1 #b0)')
        elif v.w==1:out=v
        else:out=E(f'((_ extract {j} {j}) {v.s})')
        self.bitcache[k]=out;return out
    def sig(self,bits,formal_boundary=False,ctx=''):
        xs=[self.bit(b,formal_boundary,ctx+f':{i}') for i,b in enumerate(bits)]
        if not xs:return E('#b0')
        if len(xs)==1:return xs[0]
        return E('(concat '+' '.join(x.s for x in reversed(xs))+')',len(xs))
    def cell(self,cn,c):
        if cn in self.cache:return self.cache[cn]
        t,con,par=c['type'],c.get('connections',{}),c.get('parameters',{})
        def S(p):return self.sig(con[p],False,cn+':'+p)
        def P(n,default='0'):
            v=par.get(n,default)
            try:return int(v,2) if isinstance(v,str) and set(v)<=set('01') else int(v)
            except:return 0
        yw=len(con.get('Y',[])) or len(con.get('Q',[])) or 1
        if t=='$mux':out=E(f"(ite {bool_of(S('S'))} {bv_of(S('B'),yw)} {bv_of(S('A'),yw)})",yw)
        elif t in ('$logic_and','$logic_or'):
            op='and' if t=='$logic_and' else 'or';out=E(f"(ite ({op} {bool_of(S('A'))} {bool_of(S('B'))}) #b1 #b0)")
        elif t in ('$logic_not','$reduce_bool'):
            q=f"(not {bool_of(S('A'))})" if t=='$logic_not' else bool_of(S('A'));out=E(f'(ite {q} #b1 #b0)')
        elif t in ('$reduce_or','$reduce_and','$reduce_xor','$reduce_xnor'):
            a=S('A');bits=[f'(= ((_ extract {i} {i}) {bv_of(a)}) #b1)' for i in range(a.w)]
            if t=='$reduce_or':q='false' if not bits else '(or '+' '.join(bits)+')'
            elif t=='$reduce_and':q='true' if not bits else '(and '+' '.join(bits)+')'
            else:
                q='false'
                for x in bits:q=f'(xor {q} {x})'
                if t=='$reduce_xnor':q=f'(not {q})'
            out=E(f'(ite {q} #b1 #b0)')
        elif t in ('$eq','$ne','$eqx','$nex'):
            a,b=S('A'),S('B');w=max(a.w,b.w);q=f'(= {bv_of(a,w)} {bv_of(b,w)})';q=f'(not {q})' if t in ('$ne','$nex') else q;out=E(f'(ite {q} #b1 #b0)')
        elif t in ('$lt','$le','$gt','$ge'):
            if P('A_SIGNED') or P('B_SIGNED'):raise Unsupported('signed compare')
            a,b=S('A'),S('B');w=max(a.w,b.w);op={'$lt':'bvult','$le':'bvule','$gt':'bvugt','$ge':'bvuge'}[t];out=E(f'(ite ({op} {bv_of(a,w)} {bv_of(b,w)}) #b1 #b0)')
        elif t in ('$add','$sub','$mul'):
            op={'$add':'bvadd','$sub':'bvsub','$mul':'bvmul'}[t];out=E(f"({op} {bv_of(S('A'),yw)} {bv_of(S('B'),yw)})",yw)
        elif t in ('$and','$or','$xor','$xnor'):
            a,b=bv_of(S('A'),yw),bv_of(S('B'),yw)
            if t=='$xnor':out=E(f'(bvnot (bvxor {a} {b}))',yw)
            else:op={'$and':'bvand','$or':'bvor','$xor':'bvxor'}[t];out=E(f'({op} {a} {b})',yw)
        elif t in ('$not','$pos','$neg'):
            a=bv_of(S('A'),yw);out=E(a,yw) if t=='$pos' else E(f"({'bvnot' if t=='$not' else 'bvneg'} {a})",yw)
        elif t in ('$shl','$shr','$sshl','$sshr'):
            if t=='$sshr' and P('A_SIGNED'):op='bvashr'
            else:op='bvshl' if t in ('$shl','$sshl') else 'bvlshr'
            out=E(f"({op} {bv_of(S('A'),yw)} {bv_of(S('B'),yw)})",yw)
        elif t=='$initstate':out=self._var('INITSTATE',{'category':'INITSTATE'})
        elif t in ('$dff','$adff','$sdff','$dffe','$sdffe'):
            xs=[self._leaf(b) if isinstance(b,int) else self.bit(b) for b in con.get('Q',[])];out=xs[0] if len(xs)==1 else E('(concat '+' '.join(x.s for x in reversed(xs))+')',len(xs))
        else:raise Unsupported('unsupported cell '+t)
        self.cache[cn]=out;return out
    def formal(self,kind):
        out=[]
        for cn,c in self.m.get('cells',{}).items():
            if c.get('type')!=kind:continue
            con=c.get('connections',{});a0=con.get('A',['?'])[0];e0=con.get('EN',['?'])[0];aq=isinstance(a0,int) and a0 in self.qbits;eq=isinstance(e0,int) and e0 in self.qbits
            if aq!=eq:raise Unsupported('mixed formal registration')
            a=self.sig(con['A'],aq,cn+':A');en=self.sig(con['EN'],eq,cn+':EN');out.append(f'(or (not {bool_of(en)}) {bool_of(a)})')
        return out
    def build(self):
        A=self.formal('$assume');G=self.formal('$assert');acon='true' if not A else '(and '+' '.join(A)+')';gcon='true' if not G else '(and '+' '.join(G)+')';raw=f'(and {acon} (not {gcon}))';qvars=sorted(self.private);challenge=raw if not qvars else '(exists ('+' '.join(f'({x} (_ BitVec 1))' for x in qvars)+f') {raw})';public=sorted(s for s in self.varmeta if s not in self.private);return challenge,public

def main():
    ap=argparse.ArgumentParser();ap.add_argument('base');ap.add_argument('mutant');ap.add_argument('out');ns=ap.parse_args()
    base=IR(ns.base);mut=IR(ns.mutant)
    if base.sha256!='81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05':raise SystemExit('base hash drift')
    if mut.sha256!='1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51':raise SystemExit('mutant hash drift')
    old,ov=base.build();new,nv=mut.build();q=f'(and {new} (not {old}))';h=sha_text(q);expected='eee29444ffd94478a71d7a076e255d2d8066ec0a6790d75560bec6f5402592ba'
    if h!=expected:raise SystemExit(f'query hash drift {h}')
    union=sorted(set(ov)|set(nv));text='(set-logic ALL)\n'+'\n'.join(f'(declare-fun {s} () (_ BitVec 1))' for s in union)+'\n(assert '+q+')\n(check-sat)\n'
    Path(ns.out).write_text(text)
    print(json.dumps({'schema':'g3-exact-headline-query-v1','query':'controlled_mirror_GAIN','query_sha256':h,'smt2_sha256':sha_text(text),'declarations':len(union),'private_old':len(base.private),'private_new':len(mut.private)},sort_keys=True))
if __name__=='__main__':main()
