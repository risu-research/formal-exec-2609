#!/usr/bin/env python3
import csv, io, ipaddress, json, lzma, time, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE=Path(__file__).resolve().parent
EXACT=BASE/'results'/'rpki_outcomeblind_validate_v2_20261002'/'exact_rov_validate.csv'
LIFE=BASE/'results'/'rpki_lifecycle_full309_contiguous_v3_20261002'/'lifecycle_309_contiguous.csv'
OUT=BASE/'results'/'rpki_lifecycle_endpoint_consistency_v4_20261002'; OUT.mkdir(parents=True,exist_ok=True)
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal']
UA='risu-rpki-lifecycle-endpoint-consistency-v4/1.0 research-contact-moon1002'

def truth(x): return str(x).strip().lower()=='true'
def norm(x):
    x=str(x or '').strip().lower()
    if x.startswith('valid'): return 'valid'
    if x.startswith('invalid'): return 'invalid'
    return 'unknown'
def key(r): return (r['exact_event_date'],r['representative_prefix'],str(r['A']),str(r['B']))
def get_bytes(url,retries=5,timeout=180):
    err=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as rr:return rr.read()
        except Exception as e:
            err=e; time.sleep(min(15,1.5*(2**i)))
    raise RuntimeError(f'{url}: {err}')
def all_supernets(p):
    n=ipaddress.ip_network(p,strict=False)
    return {str(n.supernet(new_prefix=k)) if k<n.prefixlen else str(n) for k in range(0,n.prefixlen+1)}
def scan_tal(ds,tal,wanted):
    y,m,d=ds[:4],ds[5:7],ds[8:10]
    raw=get_bytes(f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz')
    text=lzma.decompress(raw).decode('utf8','replace')
    hits=[]; total=0
    for z in csv.DictReader(io.StringIO(text)):
        total+=1
        vp=(z.get('IP Prefix') or z.get('prefix') or '').strip()
        if vp not in wanted: continue
        aa=(z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
        if not aa.isdigit(): continue
        try:
            n=ipaddress.ip_network(vp,strict=False)
            mx=z.get('Max Length') or z.get('maxLength') or z.get('max_length') or n.prefixlen
            hits.append((vp,int(aa),int(mx)))
        except Exception: pass
    return hits,total
def classify(cov,prefix,asn):
    q=ipaddress.ip_network(prefix,strict=False); cover=[]
    for vp,a,mx in cov:
        try:
            v=ipaddress.ip_network(vp,strict=False)
            if q.subnet_of(v): cover.append((a,mx))
        except Exception: pass
    if not cover:return 'unknown'
    return 'valid' if any(a==int(asn) and q.prefixlen<=mx for a,mx in cover) else 'invalid'
def write_csv(path,rows):
    if not rows:path.write_text('',encoding='utf8');return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with open(path,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def matrix(rows, side):
    c=Counter()
    for r in rows:
        s=norm(r[f'{side}_rov']); h=truth(r[f'{side}_auth_at_event_contiguous'])
        c[f'{s}/history_{"authorized" if h else "not_authorized"}']+=1
    return dict(sorted(c.items()))
def main():
    erows=list(csv.DictReader(open(EXACT,encoding='utf8'))); lrows=list(csv.DictReader(open(LIFE,encoding='utf8')))
    if len(erows)!=309 or len(lrows)!=309: raise SystemExit(f'expected 309+309 rows, got {len(erows)}+{len(lrows)}')
    em={key(r):r for r in erows}; lm={key(r):r for r in lrows}
    if len(em)!=309 or len(lm)!=309 or set(em)!=set(lm): raise SystemExit('exact/lifecycle canonical key mismatch')
    joined=[]; discord=[]
    for k in sorted(em):
        e=em[k]; l=lm[k]
        r={**e,
           'A_auth_at_event_contiguous':l['A_auth_at_event_contiguous'],
           'B_auth_at_event_contiguous':l['B_auth_at_event_contiguous'],
           'A_contiguous_auth_start':l['A_contiguous_auth_start'],'A_contiguous_auth_end':l['A_contiguous_auth_end'],
           'B_contiguous_auth_start':l['B_contiguous_auth_start'],'B_contiguous_auth_end':l['B_contiguous_auth_end']}
        for side in ('A','B'):
            expected=(norm(r[f'{side}_rov'])=='valid')
            hist=truth(r[f'{side}_auth_at_event_contiguous'])
            r[f'{side}_endpoint_consistent']=(expected==hist)
            r[f'{side}_discordance_type']='' if expected==hist else ('validator_valid_history_false' if expected else 'validator_nonvalid_history_true')
        r['any_endpoint_discordance']=not (r['A_endpoint_consistent'] and r['B_endpoint_consistent'])
        joined.append(r)
        if r['any_endpoint_discordance']:discord.append(r)
    # Third-source adjudication only for discordant cases, grouped by exact calendar date.
    bydate=defaultdict(list)
    for r in discord: bydate[r['exact_event_date']].append(r)
    archived=[]; errors=[]
    for i,ds in enumerate(sorted(bydate),1):
        rs=bydate[ds]; wanted=set()
        for r in rs:wanted.update(all_supernets(r['representative_prefix']))
        cov=[]; total=0
        try:
            with ThreadPoolExecutor(max_workers=5) as ex:
                fs={ex.submit(scan_tal,ds,tal,wanted):tal for tal in TALS}
                for f in as_completed(fs):
                    h,n=f.result();cov.extend(h);total+=n
            for r in rs:
                z={k:r[k] for k in ['exact_event_date','representative_prefix','A','B','A_rov','B_rov','A_auth_at_event_contiguous','B_auth_at_event_contiguous','A_discordance_type','B_discordance_type']}
                z['archive_A']=classify(cov,r['representative_prefix'],r['A']); z['archive_B']=classify(cov,r['representative_prefix'],r['B'])
                for side in ('A','B'):
                    if r[f'{side}_discordance_type']:
                        z[f'archive_agrees_validator_{side}']=norm(z[f'archive_{side}'])==norm(r[f'{side}_rov'])
                        z[f'archive_agrees_history_{side}']=(norm(z[f'archive_{side}'])=='valid')==truth(r[f'{side}_auth_at_event_contiguous'])
                    else:
                        z[f'archive_agrees_validator_{side']='']
                z['archive_rows_scanned_n']=total
                archived.append(z)
        except Exception as e: errors.append({'exact_event_date':ds,'case_n':len(rs),'error':repr(e)})
        print('archive date',i,'/',len(bydate),ds,'cases',len(rs),'errors',len(errors),flush=True)
    # Aggregate third-source verdict by discordant side.
    sideverdict={}
    for side in ('A','B'):
        items=[z for z in archived if z[f'{side}_discordance_type']]
        c=Counter()
        for z in items:
            av=norm(z[f'archive_{side}']); fv=norm(z[f'{side}_rov']); hist=truth(z[f'{side}_auth_at_event_contiguous'])
            if av==fv: c['archive_agrees_validator']+=1
            if (av=='valid')==hist: c['archive_agrees_history_boolean']+=1
            c[f'archive_state_{av}']+=1
        c['discordant_side_n']=len(items); sideverdict[side]=dict(c)
    summary={
      'design':{
        'exact_n':309,
        'consistency_rule':'history authorization Boolean must be true iff frozen exact ROV validator state is Valid; Invalid/Unknown imply no matching authorization for that ASN',
        'third_source':'RIPE NCC daily validated ROA archive, all five trust anchors, exact_event_date only for endpoint-discordant cases',
      },
      'key_integrity':{'exact_unique_n':len(em),'history_unique_n':len(lm),'joined_n':len(joined)},
      'A_cross_tab':matrix(joined,'A'),'B_cross_tab':matrix(joined,'B'),
      'A_discordant_side_n':sum(not r['A_endpoint_consistent'] for r in joined),
      'B_discordant_side_n':sum(not r['B_endpoint_consistent'] for r in joined),
      'any_discordant_case_n':len(discord),
      'discordance_types_A':dict(Counter(r['A_discordance_type'] for r in joined if r['A_discordance_type'])),
      'discordance_types_B':dict(Counter(r['B_discordance_type'] for r in joined if r['B_discordance_type'])),
      'archive_crosscheck':{'target_case_n':len(discord),'completed_case_n':len(archived),'date_errors_n':len(errors),'side_verdict':sideverdict},
      'guardrail':'A lifecycle timing claim should condition on reconstructed historical authorization intervals; frozen validator remains authority for exact-event ROV state. Discordance must be reported, not silently imputed.'
    }
    write_csv(OUT/'joined_309.csv',joined); write_csv(OUT/'discordant_cases.csv',discord); write_csv(OUT/'archive_adjudication.csv',archived)
    (OUT/'archive_errors.json').write_text(json.dumps(errors,indent=2),encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
