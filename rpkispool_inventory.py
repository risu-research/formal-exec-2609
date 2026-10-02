#!/usr/bin/env python3
import re,json,urllib.request,urllib.parse
from pathlib import Path

OUT=Path(__file__).resolve().parent/'results'/'rpkispool_inventory_20261002';OUT.mkdir(parents=True,exist_ok=True)
UA='risu-rpki-rpkispool-inventory/1.0'
BASES=[
 'https://rpkiviews.kerfuffle.net/rpkidata/rpkispools/2026/09/',
 'https://josephine.sobornost.net/rpkidata/rpkispools/2026/09/',
 'https://dango.attn.jp/rpkidata/rpkispools/2026/09/'
]

def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':UA})
 with urllib.request.urlopen(req,timeout=60) as r:return r.read().decode('utf8','replace')

def head(url):
 try:
  req=urllib.request.Request(url,headers={'User-Agent':UA},method='HEAD')
  with urllib.request.urlopen(req,timeout=30) as r:return {'status':r.status,'length':r.headers.get('Content-Length'),'type':r.headers.get('Content-Type'),'modified':r.headers.get('Last-Modified')}
 except Exception as e:return {'error':repr(e)}

def main():
 out={}
 for base in BASES:
  try:
   html=get(base);hrefs=re.findall(r'href=["\']([^"\']+)["\']',html,re.I)
   links=[]
   for h in hrefs:
    if h in ('../','./') or h.startswith('?'):continue
    u=urllib.parse.urljoin(base,h)
    links.append({'href':h,'url':u})
   # HEAD archive-like links around dates of interest first
   cand=[x for x in links if any(d in x['href'] for d in ['20260902','20260903','20260904','20260905','20260906','20260907','20260908']) or x['href'].endswith(('.tar.zst','.zst','.tar.gz'))]
   for x in cand[:100]:x['head']=head(x['url'])
   out[base]={'n_links':len(links),'links':links[:300],'candidates':cand[:100]}
  except Exception as e:out[base]={'error':repr(e)}
 (OUT/'inventory.json').write_text(json.dumps(out,indent=2),encoding='utf8')
 print(json.dumps({k:{'error':v.get('error'),'n_links':v.get('n_links'),'candidate_n':len(v.get('candidates',[]))} for k,v in out.items()},indent=2))
if __name__=='__main__':main()
