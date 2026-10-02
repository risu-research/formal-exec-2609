#!/usr/bin/env python3
import csv, io, ipaddress, json, lzma, re, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_outcomeblind_exact_v1_20261002'/'exact_reconstruction.csv'
OUT=BASE/'results'/'rpki_outcomeblind_validate_v2_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-outcomeblind-validate-v2/1.0 research-contact-moon1002'
WORKERS=8
MIN_RRCS=3
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']

def get_bytes(url,retries=5,timeout=90):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()
        except Exception as e:
            err=e;time.sleep(min(10,1.5*(2**i)))
    raise RuntimeError(f'{url}: {err}')

def getj(url,retries=5,timeout=60):return json.loads(get_bytes(url,retries,timeout).decode('utf8','replace'))

def norm_status(x):
    if x is None:return None
    s=str(x).strip().lower().replace('-','_').replace(' ','_')
    if s in ('notfound','not_found'):s='unknown'
    if s.startswith('invalid'):return s if s in ('invalid_asn','invalid_length') else 'invalid'
    if s in ('valid','unknown'):return s
    return None

def extract_status(obj):
    if isinstance(obj,dict):
        for k in ('validity','validation','rpki_status','roa_status','route_status','result'):
            if k in obj:
                s=norm_status(obj[k])
                if s:return s
        if 'status' in obj:
            s=norm_status(obj['status'])
            if s:return s
        for k in ('data','payload','results','items'):
            if k in obj:
                s=extract_status(obj[k])
                if s:return s
        for v in obj.values():
            s=extract_status(v)
            if s:return s
    elif isinstance(obj,list):
        for v in obj:
            s=extract_status(v)
            if s:return s
    elif isinstance(obj,str):return norm_status(obj)
    return None

def validate(prefix,asn,day):
    q=urllib.parse.urlencode({'prefix':prefix,'asn':int(asn),'date':day.isoformat()})
    j=getj('https://api.bgpkit.com/v3/roas/validate?'+q)
    s=extract_status(j)
    if not s:raise RuntimeError('unparsed validation response '+json.dumps(j)[:800])
    return s

def classify(r):
    p=r['representative_prefix'];A=int(r['A']);B=int(r['B'])
    ed=datetime.fromisoformat(r['B_route_median'].replace('Z','+00:00')).date()
    # Same validation endpoint and parser used by the prior 185-case policy-consequence run.
    bs=validate(p,B,ed);as_=validate(p,A,ed)
    return {**r,'exact_event_date':ed.isoformat(),'A_rov':as_,'B_rov':bs,'A_valid_B_invalid':as_=='valid' and str(bs).startswith('invalid')}

def subnet_of(observed,vrp_prefix):
    try:
        q=ipaddress.ip_network(observed,strict=False);v=ipaddress.ip_network(vrp_prefix,strict=False)
        return q.version==v.version and q.subnet_of(v)
    except:return False

def matching(observed,origin,vp,asn,mx):
    try:
        q=ipaddress.ip_network(observed,strict=False);v=ipaddress.ip_network(vp,strict=False)
        m=int(mx) if mx not in (None,'') else v.prefixlen
        return q.version==v.version and q.subnet_of(v) and q.prefixlen<=m and int(asn)==int(origin)
    except:return False

def archive_day(ed):
    y,m,d=ed.strftime('%Y'),ed.strftime('%m'),ed.strftime('%d');rows=[]
    for tal in TALS:
        raw=get_bytes(f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz',retries=4,timeout=120)
        text=lzma.decompress(raw).decode('utf8','replace')
        for z in csv.DictReader(io.StringIO(text)):
            vp=(z.get('IP Prefix') or z.get('prefix') or '').strip();aa=(z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
            if not vp or not aa.isdigit():continue
            mx=z.get('Max Length') or z.get('maxLength') or z.get('max_length') or ''
            rows.append((vp,int(aa),mx))
    return rows

def archive_classify(vrps,prefix,origin):
    cov=[z for z in vrps if subnet_of(prefix,z[0])]
    if not cov:return 'unknown'
    return 'valid' if any(matching(prefix,origin,*z) for z in cov) else 'invalid'

def write_csv(p,rows):
    if not rows:p.write_text('',encoding='utf8');return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(p,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def main():
    rows=list(csv.DictReader(open(INP,encoding='utf8')))
    rows=[r for r in rows if int(r.get('exact_rrc_n') or 0)>=MIN_RRCS and r.get('B_route_median')]
    # Exact set is already frozen outcome-blind; no RPKI field participates in this filter.
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fs={ex.submit(classify,r):r['representative_prefix'] for r in rows}
        for i,f in enumerate(as_completed(fs),1):
            try:out.append(f.result())
            except Exception as e:errs.append({'prefix':fs[f],'error':repr(e)})
            if i%25==0 or i==len(fs):print('validate',i,'/',len(fs),'errors',len(errs),flush=True)
    out.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix']))
    write_csv(OUT/'exact_rov_validate.csv',out);(OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    inv=[r for r in out if r['A_valid_B_invalid']]
    ar=[];aerr=[];by={}
    for r in inv:by.setdefault(r['exact_event_date'],[]).append(r)
    for i,(ds,rs) in enumerate(sorted(by.items()),1):
        try:
            vrps=archive_day(date.fromisoformat(ds))
            for r in rs:
                ar.append({'representative_prefix':r['representative_prefix'],'A':r['A'],'B':r['B'],'exact_event_date':ds,
                           'primary_A_rov':r['A_rov'],'primary_B_rov':r['B_rov'],
                           'archive_A_rov':archive_classify(vrps,r['representative_prefix'],int(r['A'])),
                           'archive_B_rov':archive_classify(vrps,r['representative_prefix'],int(r['B'])),'archive_vrp_n':len(vrps)})
        except Exception as e:aerr.append({'date':ds,'case_n':len(rs),'error':repr(e)})
        print('archive',i,'/',len(by),ds,flush=True)
    ar.sort(key=lambda r:(r['exact_event_date'],r['representative_prefix']));write_csv(OUT/'inversion_archive_crosscheck.csv',ar)
    (OUT/'archive_errors.json').write_text(json.dumps(aerr,indent=2),encoding='utf8')
    joint=Counter((r['A_rov'],r['B_rov']) for r in out);repro=sum(r['archive_A_rov']=='valid' and r['archive_B_rov']=='invalid' for r in ar)
    summary={'design':{'input':'frozen outcome-blind exact reconstruction','exact_ge3_n':len(rows),'validator':'same BGPKIT /v3/roas/validate endpoint and parser as prior 185-case run','independent_crosscheck':'RIPE NCC daily validated ROA archive, five trust anchors'},
             'completed_n':len(out),'errors_n':len(errs),'joint_state_matrix':{f'{a}/{b}':n for (a,b),n in sorted(joint.items())},
             'old_valid_new_invalid_n':len(inv),'old_valid_new_invalid_fraction_exact':len(inv)/len(out) if out else None,
             'old_valid_new_invalid_fraction_all419_lower_bound':len(inv)/419,
             'archive_crosscheck_n':len(ar),'archive_reproduced_n':repro,'archive_discrepancy_n':len(ar)-repro,'archive_errors_n':len(aerr),
             'guardrail':'This rerun changes no case selection or exact timing. It replaces only the local history-range classifier with the same historical validation endpoint used in the prior policy-consequence experiment.'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8');print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
