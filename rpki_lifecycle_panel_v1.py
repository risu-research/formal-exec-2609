#!/usr/bin/env python3
import csv,gzip,hashlib,ipaddress,json,math,random,time,urllib.parse,urllib.request
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timezone,timedelta
from pathlib import Path

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_population_v1_20261002'/'frozen_no_recurrence.csv'
OUT=BASE/'results'/'rpki_lifecycle_panel_v1_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-lifecycle-panel-v1/1.0 research-contact-moon1002'
CUTOFF=date(2026,10,2)
PANEL_CAP=500
BGPLAY_CAP=220
WORKERS=8


def getj(url,retries=4,timeout=90):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
        except Exception as e:
            err=e;time.sleep(min(8,1.4*(2**i)))
    raise RuntimeError(f'{url}: {err}')

def ts(s):
    if not s:return None
    try:
        d=datetime.fromisoformat(str(s).replace('Z','+00:00'));return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except:return None

def iso(t):return datetime.fromtimestamp(float(t),timezone.utc).isoformat().replace('+00:00','Z') if t is not None else ''

def dd(s):return date.fromisoformat(str(s)[:10])

def subnet_authorized(observed,vrp_prefix,maxlen):
    try:
        q=ipaddress.ip_network(observed,strict=False);v=ipaddress.ip_network(vrp_prefix,strict=False)
        mx=int(maxlen) if maxlen not in (None,'') else v.prefixlen
        return q.version==v.version and q.subnet_of(v) and q.prefixlen<=mx
    except:return False

def resource_related(observed,resource):
    try:
        q=ipaddress.ip_network(observed,strict=False)
        # RIR transfer endpoints occasionally return address ranges, not CIDRs.
        if '-' in str(resource):
            a,b=[ipaddress.ip_address(x.strip()) for x in str(resource).split('-',1)]
            return q.version==a.version==b.version and int(a)<=int(q.network_address) and int(q.broadcast_address)<=int(b)
        r=ipaddress.ip_network(resource,strict=False)
        return q.version==r.version and (q.subnet_of(r) or r.subnet_of(q))
    except:return False

def load_as2org():
    url='https://data.caida.org/datasets/as-organizations/20260901.as-org2info.txt.gz'
    raw=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=120).read()
    txt=gzip.decompress(raw).decode('utf8','replace');asm={};org={};mode=None
    for line in txt.splitlines():
        if line.startswith('# format:'):
            mode='org' if 'org_id|changed|org_name' in line else ('as' if 'aut|changed|aut_name|org_id' in line else mode);continue
        if not line or line.startswith('#'):continue
        p=line.split('|')
        if mode=='as' and len(p)>=4 and p[0].isdigit():asm[int(p[0])]=p[3]
        elif mode=='org' and len(p)>=3:org[p[0]]=p[2]
    return asm,org

def roa_history(prefix):
    q=urllib.parse.urlencode({'prefix':prefix,'exact':'false','page_size':1000})
    j=getj('https://api.bgpkit.com/v3/roas/search?'+q)
    data=j.get('data',j.get('items',[])) if isinstance(j,dict) else []
    if isinstance(data,dict):data=data.get('data',data.get('items',data.get('results',[])))
    return data if isinstance(data,list) else []

def auth_intervals(records,prefix,asn):
    out=[]
    for z in records:
        try:a=int(z.get('asn') or z.get('origin_asn') or 0)
        except:a=0
        vp=z.get('prefix') or '';
        if a!=asn or not subnet_authorized(prefix,vp,z.get('max_len',z.get('maxLength'))):continue
        dr=z.get('date_ranges',z.get('dateRanges')) or []
        for x in dr:
            if not isinstance(x,(list,tuple)) or len(x)<2:continue
            try:out.append((dd(x[0]),dd(x[1]),vp,z.get('max_len',z.get('maxLength'))))
            except:pass
    return sorted(out)

def transfer_context(prefix,event_date):
    q=urllib.parse.urlencode({'resource':prefix,'sourceapp':'risu-rpki-study'})
    tr=getj('https://stat.ripe.net/data/transfer-history/data.json?'+q).get('data',{}).get('transfers',[]) or []
    hits=[]
    center=datetime.combine(event_date,datetime.min.time(),tzinfo=timezone.utc).timestamp()
    for x in tr:
        sr=x.get('sourceResource') or '';rr=x.get('recipientResource') or ''
        if not (resource_related(prefix,sr) or resource_related(prefix,rr)):continue
        tt=ts(x.get('transferTime'))
        if tt is None:continue
        delta=(tt-center)/86400
        if abs(delta)<=30:hits.append((delta,x))
    hits.sort(key=lambda x:abs(x[0]));return hits

def deterministic_panel(rows,cap):
    if len(rows)<=cap:return rows,'census'
    # Month-stratified deterministic allocation, preserving rare large clusters by always taking top 2 per month.
    by=defaultdict(list)
    for r in rows:by[r['event_date'][:7]].append(r)
    chosen=[];remaining=[]
    for m,xs in sorted(by.items()):
        xs=sorted(xs,key=lambda r:(-int(r['cluster_prefix_n']),hashlib.sha256((r['event_date']+r['representative_prefix']).encode()).hexdigest()))
        chosen+=xs[:min(2,len(xs))];remaining+=xs[min(2,len(xs)):]
    room=max(0,cap-len(chosen))
    remaining=sorted(remaining,key=lambda r:hashlib.sha256((r['event_date']+'|'+r['representative_prefix']).encode()).hexdigest())
    chosen+=remaining[:room]
    return sorted(chosen,key=lambda r:(r['event_date'],r['representative_prefix'])),'month_stratified_deterministic'

def screen_one(r,asm,org):
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B']);ed=dd(r['event_date'])
    rec=roa_history(p);ai=auth_intervals(rec,p,A);bi=auth_intervals(rec,p,B)
    orgA=asm.get(A,'');orgB=asm.get(B,'');same=bool(orgA and orgB and orgA==orgB)
    try:tx=transfer_context(p,ed)
    except Exception:tx=[]
    b_event=[x for x in bi if x[0]<=ed<=x[1]];a_event=[x for x in ai if x[0]<=ed<=x[1]]
    b_start=min((x[0] for x in b_event),default=None)
    # If B not authorized on event date, keep nearest later start as an activation-boundary candidate.
    b_after=min((x[0] for x in bi if x[0]>ed),default=None)
    # retirement is event->last date old A remains authorized; intervals ending at study cutoff are censored.
    a_after=[x for x in ai if x[1]>=ed]
    a_last=max((x[1] for x in a_after),default=None)
    a_cens=bool(a_last and a_last>=CUTOFF)
    ret_days=((a_last-ed).days if a_last else None)
    if same:ctx='exclude_same_org'
    elif tx:ctx='exclude_transfer_near30'
    else:ctx='clean_context_proxy'
    return {**r,'orgA_id':orgA,'orgB_id':orgB,'orgA_name':org.get(orgA,''),'orgB_name':org.get(orgB,''),
            'same_org':same,'transfer_near30_n':len(tx),'nearest_transfer_delta_days':(tx[0][0] if tx else ''),
            'context_class':ctx,'A_auth_at_event':bool(a_event),'B_auth_at_event':bool(b_event),
            'B_auth_start':b_start.isoformat() if b_start else '','B_next_auth_start':b_after.isoformat() if b_after else '',
            'B_lead_calendar_days':((ed-b_start).days if b_start else ''),
            'A_auth_last_observed_date':a_last.isoformat() if a_last else '',
            'A_retirement_right_censored':a_cens,'A_linger_observed_days':ret_days if ret_days is not None else '',
            'roa_record_n':len(rec)}

def origin_from_path(path):
    if not path:return None
    vals=[]
    if isinstance(path,str):vals=[x for x in path.replace('{',' ').replace('}',' ').replace(',',' ').split() if x.isdigit()]
    elif isinstance(path,list):
        for x in path:
            if isinstance(x,int):vals.append(str(x))
            elif isinstance(x,str):vals += [y for y in x.replace('{',' ').replace('}',' ').replace(',',' ').split() if y.isdigit()]
    return int(vals[-1]) if vals else None

def bgplay_exact(r):
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B']);ed=dd(r['event_date'])
    t0=datetime.combine(ed-timedelta(days=7),datetime.min.time()).isoformat();t1=datetime.combine(ed+timedelta(days=7),datetime.min.time()).isoformat()
    q=urllib.parse.urlencode({'resource':p,'starttime':t0,'endtime':t1,'unix_timestamps':'TRUE','sourceapp':'risu-rpki-study'})
    data=getj('https://stat.ripe.net/data/bgplay/data.json?'+q,timeout=120).get('data',{})
    src={str(s.get('id')):s for s in data.get('sources',[]) or []};state={};lastAoff={};hits=[]
    for e in data.get('initial_state',[]) or []:
        a=e.get('attrs',e);sid=str(a.get('source_id',e.get('source_id','')));tp=a.get('target_prefix',e.get('target_prefix'))
        if tp==p:state[sid]=origin_from_path(a.get('path',e.get('path')))
    evs=sorted(data.get('events',[]) or [],key=lambda e:ts(e.get('timestamp')) or 0)
    for e in evs:
        t=ts(e.get('timestamp'));a=e.get('attrs',{}) or {};sid=str(a.get('source_id',e.get('source_id','')));tp=a.get('target_prefix',e.get('target_prefix'))
        if t is None or tp!=p or not sid:continue
        typ=e.get('type') or a.get('type');old=state.get(sid)
        if typ=='W':
            if old==A:lastAoff[sid]=t
            state[sid]=None
        elif typ=='A':
            new=origin_from_path(a.get('path',e.get('path')))
            if new is None:continue
            if new==B and old!=B:
                direct=(old==A);wa=lastAoff.get(sid);via=(old is None and wa and 0<=t-wa<=3600)
                if direct or via:hits.append((sid,t,t if direct else wa,src.get(sid,{}).get('rrc')))
            state[sid]=new
    first={}
    for h in hits:
        if h[0] not in first or h[1]<first[h[0]][1]:first[h[0]]=h
    h=list(first.values())
    if not h:return {**r,'exact_peer_n':0,'exact_rrc_n':0,'B_route_median':'','A_off_median':'','B_route_spread_s':''}
    bt=sorted(x[1] for x in h);at=sorted(x[2] for x in h);med=lambda x:x[(len(x)-1)//2]
    return {**r,'exact_peer_n':len(h),'exact_rrc_n':len({str(x[3]) for x in h if x[3] is not None}),
            'B_route_median':iso(med(bt)),'A_off_median':iso(med(at)),'B_route_spread_s':bt[-1]-bt[0]}

def qtile(vals,q):
    if not vals:return None
    x=sorted(vals);return x[int(math.ceil(q*(len(x)-1)))]

def main():
    if not INP.exists():
        print('population input not present yet; clean no-op');return
    rows=list(csv.DictReader(open(INP,encoding='utf8')));panel,mode=deterministic_panel(rows,PANEL_CAP)
    asm,org=load_as2org();screen=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(screen_one,r,asm,org):r['representative_prefix'] for r in panel}
        for i,f in enumerate(as_completed(fs),1):
            try:screen.append(f.result())
            except Exception as e:errs.append({'prefix':fs[f],'error':repr(e)})
            if i%25==0:print('screen',i,'/',len(fs),flush=True)
    screen.sort(key=lambda r:(r['event_date'],r['representative_prefix']))
    clean=[r for r in screen if r['context_class']=='clean_context_proxy']
    # Exact BGP first on cases that carry lifecycle information: A authorized at event or B missing/delayed; then deterministic fill.
    ranked=sorted(clean,key=lambda r:(0 if (r['A_auth_at_event'] or not r['B_auth_at_event']) else 1,
                                     hashlib.sha256((r['event_date']+r['representative_prefix']).encode()).hexdigest()))[:BGPLAY_CAP]
    exact=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs={ex.submit(bgplay_exact,r):r['representative_prefix'] for r in ranked}
        for i,f in enumerate(as_completed(fs),1):
            try:exact.append(f.result())
            except Exception as e:errs.append({'prefix':fs[f],'stage':'bgplay','error':repr(e)})
            if i%20==0:print('bgplay',i,'/',len(fs),flush=True)
    exact.sort(key=lambda r:(r['event_date'],r['representative_prefix']))
    exact3=[r for r in exact if int(r.get('exact_rrc_n') or 0)>=3 and r.get('B_route_median')]
    # Date-granular lifecycle distributions are conservative calendar-day bounds.
    leads=[int(r['B_lead_calendar_days']) for r in exact3 if str(r.get('B_lead_calendar_days',''))!='']
    lingers=[int(r['A_linger_observed_days']) for r in exact3 if str(r.get('A_linger_observed_days',''))!='']
    delayed=sum((not r['B_auth_at_event']) and bool(r.get('B_next_auth_start')) for r in exact3)
    cens=sum(str(r.get('A_retirement_right_censored','')).lower()=='true' for r in exact3)
    with open(OUT/'screened_panel.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(screen[0]) if screen else ['event_date']);w.writeheader();w.writerows(screen)
    with open(OUT/'exact_lifecycle.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(exact[0]) if exact else ['event_date']);w.writeheader();w.writerows(exact)
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    s={'population_clusters_input':len(rows),'panel_n':len(panel),'panel_mode':mode,'screened_n':len(screen),
       'context_counts':dict(Counter(r['context_class'] for r in screen)),'clean_context_n':len(clean),
       'exact_attempt_n':len(ranked),'exact_resolved_n':sum(bool(r.get('B_route_median')) for r in exact),
       'exact_multi_rrc_ge3_n':len(exact3),'B_authorized_event_n':sum(bool(r['B_auth_at_event']) for r in exact3),
       'B_authorization_observed_after_event_day_n':delayed,'A_authorized_event_n':sum(bool(r['A_auth_at_event']) for r in exact3),
       'A_retirement_right_censored_n':cens,
       'B_lead_days':{'n':len(leads),'median':qtile(leads,.5),'p10':qtile(leads,.1),'p90':qtile(leads,.9),'p99':qtile(leads,.99)},
       'A_linger_observed_days':{'n':len(lingers),'median':qtile(lingers,.5),'p10':qtile(lingers,.1),'p90':qtile(lingers,.9),'p99':qtile(lingers,.99)},
       'guardrails':['ROA authorization requires observed prefix subnet_of VRP prefix and observed prefix length <= maxLength.',
         'same-organization and RIR transfer within +/-30 calendar days are excluded from clean context proxy; this is not operator-intent ground truth.',
         'ROA history date ranges are date-granular; same-day ordering is not inferred.','A retirement observations ending at the Oct 2 cutoff are right-censored, not withdrawal dates.']}
    (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2),flush=True)
if __name__=='__main__':main()
