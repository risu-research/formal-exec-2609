#!/usr/bin/env python3
"""ProofScope transition-normal-form comparator for flattened Yosys JSON.

Scientific rules:
- Classification is over whole assumption conjunctions, never line/pair matching.
- Clocked formal monitor registers are normalized by their D update, so the object
  compared is the obligation posted by the current transition (TNF), not a guessed
  PREV/source reconstruction.
- Ordinary design registers remain state. Generated $past registers are given a
  semantic state identity derived from the update function they store.
- Unsupported/mixed formal encodings fail closed.
"""
import argparse, hashlib, json
from collections import Counter, defaultdict
import z3


def _pi(v):
    if isinstance(v, int): return v
    try: return int(v, 2)
    except Exception: return int(v)

def _resize(x, w, signed=False):
    if z3.is_bool(x): x = z3.If(x, z3.BitVecVal(1,1), z3.BitVecVal(0,1))
    if x.size() == w: return x
    if x.size() < w: return z3.SignExt(w-x.size(), x) if signed else z3.ZeroExt(w-x.size(), x)
    return z3.Extract(w-1, 0, x)

def _nz(x):
    return x if z3.is_bool(x) else x != z3.BitVecVal(0, x.size())

def _stable_name(n):
    return n.replace('\\', '')

class IR:
    def __init__(self, path):
        self.path = path
        raw = open(path, 'rb').read()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        if len(data.get('modules', {})) != 1:
            raise RuntimeError(f'{path}: expected exactly one flattened module')
        self.module_name, self.m = next(iter(data['modules'].items()))
        self.names = defaultdict(list)
        self.drivers, self.qbits = {}, {}
        for n, nn in self.m.get('netnames', {}).items():
            for j,b in enumerate(nn.get('bits', [])):
                if isinstance(b, int): self.names[b].append((n,j,len(nn['bits'])))
        for cn,c in self.m.get('cells', {}).items():
            dirs = c.get('port_directions', {})
            if c.get('type') == '$dff':
                for j,b in enumerate(c['connections']['Q']):
                    if isinstance(b,int): self.qbits[b] = (cn,c,j)
            for p,bits in c.get('connections', {}).items():
                if dirs.get(p) == 'output':
                    for j,b in enumerate(bits):
                        if isinstance(b,int): self.drivers[b] = (cn,c,p,j)
        self.vars, self.bit_cache, self.cell_cache = {}, {}, {}
        self.unsupported = set()
        self.mixed_formal = []

    def _leaf_key(self, b):
        cand=[]
        for n,j,w in self.names.get(b, []):
            nn=_stable_name(n)
            generated = nn.startswith('$') or '.$' in nn
            cand.append((1 if generated else 0, len(nn), nn, j, w))
        if not cand: return f'__bit_{b}'
        _,_,n,j,_ = min(cand)
        return f'{n}[{j}]'

    def _var(self, key):
        if key not in self.vars: self.vars[key] = z3.BitVec(key, 1)
        return self.vars[key]

    def bit(self, b, formal_boundary=False, ctx=''):
        if isinstance(b,str):
            if b == '0': return z3.BitVecVal(0,1)
            if b == '1': return z3.BitVecVal(1,1)
            if b in ('x','z'):
                return self._var('X_' + hashlib.sha256(ctx.encode()).hexdigest()[:16])
            raise RuntimeError(f'unknown constant {b}')
        key=(b,formal_boundary)
        if key in self.bit_cache: return self.bit_cache[key]
        if b in self.qbits:
            cn,c,j = self.qbits[b]
            qnames = ' '.join(n for n,_,_ in self.names.get(b, []))
            is_formal = '$formal$' in cn or '$formal$' in qnames
            is_past = '$past$' in cn or '$past$' in qnames
            if formal_boundary and is_formal:
                out = self.bit(c['connections']['D'][j], False, cn+f':D{j}')
            elif is_past:
                de = z3.simplify(self.bit(c['connections']['D'][j], False, cn+f':PAST_D{j}'))
                fp = hashlib.sha256(de.sexpr().encode()).hexdigest()[:24]
                out = self._var('PAST_' + fp)
            else:
                out = self._var(self._leaf_key(b))
            self.bit_cache[key]=out; return out
        if b not in self.drivers:
            out=self._var(self._leaf_key(b)); self.bit_cache[key]=out; return out
        cn,c,p,j = self.drivers[b]
        v=self.cell(cn,c)
        out = z3.If(v,z3.BitVecVal(1,1),z3.BitVecVal(0,1)) if z3.is_bool(v) else z3.Extract(j,j,v)
        self.bit_cache[key]=out; return out

    def sig(self, bits, formal_boundary=False, ctx=''):
        xs=[self.bit(b,formal_boundary,ctx+f':{i}') for i,b in enumerate(bits)]
        return xs[0] if len(xs)==1 else z3.Concat(*reversed(xs))

    def cell(self, cn, c):
        if cn in self.cell_cache: return self.cell_cache[cn]
        t, con, par = c['type'], c['connections'], c.get('parameters', {})
        def S(p): return self.sig(con[p], False, cn+':'+p)
        yw=len(con.get('Y',[])) or len(con.get('Q',[]))
        try:
            if t=='$mux': out=z3.If(_nz(S('S')), _resize(S('B'),yw), _resize(S('A'),yw))
            elif t=='$logic_and': out=z3.If(z3.And(_nz(S('A')),_nz(S('B'))),z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t=='$logic_or': out=z3.If(z3.Or(_nz(S('A')),_nz(S('B'))),z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t=='$logic_not': out=z3.If(z3.Not(_nz(S('A'))),z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t=='$reduce_bool': out=z3.If(_nz(S('A')),z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t in ('$eq','$ne'):
                a,b=S('A'),S('B'); w=max(a.size(),b.size()); q=_resize(a,w)==_resize(b,w)
                if t=='$ne': q=z3.Not(q)
                out=z3.If(q,z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t in ('$lt','$le','$ge','$gt'):
                if _pi(par.get('A_SIGNED','0')) or _pi(par.get('B_SIGNED','0')): raise NotImplementedError('signed compare')
                a,b=S('A'),S('B'); w=max(a.size(),b.size()); a,b=_resize(a,w),_resize(b,w)
                q={'$lt':z3.ULT(a,b),'$le':z3.ULE(a,b),'$ge':z3.UGE(a,b),'$gt':z3.UGT(a,b)}[t]
                out=z3.If(q,z3.BitVecVal(1,1),z3.BitVecVal(0,1))
            elif t in ('$add','$sub'):
                a,b=_resize(S('A'),yw),_resize(S('B'),yw); out=_resize(a+b if t=='$add' else a-b,yw)
            elif t in ('$or','$and','$xor','$xnor'):
                a,b=_resize(S('A'),yw),_resize(S('B'),yw)
                out={'$or':a|b,'$and':a&b,'$xor':a^b,'$xnor':~(a^b)}[t]; out=_resize(out,yw)
            elif t=='$initstate': out=self._var('INITSTATE')
            elif t=='$dff':
                xs=[self._var(self._leaf_key(b)) if isinstance(b,int) else self.bit(b) for b in con['Q']]
                out=xs[0] if len(xs)==1 else z3.Concat(*reversed(xs))
            else: raise NotImplementedError(t)
        except Exception:
            self.unsupported.add(t); raise
        self.cell_cache[cn]=z3.simplify(out); return self.cell_cache[cn]

    def assumptions(self):
        out=[]
        for cn,c in self.m.get('cells', {}).items():
            if c.get('type') != '$assume': continue
            a0,e0=c['connections']['A'][0],c['connections']['EN'][0]
            aq,eq=a0 in self.qbits,e0 in self.qbits
            if aq != eq:
                self.mixed_formal.append(cn); raise RuntimeError(f'{self.path}: mixed registered/combinational formal cell {cn}')
            phase='TRANSITION' if aq and eq else 'STATE'
            a=self.sig(c['connections']['A'], phase=='TRANSITION', cn+':A')
            en=self.sig(c['connections']['EN'], phase=='TRANSITION', cn+':EN')
            f=z3.simplify(z3.Or(z3.Not(_nz(en)), _nz(a)))
            out.append({'cell':cn,'src':c.get('attributes',{}).get('src',''),'phase':phase,'f':f})
        return out

def _check(s, timeout_ms):
    s.set(timeout=timeout_ms); r=s.check()
    if r==z3.unknown: raise RuntimeError('solver returned unknown: '+s.reason_unknown())
    return r

def _relation(f0,f1,timeout_ms):
    s=z3.Solver(); s.add(f0,z3.Not(f1)); a=_check(s,timeout_ms)
    s=z3.Solver(); s.add(f1,z3.Not(f0)); b=_check(s,timeout_ms)
    if a==z3.unsat and b==z3.unsat: rel='EQ'
    elif a==z3.sat and b==z3.unsat: rel='CONTRACT'
    elif a==z3.unsat and b==z3.sat: rel='EXPAND'
    elif a==z3.sat and b==z3.sat: rel='INCOMPARABLE'
    else: raise RuntimeError('unexpected solver result')
    return rel, a==z3.sat, b==z3.sat

def analyze(old_path,new_path,timeout_ms=10000):
    old,new=IR(old_path),IR(new_path); A,B=old.assumptions(),new.assumptions()
    if old.unsupported or new.unsupported: raise RuntimeError(f'unsupported cells: {old.unsupported|new.unsupported}')
    F0=z3.And(*[x['f'] for x in A]) if A else z3.BoolVal(True)
    F1=z3.And(*[x['f'] for x in B]) if B else z3.BoolVal(True)
    rel,old_only,new_only=_relation(F0,F1,timeout_ms)
    old_blame=[]
    for i,x in enumerate(B):
        s=z3.Solver(); s.add(F0,z3.Not(x['f']))
        if _check(s,timeout_ms)==z3.sat: old_blame.append({'index':i,'src':x['src'],'phase':x['phase']})
    new_blame=[]
    for i,x in enumerate(A):
        s=z3.Solver(); s.add(F1,z3.Not(x['f']))
        if _check(s,timeout_ms)==z3.sat: new_blame.append({'index':i,'src':x['src'],'phase':x['phase']})
    def isolated(sourceF,target,blame):
        ans=[]; ids=[b['index'] for b in blame]
        for b in blame:
            s=z3.Solver(); s.add(sourceF,z3.Not(target[b['index']]['f']))
            for j in ids:
                if j!=b['index']: s.add(target[j]['f'])
            ans.append({'index':b['index'],'isolated_sat':_check(s,timeout_ms)==z3.sat})
        return ans
    return {
      'schema':'proofscope-tnf-v1',
      'old':{'file':old_path,'sha256':old.sha256,'assumptions':len(A),'phases':dict(Counter(x['phase'] for x in A))},
      'new':{'file':new_path,'sha256':new.sha256,'assumptions':len(B),'phases':dict(Counter(x['phase'] for x in B))},
      'relation':rel,'old_only_sat':old_only,'new_only_sat':new_only,
      'old_only_blame':old_blame,'new_only_blame':new_blame,
      'old_only_isolation':isolated(F0,B,old_blame),'new_only_isolation':isolated(F1,A,new_blame),
      'unsupported':sorted(old.unsupported|new.unsupported),
      'method':{'pair_matching_used':False,'formal_clocked_normal_form':'D_EN => D_CHECK','generated_past_identity':'sha256(update-function)'},
    }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('old'); ap.add_argument('new'); ap.add_argument('--expect'); ap.add_argument('--timeout-ms',type=int,default=10000)
    ns=ap.parse_args(); r=analyze(ns.old,ns.new,ns.timeout_ms); print(json.dumps(r,indent=2,sort_keys=True))
    if ns.expect and r['relation']!=ns.expect: raise SystemExit(f"expected {ns.expect}, got {r['relation']}")
    if not all(x['isolated_sat'] for x in r['old_only_isolation']+r['new_only_isolation']): raise SystemExit('non-isolable blame basis')
if __name__=='__main__': main()
