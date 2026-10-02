#!/usr/bin/env python3
import re,json,urllib.request,urllib.parse
from pathlib import Path
from datetime import datetime,timezone

OUT=Path(__file__).resolve().parent/'results'/'rpkispool_day_probe_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-rpkispool-day-probe/1.0'
MIRRORS=['https://rpkiviews.kerfuffle.net/rpkidata/rpkispools/','https://josephine.sobornost.net/rpkidata/rpkispools/','https://dango.attn.jp/rpkidata/rpkispools/']
CASES=[
 {'prefix':'188.119.156.0/23','bgp':'2026-09-01T13:53:03Z'},
 {'prefix':'38.60.218.0/24','bgp':'2026-09-03T03:17:08Z'},
 {'prefix':'45.176.188.0/24','bgp':'2026-09-04T14:31:52Z'},
 {'prefix':'96.62.12.0/22','bgp':'2026-09-04T16:35:33Z'},
 {'prefix':'207.89.18.0/24','bgp':'2026-09-05T22:14:50Z'},
]

def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':UA})
 with urllib.request.urlopen(req,timeout=60) as r:return r.read().decode('utf8','replace')

def head(url):
 try:
  req=urllib.request.Request(url,headers={'User-Agent':UA},method='HEAD')
  with urllib.request.urlopen(req,timeout=30) as r:return {'status':r.status,'length':r.headers.get('Content-Length'),'type':r.headers.get('Content-Type'),'modified':r.headers.get('Last-Modified')}
 except Exception as e:return {'error':repr(e)}

def extract_ts(name):
 # handles YYYYMMDD-HHMMSS, YYYYMMDDHHMMSS, HHMM etc
 nums=re.findall(r'\d+',name)
 s=''.join(nums)
 m=re.search(r'202609\d{2}\d{4,6}',s)
 if m:
  z=m.group(0)[:14]
  try:return datetime.strptime(z,'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
  except: pass
 m=re.search(r'(\d{2})(\d{2})(\d{2})',s)
 return None

def main():
 out={'cases':CASES,'mirrors':{}}
 days=sorted({c['bgp'][8:10] for c in CASES})
 for base in MIRRORS:
  mo={}
  for day in days:
   url=f'{base}2026/09/{day}/'
   try:
    html=get(url);hrefs=re.findall(r'href=["\']([^"\']+)["\']',html,re.I)
    links=[]
    for h in hrefs:
     if h in ('../','./') or h.startswith('?'):continue
     u=urllib.parse.urljoin(url,h);links.append({'href':h,'url':u})
    # HEAD first 20, middle 5, last 20 unique to infer cadence and sizes
    idx=list(range(min(20,len(links))))+list(range(max(0,len(links)//2-2),min(len(links),len(links)//2+3)))+list(range(max(0,len(links)-20),len(links)))
    seen=set();sample=[]
    for i in idx:
     if i in seen:continue
     seen.add(i);x=dict(links[i]);x['head']=head(x['url']);sample.append(x)
    mo[day]={'url':url,'n_links':len(links),'first':links[:10],'last':links[-10:],'sample_heads':sample}
   except Exception as e:mo[day]={'url':url,'error':repr(e)}
  out['mirrors'][base]=mo
 (OUT/'day_inventory.json').write_text(json.dumps(out,indent=2),encoding='utf8')
 print(json.dumps({b:{d:{'n_links':v.get('n_links'),'error':v.get('error'),'first':[x['href'] for x in v.get('first',[])[:3]],'last':[x['href'] for x in v.get('last',[])[-3:]]} for d,v in ds.items()} for b,ds in out['mirrors'].items()},indent=2))
if __name__=='__main__':main()
