#!/usr/bin/env python3
import csv,json,urllib.request,urllib.parse,time,ipaddress,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import defaultdict
from pybgpkit_parser import Parser

BASE=Path(__file__).resolve().parent
AUD=BASE/'results'/'rpki_context_audit_20261002'/'audit.csv'
OUT=BASE/'results'/'rpki_routeviews_crosscheck_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-routeviews-crosscheck/1.0'
COLLECTORS=['route-views2','route-views.eqix','route-views.linx']
TOPN=5


def tsv(s):
    if not s:return None
    d=datetime.fromisoformat(str(s).replace('Z','+00:00'));d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.timestamp()

def iso(t):return datetime.fromtimestamp(float(t),tz=timezone.utc).isoformat().replace('+00:00','Z') if t else ''

def getj(url,retries=4):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
        except Exception as e:err=e;time.sleep(min(8,1.4*(2**i)))
    raise RuntimeError(f'{url}: {err}')

def broker_files(collector,start,end):
    q=urllib.parse.urlencode({'ts_start':iso(start),'ts_end':iso(end),'project':'routeviews','collector_id':collector,'data_type':'updates','page_size':100})
    j=getj('https://api.bgpkit.com/v3/broker/search?'+q)
    data=j.get('data',j.get('items',j.get('results',[]))) if isinstance(j,dict) else []
    if isinstance(data,dict):data=data.get('data',data.get('items',data.get('results',[])))
    if not isinstance(data,list):data=[]
    return [x for x in data if isinstance(x,dict) and x.get('url')]

def origin_from_path(path):
    if not path:return None
    # ignore AS-set ambiguity; accept last plain ASN token only
    toks=re.findall(r'\d+',str(path));return int(toks[-1]) if toks else None

def parse_window(prefix,A,B,collector,center,half_minutes):
    st=center-half_minutes*60;en=center+half_minutes*60
    files=broker_files(collector,st,en)
    rows=[];bpeers=set();apeers=set();withdraws=[]
    firstB=None;lastA=None
    for it in files:
        url=it['url']
        try:
            p=Parser(url=url,filters={'prefix':prefix})
            for batch in p.iter_tuple_batches(['timestamp','elem_type','peer_ip','peer_asn','prefix','as_path'],batch_size=5000):
                for tt,typ,pip,pasn,pfx,path in batch:
                    if pfx!=prefix or tt<st or tt>en:continue
                    typ=str(typ).lower()
                    if typ.startswith('a'):
                        o=origin_from_path(path)
                        if o==B:
                            bpeers.add(str(pip)); firstB=tt if firstB is None or tt<firstB else firstB
                        elif o==A:
                            apeers.add(str(pip)); lastA=tt if lastA is None or tt>lastA else lastA
                    elif typ.startswith('w'):
                        withdraws.append((tt,str(pip)))
        except Exception as e:
            rows.append({'file':url,'error':repr(e)})
    # A withdrawal on a peer followed by B on same collector cannot be mapped without full per-peer state here; keep as supporting metadata.
    return {'collector':collector,'half_window_min':half_minutes,'files_n':len(files),'B_peer_n':len(bpeers),'A_peer_n':len(apeers),'withdrawal_n':len(withdraws),
            'B_first':iso(firstB),'A_last':iso(lastA),'B_first_delta_s':(firstB-center) if firstB is not None else '',
            'corroborated':len(bpeers)>0,'file_errors_n':sum('error' in r for r in rows)}

def check_case(r):
    p=r['prefix'];A=int(r['A']);B=int(r['B']);center=tsv(r['B_median'])
    out=[]
    for c in COLLECTORS:
        z=parse_window(p,A,B,c,center,20)
        if not z['corroborated']:
            z2=parse_window(p,A,B,c,center,120)
            z2['fallback_from_20m']=True;z=z2
        z.update({'prefix':p,'A':A,'B':B,'RIS_B_median':r['B_median'],'RIS_rrc_n':int(r['rrc_n'] or 0),'RIS_peer_n':int(r['peer_n'] or 0)})
        out.append(z)
    return out

def main():
    rows=list(csv.DictReader(open(AUD,encoding='utf8')))
    clean=[]
    for r in rows:
        if str(r['same_org']).lower()=='true':continue
        if int(r['A_reappearing_peer_n'] or 0)>0:continue
        if int(r['transfer_near30_n'] or 0)>0:continue
        if int(r['rrc_n'] or 0)<3 or not r['B_median']:continue
        clean.append(r)
    clean.sort(key=lambda r:(int(r['rrc_n'] or 0),int(r['peer_n'] or 0)),reverse=True)
    sample=clean[:TOPN]
    print('clean_n',len(clean),'sample',[(r['prefix'],r['rrc_n'],r['peer_n']) for r in sample],flush=True)
    allr=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs={ex.submit(check_case,r):r['prefix'] for r in sample}
        for f in as_completed(fs):
            try:allr += f.result()
            except Exception as e:errors.append({'prefix':fs[f],'error':repr(e)})
            print('done',fs[f],flush=True)
    allr.sort(key=lambda x:(x['prefix'],x['collector']))
    if allr:
        with open(OUT/'collector_checks.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=list(allr[0]));w.writeheader();w.writerows(allr)
    (OUT/'errors.json').write_text(json.dumps(errors,indent=2),encoding='utf8')
    by=defaultdict(list)
    for x in allr:by[x['prefix']].append(x)
    cases=[]
    for p,xs in by.items():
        n=sum(x['corroborated'] for x in xs);deltas=[abs(float(x['B_first_delta_s'])) for x in xs if x['B_first_delta_s']!='']
        cases.append({'prefix':p,'routeviews_collectors_corroborated':n,'collectors_tested':len(xs),'max_abs_B_delta_s':max(deltas) if deltas else None,
                      'within_10min_collectors':sum(x['B_first_delta_s']!='' and abs(float(x['B_first_delta_s']))<=600 for x in xs),
                      'within_30min_collectors':sum(x['B_first_delta_s']!='' and abs(float(x['B_first_delta_s']))<=1800 for x in xs)})
    s={'clean_core_n':len(clean),'sample_n':len(sample),'collectors':COLLECTORS,'cases':cases,
       'cases_any_routeviews':sum(c['routeviews_collectors_corroborated']>=1 for c in cases),
       'cases_ge2_routeviews':sum(c['routeviews_collectors_corroborated']>=2 for c in cases),
       'cases_all3_routeviews':sum(c['routeviews_collectors_corroborated']==3 for c in cases),
       'errors_n':len(errors),'guardrail':'RouteViews cross-check corroborates independent-project observation of B announcements near the RIS transition. It does not by itself reconstruct the complete A→B per-peer state transition.'}
    (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2),flush=True)
if __name__=='__main__':main()
