#!/usr/bin/env python3
import argparse,csv,io,ipaddress,json,lzma,time,urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_full_census_fast_closeout_20261002'/'census_weekly_rov.csv'
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']
UA='risu-rpki-census-archive-crosscheck-v2/1.0 research-contact-moon1002'

def get_bytes(url,retries=5,timeout=180):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()
        except Exception as e:
            err=e;time.sleep(min(15,1.5*(2**i)))
    raise RuntimeError(f'{url}: {err}')

def all_supernets(p):
    n=ipaddress.ip_network(p,strict=False)
    return {str(n.supernet(new_prefix=k)) if k<n.prefixlen else str(n) for k in range(0,n.prefixlen+1)}

def scan_tal(ds,tal,wanted):
    y,m,d=ds[:4],ds[5:7],ds[8:10]
    raw=get_bytes(f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz')
    text=lzma.decompress(raw).decode('utf8','replace')
    hits=[];total=0
    for z in csv.DictReader(io.StringIO(text)):
        total+=1
        vp=(z.get('IP Prefix') or z.get('prefix') or '').strip()
        if vp not in wanted:continue
        aa=(z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
        if not aa.isdigit():continue
        try:
            n=ipaddress.ip_network(vp,strict=False)
            mx=z.get('Max Length') or z.get('maxLength') or z.get('max_length') or n.prefixlen
            hits.append((vp,int(aa),int(mx)))
        except:pass
    return hits,total

def classify(cov,prefix,asn):
    q=ipaddress.ip_network(prefix,strict=False); cover=[]
    for vp,a,mx in cov:
        try:
            v=ipaddress.ip_network(vp,strict=False)
            if q.subnet_of(v):cover.append((a,mx))
        except:pass
    if not cover:return 'unknown'
    return 'valid' if any(a==int(asn) and q.prefixlen<=mx for a,mx in cover) else 'invalid'

def write_csv(path,rows):
    if not rows:path.write_text('',encoding='utf8');return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(path,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,default=8);a=ap.parse_args()
    rows=[r for r in csv.DictReader(open(INP,encoding='utf8')) if str(r['weekly_inversion']).lower()=='true']
    bydate=defaultdict(list)
    for r in rows:bydate[r['event_date'][:10]].append(r)
    dates=sorted(bydate); chosen=[ds for i,ds in enumerate(dates) if i%a.shard_count==a.shard_index]
    out=[];errs=[]
    for j,ds in enumerate(chosen,1):
        rs=bydate[ds];wanted=set()
        for r in rs:wanted.update(all_supernets(r['representative_prefix']))
        cov=[];total=0
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                fs={ex.submit(scan_tal,ds,tal,wanted):tal for tal in TALS}
                for f in as_completed(fs):
                    h,n=f.result();cov.extend(h);total+=n
            for r in rs:
                aa=classify(cov,r['representative_prefix'],r['A']);bb=classify(cov,r['representative_prefix'],r['B'])
                out.append({'event_date':ds,'representative_prefix':r['representative_prefix'],'A':r['A'],'B':r['B'],'primary_A':r['A_rov_weekly'],'primary_B':r['B_rov_weekly'],'archive_A':aa,'archive_B':bb,'reproduced':aa=='valid' and bb=='invalid','matched_covering_vrps_n':sum(ipaddress.ip_network(r['representative_prefix']).subnet_of(ipaddress.ip_network(vp)) for vp,_,_ in cov),'archive_rows_scanned_n':total})
        except Exception as e:errs.append({'event_date':ds,'case_n':len(rs),'error':repr(e)})
        print('shard',a.shard_index,'date',j,'/',len(chosen),ds,'cases',len(rs),'errors',len(errs),flush=True)
    write_csv(Path(f'/tmp/rpki_census_archive_{a.shard_index:02d}.csv'),out)
    Path(f'/tmp/rpki_census_archive_{a.shard_index:02d}_errors.json').write_text(json.dumps(errs,indent=2))
    print(json.dumps({'shard':a.shard_index,'dates':len(chosen),'cases':len(out),'errors':len(errs)},indent=2))
if __name__=='__main__':main()
