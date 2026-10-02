#!/usr/bin/env python3
import csv,io,ipaddress,json,lzma,time,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

BASE=Path(__file__).resolve().parent
CASES=BASE/'results'/'rpki_policy_consequence_v1_20261002'/'cases.csv'
OUT=BASE/'results'/'rpki_ripe_archive_crosscheck_20261002';OUT.mkdir(parents=True,exist_ok=True)
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']
UA='risu-rpki-ripe-independent-crosscheck/1.0'

def fetch(url,retries=4):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=90) as r:return r.read()
        except Exception as e:
            err=e;time.sleep(min(8,1.5*(2**i)))
    raise err

def field(row,names):
    m={k.strip().lower():v for k,v in row.items() if k is not None}
    for n in names:
        if n in m:return m[n]
    for k,v in m.items():
        if any(n in k for n in names):return v
    return None

def scan_one(args):
    ds,pfx,tal=args
    y,m,d=ds.split('-')
    url=f'https://ftp.ripe.net/ripe/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz'
    try:
        raw=fetch(url)
    except Exception as e:
        return {'tal':tal,'url':url,'error':repr(e),'covering':[]}
    try:
        txt=lzma.decompress(raw).decode('utf8','replace')
    except Exception as e:
        return {'tal':tal,'url':url,'error':'decompress '+repr(e),'covering':[]}
    reader=csv.DictReader(io.StringIO(txt))
    route=ipaddress.ip_network(pfx,strict=False)
    cov=[]
    header=reader.fieldnames
    for row in reader:
        ps=field(row,['ip prefix','prefix','ip_prefix'])
        if not ps:continue
        try:vnet=ipaddress.ip_network(str(ps).strip(),strict=False)
        except:continue
        if vnet.version!=route.version or not route.subnet_of(vnet):continue
        a=field(row,['asn','as'])
        ml=field(row,['max length','maxlength','max_length'])
        uri=field(row,['uri'])
        try:asn=int(str(a).upper().replace('AS','').strip())
        except:continue
        try:maxlen=int(str(ml).strip()) if str(ml or '').strip() else vnet.prefixlen
        except:maxlen=vnet.prefixlen
        cov.append({'tal':tal,'vrp_prefix':str(vnet),'asn':asn,'max_length':maxlen,'uri':uri or ''})
    return {'tal':tal,'url':url,'header':header,'covering':cov,'compressed_bytes':len(raw)}

def classify(route_prefix,origin,vrps):
    route=ipaddress.ip_network(route_prefix,strict=False)
    if not vrps:return 'unknown'
    for v in vrps:
        if int(v['asn'])==int(origin) and route.prefixlen<=int(v['max_length']):return 'valid'
    return 'invalid'

def main():
    rows=list(csv.DictReader(open(CASES,encoding='utf8')))
    inv=[r for r in rows if r.get('A_valid_B_invalid','').lower()=='true']
    tasks=[]
    for r in inv:
        for tal in TALS:tasks.append((r['exact_event_date'],r['representative_prefix'],tal))
    by={}
    errs=[]
    with ThreadPoolExecutor(max_workers=10) as ex:
        fs={ex.submit(scan_one,t):t for t in tasks}
        for i,f in enumerate(as_completed(fs),1):
            ds,pfx,tal=fs[f]
            try:z=f.result()
            except Exception as e:z={'tal':tal,'error':repr(e),'covering':[]}
            by.setdefault((ds,pfx),[]).append(z)
            if z.get('error'):errs.append({'date':ds,'prefix':pfx,'tal':tal,'error':z['error']})
            if i%10==0:print('archive',i,'/',len(tasks),flush=True)
    out=[]
    for r in inv:
        k=(r['exact_event_date'],r['representative_prefix'])
        scans=by.get(k,[])
        vrps=[v for s in scans for v in s.get('covering',[])]
        a=classify(r['representative_prefix'],r['A'],vrps)
        b=classify(r['representative_prefix'],r['B'],vrps)
        rec={
          'representative_prefix':r['representative_prefix'],'A':int(r['A']),'B':int(r['B']),'exact_event_date':r['exact_event_date'],
          'bgpkit_A':r['A_validation_event'],'bgpkit_B':r['B_validation_event'],
          'ripe_A':a,'ripe_B':b,'ripe_policy_inversion':a=='valid' and b=='invalid',
          'agreement_A':a==r['A_validation_event'],'agreement_B':b==r['B_validation_event'],
          'covering_vrp_n':len(vrps),'covering_vrps':vrps,
          'available_tals':[s['tal'] for s in scans if not s.get('error')],
          'missing_tals':[s['tal'] for s in scans if s.get('error')]
        }
        out.append(rec)
    summary={
      'headline_input_inversions_n':len(inv),'cases_crosschecked_n':len(out),
      'full_5_tal_archive_available_n':sum(len(z['available_tals'])==5 for z in out),
      'ripe_reproduced_policy_inversion_n':sum(z['ripe_policy_inversion'] for z in out),
      'ripe_reproduced_policy_inversion_fraction':sum(z['ripe_policy_inversion'] for z in out)/len(out) if out else None,
      'A_status_agreement_n':sum(z['agreement_A'] for z in out),'B_status_agreement_n':sum(z['agreement_B'] for z in out),
      'both_status_agreement_n':sum(z['agreement_A'] and z['agreement_B'] for z in out),
      'download_errors_n':len(errs),
      'guardrail':'Independent reconstruction from RIPE NCC daily validated VRP CSVs across five RIR trust anchors. A route is Valid if any covering VRP matches origin ASN and permits route prefix length; Invalid if covered but no VRP validates it; Unknown if no covering VRP is present.'
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    (OUT/'cases.json').write_text(json.dumps(out,indent=2),encoding='utf8')
    (OUT/'download_errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
