#!/usr/bin/env python3
import csv,json,re,urllib.parse,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parent
INP=BASE/'results'/'rpki_policy_consequence_v1_20261002'/'cases.csv'
OUT=BASE/'results'/'rpki_inversion_spool_probe_20261002';OUT.mkdir(parents=True,exist_ok=True)
MIRRORS=['https://rpkiviews.kerfuffle.net/rpkidata/rpkispools/','https://josephine.sobornost.net/rpkidata/rpkispools/','https://dango.attn.jp/rpkidata/rpkispools/']
UA='risu-rpki-inversion-spool-probe/1.0'
def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':UA})
 with urllib.request.urlopen(req,timeout=60) as r:return r.read().decode('utf8','replace')
def head(url):
 try:
  req=urllib.request.Request(url,headers={'User-Agent':UA},method='HEAD')
  with urllib.request.urlopen(req,timeout=30) as r:return {'status':r.status,'length':r.headers.get('Content-Length'),'type':r.headers.get('Content-Type'),'modified':r.headers.get('Last-Modified')}
 except Exception as e:return {'error':repr(e)}
def main():
 rows=list(csv.DictReader(open(INP,encoding='utf8')))
 inv=[r for r in rows if r.get('A_valid_B_invalid','').lower()=='true']
 dates=sorted({r['exact_event_date'] for r in inv})
 out={'inversion_n':len(inv),'dates':dates,'cases':inv,'mirrors':{}}
 for base in MIRRORS:
  md={}
  for ds in dates:
   y,m,d=ds.split('-');url=f'{base}{y}/{m}/{d}/'
   try:
    html=get(url);hrefs=re.findall(r'href=["\']([^"\']+)["\']',html,re.I)
    links=[]
    for h in hrefs:
     if h in ('../','./') or h.startswith('?'):continue
     u=urllib.parse.urljoin(url,h)
     links.append({'href':h,'url':u})
    cand=[x for x in links if any(t in x['href'].lower() for t in ['vrp','csv','json','routinator','fort','rpki','spool','.gz','.bz2','.xz','.zst','.tar'])]
    probe=(cand[:12] if cand else links[:12])
    for x in probe:x['head']=head(x['url'])
    md[ds]={'url':url,'n_links':len(links),'links':links[:50],'candidates':probe}
   except Exception as e:md[ds]={'url':url,'error':repr(e)}
  out['mirrors'][base]=md
 (OUT/'probe.json').write_text(json.dumps(out,indent=2),encoding='utf8')
 compact={b:{d:{'n':v.get('n_links'),'err':v.get('error'),'hrefs':[x['href'] for x in v.get('candidates',[])[:8]]} for d,v in md.items()} for b,md in out['mirrors'].items()}
 print(json.dumps({'inversion_n':len(inv),'dates':dates,'compact':compact},indent=2))
if __name__=='__main__':main()
