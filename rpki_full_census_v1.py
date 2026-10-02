#!/usr/bin/env python3
import argparse,csv,gzip,hashlib,ipaddress,io,json,lzma,math,re,time,urllib.parse,urllib.request
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
from pathlib import Path

BASE=Path(__file__).resolve().parent
POP=BASE/'results'/'rpki_population_v1_20261002'/'frozen_no_recurrence.csv'
EXACT=BASE/'results'/'rpki_outcomeblind_validate_v2_20261002'/'exact_rov_validate.csv'
OUT=BASE/'results'/'rpki_full_census_v1_20261002'
UA='risu-rpki-full-census-v1/1.0 research-contact-moon1002'
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']

def get_bytes(url,retries=6,timeout=120):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()
        except Exception as e:
            err=e;time.sleep(min(20,1.5*(2**i)))
    raise RuntimeError(f'GET failed {url}: {err}')

def getj(url,retries=6,timeout=90):return json.loads(get_bytes(url,retries,timeout).decode('utf8','replace'))

def norm_status(x):
    if x is None:return None
    s=str(x).strip().lower().replace('-','_').replace(' ','_')
    if s in ('notfound','not_found'):s='unknown'
    if s.startswith('invalid'):return 'invalid'
    if s in ('valid','unknown'):return s
    return None

def extract_status(obj):
    if isinstance(obj,dict):
        for k in ('validity','validation','rpki_status','roa_status','route_status','result','status'):
            if k in obj:
                s=norm_status(obj[k])
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

def validate(prefix,asn,ds):
    q=urllib.parse.urlencode({'prefix':prefix,'asn':int(asn),'date':ds})
    j=getj('https://api.bgpkit.com/v3/roas/validate?'+q)
    s=extract_status(j)
    if not s:raise RuntimeError('unparsed validation response '+json.dumps(j)[:500])
    return s

def load_population():
    rows=list(csv.DictReader(open(POP,encoding='utf8')))
    rows.sort(key=lambda r:(r['event_date'],r['representative_prefix'],int(r['A']),int(r['B'])))
    return rows

def classify_one(r):
    a=validate(r['representative_prefix'],r['A'],r['event_date'][:10])
    b=validate(r['representative_prefix'],r['B'],r['event_date'][:10])
    return {**r,'A_rov_weekly':a,'B_rov_weekly':b,'weekly_inversion':a=='valid' and b=='invalid'}

def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    if not rows:path.write_text('',encoding='utf8');return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with open(path,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def run_shard(si,sn,workers):
    rows=load_population();sel=[r for i,r in enumerate(rows) if i%sn==si]
    out=[];errs=[]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs={ex.submit(classify_one,r):r for r in sel}
        for i,f in enumerate(as_completed(fs),1):
            r=fs[f]
            try:out.append(f.result())
            except Exception as e:errs.append({'representative_prefix':r['representative_prefix'],'event_date':r['event_date'],'A':r['A'],'B':r['B'],'error':repr(e)})
            if i%100==0 or i==len(fs):print('shard',si,i,'/',len(fs),'errors',len(errs),flush=True)
    out.sort(key=lambda r:(r['event_date'],r['representative_prefix'],int(r['A']),int(r['B'])))
    write_csv(Path(f'/tmp/rpki_census_shard_{si:02d}.csv'),out)
    Path(f'/tmp/rpki_census_shard_{si:02d}_errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
    print(json.dumps({'shard':si,'shard_count':sn,'selected':len(sel),'completed':len(out),'errors':len(errs)},indent=2))

def ipv4_key(p):
    n=ipaddress.ip_network(p,strict=False);return int(n.network_address),n.prefixlen

def load_archive_index(ds):
    y,m,d=ds[:4],ds[5:7],ds[8:10];idx=defaultdict(list);total=0
    for tal in TALS:
        raw=get_bytes(f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz',retries=5,timeout=180)
        text=lzma.decompress(raw).decode('utf8','replace')
        for z in csv.DictReader(io.StringIO(text)):
            p=(z.get('IP Prefix') or z.get('prefix') or '').strip()
            a=(z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
            if not p or not a.isdigit():continue
            try:
                n=ipaddress.ip_network(p,strict=False)
                if n.version!=4:continue
                mx=z.get('Max Length') or z.get('maxLength') or z.get('max_length') or n.prefixlen
                idx[(n.prefixlen,int(n.network_address))].append((int(a),int(mx)))
                total+=1
            except:pass
    return idx,total

def archive_classify(idx,prefix,asn):
    q=ipaddress.ip_network(prefix,strict=False);x=int(q.network_address);ql=q.prefixlen;cover=[]
    for plen in range(ql,-1,-1):
        mask=(0xffffffff << (32-plen)) & 0xffffffff if plen else 0
        cover.extend(idx.get((plen,x&mask),[]))
    if not cover:return 'unknown'
    return 'valid' if any(a==int(asn) and ql<=mx for a,mx in cover) else 'invalid'

def cluster_bin(n):
    n=int(n)
    if n==1:return '1'
    if n<=4:return '2-4'
    if n<=9:return '5-9'
    return '10+'

def frac(a,b):return a/b if b else None

def run_aggregate(parts_root):
    pop=load_population();expected={(r['event_date'],r['representative_prefix'],r['A'],r['B']) for r in pop}
    parts=[]
    for p in sorted(Path(parts_root).glob('rpki_census_shard_*.csv')):
        parts.extend(csv.DictReader(open(p,encoding='utf8')))
    parts.sort(key=lambda r:(r['event_date'],r['representative_prefix'],int(r['A']),int(r['B'])))
    got={(r['event_date'],r['representative_prefix'],r['A'],r['B']) for r in parts}
    missing=expected-got;extra=got-expected
    if missing or extra or len(parts)!=len(pop):
        raise SystemExit(f'aggregate key mismatch rows={len(parts)} pop={len(pop)} missing={len(missing)} extra={len(extra)}')
    OUT.mkdir(parents=True,exist_ok=True);write_csv(OUT/'census_weekly_rov.csv',parts)
    sel_bytes=''.join(f"{r['event_date']}|{r['representative_prefix']}|{r['A']}|{r['B']}|{r['cluster_prefix_n']}\n" for r in pop).encode()
    selection_sha=hashlib.sha256(sel_bytes).hexdigest()
    states=Counter((r['A_rov_weekly'],r['B_rov_weekly']) for r in parts)
    inv=[r for r in parts if str(r['weekly_inversion']).lower()=='true']
    weight_states=Counter();total_w=0;inv_w=0
    by_month=defaultdict(lambda:{'n':0,'inv':0});by_bin=defaultdict(lambda:{'n':0,'inv':0})
    for r in parts:
        w=int(r.get('cluster_prefix_n') or 1);total_w+=w;weight_states[(r['A_rov_weekly'],r['B_rov_weekly'])]+=w
        ii=str(r['weekly_inversion']).lower()=='true'
        if ii:inv_w+=w
        m=r['event_date'][:7];by_month[m]['n']+=1;by_month[m]['inv']+=int(ii)
        b=cluster_bin(w);by_bin[b]['n']+=1;by_bin[b]['inv']+=int(ii)
    # Exact-audit calibration: weekly checkpoint versus outcome-blind exact-date validator.
    exact=list(csv.DictReader(open(EXACT,encoding='utf8'))) if EXACT.exists() else []
    cm=Counter();overlap=0
    pmap={(r['representative_prefix'],r['A'],r['B']):r for r in parts}
    cal_rows=[]
    for e in exact:
        c=pmap.get((e['representative_prefix'],e['A'],e['B']))
        if not c:continue
        overlap+=1;wi=str(c['weekly_inversion']).lower()=='true';ei=e['A_rov']=='valid' and e['B_rov']=='invalid'
        cm[(wi,ei)]+=1
        cal_rows.append({'representative_prefix':e['representative_prefix'],'A':e['A'],'B':e['B'],'weekly_event_date':c['event_date'],'exact_event_date':e['exact_event_date'],'weekly_A':c['A_rov_weekly'],'weekly_B':c['B_rov_weekly'],'exact_A':e['A_rov'],'exact_B':e['B_rov'],'weekly_inversion':wi,'exact_inversion':ei})
    write_csv(OUT/'weekly_vs_exact_calibration.csv',cal_rows)
    # Independent archive crosscheck of every census weekly inversion, grouped by checkpoint date.
    bydate=defaultdict(list)
    for r in inv:bydate[r['event_date'][:10]].append(r)
    ar=[];aerr=[]
    for i,(ds,rs) in enumerate(sorted(bydate.items()),1):
        try:
            idx,nvrp=load_archive_index(ds)
            for r in rs:
                aa=archive_classify(idx,r['representative_prefix'],r['A']);bb=archive_classify(idx,r['representative_prefix'],r['B'])
                ar.append({'representative_prefix':r['representative_prefix'],'event_date':ds,'A':r['A'],'B':r['B'],'primary_A':r['A_rov_weekly'],'primary_B':r['B_rov_weekly'],'archive_A':aa,'archive_B':bb,'archive_vrp_n':nvrp,'reproduced':aa=='valid' and bb=='invalid'})
        except Exception as e:aerr.append({'date':ds,'case_n':len(rs),'error':repr(e)})
        print('archive',i,'/',len(bydate),ds,'cases',len(rs),'errors',len(aerr),flush=True)
    ar.sort(key=lambda r:(r['event_date'],r['representative_prefix']));write_csv(OUT/'weekly_inversion_archive_crosscheck.csv',ar)
    (OUT/'archive_errors.json').write_text(json.dumps(aerr,indent=2),encoding='utf8')
    repro=sum(str(r['reproduced']).lower()=='true' for r in ar)
    summary={
      'design':{
        'population':'all 7,046 frozen no-A-recurrence persistent observed IPv4 A->B event clusters',
        'selection_outcome_blind':True,'selection_sha256':selection_sha,
        'checkpoint':'weekly event_date from frozen population; not exact BGP transition time',
        'validator':'BGPKIT /v3/roas/validate, same endpoint/parser as outcome-blind exact v2',
        'independent_crosscheck':'RIPE NCC daily validated ROA archive, five trust anchors, for every weekly census inversion',
        'exact_calibration':'join against 309-case outcome-blind >=3-RRC exact-date audit'
      },
      'population_n':len(parts),'classification_errors_n':0,
      'joint_state_matrix':{f'{a}/{b}':n for (a,b),n in sorted(states.items())},
      'weekly_old_valid_new_invalid_n':len(inv),'weekly_old_valid_new_invalid_fraction_all':frac(len(inv),len(parts)),
      'cluster_prefix_weight_n':total_w,'weekly_inversion_prefix_weight_n':inv_w,'weekly_inversion_prefix_weight_fraction':frac(inv_w,total_w),
      'prefix_weighted_joint_state_matrix':{f'{a}/{b}':n for (a,b),n in sorted(weight_states.items())},
      'month_breakdown':{m:{**v,'fraction':frac(v['inv'],v['n'])} for m,v in sorted(by_month.items())},
      'cluster_size_breakdown':{b:{**v,'fraction':frac(v['inv'],v['n'])} for b,v in sorted(by_bin.items())},
      'exact_calibration':{
        'exact_rows_n':len(exact),'overlap_n':overlap,
        'weekly_true_exact_true':cm[(True,True)],'weekly_true_exact_false':cm[(True,False)],
        'weekly_false_exact_true':cm[(False,True)],'weekly_false_exact_false':cm[(False,False)],
        'weekly_inversion_n_in_exact_overlap':cm[(True,True)]+cm[(True,False)],
        'exact_inversion_n_in_overlap':cm[(True,True)]+cm[(False,True)]
      },
      'independent_archive_crosscheck':{
        'weekly_inversions_n':len(inv),'crosschecked_n':len(ar),'reproduced_n':repro,'discrepancy_n':len(ar)-repro,'date_errors_n':len(aerr)
      },
      'guardrails':[
        'The 7,046 census rate is a weekly-checkpoint rate over the frozen persistent-origin-replacement population, not an exact-transition prevalence estimate.',
        'Exact-date mechanism evidence remains the outcome-blind 309-case >=3-RRC audit.',
        'Cluster-prefix weighting is descriptive only; the event cluster remains the primary unit.',
        'No RPKI field is used to select census cases.'
      ]
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    (OUT/'selection_protocol.json').write_text(json.dumps({'input':str(POP.relative_to(BASE)),'rule':'include every frozen_no_recurrence row; sort event_date,prefix,A,B','selected_n':len(pop),'selection_sha256':selection_sha,'forbidden_for_selection':['any RPKI/ROA state or lifecycle field']},indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['shard','aggregate'],required=True);ap.add_argument('--shard-index',type=int);ap.add_argument('--shard-count',type=int,default=8);ap.add_argument('--workers',type=int,default=4);ap.add_argument('--parts-root',default='/tmp/census-parts');a=ap.parse_args()
    if a.mode=='shard':run_shard(a.shard_index,a.shard_count,a.workers)
    else:run_aggregate(a.parts_root)
if __name__=='__main__':main()
