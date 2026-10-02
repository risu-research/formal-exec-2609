#!/usr/bin/env python3
import csv,gzip,hashlib,json,re,shutil,time,urllib.request
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path

BASE=Path(__file__).resolve().parent
DATA=BASE/'_rpki_population_cache'
OUT=BASE/'results'/'rpki_population_v1_20261002'
DATA.mkdir(exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-population-v1/1.0'
START=date(2025,10,6); END=date(2026,9,28)
PRE_STABLE_OBS=3          # A seen unchanged at event-2w,event-1w plus preceding state => >=14d lower bound
B_CONFIRM_OBS=5           # event week + four following weekly observations => >=28d persistence lower bound
RECURRENCE_WEEKS=8        # mark if A reappears within eight weekly checkpoints after event


def get(url,retries=4):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=120) as r:return r.read()
        except Exception as e:
            err=e;time.sleep(min(8,2**i))
    raise RuntimeError(f'{url}: {err}')

def month_inventory(y,m):
    key=f'{y:04d}{m:02d}'; cache=DATA/f'index-{key}.html'
    if not cache.exists():
        u=f'https://publicdata.caida.org/datasets/routing/routeviews-prefix2as/{y:04d}/{m:02d}/'
        cache.write_bytes(get(u))
    txt=cache.read_text(errors='replace')
    pat=re.compile(r'(routeviews-rv2-(\d{8})-(\d{4})\.pfx2as\.gz)')
    out=defaultdict(list)
    for fn,ds,hhmm in pat.findall(txt):out[ds].append((hhmm,fn))
    return out

def choose_files():
    inv={};d=START;rows=[]
    while d<=END:
        ym=(d.year,d.month)
        if ym not in inv:inv[ym]=month_inventory(*ym)
        ds=d.strftime('%Y%m%d'); cand=inv[ym].get(ds,[])
        if not cand:raise RuntimeError(f'no CAIDA pfx2as files for {ds}')
        hh,fn=min(cand,key=lambda x:abs(int(x[0][:2])*60+int(x[0][2:])-12*60))
        rows.append((d,fn));d+=timedelta(days=7)
    return rows

def fetch_snapshot(d,fn):
    dest=DATA/fn
    if not dest.exists() or not dest.stat().st_size:
        url=f'https://publicdata.caida.org/datasets/routing/routeviews-prefix2as/{d.year:04d}/{d.month:02d}/{fn}'
        tmp=dest.with_suffix(dest.suffix+'.part')
        req=urllib.request.Request(url,headers={'User-Agent':UA})
        with urllib.request.urlopen(req,timeout=180) as r,open(tmp,'wb') as f:shutil.copyfileobj(r,f,1024*1024)
        tmp.replace(dest)
    return dest

def iter_snapshot(path):
    with gzip.open(path,'rt',encoding='utf8',errors='replace') as g:
        for line in g:
            if not line or line.startswith('#'):continue
            p=line.rstrip('\n').split('\t')
            if len(p)<3:continue
            addr,plen,asn=p[0],p[1],p[2].strip()
            if ':' in addr or not asn.isdigit():continue
            try:pl=int(plen)
            except:continue
            if not 8<=pl<=32:continue
            yield f'{addr}/{pl}',int(asn)

def main():
    files=choose_files();
    manifest=[{'week_i':i,'date':d.isoformat(),'file':fn} for i,(d,fn) in enumerate(files)]
    state={}  # prefix -> [asn,stable,last_seen,pending]
    events=[] # mutable dicts; recurrence is updated on later weeks
    live_events=defaultdict(list)
    raw_change_n=0; qualified_change_n=0
    for i,(d,fn) in enumerate(files):
        f=fetch_snapshot(d,fn);seen_pending=set();seen_n=0
        for p,asn in iter_snapshot(f):
            seen_n+=1
            s=state.get(p)
            if s is None:
                state[p]=[asn,1,i,None];continue
            old,stable,last_seen,pending=s
            consecutive=(last_seen==i-1)
            # update recurrence flags on already-confirmed events for this prefix
            if p in live_events:
                keep=[]
                for ev in live_events[p]:
                    age=i-ev['event_i']
                    if 0<age<=RECURRENCE_WEEKS and asn==ev['A']:
                        ev['A_recurrence_within_8w']=True;ev['A_recurrence_week_i']=i
                    if age<RECURRENCE_WEEKS:keep.append(ev)
                live_events[p]=keep
            if pending is not None:
                if consecutive and asn==pending['B']:
                    pending['confirm_obs']+=1;seen_pending.add(p)
                    if pending['confirm_obs']>=B_CONFIRM_OBS:
                        pending['confirmed_i']=i;pending['confirmed_date']=d.isoformat();pending['A_recurrence_within_8w']=False;pending['A_recurrence_week_i']=''
                        events.append(pending);live_events[p].append(pending);pending=None
                else:
                    pending=None
            if consecutive and asn==old:
                stable+=1
            elif consecutive and asn!=old:
                raw_change_n+=1
                if stable>=PRE_STABLE_OBS:
                    qualified_change_n+=1
                    pending={'prefix':p,'A':old,'B':asn,'event_i':i,'event_date':d.isoformat(),'pre_stable_obs':stable,'confirm_obs':1}
                    seen_pending.add(p)
                stable=1
            else:
                pending=None;stable=1
            state[p]=[asn,stable,i,pending]
        # pending cases not observed this week automatically fail next time because last_seen gap; prune very stale state monthly
        if i%8==7:
            cutoff=i-8
            stale=[p for p,s in state.items() if s[2]<cutoff and not live_events.get(p)]
            for p in stale:state.pop(p,None)
        print(i,d,fn,'rows',seen_n,'state',len(state),'events',len(events),flush=True)
    # cluster correlated prefixes by transition week and A->B pair
    clusters=defaultdict(list)
    for ev in events:clusters[(ev['event_i'],ev['A'],ev['B'])].append(ev)
    rows=[]
    for (ei,A,B),es in clusters.items():
        rep=min(es,key=lambda e:hashlib.sha256(e['prefix'].encode()).hexdigest())
        rows.append({'event_i':ei,'event_date':rep['event_date'],'A':A,'B':B,
                     'cluster_prefix_n':len(es),'representative_prefix':rep['prefix'],
                     'pre_stable_obs_min':min(e['pre_stable_obs'] for e in es),
                     'B_confirm_obs_min':min(e['confirm_obs'] for e in es),
                     'A_recurrence_within_8w':any(e['A_recurrence_within_8w'] for e in es),
                     'recurrence_prefix_n':sum(e['A_recurrence_within_8w'] for e in es)})
    rows.sort(key=lambda r:(r['event_date'],r['A'],r['B']))
    frozen=[r for r in rows if not r['A_recurrence_within_8w']]
    with open(OUT/'event_clusters.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['event_i']);w.writeheader();w.writerows(rows)
    with open(OUT/'frozen_no_recurrence.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(frozen[0]) if frozen else list(rows[0]));w.writeheader();w.writerows(frozen)
    summary={'window':{'start':START.isoformat(),'end':END.isoformat(),'weekly_snapshots':len(files)},
             'frozen_rules':{'address_family':'IPv4','snapshot_route':'exact-prefix single-origin numeric ASN only',
               'pre_stability':f'>={PRE_STABLE_OBS} consecutive weekly observations under A before change',
               'post_persistence':f'B at event plus >={B_CONFIRM_OBS-1} further consecutive weekly observations (>=28d lower bound)',
               'unit':'cluster all qualifying prefixes sharing transition week and A->B pair; deterministic representative prefix',
               'recurrence':'flag/exclude if old A is observed again at a weekly checkpoint within 8 weeks'},
             'counts':{'raw_single_origin_changes':raw_change_n,'prestable_changes':qualified_change_n,
               'confirmed_prefix_events':len(events),'event_clusters':len(rows),'clusters_no_A_recurrence_8w':len(frozen),
               'clusters_with_A_recurrence_8w':len(rows)-len(frozen)},
             'guardrail':'This enumerator detects persistent observed single-origin replacements in weekly RouteViews rv2 snapshots. It does not by itself establish transfer intent, stale authorization, or global visibility.'}
    (OUT/'snapshot_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
