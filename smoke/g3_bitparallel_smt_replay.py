#!/usr/bin/env python3
"""DIAGNOSTIC ONLY.

Independently evaluates every frozen NC1/NC2 divergence SMT2 query under the canonical
witness and under every possible single-bit corruption.  It does not call the G3 TNF
semantic evaluator and does not search for a new model.  Instead, it parses the sealed
SMT2 query and evaluates all complete fixed assignments in parallel with Python integer
bitsets.  Every candidate is hash-bound to the sealed Amendment-07 RED artifact.
"""
from __future__ import annotations
from pathlib import Path
import argparse, collections, hashlib, json


def tokenize(s: str):
    out=[]; i=0; n=len(s)
    while i<n:
        c=s[i]
        if c.isspace(): i+=1; continue
        if c==';':
            j=s.find('\n',i); i=n if j<0 else j+1; continue
        if c in '()': out.append(c); i+=1; continue
        if c=='|':
            j=i+1; buf=[]
            while j<n:
                if s[j]=='\\' and j+1<n:
                    buf.extend((s[j],s[j+1])); j+=2; continue
                if s[j]=='|': break
                buf.append(s[j]); j+=1
            if j>=n: raise RuntimeError('unterminated quoted SMT symbol')
            out.append('|'+''.join(buf)+'|'); i=j+1; continue
        j=i
        while j<n and (not s[j].isspace()) and s[j] not in '();': j+=1
        out.append(s[i:j]); i=j
    return out


def parse_all(ts):
    pos=0
    def one():
        nonlocal pos
        if pos>=len(ts): raise RuntimeError('unexpected SMT EOF')
        t=ts[pos]; pos+=1
        if t=='(':
            a=[]
            while pos<len(ts) and ts[pos]!=')': a.append(one())
            if pos>=len(ts): raise RuntimeError('missing SMT close paren')
            pos+=1; return a
        if t==')': raise RuntimeError('unexpected SMT close paren')
        return t
    out=[]
    while pos<len(ts): out.append(one())
    return out


def Bool(m): return ('B',m)
def BV(bs): return ('V',tuple(bs))
def bm(v):
    if v[0]!='B': raise RuntimeError('expected Bool')
    return v[1]
def vb(v):
    if v[0]!='V': raise RuntimeError('expected BitVec')
    return v[1]


def make_evaluator(nscen, witness, ordered):
    ALL=(1<<nscen)-1; idx={k:i for i,k in enumerate(ordered)}; env={}
    for k,v in witness.items():
        m=ALL if int(v) else 0
        m ^= 1<<idx[k]  # scenario i flips exactly variable i; final scenario is canonical
        env[k]=BV((m,))
    def bc(num,w): return BV(tuple(ALL if ((int(num)>>i)&1) else 0 for i in range(int(w))))
    def bn(x): return x ^ ALL
    def eq(a,b):
        if a[0]!=b[0]: raise RuntimeError('equality sort mismatch')
        if a[0]=='B': return Bool(bn(bm(a)^bm(b)))
        aa,bb=vb(a),vb(b)
        if len(aa)!=len(bb): raise RuntimeError('equality width mismatch')
        m=ALL
        for x,y in zip(aa,bb): m &= bn(x^y)
        return Bool(m)
    def bbin(a,b,op):
        aa,bb=vb(a),vb(b)
        if len(aa)!=len(bb): raise RuntimeError('bitvector width mismatch')
        return BV(tuple(op(x,y)&ALL for x,y in zip(aa,bb)))
    def add(a,b):
        aa,bb=vb(a),vb(b)
        if len(aa)!=len(bb): raise RuntimeError('add width mismatch')
        c=0; oo=[]
        for x,y in zip(aa,bb):
            xy=x^y; oo.append((xy^c)&ALL); c=((x&y)|(c&xy))&ALL
        return BV(oo)
    def neg(a):
        aa=vb(a); return add(BV(tuple(bn(x) for x in aa)),bc(1,len(aa)))
    def mul(a,b):
        aa,bb=vb(a),vb(b); w=len(aa)
        if w!=len(bb): raise RuntimeError('mul width mismatch')
        r=bc(0,w)
        for j,y in enumerate(bb):
            part=[0]*w
            for i,x in enumerate(aa):
                if i+j<w: part[i+j]=x&y
            r=add(r,BV(part))
        return r
    def ule(a,b,strict=False):
        aa,bb=vb(a),vb(b)
        if len(aa)!=len(bb): raise RuntimeError('compare width mismatch')
        same=ALL; lt=0
        for x,y in zip(reversed(aa),reversed(bb)):
            lt |= same & bn(x) & y; same &= bn(x^y)
        return Bool(lt if strict else (lt|same))
    def ite(c,a,b):
        cm=bm(c); nc=bn(cm)
        if a[0]!=b[0]: raise RuntimeError('ite sort mismatch')
        if a[0]=='B': return Bool((cm&bm(a))|(nc&bm(b)))
        aa,bb=vb(a),vb(b)
        if len(aa)!=len(bb): raise RuntimeError('ite width mismatch')
        return BV(tuple((cm&x)|(nc&y) for x,y in zip(aa,bb)))
    def ev(x,local):
        if isinstance(x,str):
            if x=='true': return Bool(ALL)
            if x=='false': return Bool(0)
            if x.startswith('#b'): return bc(int(x[2:],2),len(x)-2)
            if x.startswith('#x'): return bc(int(x[2:],16),4*(len(x)-2))
            if x in local: return local[x]
            if x in env: return env[x]
            raise RuntimeError('unknown SMT atom '+x)
        if not x: raise RuntimeError('empty SMT expression')
        h=x[0]
        if h=='_':
            if len(x)==3 and isinstance(x[1],str) and x[1].startswith('bv'): return bc(int(x[1][2:]),int(x[2]))
            raise RuntimeError('unsupported indexed constant '+repr(x))
        if h=='let':
            vals=[(b[0],ev(b[1],local)) for b in x[1]]  # SMT let bindings are simultaneous
            q=dict(local); q.update(vals); return ev(x[2],q)
        if isinstance(h,list):
            if len(h)==4 and h[0]=='_' and h[1]=='extract':
                hi,lo=int(h[2]),int(h[3]); return BV(vb(ev(x[1],local))[lo:hi+1])
            raise RuntimeError('unsupported indexed operator '+repr(h))
        a=[ev(z,local) for z in x[1:]]
        if h=='not': return Bool(bn(bm(a[0])))
        if h=='and':
            m=ALL
            for q in a: m &= bm(q)
            return Bool(m)
        if h=='or':
            m=0
            for q in a: m |= bm(q)
            return Bool(m&ALL)
        if h=='xor':
            m=0
            for q in a: m ^= bm(q)
            return Bool(m&ALL)
        if h=='=>': return Bool(bn(bm(a[0]))|bm(a[1]))
        if h=='=':
            m=ALL
            for q,r in zip(a,a[1:]): m &= bm(eq(q,r))
            return Bool(m)
        if h=='distinct':
            if len(a)!=2: raise RuntimeError('only binary distinct supported')
            return Bool(bn(bm(eq(a[0],a[1]))))
        if h=='ite': return ite(*a)
        if h=='concat':
            cur=vb(a[-1])
            for q in reversed(a[:-1]): cur=cur+vb(q)
            return BV(cur)
        if h=='bvnot': return BV(tuple(bn(x) for x in vb(a[0])))
        if h=='bvand': return bbin(a[0],a[1],lambda x,y:x&y)
        if h=='bvor': return bbin(a[0],a[1],lambda x,y:x|y)
        if h=='bvxor': return bbin(a[0],a[1],lambda x,y:x^y)
        if h=='bvnand': return BV(tuple(bn(x) for x in vb(bbin(a[0],a[1],lambda x,y:x&y))))
        if h=='bvnor': return BV(tuple(bn(x) for x in vb(bbin(a[0],a[1],lambda x,y:x|y))))
        if h=='bvxnor': return BV(tuple(bn(x) for x in vb(bbin(a[0],a[1],lambda x,y:x^y))))
        if h=='bvadd': return add(a[0],a[1])
        if h=='bvsub': return add(a[0],neg(a[1]))
        if h=='bvmul': return mul(a[0],a[1])
        if h=='bvneg': return neg(a[0])
        if h=='bvule': return ule(a[0],a[1],False)
        if h=='bvult': return ule(a[0],a[1],True)
        if h=='bvuge': return ule(a[1],a[0],False)
        if h=='bvugt': return ule(a[1],a[0],True)
        raise RuntimeError('unsupported SMT operator '+str(h))
    return ev


def query(path):
    forms=parse_all(tokenize(path.read_text())); decl=[]; ass=[]
    for f in forms:
        if not isinstance(f,list) or not f: continue
        if f[0]=='declare-fun': decl.append((f[1],f[3]))
        elif f[0]=='assert': ass.append(f[1])
        elif f[0] in ('set-info','check-sat','set-logic','set-option'): pass
        else: raise RuntimeError('unsupported top-level SMT form '+str(f[0]))
    if len(ass)!=1: raise RuntimeError('expected exactly one assertion')
    return decl,ass[0]


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    root=Path(a.root); rows=[]
    for sp in sorted((root/'shards').glob('SHARD_??.json')):
        x=json.loads(sp.read_text())
        if x.get('schema')!='g3-negative-shard-v2' or x.get('error_count')!=0: raise RuntimeError('bad sealed shard')
        rows += x['records']['nc1'] + x['records']['nc2']
    rows.sort(key=lambda r:(r['lane'],r['candidate_index']))
    if len(rows)!=237: raise RuntimeError('expected 237 SAT candidates')
    if collections.Counter(r['lane'] for r in rows)!={'nc1':102,'nc2':135}: raise RuntimeError('frozen population drift')
    result=[]
    for r in rows:
        if r.get('result')!='SAT': raise RuntimeError('non-SAT frozen candidate')
        w=r['canonical_witness']; ordered=sorted(w); n=len(ordered)
        qp=root/'shards'/r['query_relpath']
        if hashlib.sha256(qp.read_bytes()).hexdigest()!=r['query_sha256']: raise RuntimeError('query hash mismatch')
        decl,expr=query(qp)
        if len(decl)!=len(w) or {n for n,_ in decl}!=set(w): raise RuntimeError('query/witness variable mismatch')
        if any(s!=['_','BitVec','1'] for _,s in decl): raise RuntimeError('unexpected query declaration sort')
        val=make_evaluator(n+1,w,ordered)(expr,{})
        mask=bm(val); canonical=bool((mask>>n)&1)
        if not canonical: raise RuntimeError('canonical witness no longer satisfies frozen divergence query')
        critical=[bit for i,bit in enumerate(ordered) if not ((mask>>i)&1)]
        divergent=[bit for i,bit in enumerate(ordered) if ((mask>>i)&1)]
        co=r['one_bit_corruption']; stored=co['bit']; stored_rejected=stored in critical
        if stored_rejected!=bool(co['rejected']): raise RuntimeError('stored first-bit replay mismatch')
        result.append({
          'lane':r['lane'],'candidate_index':r['candidate_index'],'artifact':r['artifact'],'cell':r['cell'],'query_sha256':r['query_sha256'],
          'witness_bits':n,'canonical_query_true':True,'stored_bit':stored,'stored_rejected':stored_rejected,
          'critical_single_bits':critical,'critical_count':len(critical),'divergent_count':len(divergent),'has_critical':bool(critical),
          'first_critical_bit':critical[0] if critical else None,'first_noncritical_bit':divergent[0] if divergent else None,
        })
    lane={}
    for l in ('nc1','nc2'):
        rr=[r for r in result if r['lane']==l]
        lane[l]={'n':len(rr),'flips':sum(r['witness_bits'] for r in rr),'stored_fail':sum(not r['stored_rejected'] for r in rr),
                 'no_critical':sum(not r['has_critical'] for r in rr),'critical_total':sum(r['critical_count'] for r in rr),
                 'divergent_total':sum(r['divergent_count'] for r in rr)}
    out={'schema':'g3-independent-bitparallel-fixed-replay-v1','authority':'DIAGNOSTIC_ONLY_NON_AUTHORITY',
         'source_red_run':34804261148,'source_red_head':'ccd83335a9b81bc447d9740129668619c4e77267',
         'source_red_artifact_digest':'sha256:adce6b557ecdd0d5d7e21a370cc0e1090c07e2dfbc312dcfca3f619d83576881',
         'total_candidates':len(result),'total_flips':sum(r['witness_bits'] for r in result),
         'stored_rejected':sum(r['stored_rejected'] for r in result),'stored_not_rejected':sum(not r['stored_rejected'] for r in result),
         'has_critical':sum(r['has_critical'] for r in result),'no_critical':sum(not r['has_critical'] for r in result),
         'critical_total':sum(r['critical_count'] for r in result),'divergent_total':sum(r['divergent_count'] for r in result),
         'lane':lane,'records':result}
    if out['total_flips']!=out['critical_total']+out['divergent_total']: raise RuntimeError('global flip partition mismatch')
    Path(a.out).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:out[k] for k in ('total_candidates','total_flips','stored_rejected','stored_not_rejected','has_critical','no_critical','critical_total','divergent_total','lane')},sort_keys=True))

if __name__=='__main__': main()
