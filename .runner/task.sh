#!/usr/bin/env bash
set -euo pipefail
REPO=unsoundsystem/rlsf-verified; OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT/patches"; W=/tmp/vhu; rm -rf "$W"; git clone --quiet --single-branch --filter=blob:none "https://github.com/$REPO.git" "$W"
python3 - "$REPO" "$OUT" "$W" <<'PY'
import sys,subprocess,re,csv
from pathlib import Path
repo,out,w=sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3]); cr=re.compile(r'\b(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)\b'); nr=re.compile(r'^\s*(//|/\*|\*|\*/|\{|\}|$)')
def r(a,t=420): return subprocess.run(a,cwd=w,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
win=['--since=2024-01-01','--until=2026-09-27']; allc=[x for x in r(['git','rev-list','HEAD',*win,'--','*.rs']).stdout.splitlines() if x]; cs=[]
for x in r(['git','log','HEAD',*win,'--format=%H','-G(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)','--','*.rs'],600).stdout.splitlines():
 if x and x not in cs:cs.append(x)
rows=[];st=0
for s in cs:
 p=r(['git','rev-parse',s+'^']).stdout.strip(); m=r(['git','show','-s','--format=%cs%x09%s',s]).stdout.strip().split('\t',1); d=r(['git','diff','--unified=3',p,s,'--','*.rs']).stdout; ch=[l[1:] for l in d.splitlines() if l.startswith(('+','-')) and not l.startswith(('+++','---'))]; cc=[l for l in ch if cr.search(l)]; oo=[l for l in ch if not cr.search(l) and not nr.match(l)]; f=bool(cc and oo); st+=int(f)
 if f:(out/'patches'/f'{s}.diff').write_text(d)
 rows.append([repo,s,p,m[0] if m else '',m[1] if len(m)>1 else '',len(ch),len(cc),len(oo),int(f)])
with (out/'repo_summary.csv').open('w',newline='') as f:csv.writer(f).writerows([['repo','clone_status','rs_commits_window','contract_diff_commits','structural_candidates'],[repo,'OK',len(allc),len(cs),st]])
with (out/'commit_candidates.csv').open('w',newline='') as f: cw=csv.writer(f);cw.writerow(['repo','commit','parent','date','subject','changed_rs_lines','contract_changed_lines','other_changed_lines','structural_candidate']);cw.writerows(rows)
PY
cat "$OUT/repo_summary.csv"