#!/usr/bin/env python3
import csv,json,urllib.request,urllib.parse,time,re,gzip,hashlib
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_retirement_tail_20261002'/'events.csv'
OUT=BASE/'results'/'rpki_updates_state_fast_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-updates-state/1.0'
START='2026-09-01T11:00:00'; END='2026-09-09T12:00:00'; WORKERS=6

def getj(endpoint,params,retries=4):
    url='https://stat.ripe.net/data/'+endpoint+'/data.json?'+urllib.parse.urlencode(params)
    e=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=120) as r:return json.load(r).get('data',{})
        except Exception as x:e=x;time.sleep(min(8,1.2*(2**i)))
    raise RuntimeError(f'{endpoint}: {e}')

def tsv(x):
    if x is None:return None
    if isinstance(x,(int,float)):return float(x)
    try:
        d=datetime.fromisoformat(str(x).replace('Z','+00:00'));d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.timestamp()
    except:return None

def iso(x):return datetime.fromtimestamp(float(x),tz=timezone.utc).isoformat().replace('+00:00','Z') if x is not None else ''

def org(path):
    if not path:return None
    vals=[]
    if isinstance(path,list):
        for x in path:
            m=re.findall(r'\d+',str(x)); vals.extend(m[-1:] if m else [])
    else: vals=re.findall(r'\d+',str(path))
    return int(vals[-1]) if vals else None

def source_rrc(sid):
    s=str(sid or '')
    return s.split('-',1)[0].lstrip('0') or '0' if '-' in s else ''

def load_asorg():
    url='https://data.caida.org/datasets/as-organizations/20260901.as-org2info.txt.gz'
    raw=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=120).read()
    txt=gzip.decompress(raw).decode('utf8','replace'); amap={};names={};mode=''
    for line in txt.splitlines():
        if line.startswith('# format:'):
            if 'aut|changed|aut_name|org_id' in line:mode='as'
            elif 'org_id|changed|org_name' in line:mode='org'
            continue
        if not line or line.startswith('#'):continue
        p=line.split('|')
        if mode=='as' and len(p)>=4 and p[0].isdigit():amap[int(p[0])]=p[3]
        elif mode=='org' and len(p)>=3:names[p[0]]=p[2]
    return amap,names

def initial_state(prefix):
    d=getj('bgp-state',{'resource':prefix,'timestamp':START,'unix_timestamps':'TRUE','sourceapp':'risu-rpki-study'})
    state={}
    for e in d.get('bgp_state',[]) or []:
        if e.get('target_prefix')!=prefix:continue
        sid=str(e.get('source_id',''))
        state[sid]=org(e.get('path'))
    return state

def updates(prefix):
    return getj('bgp-updates',{'resource':prefix,'starttime':START,'endtime':END,'unix_timestamps':'TRUE','sourceapp':'risu-rpki-study','data_overload_limit':'ignore'})

def reconstruct(prefix,A,B):
    state=initial_state(prefix); d=updates(prefix); hits=[];bseen={};arecur=set(); prev_w={}
    ups=sorted(d.get('updates',[]) or [],key=lambda e:tsv(e.get('timestamp')) or 0)
    for e in ups:
        t=tsv(e.get('timestamp')); attrs=e.get('attrs',{}) or {}; sid=str(attrs.get('source_id',e.get('source_id',''))); tp=attrs.get('target_prefix',e.get('target_prefix'))
        if t is None or not sid or tp!=prefix:continue
        old=state.get(sid); typ=e.get('type')
        if typ=='W':
            if old is not None:prev_w[sid]=(t,old)
            state[sid]=None
        elif typ=='A':
            new=org(attrs.get('path',e.get('path')))
            if new is None:continue
            if new==A and sid in bseen and t>bseen[sid]:arecur.add(sid)
            if old!=new and new==B:
                direct=(old==A); w=prev_w.get(sid); via=(old is None and w and w[1]==A and 0<=t-w[0]<=7200)
                if direct or via:
                    hits.append({'source_id':sid,'rrc':source_rrc(sid),'A_off':t if direct else w[0],'B_on':t,'mode':'direct' if direct else 'withdraw_then_B'})
                    bseen[sid]=t
            state[sid]=new
    dd={}
    for h in hits:
        if h['source_id'] not in dd or h['B_on']<dd[h['source_id']]['B_on']:dd[h['source_id']]=h
    hits=list(dd.values()); bt=sorted(h['B_on'] for h in hits); at=sorted(h['A_off'] for h in hits); rrcs=sorted({h['rrc'] for h in hits if h['rrc']!=''})
    med=lambda v:v[len(v)//2] if v else None
    return {'peer_n':len(hits),'rrc_n':len(rrcs),'rrcs':','.join(rrcs),'B_first':iso(bt[0]) if bt else '','B_median':iso(med(bt)) if bt else '','B_last':iso(bt[-1]) if bt else '','B_spread_s':bt[-1]-bt[0] if len(bt)>1 else 0 if bt else '','A_off_median':iso(med(at)) if at else '','A_reappearing_peer_n':len(arecur),'direct_n':sum(h['mode']=='direct' for h in hits),'withdraw_then_B_n':sum(h['mode']!='direct' for h in hits),'nr_updates':d.get('nr_updates')},hits

def one(r,amap,names):
    p=r['prefix'];A=int(r['A']);B=int(r['B']);oa=amap.get(A,'');ob=amap.get(B,'')
    rec=dict(r);rec.update({'tail3w':r['A_20260929']=='valid','orgA_id':oa,'orgB_id':ob,'orgA_name':names.get(oa,''),'orgB_name':names.get(ob,''),'same_org':bool(oa and ob and oa==ob)})
    try:
        sm,hits=reconstruct(p,A,B);rec.update(sm)
        if rec['same_org']:cls='same_org'
        elif int(rec.get('A_reappearing_peer_n') or 0)>0:cls='observed_A_recurrence'
        elif int(rec.get('rrc_n') or 0)>=2:cls='persistent_cross_org_or_unresolved'
        else:cls='unresolved'
        rec['context_class']=cls
        return rec,[{'prefix':p,'A':A,'B':B,**{k:(iso(v) if k in ('A_off','B_on') else v) for k,v in h.items()}} for h in hits],None
    except Exception as e:
        rec.update({'peer_n':0,'rrc_n':0,'context_class':'api_unresolved'});return rec,[],{'prefix':p,'error':repr(e)}

def main():
    inp=list(csv.DictReader(open(INP,encoding='utf8')))
    linger=[r for r in inp if r['A_20260929']=='valid']; other=[r for r in inp if r['A_20260929']!='valid']
    controls=sorted(other,key=lambda r:hashlib.sha256(r['prefix'].encode()).hexdigest())[:18]
    sample=linger+controls;amap,names=load_asorg();out=[];peer=[];errs=[]
    print('sample',len(sample),'tail',len(linger),'controls',len(controls),flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(one,r,amap,names):r['prefix'] for r in sample}
        for i,f in enumerate(as_completed(fs),1):
            try:r,p,e=f.result();out.append(r);peer+=p;errs += [e] if e else []
            except Exception as e:errs.append({'prefix':fs[f],'error':'future '+repr(e)})
            print(i,'/',len(fs),fs[f],flush=True)
    out.sort(key=lambda r:r['prefix'])
    keys=[]
    for r in out:
        for k in r:
            if k not in keys:keys.append(k)
    with open(OUT/'transitions.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(out)
    if peer:
        with open(OUT/'peer_updates.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=list(peer[0]));w.writeheader();w.writerows(peer)
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    def st(g):
        return {'n':len(g),'resolved':sum(int(x.get('rrc_n') or 0)>0 for x in g),'multi_rrc_ge2':sum(int(x.get('rrc_n') or 0)>=2 for x in g),'multi_rrc_ge3':sum(int(x.get('rrc_n') or 0)>=3 for x in g),'same_org':sum(bool(x.get('same_org')) for x in g),'A_recurrence':sum(int(x.get('A_reappearing_peer_n') or 0)>0 for x in g),'classes':dict(Counter(x.get('context_class') for x in g))}
    L=[x for x in out if x.get('tail3w')];C=[x for x in out if not x.get('tail3w')];res=[x for x in out if int(x.get('rrc_n') or 0)>0];sp=sorted(float(x['B_spread_s']) for x in res if x.get('B_spread_s') not in ('',None))
    s={'design':{'method':'independent RIPEstat BGP State at Sep01 11Z + BGP Updates through Sep09 12Z','workers':WORKERS,'tail_cases_all':len(linger),'controls':len(controls),'guardrail':'context_class is an operational-context proxy, not operator intent'},'tail3w':st(L),'controls':st(C),'overall':st(out),'B_spread_s_median':sp[len(sp)//2] if sp else None,'peer_rows':len(peer),'errors_n':len(errs)}
    (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2),flush=True)
if __name__=='__main__':main()
