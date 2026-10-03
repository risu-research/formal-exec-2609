#!/usr/bin/env python3
import csv, json, math, statistics, time, urllib.parse, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE=Path(__file__).resolve().parent
OUT=BASE/'results'/'rpki_event_definition_sensitivity_v1_20261002'
OUT.mkdir(parents=True,exist_ok=True)
CENSUS=BASE/'results'/'rpki_full_census_fast_closeout_20261002'/'census_weekly_rov.csv'
ARCH=BASE/'results'/'rpki_census_archive_crosscheck_v2_20261002'/'cases.csv'
SEL=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'selection.csv'
EXACT_ALL=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'exact_reconstruction.csv'
EXACT_ROV=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'exact_rov.csv'
UA='risu-rpki-event-sensitivity-v1/1.0 research-contact-moon1002'
WINDOWS=[('direct_only',0),('15m',900),('1h',3600),('6h',21600)]


def rows(p):
    with open(p,newline='',encoding='utf8') as f:return list(csv.DictReader(f))
def key(r):return (r['event_date'],r['representative_prefix'],str(r['A']),str(r['B']))
def truth(x):return str(x).strip().lower()=='true'
def get_json(url,retries=4,timeout=90):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode('utf8','replace'))
        except Exception as e:
            err=e; time.sleep(min(8,1.5*(2**i)))
    raise RuntimeError(str(err))
def to_ts(x):
    if x is None or x=='':return None
    if isinstance(x,(int,float)):return float(x)
    s=str(x).strip()
    try:return float(s)
    except:pass
    try:
        d=datetime.fromisoformat(s.replace('Z','+00:00'))
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except:return None
def origin(path):
    if path is None:return None
    import re
    vals=[]
    if isinstance(path,list):
        for x in path: vals += re.findall(r'\d+',str(x))
    else: vals=re.findall(r'\d+',str(path))
    return int(vals[-1]) if vals else None

def pre_sensitivity():
    c=rows(CENSUS); a=rows(ARCH)
    ad={key(r):truth(r.get('reproduced')) for r in a}
    out=[]
    for t in [3,4,5,6,8,10]:
        s=[r for r in c if int(float(r['pre_stable_obs_min']))>=t]
        inv=[r for r in s if truth(r['weekly_inversion'])]
        rep=sum(ad.get(key(r),False) for r in inv)
        out.append({'pre_min':t,'clusters':len(s),'weekly_inversions':len(inv),
                    'weekly_inversion_rate':len(inv)/len(s) if s else None,
                    'crosssource_reproduced_inversions':rep,
                    'crosssource_rate_over_clusters':rep/len(s) if s else None,
                    'retained_vs_pre3':len(s)/len(c) if c else None})
    return out

def bgplay_eval(r):
    p=r['representative_prefix']; A=int(r['A']); B=int(r['B']); ed=date.fromisoformat(r['event_date'][:10])
    t0=datetime.combine(ed-timedelta(days=7),datetime.min.time(),tzinfo=timezone.utc)
    t1=datetime.combine(ed+timedelta(days=7),datetime.min.time(),tzinfo=timezone.utc)
    q=urllib.parse.urlencode({'resource':p,'starttime':t0.isoformat().replace('+00:00','Z'),
                              'endtime':t1.isoformat().replace('+00:00','Z'),'unix_timestamps':'TRUE',
                              'sourceapp':'risu-rpki-study'})
    data=get_json('https://stat.ripe.net/data/bgplay/data.json?'+q).get('data',{})
    sources={str(s.get('id')):s for s in data.get('sources',[]) or []}
    state={}
    for e in data.get('initial_state',[]) or []:
        a=e.get('attrs',e) or {}; sid=str(a.get('source_id',e.get('source_id',''))); tp=a.get('target_prefix',e.get('target_prefix'))
        if tp==p and sid:state[sid]=origin(a.get('path',e.get('path')))
    last_aw={}; direct_hits=[]; withdraw_candidates=[]
    evs=sorted(data.get('events',[]) or [],key=lambda z:to_ts(z.get('timestamp')) or 0)
    for e in evs:
        t=to_ts(e.get('timestamp')); a=e.get('attrs',{}) or {}; sid=str(a.get('source_id',e.get('source_id',''))); tp=a.get('target_prefix',e.get('target_prefix'))
        if t is None or tp!=p or not sid:continue
        typ=e.get('type') or a.get('type'); old=state.get(sid)
        if typ=='W':
            if old==A:last_aw[sid]=t
            state[sid]=None
        elif typ=='A':
            new=origin(a.get('path',e.get('path')))
            if new is None:continue
            if new==B and old!=B:
                if old==A:
                    direct_hits.append((sid,t,t))
                elif old is None and sid in last_aw and t>=last_aw[sid]:
                    withdraw_candidates.append((sid,last_aw[sid],t,t-last_aw[sid]))
            state[sid]=new
    result={'key':key(r),'prefix':p,'event_date':r['event_date'],'A':A,'B':B}
    for label,sec in WINDOWS:
        cand=list(direct_hits)
        if sec>0:cand += [(sid,aw,bon) for sid,aw,bon,gap in withdraw_candidates if gap<=sec]
        first={}
        for sid,aoff,bon in cand:
            if sid not in first or bon<first[sid][2]:first[sid]=(sid,aoff,bon)
        hs=list(first.values()); rrcs={str(sources.get(sid,{}).get('rrc')) for sid,_,_ in hs if sources.get(sid,{}).get('rrc') is not None}
        bt=sorted(x[2] for x in hs)
        med=bt[(len(bt)-1)//2] if bt else None
        result[label]={'peer_n':len(hs),'rrc_n':len(rrcs),'resolved_ge3':len(rrcs)>=3,
                       'median_ts':med,'median_date':datetime.fromtimestamp(med,timezone.utc).date().isoformat() if med is not None else ''}
    result['withdraw_gap_candidates_n']=len(withdraw_candidates)
    return result

def bgplay_sensitivity():
    sel=rows(SEL); prior=rows(EXACT_ALL); rov=rows(EXACT_ROV)
    prior_d={key(r):r for r in prior}; inv_keys={key(r) for r in rov if r.get('A_rov')=='valid' and r.get('B_rov')=='invalid'}
    out=[]; errors=[]
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut={ex.submit(bgplay_eval,r):r for r in sel}
        for i,f in enumerate(as_completed(fut),1):
            try:out.append(f.result())
            except Exception as e:
                r=fut[f]; errors.append({'event_date':r['event_date'],'representative_prefix':r['representative_prefix'],'A':r['A'],'B':r['B'],'error':repr(e)})
            if i%50==0:print('bgplay',i,'/',len(sel),'errors',len(errors),flush=True)
    # Validate the 1h implementation against frozen reconstruction.
    reproduce=0; comparable=0
    for z in out:
        p=prior_d.get(tuple(z['key']))
        if not p:continue
        comparable+=1
        if int(float(p.get('exact_rrc_n') or 0))==z['1h']['rrc_n']:reproduce+=1
    summaries=[]
    base_resolved={tuple(z['key']) for z in out if z['1h']['resolved_ge3']}
    for label,_ in WINDOWS:
        resolved={tuple(z['key']) for z in out if z[label]['resolved_ge3']}
        retained_inv=len(resolved & inv_keys)
        changed_date=0; overlap=0
        for z in out:
            if z[label]['resolved_ge3'] and z['1h']['resolved_ge3']:
                overlap+=1
                if z[label]['median_date']!=z['1h']['median_date']:changed_date+=1
        summaries.append({'window':label,'resolved_ge3_n':len(resolved),
                          'retained_of_canonical_309':len(resolved & base_resolved),
                          'canonical_41_inversions_still_resolved_n':retained_inv,
                          'canonical_41_retention':retained_inv/len(inv_keys) if inv_keys else None,
                          'resolved_overlap_with_1h_n':overlap,'median_calendar_date_changed_vs_1h_n':changed_date})
    # flatten compact csv
    with open(OUT/'bgplay_cases.json','w',encoding='utf8') as f:json.dump(out,f,indent=2)
    with open(OUT/'bgplay_errors.json','w',encoding='utf8') as f:json.dump(errors,f,indent=2)
    return {'attempted':len(sel),'completed':len(out),'errors':len(errors),
            'one_hour_rrc_count_exact_match_n':reproduce,'one_hour_comparable_n':comparable,
            'windows':summaries}

def main():
    pre=pre_sensitivity(); bg=bgplay_sensitivity()
    summary={'design':{
        'pre_stability':'strictly stronger filters applied to already-frozen 7046 weekly census; no new RPKI selection',
        'bgplay_linkage':'same 419 frozen panel; same BGPlay +/-7d data; only withdrawal-to-B linkage window changes; direct A->B always accepted',
        'guardrail':'This does not test looser pre-stability, alternative post-persistence, or 4/12-week recurrence because those cannot be reconstructed honestly from stored outputs without rerunning the 52 weekly snapshots.'},
        'pre_stability':pre,'bgplay_linkage':bg}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    with open(OUT/'pre_stability.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(pre[0]));w.writeheader();w.writerows(pre)
    with open(OUT/'bgplay_windows.csv','w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(bg['windows'][0]));w.writeheader();w.writerows(bg['windows'])
    print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
