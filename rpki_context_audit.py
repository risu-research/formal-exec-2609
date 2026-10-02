#!/usr/bin/env python3
import csv,json,urllib.request,urllib.parse,time,ipaddress
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_confirmatory_fast_20261002'/'transitions.csv'
OUT=BASE/'results'/'rpki_context_audit_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-context-audit/1.0';WORKERS=6

def getj(url,retries=4):
    e=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
        except Exception as x:e=x;time.sleep(min(8,1.3*(2**i)))
    raise RuntimeError(str(e))

def tsv(s):
    if not s:return None
    try:
        d=datetime.fromisoformat(str(s).replace('Z','+00:00'));d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.timestamp()
    except:return None

def covers(q,r):
    try:
        qn=ipaddress.ip_network(q,strict=False);rn=ipaddress.ip_network(r,strict=False)
        return qn.version==rn.version and (qn.subnet_of(rn) or rn.subnet_of(qn))
    except:return False

def audit(r):
    if str(r.get('tail3w','')).lower()!='true':return None
    p=r['prefix']; bt=tsv(r.get('B_median') or r.get('B_first'))
    # transfer endpoint returns complete relevant history; explicitly filter locally by transferTime and resource relation.
    u='https://stat.ripe.net/data/transfer-history/data.json?'+urllib.parse.urlencode({'resource':p,'sourceapp':'risu-rpki-study'})
    tr=getj(u).get('data',{}).get('transfers',[]) or []
    tx=[]
    for x in tr:
        tt=tsv(x.get('transferTime')); sr=x.get('sourceResource') or ''; rr=x.get('recipientResource') or ''
        if tt is None or bt is None or not (covers(p,sr) or covers(p,rr)):continue
        dd=(tt-bt)/86400.0
        tx.append({'transferTime':x.get('transferTime'),'delta_days':dd,'sourceResource':sr,'recipientResource':rr,
                   'sourceRegistry':x.get('sourceRegistry'),'recipientRegistry':x.get('recipientRegistry'),
                   'sourceHolderName':x.get('sourceHolderName'),'recipientHolderName':x.get('recipientHolderName'),
                   'transferType':x.get('transferType')})
    tx.sort(key=lambda x:abs(x['delta_days']))
    near7=sum(abs(x['delta_days'])<=7 for x in tx); near30=sum(abs(x['delta_days'])<=30 for x in tx);near180=sum(abs(x['delta_days'])<=180 for x in tx)
    nearest=tx[0] if tx else None
    # Historical ROA ranges from independent BGPKIT wayback database (date granularity).
    ru='https://api.bgpkit.com/v3/roas/search?'+urllib.parse.urlencode({'prefix':p,'exact':'false','page_size':1000})
    try:rj=getj(ru)
    except Exception as e:rj={'error':repr(e)}
    data=rj.get('data',rj.get('items',[])) if isinstance(rj,dict) else []
    if isinstance(data,dict):data=data.get('data',data.get('items',data.get('results',[])))
    if not isinstance(data,list):data=[]
    A=int(r['A']);B=int(r['B']);aro=[];bro=[]
    for z in data:
        try:a=int(z.get('asn') or z.get('origin_asn') or 0)
        except:a=0
        q=z.get('prefix') or ''
        if not q or not covers(p,q):continue
        obj={'prefix':q,'asn':a,'max_len':z.get('max_len',z.get('maxLength')),'date_ranges':z.get('date_ranges',z.get('dateRanges'))}
        if a==A:aro.append(obj)
        if a==B:bro.append(obj)
    return {'prefix':p,'A':A,'B':B,'B_median':r.get('B_median'),'rrc_n':r.get('rrc_n'),'peer_n':r.get('peer_n'),
            'same_org':r.get('same_org'),'A_reappearing_peer_n':r.get('A_reappearing_peer_n'),
            'transfer_total_relevant':len(tx),'transfer_near7_n':near7,'transfer_near30_n':near30,'transfer_near180_n':near180,
            'nearest_transfer_delta_days':nearest['delta_days'] if nearest else '',
            'nearest_transfer_time':nearest['transferTime'] if nearest else '',
            'nearest_source_resource':nearest['sourceResource'] if nearest else '',
            'nearest_recipient_resource':nearest['recipientResource'] if nearest else '',
            'nearest_source_holder':nearest['sourceHolderName'] if nearest else '',
            'nearest_recipient_holder':nearest['recipientHolderName'] if nearest else '',
            'nearest_transfer_type':nearest['transferType'] if nearest else '',
            'A_roa_history_json':json.dumps(aro,sort_keys=True),'B_roa_history_json':json.dumps(bro,sort_keys=True),
            'bgpkit_roa_records_n':len(data)}

def main():
    rows=list(csv.DictReader(open(INP,encoding='utf8')));tail=[r for r in rows if r.get('tail3w','').lower()=='true']
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(audit,r):r['prefix'] for r in tail}
        for i,f in enumerate(as_completed(fs),1):
            try:
                z=f.result()
                if z:out.append(z)
            except Exception as e:errs.append({'prefix':fs[f],'error':repr(e)})
            print(i,'/',len(fs),fs[f],flush=True)
    out.sort(key=lambda x:x['prefix'])
    if out:
        with open(OUT/'audit.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    s={'tail_n':len(tail),'audited_n':len(out),'transfer_near7':sum(x['transfer_near7_n']>0 for x in out),'transfer_near30':sum(x['transfer_near30_n']>0 for x in out),'transfer_near180':sum(x['transfer_near180_n']>0 for x in out),
       'same_org':sum(str(x['same_org']).lower()=='true' for x in out),'A_recurrence':sum(int(x['A_reappearing_peer_n'] or 0)>0 for x in out),
       'clean_crossorg_no_recurrence_no_transfer30_multi3':sum(str(x['same_org']).lower()!='true' and int(x['A_reappearing_peer_n'] or 0)==0 and x['transfer_near30_n']==0 and int(x['rrc_n'] or 0)>=3 for x in out),
       'errors_n':len(errs),'guardrail':'Transfer evidence requires resource overlap and local comparison of transferTime to exact BGP handoff; API query time filters are not assumed. ROA history is independent date-range corroboration, not sub-day publication timing.'}
    (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2),flush=True)
if __name__=='__main__':main()
