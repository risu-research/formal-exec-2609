#!/usr/bin/env python3
import csv,io,ipaddress,json,lzma,time,urllib.request
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
B=Path(__file__).resolve().parent
E=B/'results/rpki_outcomeblind_validate_v2_20261002/exact_rov_validate.csv'
L=B/'results/rpki_lifecycle_full309_contiguous_v3_20261002/lifecycle_309_contiguous.csv'
O=B/'results/rpki_lifecycle_endpoint_consistency_v4b_20261002';O.mkdir(parents=True,exist_ok=True)
TALS=['afrinic.tal','apnic.tal','arin.tal','lacnic.tal','ripencc.tal'];UA='risu-rpki-life-v4b/1.0 research-contact-moon1002'
def t(x):return str(x).strip().lower()=='true'
def n(x):
 x=str(x or '').lower();return 'valid' if x.startswith('valid') else ('invalid' if x.startswith('invalid') else 'unknown')
def k(r):return (r['exact_event_date'],r['representative_prefix'],str(r['A']),str(r['B']))
def get(u):
 e=None
 for i in range(5):
  try:
   q=urllib.request.Request(u,headers={'User-Agent':UA});return urllib.request.urlopen(q,timeout=180).read()
  except Exception as z:e=z;time.sleep(min(15,1.5*2**i))
 raise RuntimeError(f'{u}: {e}')
def supers(p):
 x=ipaddress.ip_network(p);return {str(x.supernet(new_prefix=i)) if i<x.prefixlen else str(x) for i in range(x.prefixlen+1)}
def scan(ds,tal,wanted):
 y,m,d=ds[:4],ds[5:7],ds[8:10]; txt=lzma.decompress(get(f'https://ftp.ripe.net/rpki/{tal}/{y}/{m}/{d}/roas.csv.xz')).decode('utf8','replace');h=[];tot=0
 for z in csv.DictReader(io.StringIO(txt)):
  tot+=1;vp=(z.get('IP Prefix') or z.get('prefix') or '').strip()
  if vp not in wanted:continue
  a=(z.get('ASN') or z.get('asn') or '').strip().upper().removeprefix('AS')
  if not a.isdigit():continue
  try:
   q=ipaddress.ip_network(vp);mx=int(z.get('Max Length') or z.get('maxLength') or z.get('max_length') or q.prefixlen);h.append((vp,int(a),mx))
  except:pass
 return h,tot
def cls(c,p,a):
 q=ipaddress.ip_network(p);v=[]
 for vp,aa,mx in c:
  try:
   if q.subnet_of(ipaddress.ip_network(vp)):v.append((aa,mx))
  except:pass
 if not v:return 'unknown'
 return 'valid' if any(aa==int(a) and q.prefixlen<=mx for aa,mx in v) else 'invalid'
def wc(p,rows):
 if not rows:p.write_text('');return
 f=[]
 for r in rows:
  for x in r:
   if x not in f:f.append(x)
 with open(p,'w',newline='',encoding='utf8') as g:w=csv.DictWriter(g,fieldnames=f);w.writeheader();w.writerows(rows)
def mat(rows,s):
 c=Counter()
 for r in rows:c[f'{n(r[s+"_rov"])}/history_{"authorized" if t(r[s+"_auth_at_event_contiguous"]) else "not_authorized"}']+=1
 return dict(c)
def main():
 er=list(csv.DictReader(open(E)));lr=list(csv.DictReader(open(L)));em={k(r):r for r in er};lm={k(r):r for r in lr}
 if len(er)!=309 or len(lr)!=309 or len(em)!=309 or set(em)!=set(lm):raise SystemExit('309-key integrity failed')
 j=[];disc=[]
 for kk in sorted(em):
  e=em[kk];l=lm[kk];r={**e}
  for s in ('A','B'):
   r[s+'_auth_at_event_contiguous']=l[s+'_auth_at_event_contiguous'];ev=n(r[s+'_rov'])=='valid';hv=t(r[s+'_auth_at_event_contiguous']);r[s+'_endpoint_consistent']=ev==hv;r[s+'_discordance_type']='' if ev==hv else ('validator_valid_history_false' if ev else 'validator_nonvalid_history_true')
  r['any_endpoint_discordance']=not(t(r['A_endpoint_consistent']) and t(r['B_endpoint_consistent']));j.append(r)
  if r['any_endpoint_discordance']:disc.append(r)
 by=defaultdict(list)
 for r in disc:by[r['exact_event_date']].append(r)
 arch=[];errs=[]
 for ii,ds in enumerate(sorted(by),1):
  rs=by[ds];want=set()
  for r in rs:want|=supers(r['representative_prefix'])
  cov=[];total=0
  try:
   with ThreadPoolExecutor(max_workers=5) as ex:
    fs=[ex.submit(scan,ds,x,want) for x in TALS]
    for f in as_completed(fs):h,z=f.result();cov+=h;total+=z
   for r in rs:
    z={x:r[x] for x in ['exact_event_date','representative_prefix','A','B','A_rov','B_rov','A_auth_at_event_contiguous','B_auth_at_event_contiguous','A_discordance_type','B_discordance_type']};z['archive_A']=cls(cov,r['representative_prefix'],r['A']);z['archive_B']=cls(cov,r['representative_prefix'],r['B']);z['archive_rows_scanned_n']=total;arch.append(z)
  except Exception as x:errs.append({'date':ds,'case_n':len(rs),'error':repr(x)})
  print('archive',ii,'/',len(by),ds,'cases',len(rs),'errors',len(errs),flush=True)
 verdict={}
 for s in ('A','B'):
  xs=[z for z in arch if z[s+'_discordance_type']];c=Counter()
  for z in xs:
   av=n(z['archive_'+s]);fv=n(z[s+'_rov']);hv=t(z[s+'_auth_at_event_contiguous']);c['archive_agrees_validator']+=av==fv;c['archive_agrees_history_boolean']+=(av=='valid')==hv;c['archive_state_'+av]+=1
  c['discordant_side_n']=len(xs);verdict[s]=dict(c)
 S={'design':{'n':309,'rule':'history authorization iff frozen exact ROV is Valid','third_source':'RIPE NCC daily validated ROA archive, five trust anchors, exact event date, discordant cases only'},'A_cross_tab':mat(j,'A'),'B_cross_tab':mat(j,'B'),'A_discordant_side_n':sum(not t(r['A_endpoint_consistent']) for r in j),'B_discordant_side_n':sum(not t(r['B_endpoint_consistent']) for r in j),'any_discordant_case_n':len(disc),'A_discordance_types':dict(Counter(r['A_discordance_type'] for r in j if r['A_discordance_type'])),'B_discordance_types':dict(Counter(r['B_discordance_type'] for r in j if r['B_discordance_type'])),'archive':{'target_case_n':len(disc),'completed_case_n':len(arch),'errors_n':len(errs),'verdict':verdict},'guardrail':'Use frozen validator for event-day ROV state; use history intervals for timing only where reconstructed. Report endpoint discordance explicitly.'}
 wc(O/'joined_309.csv',j);wc(O/'discordant_cases.csv',disc);wc(O/'archive_adjudication.csv',arch);(O/'errors.json').write_text(json.dumps(errs,indent=2));(O/'summary.json').write_text(json.dumps(S,indent=2));print(json.dumps(S,indent=2))
if __name__=='__main__':main()
