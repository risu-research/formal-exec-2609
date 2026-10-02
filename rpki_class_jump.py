#!/usr/bin/env python3
import csv, json, urllib.request, urllib.parse, time, re, ipaddress, gzip, io
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

BASE = Path(__file__).resolve().parent
INP = BASE/'results'/'rpki_handoff_fast_20261002'/'events.csv'
OUT = BASE/'results'/'rpki_class_jump_20261002'
OUT.mkdir(parents=True, exist_ok=True)
UA='risu-rpki-class-jump/1.0 research-contact-moon1002'
START='2026-09-01T00:00:00'
END='2026-09-30T23:59:59'
MAX_EVENTS=100
BGPLAY_N=60


def get_json(url, retries=4, timeout=90):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':UA, 'Accept':'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            err=e; time.sleep(min(8, 1.5*(2**i)))
    raise RuntimeError(f'GET failed {url}: {err}')


def get_text(url, retries=3, timeout=90):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8','replace')
        except Exception as e:
            err=e; time.sleep(min(8, 1.5*(2**i)))
    raise RuntimeError(f'GET failed {url}: {err}')


def iso_to_ts(x):
    if x is None: return None
    if isinstance(x,(int,float)): return float(x)
    s=str(x).strip()
    if not s: return None
    if re.fullmatch(r'\d+(\.\d+)?',s): return float(s)
    s=s.replace('Z','+00:00')
    try:
        d=datetime.fromisoformat(s)
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def ts_iso(t):
    if t is None: return ''
    return datetime.fromtimestamp(float(t),tz=timezone.utc).isoformat().replace('+00:00','Z')


def overlap(p,q):
    try:
        a=ipaddress.ip_network(p,strict=False); b=ipaddress.ip_network(q,strict=False)
        return a.version==b.version and (a.subnet_of(b) or b.subnet_of(a))
    except: return False


def origin_from_path(path):
    if not path: return None
    if isinstance(path,str):
        vals=re.findall(r'\d+',path)
        return int(vals[-1]) if vals else None
    if isinstance(path,list):
        for x in reversed(path):
            if isinstance(x,int): return x
            if isinstance(x,str):
                vals=re.findall(r'\d+',x)
                if vals: return int(vals[-1])
    return None


def load_as2org():
    url='https://data.caida.org/datasets/as-organizations/20260901.as-org2info.txt.gz'
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    raw=urllib.request.urlopen(req,timeout=120).read()
    text=gzip.decompress(raw).decode('utf-8','replace')
    asmap={}; orgs={}; mode=None
    for line in text.splitlines():
        if line.startswith('# format:'):
            mode='org' if 'org_id|changed|org_name' in line else ('as' if 'aut|changed|aut_name|org_id' in line else mode)
            continue
        if not line or line.startswith('#'): continue
        p=line.split('|')
        if mode=='as' and len(p)>=4 and p[0].isdigit():
            asmap[int(p[0])]=p[3]
        elif mode=='org' and len(p)>=3:
            orgs[p[0]]=p[2]
    return asmap,orgs


def routing_history(prefix):
    q=urllib.parse.urlencode({'resource':prefix,'starttime':START,'endtime':END,'min_peers':10,'normalise_visibility':'true','sourceapp':'risu-rpki-study'})
    j=get_json('https://stat.ripe.net/data/routing-history/data.json?'+q)
    return j.get('data',{})


def timeline_rows(data,prefix):
    rows=[]
    for bo in data.get('by_origin',[]) or []:
        o=bo.get('origin')
        try: origin=int(str(o).split(',')[-1].strip().removeprefix('AS'))
        except: continue
        for pp in bo.get('prefixes',[]) or []:
            if pp.get('prefix')!=prefix: continue
            for tl in pp.get('timelines',[]) or []:
                st=iso_to_ts(tl.get('starttime')); en=iso_to_ts(tl.get('endtime'))
                rows.append({'origin':origin,'start':st,'end':en,'visibility':tl.get('visibility'),'full_peers_seeing':tl.get('full_peers_seeing')})
    return rows


def choose_transition(tls,A,B):
    # Candidate B starts in the Sep1-Sep12 coarse change interval; prefer one followed by long persistence.
    lo=iso_to_ts('2026-09-01T00:00:00Z'); hi=iso_to_ts('2026-09-12T23:59:59Z')
    bs=[r for r in tls if r['origin']==B and r['start'] and lo<=r['start']<=hi]
    if not bs:
        bs=[r for r in tls if r['origin']==B and r['start']]
    if not bs: return None
    bs.sort(key=lambda r: ((r['end'] or iso_to_ts(END))-r['start']), reverse=True)
    b=bs[0]; t=b['start']
    aa=[r for r in tls if r['origin']==A and r['start'] and r['start']<=t+86400]
    a=max(aa,key=lambda r:r['end'] or r['start']) if aa else None
    return {'coarse_B_start':t,'coarse_B_end':b['end'],'coarse_A_end':a['end'] if a else None,
            'B_visibility':b.get('visibility'),'B_full_peers':b.get('full_peers_seeing')}


def bgplay(prefix,t0,t1):
    q=urllib.parse.urlencode({'resource':prefix,'starttime':ts_iso(t0),'endtime':ts_iso(t1),'unix_timestamps':'TRUE','sourceapp':'risu-rpki-study'})
    j=get_json('https://stat.ripe.net/data/bgplay/data.json?'+q,timeout=120)
    return j.get('data',{})


def reconstruct_transition(data,prefix,A,B):
    sources={str(s.get('id')):s for s in (data.get('sources',[]) or [])}
    state={}
    initial=data.get('initial_state',[]) or []
    for e in initial:
        attrs=e.get('attrs',e)
        sid=str(attrs.get('source_id',e.get('source_id','')))
        tp=attrs.get('target_prefix',e.get('target_prefix'))
        if tp!=prefix: continue
        path=attrs.get('path',e.get('path'))
        state[sid]=origin_from_path(path)
    per=[]
    # track per-source exact A->B or withdrawal->B sequences
    prev_change={}
    for ev in sorted(data.get('events',[]) or [], key=lambda x: iso_to_ts(x.get('timestamp')) or 0):
        t=iso_to_ts(ev.get('timestamp'))
        attrs=ev.get('attrs',{}) or {}
        sid=str(attrs.get('source_id',ev.get('source_id','')))
        tp=attrs.get('target_prefix',ev.get('target_prefix'))
        if tp!=prefix or not sid or t is None: continue
        typ=ev.get('type') or attrs.get('type')
        old=state.get(sid)
        if typ=='W':
            if old is not None: prev_change[sid]=(t,old,None,'W')
            state[sid]=None
        elif typ=='A':
            new=origin_from_path(attrs.get('path',ev.get('path')))
            if new is None: continue
            if old!=new:
                # record first observed transition into B; retain whether A was direct predecessor or just withdrawn shortly before
                if new==B:
                    direct=(old==A)
                    w=prev_change.get(sid)
                    via_withdraw=(old is None and w and w[1]==A and 0 <= t-w[0] <= 3600)
                    if direct or via_withdraw:
                        rrc=sources.get(sid,{}).get('rrc')
                        per.append({'source_id':sid,'rrc':rrc,'peer_asn':sources.get(sid,{}).get('as_number'),
                                    'B_first':t,'A_off': (t if direct else w[0]),'mode':'path_change' if direct else 'withdraw_then_B'})
                prev_change[sid]=(t,old,new,'A')
            state[sid]=new
    # de-duplicate source: first transition
    dd={}
    for r in per:
        if r['source_id'] not in dd or r['B_first']<dd[r['source_id']]['B_first']: dd[r['source_id']]=r
    per=list(dd.values())
    if not per: return None,[]
    bts=sorted(r['B_first'] for r in per); ats=sorted(r['A_off'] for r in per)
    def quant(v,q):
        if not v:return None
        return v[int(round((len(v)-1)*q))]
    rrcs=sorted({str(r['rrc']) for r in per if r['rrc'] is not None})
    summary={'peer_transitions':len(per),'distinct_rrcs':len(rrcs),'rrcs':','.join(rrcs),
             'B_first_min':bts[0],'B_first_median':quant(bts,.5),'B_first_max':bts[-1],
             'B_spread_s':bts[-1]-bts[0],
             'A_off_median':quant(ats,.5),
             'path_change_n':sum(r['mode']=='path_change' for r in per),'withdraw_then_B_n':sum(r['mode']=='withdraw_then_B' for r in per)}
    return summary,per


def transfer_history(prefix):
    q=urllib.parse.urlencode({'resource':prefix,'starttime':'2026-01-01T00:00','sourceapp':'risu-rpki-study'})
    try:
        j=get_json('https://stat.ripe.net/data/transfer-history/data.json?'+q,timeout=60)
        return j.get('data',{}).get('transfers',[]) or []
    except Exception:
        return []


def transfer_near(transfers,prefix,center_ts,days=120):
    hits=[]
    for tr in transfers:
        txt=json.dumps(tr,sort_keys=True)
        resources=[]
        for k,v in tr.items():
            if 'resource' in k.lower() and isinstance(v,str): resources.append(v)
        if resources and not any(overlap(prefix,r) for r in resources): continue
        dates=[]
        for k,v in tr.items():
            if 'date' in k.lower() or 'time' in k.lower():
                t=iso_to_ts(v)
                if t: dates.append(t)
        if center_ts and dates and min(abs(t-center_ts) for t in dates) > days*86400: continue
        hits.append(tr)
    return hits


def classify_intent(A,B,orgA,orgB,tls,tcenter,transfer_hits):
    # Evidence-tiered operational-context proxy, not a claim of operator intent.
    if orgA and orgB and orgA==orgB:
        return 'same_org_migration_proxy','high'
    if transfer_hits:
        return 'documented_resource_transfer_proxy','high'
    # recurrence / overlap in Sep after B starts => planned multi-origin/failover plausible
    a_after=[r for r in tls if r['origin']==A and r['start'] and tcenter and r['start']>tcenter+86400]
    if a_after:
        return 'recurrent_or_failover_proxy','medium'
    # persistent B with no A recurrence and distinct orgs
    b_long=[r for r in tls if r['origin']==B and r['start'] and tcenter and r['start']<=tcenter+86400 and (r['end'] or iso_to_ts(END))>=tcenter+14*86400]
    if b_long and orgA and orgB and orgA!=orgB:
        return 'persistent_cross_org_unresolved','medium'
    return 'unresolved','low'


def rpki_daily_status(prefix,asn,date):
    # Independent daily VRP check via RIPE historical archive; returns valid/invalid/notfound.
    y,m,d=date[:4],date[4:6],date[6:8]
    cov=[]
    for tal in ['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']:
        url=f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz'
        try:
            import lzma
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            raw=urllib.request.urlopen(req,timeout=60).read()
            text=lzma.decompress(raw).decode('utf-8','replace')
            rd=csv.DictReader(io.StringIO(text))
            for r in rd:
                p=(r.get('IP Prefix') or r.get('prefix') or '').strip()
                if not p or ':' in p: continue
                try:
                    pn=ipaddress.ip_network(prefix); rn=ipaddress.ip_network(p,strict=False)
                    if not pn.subnet_of(rn): continue
                except: continue
                aa=str(r.get('ASN') or r.get('asn') or '').upper().removeprefix('AS')
                try: mx=int(r.get('Max Length') or r.get('maxLength') or r.get('max_length') or rn.prefixlen)
                except: mx=rn.prefixlen
                cov.append((aa,mx))
        except Exception:
            continue
    if not cov:return 'notfound'
    plen=ipaddress.ip_network(prefix).prefixlen
    return 'valid' if any(a==str(asn) and plen<=mx for a,mx in cov) else 'invalid'


def main():
    rows=list(csv.DictReader(open(INP,encoding='utf-8')))
    rows=rows[:MAX_EVENTS]
    asmap,orgs=load_as2org()
    out=[]; peers=[]; errors=[]
    for i,r in enumerate(rows,1):
        prefix=r['prefix']; A=int(r['old_origin_A']); B=int(r['new_origin_B'])
        try:
            rh=routing_history(prefix); tls=timeline_rows(rh,prefix); tr=choose_transition(tls,A,B)
            if not tr:
                errors.append({'prefix':prefix,'stage':'routing_history','error':'no A->B candidate'})
                continue
            t=tr['coarse_B_start']
            transfers=transfer_history(prefix); th=transfer_near(transfers,prefix,t)
            orgA=asmap.get(A); orgB=asmap.get(B)
            label,conf=classify_intent(A,B,orgA,orgB,tls,t,th)
            rec={'prefix':prefix,'A':A,'B':B,'orgA_id':orgA or '','orgB_id':orgB or '',
                 'orgA_name':orgs.get(orgA,'') if orgA else '','orgB_name':orgs.get(orgB,'') if orgB else '',
                 'intent_proxy':label,'intent_confidence':conf,'transfer_hit_n':len(th),
                 'coarse_B_start':ts_iso(tr['coarse_B_start']),'coarse_A_end':ts_iso(tr['coarse_A_end']),
                 'coarse_B_end':ts_iso(tr['coarse_B_end']),'B_visibility':tr['B_visibility'],'B_full_peers':tr['B_full_peers']}
            if len(out)<BGPLAY_N:
                b0=t-12*3600; b1=t+12*3600
                try:
                    bp=bgplay(prefix,b0,b1); sm,pr=reconstruct_transition(bp,prefix,A,B)
                    if sm:
                        rec.update({k:(ts_iso(v) if k.startswith(('B_first','A_off')) and k!='B_spread_s' else v) for k,v in sm.items()})
                        for x in pr:
                            peers.append({'prefix':prefix,'A':A,'B':B,'source_id':x['source_id'],'rrc':x['rrc'],'peer_asn':x['peer_asn'],
                                          'B_first':ts_iso(x['B_first']),'A_off':ts_iso(x['A_off']),'mode':x['mode']})
                    else:
                        rec.update({'peer_transitions':0,'distinct_rrcs':0,'rrcs':''})
                except Exception as e:
                    errors.append({'prefix':prefix,'stage':'bgplay','error':repr(e)})
            # daily authorization boundary around exact/coarse B transition: two days before..+21d
            day0=datetime.fromtimestamp(t,tz=timezone.utc).date()
            checks=[]
            for off in [-2,-1,0,1,2,7,14,21]:
                dd=day0+timedelta(days=off); ds=dd.strftime('%Y%m%d')
                sa=rpki_daily_status(prefix,A,ds); sb=rpki_daily_status(prefix,B,ds)
                checks.append((off,ds,sa,sb))
            rec['rpki_daily_trace']=';'.join(f'{off}:{ds}:A={sa}:B={sb}' for off,ds,sa,sb in checks)
            # first observed B valid and first observed A non-valid in sampled daily grid
            bvals=[(off,ds) for off,ds,sa,sb in checks if sb=='valid']
            anov=[(off,ds) for off,ds,sa,sb in checks if sa!='valid']
            rec['B_first_valid_sample']=bvals[0][1] if bvals else ''
            rec['A_first_nonvalid_sample']=anov[0][1] if anov else ''
            out.append(rec)
        except Exception as e:
            errors.append({'prefix':prefix,'stage':'main','error':repr(e)})
        print(f'[{i}/{len(rows)}] {prefix} out={len(out)} peers={len(peers)} errors={len(errors)}',flush=True)
        time.sleep(0.05)
    # write outputs
    if out:
        keys=[]
        for r in out:
            for k in r:
                if k not in keys: keys.append(k)
        with open(OUT/'transitions.csv','w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(out)
    if peers:
        with open(OUT/'peer_transitions.csv','w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(peers[0]));w.writeheader();w.writerows(peers)
    (OUT/'errors.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
    c=Counter(r['intent_proxy'] for r in out)
    exact=[r for r in out if int(r.get('peer_transitions') or 0)>0]
    mc=[r for r in exact if int(r.get('distinct_rrcs') or 0)>=2]
    mc3=[r for r in exact if int(r.get('distinct_rrcs') or 0)>=3]
    spreads=[float(r['B_spread_s']) for r in exact if r.get('B_spread_s') not in ('',None)]
    summary={'population_input_n':len(rows),'routing_history_resolved_n':len(out),'bgplay_target_n':min(BGPLAY_N,len(out)),
             'exact_peer_transition_resolved_n':len(exact),'multi_rrc_ge2_n':len(mc),'multi_rrc_ge3_n':len(mc3),
             'intent_proxy_counts':dict(c),'peer_transition_rows':len(peers),'errors_n':len(errors),
             'B_spread_seconds_median': sorted(spreads)[len(spreads)//2] if spreads else None,
             'method_guardrail':'Intent labels are evidence-tiered operational-context proxies, not claims about subjective operator intent. Multi-collector confirmation uses RIS RRCs/peers exposed by RIPEstat BGPlay. Daily RPKI traces interval-censor authorization changes; they are not yet high-frequency RPKI publication timestamps.'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    # probe RPKIViews mirror directory structure to unblock high-frequency phase
    probe={}
    for url in ['https://rpkiviews.kerfuffle.net/rpkidata/2026/09/','https://rpkiviews.kerfuffle.net/rpkidata/rpkispools/2026/09/']:
        try:
            txt=get_text(url); hrefs=re.findall(r'href=["\']([^"\']+)["\']',txt,re.I)
            probe[url]=hrefs[:200]
        except Exception as e: probe[url]={'error':repr(e)}
    (OUT/'rpkiviews_probe.json').write_text(json.dumps(probe,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__': main()
