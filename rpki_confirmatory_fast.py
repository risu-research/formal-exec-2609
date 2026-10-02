#!/usr/bin/env python3
import csv,json,urllib.request,urllib.parse,time,re,gzip,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone

BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_retirement_tail_20261002'/'events.csv'
OUT=BASE/'results'/'rpki_confirmatory_fast_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-confirmatory/1.0'

def getj(url,retries=4):
 e=None
 for i in range(retries):
  try:
   q=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
   with urllib.request.urlopen(q,timeout=120) as r:return json.load(r)
  except Exception as x:e=x;time.sleep(1.5*(2**i))
 raise RuntimeError(f'{url}: {e}')

def ts(x):
 if x is None:return None
 if isinstance(x,(int,float)):return float(x)
 s=str(x).replace('Z','+00:00')
 try:
  d=datetime.fromisoformat(s); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.timestamp()
 except:return None

def iso(x):return datetime.fromtimestamp(x,tz=timezone.utc).isoformat().replace('+00:00','Z') if x else ''

def origin(path):
 if not path:return None
 if isinstance(path,list):
  for x in reversed(path):
   m=re.findall(r'\d+',str(x))
   if m:return int(m[-1])
 m=re.findall(r'\d+',str(path));return int(m[-1]) if m else None

def load_org():
 u='https://data.caida.org/datasets/as-organizations/20260901.as-org2info.txt.gz'
 raw=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':UA}),timeout=120).read()
 text=gzip.decompress(raw).decode('utf8','replace'); amap={}; names={};mode=''
 for line in text.splitlines():
  if line.startswith('# format:'):
   if 'aut|changed|aut_name|org_id' in line:mode='as'
   elif 'org_id|changed|org_name' in line:mode='org'
   continue
  if not line or line.startswith('#'):continue
  p=line.split('|')
  if mode=='as' and len(p)>=4 and p[0].isdigit():amap[int(p[0])]=p[3]
  elif mode=='org' and len(p)>=3:names[p[0]]=p[2]
 return amap,names

def bgplay(prefix):
 q=urllib.parse.urlencode({'resource':prefix,'starttime':'2026-09-01T11:00:00','endtime':'2026-09-09T12:00:00','unix_timestamps':'TRUE','sourceapp':'risu-rpki-study'})
 return getj('https://stat.ripe.net/data/bgplay/data.json?'+q).get('data',{})

def reconstruct(d,prefix,A,B):
 src={str(s.get('id')):s for s in d.get('sources',[]) or []}; state={}
 for e in d.get('initial_state',[]) or []:
  a=e.get('attrs',e);sid=str(a.get('source_id',e.get('source_id','')));tp=a.get('target_prefix',e.get('target_prefix'))
  if tp==prefix:state[sid]=origin(a.get('path',e.get('path')))
 prior={};hits=[];a_reappears=set();b_seen={}
 for e in sorted(d.get('events',[]) or [],key=lambda x:ts(x.get('timestamp')) or 0):
  t=ts(e.get('timestamp'));a=e.get('attrs',{}) or {};sid=str(a.get('source_id',e.get('source_id','')));tp=a.get('target_prefix',e.get('target_prefix'))
  if not sid or tp!=prefix or t is None:continue
  old=state.get(sid);typ=e.get('type') or a.get('type')
  if typ=='W':
   if old is not None:prior[sid]=(t,old,None,'W')
   state[sid]=None
  elif typ=='A':
   new=origin(a.get('path',e.get('path')))
   if new is None:continue
   if new==A and sid in b_seen and t>b_seen[sid]:a_reappears.add(sid)
   if old!=new:
    if new==B:
     w=prior.get(sid);direct=(old==A);via=(old is None and w and w[1]==A and 0<=t-w[0]<=7200)
     if direct or via:
      hits.append({'source_id':sid,'rrc':src.get(sid,{}).get('rrc'),'peer_asn':src.get(sid,{}).get('as_number'),'A_off':t if direct else w[0],'B_on':t,'mode':'direct' if direct else 'withdraw_then_B'})
      b_seen[sid]=t
    prior[sid]=(t,old,new,'A')
   state[sid]=new
 dd={}
 for h in hits:
  if h['source_id'] not in dd or h['B_on']<dd[h['source_id']]['B_on']:dd[h['source_id']]=h
 hits=list(dd.values());rrcs=sorted({str(h['rrc']) for h in hits if h['rrc'] is not None})
 b=sorted(h['B_on'] for h in hits);a=sorted(h['A_off'] for h in hits)
 med=lambda v:v[len(v)//2] if v else None
 return {'peer_n':len(hits),'rrc_n':len(rrcs),'rrcs':','.join(rrcs),'B_first':iso(b[0]) if b else '','B_median':iso(med(b)) if b else '','B_last':iso(b[-1]) if b else '','B_spread_s':(b[-1]-b[0]) if len(b)>1 else 0 if b else '', 'A_off_median':iso(med(a)) if a else '','A_reappearing_peer_n':len(a_reappears),'direct_n':sum(h['mode']=='direct' for h in hits),'withdraw_then_B_n':sum(h['mode']!='direct' for h in hits)},hits

def transfer(prefix):
 q=urllib.parse.urlencode({'resource':prefix,'starttime':'2026-05-01T00:00:00','endtime':'2026-10-02T23:59:59','sourceapp':'risu-rpki-study'})
 try:return getj('https://stat.ripe.net/data/transfer-history/data.json?'+q).get('data',{}).get('transfers',[]) or []
 except:return []

def main():
 inp=list(csv.DictReader(open(INP,encoding='utf8')))
 linger=[r for r in inp if r['A_20260929']=='valid']; other=[r for r in inp if r['A_20260929']!='valid']
 # all persistent lingering cases + deterministic 24 controls
 other=sorted(other,key=lambda r:hashlib.sha256(r['prefix'].encode()).hexdigest())[:24]
 sample=linger+other;amap,names=load_org();out=[];peer=[];errs=[]
 print('sample',len(sample),'linger',len(linger),'controls',len(other),flush=True)
 for i,r in enumerate(sample,1):
  p=r['prefix'];A=int(r['A']);B=int(r['B']);oa=amap.get(A,'');ob=amap.get(B,'')
  rec=dict(r);rec.update({'orgA_id':oa,'orgB_id':ob,'orgA_name':names.get(oa,''),'orgB_name':names.get(ob,''),'same_org':bool(oa and ob and oa==ob),'tail3w':r['A_20260929']=='valid'})
  try:
   sm,hits=reconstruct(bgplay(p),p,A,B);rec.update(sm)
   for h in hits:peer.append({'prefix':p,'A':A,'B':B,**{k:(iso(v) if k in ('A_off','B_on') else v) for k,v in h.items()}})
   # high-quality operational context: same-org, A recurrence, or registry transfer record
   tx=[]
   if not rec['same_org'] and rec['tail3w']:tx=transfer(p)
   rec['transfer_records_n']=len(tx)
   if rec['same_org']: rec['context_class']='same_org';rec['context_strength']='high'
   elif tx: rec['context_class']='registry_transfer_evidence';rec['context_strength']='high'
   elif int(rec.get('A_reappearing_peer_n') or 0)>0:rec['context_class']='observed_A_recurrence';rec['context_strength']='high'
   elif int(rec.get('rrc_n') or 0)>=2:rec['context_class']='persistent_cross_org_or_unresolved';rec['context_strength']='medium'
   else:rec['context_class']='unresolved';rec['context_strength']='low'
  except Exception as e:
   errs.append({'prefix':p,'error':repr(e)});rec.update({'peer_n':0,'rrc_n':0,'context_class':'api_unresolved','context_strength':'low'})
  out.append(rec);print(i,p,'tail',rec['tail3w'],'rrc',rec.get('rrc_n'),'class',rec.get('context_class'),flush=True)
 # write
 keys=[]
 for r in out:
  for k in r:
   if k not in keys:keys.append(k)
 with open(OUT/'transitions.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(out)
 if peer:
  with open(OUT/'peer_updates.csv','w',newline='',encoding='utf8') as f:w=csv.DictWriter(f,fieldnames=list(peer[0]));w.writeheader();w.writerows(peer)
 (OUT/'errors.json').write_text(json.dumps(errs,indent=2),encoding='utf8')
 resolved=[r for r in out if int(r.get('rrc_n') or 0)>0];multi=[r for r in resolved if int(r.get('rrc_n') or 0)>=2];m3=[r for r in resolved if int(r.get('rrc_n') or 0)>=3]
 L=[r for r in out if r['tail3w']];C=[r for r in out if not r['tail3w']]
 def stats(g):
  n=len(g);return {'n':n,'exact_resolved':sum(int(r.get('rrc_n') or 0)>0 for r in g),'multi_rrc_ge2':sum(int(r.get('rrc_n') or 0)>=2 for r in g),'multi_rrc_ge3':sum(int(r.get('rrc_n') or 0)>=3 for r in g),'same_org':sum(bool(r.get('same_org')) for r in g),'A_recurrence':sum(int(r.get('A_reappearing_peer_n') or 0)>0 for r in g),'transfer_evidence':sum(int(r.get('transfer_records_n') or 0)>0 for r in g),'classes':dict(Counter(r.get('context_class') for r in g))}
 spreads=sorted(float(r['B_spread_s']) for r in resolved if r.get('B_spread_s') not in ('',None))
 s={'design':{'tail_definition':'B remains origin in Sep08/Sep15/Sep22/Sep29 daily snapshots and old A remains RPKI-valid on Sep29','tail_cases_all':len(linger),'controls':len(other),'bgp_exact_source':'RIPEstat BGPlay all RRCs, Sep01 11:00Z to Sep09 12:00Z','intent_guardrail':'context_class is evidence-backed operational context, not subjective intent'},'overall':stats(out),'tail3w':stats(L),'controls':stats(C),'B_spread_s_median':spreads[len(spreads)//2] if spreads else None,'errors_n':len(errs),'peer_rows':len(peer)}
 (OUT/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf8');print(json.dumps(s,indent=2),flush=True)
if __name__=='__main__':main()
