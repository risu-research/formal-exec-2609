#!/usr/bin/env python3
import json,re,sys
from pathlib import Path
from z3 import *
BASE=Path(sys.argv[1] if len(sys.argv)>1 else '.')
STEPS=9

def safe(s): return re.sub('[^A-Za-z0-9_]','_',s)
def b1(v): return BitVecVal(v,1)
def bool1(x): return x != b1(0)
def as1(p): return If(p,b1(1),b1(0))

def cat(bits):
    if len(bits)==1:return bits[0]
    return Concat(*reversed(bits))
def ext(x,n,target,signed=False):
    if target==n:return x
    if target<n:return Extract(target-1,0,x)
    return SignExt(target-n,x) if signed else ZeroExt(target-n,x)

class M:
  def __init__(self,side):
    self.side=side
    self.mod=json.loads((BASE/'ir'/f'{side}.json').read_text())['modules']['txuartlite']
    self.cells=self.mod['cells']; self.memo={}; self.busy=set(); self.coord={}; self.qdriver={}; self.regmap={}
    names={}
    preferred=['f_past_valid','baud_counter','f_baud_count','f_bitcount','f_txbits','f_request_tx_data','zero_baud_counter','state','lcl_data','o_uart_tx','o_busy','r_busy']
    for n in preferred:
      if n in self.mod['netnames']:
        for i,b in enumerate(self.mod['netnames'][n]['bits']):
          if isinstance(b,int) and b not in names:names[b]=(n,i)
    for n,p in self.mod['ports'].items():
      if p['direction']=='input':
        for i,b in enumerate(p['bits']): names[b]=(n,i)
    self.names=names; self.stateQ={}
    dffs=[(n,c) for n,c in self.cells.items() if c['type']=='$dff']
    for n,c in dffs:
      for i,(q,d) in enumerate(zip(c['connections']['Q'],c['connections']['D'])):
        self.qdriver[q]=(d,n,i)
        if q in names:self.stateQ[q]=names[q]
    for n,c in self.cells.items():
      if c['type'] not in ('$dff','$assume','$assert','$cover') and 'Y' in c['connections']:
        for i,b in enumerate(c['connections']['Y']): self.regmap[b]=(n,i)
    self.formal={t:sorted([(n,c) for n,c in self.cells.items() if c['type']==t],key=lambda q:q[1].get('attributes',{}).get('src','')) for t in ('$assume','$assert','$cover')}
  def var(self,name):
    if name not in self.coord:self.coord[name]=BitVec(name,1)
    return self.coord[name]
  def v(self,b,t):
    if b=='0':return b1(0)
    if b=='1':return b1(1)
    if b=='x':return self.var(f'X_shared_t{t}_uart_f_txbits_subcount')
    k=(b,t)
    if k in self.memo:return self.memo[k]
    if k in self.busy:raise RuntimeError(('loop',k))
    self.busy.add(k)
    try:
      if b in self.qdriver:
        if b in self.stateQ:
          n,i=self.stateQ[b]; r=self.var(f'V_{safe(n)}_{i}_t{t}')
        elif t>0:
          r=self.v(self.qdriver[b][0],t-1)
        else: raise RuntimeError(('anon dff at t0',self.side,b))
      elif b in self.names:
        n,i=self.names[b];r=self.var(f'V_{safe(n)}_{i}_t{t}')
      elif b in self.regmap:
        n,i=self.regmap[b];r=self.cell(n,t)[i]
      else: raise RuntimeError(('unbound',self.side,b,t))
      self.memo[k]=r
      return r
    finally:
      self.busy.remove(k)
  def cell(self,n,t):
    c=self.cells[n];op=c['type'];cn=c['connections'];P=c.get('parameters',{})
    val=lambda k:cat([self.v(x,t) for x in cn[k]])
    width=lambda k:len(cn[k]); yw=width('Y')
    if op=='$initstate': y=b1(1 if t==0 else 0)
    elif op in ('$eq','$ne','$lt','$le','$gt','$ge'):
      a,b=val('A'),val('B');aw,bw=width('A'),width('B');sgn=int(P.get('A_SIGNED','0'),2)==1 and int(P.get('B_SIGNED','0'),2)==1
      tw=max(aw,bw);a=ext(a,aw,tw,sgn);b=ext(b,bw,tw,sgn)
      q={'$eq':a==b,'$ne':a!=b,'$lt':a<b if sgn else ULT(a,b),'$le':a<=b if sgn else ULE(a,b),'$gt':a>b if sgn else UGT(a,b),'$ge':a>=b if sgn else UGE(a,b)}[op]; y=ext(as1(q),1,yw)
    elif op in ('$logic_and','$logic_or'):
      q=And(bool1(val('A')),bool1(val('B'))) if op=='$logic_and' else Or(bool1(val('A')),bool1(val('B')));y=ext(as1(q),1,yw)
    elif op=='$logic_not': y=ext(as1(Not(bool1(val('A')))),1,yw)
    elif op in ('$reduce_bool','$reduce_or'): y=ext(as1(bool1(val('A'))),1,yw)
    elif op=='$reduce_and':
      a=val('A'); y=ext(as1(a==BitVecVal((1<<width('A'))-1,width('A'))),1,yw)
    elif op=='$mux': y=If(val('S')==b1(1),ext(val('B'),width('B'),yw),ext(val('A'),width('A'),yw))
    elif op in ('$add','$sub'):
      a,b=val('A'),val('B');aw,bw=width('A'),width('B');sgn=int(P.get('A_SIGNED','0'),2)==1 and int(P.get('B_SIGNED','0'),2)==1
      aa,bb=ext(a,aw,yw,sgn),ext(b,bw,yw,sgn);y=aa+bb if op=='$add' else aa-bb
    elif op=='$shiftx':
      a,b=val('A'),val('B');aw,bw=width('A'),width('B'); assert (aw,bw,yw)==(10,4,1),(aw,bw,yw)
      x=self.var(f'X_shared_t{t}_uart_f_txbits_subcount'); y=If(ULT(b,BitVecVal(10,bw)),Extract(0,0,LShR(a,ZeroExt(aw-bw,b))),x)
    else: raise RuntimeError(('unsupported',self.side,op,n))
    return [Extract(i,i,y) for i in range(yw)]
  def form(self,c,t):
    src=c.get('attributes',{}).get('src','')
    obs=t-1 if self.side=='new' and c['type']=='$assert' and 'v:328' in src else t
    return Or(self.v(c['connections']['EN'][0],obs)==b1(0),self.v(c['connections']['A'][0],obs)==b1(1))
  def build(self):
    A=[self.form(c,STEPS) for _,c in self.formal['$assume']];G=[self.form(c,STEPS) for _,c in self.formal['$assert']]
    return A,G,And(*(A+[Not(And(*G))]))

def chk(name,q,outdir):
  s=Solver();s.add(q);r=s.check(); print(name,r)
  p=outdir/f'{name}.smt2';p.write_text(s.to_smt2())
  return str(r)

def main():
  out=BASE/'independent';out.mkdir(exist_ok=True)
  o,n=M('old'),M('new');Ao,Go,Fo=o.build();An,Gn,Fn=n.build()
  assert (len(Ao),len(Go),len(An),len(Gn))==(3,11,3,12)
  pv=o.var('V_f_past_valid_0_t9')==b1(1)
  results={}
  results['loss']=chk('loss',And(pv,Fo,Not(Fn)),out)
  results['gain']=chk('gain',And(pv,Fn,Not(Fo)),out)
  results['old_f']=chk('old_f',And(pv,Fo),out)
  results['new_f']=chk('new_f',And(pv,Fn),out)
  for i in range(3): results[f'A{i}_xor']=chk(f'A{i}_xor',Xor(Ao[i],An[i]),out)
  for i in range(11):
    j=i if i<7 else i+1
    results[f'G{i}_xor_G{j}']=chk(f'G{i}_xor_G{j}',Xor(Go[i],Gn[j]),out)
  results['extra_entailed']=chk('extra_entailed',And(pv,Go[0],Go[4],Not(Gn[7])),out)
  results['extra_unrestricted']=chk('extra_unrestricted',And(And(*Go),Not(Gn[7])),out)
  Gdrop=Go[:5]+Go[6:];Fdrop=And(*(Ao+[Not(And(*Gdrop))]))
  results['positive_loss']=chk('positive_loss',And(pv,Fo,Not(Fdrop)),out)
  artificial=Gn+[n.var('V_o_uart_tx_0_t9')==b1(0)];Fplus=And(*(An+[Not(And(*artificial))]))
  results['positive_gain']=chk('positive_gain',And(pv,Fplus,Not(Fn)),out)
  (out/'results.json').write_text(json.dumps(results,sort_keys=True,indent=2)+'\n')
  expected={'loss':'unsat','gain':'unsat','old_f':'sat','new_f':'sat','extra_entailed':'unsat','extra_unrestricted':'sat','positive_loss':'sat','positive_gain':'sat'}
  for k,v in expected.items(): assert results[k]==v,(k,results[k],v)
  assert all(results[f'A{i}_xor']=='unsat' for i in range(3))
  assert all(results[f'G{i}_xor_G{i if i<7 else i+1}']=='unsat' for i in range(11))
  print('INDEPENDENT_WHOLEF_PASS')
if __name__=='__main__':main()
