#!/usr/bin/env python3
"""Amendment-07 pre-use calibration and reachable-primitive audit."""
from __future__ import annotations
import argparse, collections, hashlib, json, re, subprocess
from pathlib import Path
import z3
import g3_negative_controls_v3 as v3

g = v3.g
LABELS = ('natural_old','natural_new','mirror_old','mirror_new')
BASE_SHA = {
 'natural_old':'7d9a87f8033b9d4b70148fc2bb3cfe916931eaa95e9e78f379d447123a53ae74',
 'natural_new':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_old':'81a07443eeb1645218a60dc996ed0c69218c5c48fe1d6a9275d6f7ec0fcc8c05',
 'mirror_new':'1a110a336047e1983decb1af450a126b6904757b3cdacd79fbec419734eb2a51',
}
ORIGINAL_SUPPORTED={'$mux','$logic_and','$logic_or','$logic_not','$reduce_bool','$eq','$ne','$lt','$le','$ge','$gt','$add','$sub','$or','$and','$xor','$xnor','$initstate','$dff'}
AUTHORIZED=ORIGINAL_SUPPORTED|{'$reduce_or'}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def wr(p,x): p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')

def module(o):
    ms=o['modules']; assert len(ms)==1; return next(iter(ms.values()))

def reachable_audit(path):
    o=json.loads(Path(path).read_text());m=module(o);cells=m.get('cells',{});names=collections.defaultdict(list);drivers={};qbits={}
    for n,nn in m.get('netnames',{}).items():
        for j,b in enumerate(nn.get('bits',[])):
            if isinstance(b,int): names[b].append(n)
    for cn,c in cells.items():
        dirs=c.get('port_directions',{})
        if c.get('type')=='$dff':
            for j,b in enumerate(c.get('connections',{}).get('Q',[])):
                if isinstance(b,int): qbits[b]=(cn,c,j)
        for port,bits in c.get('connections',{}).items():
            if dirs.get(port)=='output':
                for j,b in enumerate(bits):
                    if isinstance(b,int): drivers[b]=(cn,c,port,j)
    seen_bits=set();seen_cells=set()
    def bit(b,formal=False):
        key=(b,formal)
        if key in seen_bits:return
        seen_bits.add(key)
        if isinstance(b,str):return
        if b in qbits:
            cn,c,j=qbits[b]; qn=' '.join(names.get(b,[])); is_formal='$formal$' in cn or '$formal$' in qn; is_past='$past$' in cn or '$past$' in qn
            if formal and is_formal: bit(c['connections']['D'][j],False)
            elif is_past: bit(c['connections']['D'][j],False)
            return
        if b not in drivers:return
        cn,c,_,_=drivers[b];cell(cn,c)
    def sig(bits,formal=False):
        for b in bits:bit(b,formal)
    def cell(cn,c):
        if cn in seen_cells:return
        seen_cells.add(cn)
        dirs=c.get('port_directions',{})
        for port,bits in c.get('connections',{}).items():
            if dirs.get(port)=='input':sig(bits,False)
    for _,c in sorted(cells.items()):
        if c.get('type') not in ('$assume','$assert'):continue
        A,E=c['connections']['A'],c['connections']['EN'];aq=isinstance(A[0],int) and A[0] in qbits;eq=isinstance(E[0],int) and E[0] in qbits
        if aq!=eq:raise RuntimeError('mixed formal registration in audit')
        sig(E,eq);sig(A,aq)
    types=collections.Counter(cells[n]['type'] for n in seen_cells)
    ros=[]
    for n in seen_cells:
        c=cells[n]
        if c.get('type')=='$reduce_or':
            par=c.get('parameters',{});ros.append({'cell':n,'A_WIDTH':int(par['A_WIDTH'],2),'Y_WIDTH':int(par['Y_WIDTH'],2),'A_SIGNED':int(par['A_SIGNED'],2),'A_bits':len(c['connections']['A']),'Y_bits':len(c['connections']['Y'])})
    unsupported=sorted(set(types)-AUTHORIZED)
    return {'reachable_cell_count':len(seen_cells),'reachable_types':dict(sorted(types.items())),'unsupported_reachable':unsupported,'reduce_or':sorted(ros,key=lambda x:x['cell'])}

def synthetic_json(path):
    z='0'*32
    data={'creator':'G3 Amendment 07 calibration','modules':{'top':{
      'attributes':{'top':'1'},
      'ports':{'a':{'direction':'input','bits':[2,3,4,5]},'y':{'direction':'output','bits':[6]}},
      'cells':{'reduce':{'hide_name':0,'type':'$reduce_or','parameters':{'A_SIGNED':z,'A_WIDTH':z[:-3]+'100','Y_WIDTH':z[:-1]+'1'},'attributes':{},'port_directions':{'A':'input','Y':'output'},'connections':{'A':[2,3,4,5],'Y':[6]}}},
      'netnames':{'a':{'hide_name':0,'bits':[2,3,4,5],'attributes':{}},'y':{'hide_name':0,'bits':[6],'attributes':{}}}
    }}}
    wr(path,data)

def tnf_value(ir,expr,value):
    subs=[]
    for v in g.vs(expr):
        s=str(v);m=re.fullmatch(r'a\[(\d+)\]',s)
        if not m:raise RuntimeError('unexpected calibration variable '+s)
        i=int(m.group(1));subs.append((v,z3.BitVecVal((value>>i)&1,1)))
    q=z3.simplify(z3.substitute(expr,*subs))
    if not z3.is_bv_value(q):raise RuntimeError('ungrounded TNF calibration '+q.sexpr())
    return q.as_long()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base-dir',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();base=Path(a.base_dir);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    got={k:sha(base/(k+'.json')) for k in LABELS}
    if got!=BASE_SHA:raise RuntimeError('frozen corpus drift')
    audits={k:reachable_audit(base/(k+'.json')) for k in LABELS}
    for k,x in audits.items():
        if x['unsupported_reachable']:raise RuntimeError(k+' unsupported reachable '+repr(x['unsupported_reachable']))
        if len(x['reduce_or'])!=1:raise RuntimeError(k+' reduce_or cardinality '+str(len(x['reduce_or'])))
        r=x['reduce_or'][0]
        if (r['A_WIDTH'],r['Y_WIDTH'],r['A_SIGNED'],r['A_bits'],r['Y_bits'])!=(4,1,0,4,1):raise RuntimeError(k+' reduce_or shape '+repr(r))
    syn=out/'reduce_or_width4.json';synthetic_json(syn);ir=g.IR(str(syn));expr=ir.sig([6],False,'calibration:y')
    rows=[]
    for val in range(16):
        expected=1 if val else 0; tv=tnf_value(ir,expr,val); bits=format(val,'04b')
        cmd=f"read_json {syn}; prep -top top; sat -verify -set a 4'b{bits} -prove y {expected}"
        q=subprocess.run(['yosys','-q','-p',cmd],capture_output=True,text=True,timeout=60)
        row={'input_decimal':val,'input_bits':bits,'expected':expected,'tnf':tv,'yosys_proved_expected':q.returncode==0,'yosys_returncode':q.returncode,'yosys_stderr':q.stderr[-1000:]}
        row['pass']=tv==expected and q.returncode==0;rows.append(row)
    yv=subprocess.run(['yosys','-V'],capture_output=True,text=True,timeout=30);version=(yv.stdout+yv.stderr).strip()
    result={'schema':'g3-amendment07-reduce-or-calibration-v1','base_sha256':got,'reachable_audit':audits,'synthetic_json_sha256':sha(syn),'yosys_version':version,'rows':rows,'pass':len(rows)==16 and all(x['pass'] for x in rows)}
    wr(out/'REDUCE_OR_CALIBRATION.json',result)
    if not result['pass']:raise SystemExit('Amendment 07 reduce_or calibration RED')
    print('G3_AMENDMENT07_REDUCE_OR_CALIBRATION_PASS 16/16')
if __name__=='__main__':main()
