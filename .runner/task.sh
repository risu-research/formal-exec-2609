#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT/patches"
WORK=/tmp/r15; rm -rf "$WORK"; mkdir -p "$WORK"
REPO=FStarLang/FStar
D="$WORK/repo"
timeout 600 git clone --quiet --filter=blob:none --no-checkout https://github.com/$REPO.git "$D"
cat > "$OUT/windows.txt" <<'EOF'
2024-04-01 2024-04-30
2024-05-01 2024-05-31
2024-06-01 2024-06-30
2026-07-01 2026-07-31
2026-08-01 2026-08-31
2026-09-01 2026-09-26
EOF
python3 - "$D" "$OUT" <<'PY'
from pathlib import Path
import subprocess,re,csv,sys
D=Path(sys.argv[1]); OUT=Path(sys.argv[2]); REPO='FStarLang/FStar'
CRE=re.compile(r'\b(requires|ensures|requires_|ensures_|pre|post)\b'); PRE=re.compile(r'\b(assert|assume|admit|lemma|by_tactic|calc|rewrite|unfold|norm|simp|squash|requires|ensures|requires_|ensures_|pre|post)\b'); EX={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}
def run(a,t=180):
 try:return subprocess.run(a,cwd=D,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
 except subprocess.TimeoutExpired:
  class R:pass
  r=R();r.returncode=124;r.stdout='';r.stderr='TIMEOUT';return r
def comment(s):return s.strip().startswith(('//','(*','*','*)'))
def nonprod(p):return any(x.lower() in EX for x in p.replace('\\','/').split('/'))
def exe(s):
 s=s.strip(); return bool(s and s not in {'(',')','{','}','[',']',';'} and not comment(s) and not CRE.search(s) and not PRE.search(s) and not re.match(r'^(open|module|include|type|val|effect|class|instance)\b',s))
def parse(df):
 fs=[];cur=None;h=None
 for ln in df.splitlines():
  if ln.startswith('diff --git '):
   if cur:fs.append(cur)
   m=re.match(r'diff --git a/(.*?) b/(.*)',ln);cur={'p':m.group(2) if m else '','a':0,'d':0,'hs':[]};h=None
  elif cur and ln.startswith('--- ') and ln.strip()=='--- /dev/null':cur['a']=1
  elif cur and ln.startswith('+++ ') and ln.strip()=='+++ /dev/null':cur['d']=1
  elif cur and ln.startswith('@@'):h=[];cur['hs'].append(h)
  elif cur and h is not None and ln[:1] in '+-' and not ln.startswith(('+++','---')):h.append(ln)
 if cur:fs.append(cur)
 return fs
recs={}; fails=[]
for line in (OUT/'windows.txt').read_text().splitlines():
 a,b=line.split(); p=run(['git','log','--all',f'--since={a}',f'--until={b}T23:59:59Z','-G(requires|ensures|requires_|ensures_|(^|[^A-Za-z0-9_])(pre|post)([^A-Za-z0-9_]|$))','--format=@@@%H%x09%P%x09%cs%x09%s','--numstat','--','*.fst','*.fsti'],180)
 if p.returncode: fails.append(f'{a},{b},{p.returncode}'); continue
 cur=None
 for ln in p.stdout.splitlines():
  if ln.startswith('@@@'):
   if cur:recs[cur['sha']]=cur
   z=ln[3:].split('\t',3);cur={'sha':z[0],'parents':z[1] if len(z)>1 else '','date':z[2] if len(z)>2 else '','sub':z[3] if len(z)>3 else '','n':0}
  elif cur:
   z=ln.split('\t')
   if len(z)>=3 and z[0].isdigit() and z[1].isdigit():cur['n']+=int(z[0])+int(z[1])
 if cur:recs[cur['sha']]=cur
rows=[]
for r in recs.values():
 if not r['parents'].split() or r['n']>200:continue
 par=r['parents'].split()[0]; df=run(['git','diff','--unified=3',par,r['sha'],'--','*.fst','*.fsti'],120).stdout; paths=[];cc=ee=0
 for f in parse(df):
  if f['a'] or f['d']:continue
  for h in f['hs']:
   ch=[x[1:] for x in h if x[:1] in '+-']; c=[x for x in ch if CRE.search(x) and not comment(x)]; e=[x for x in ch if exe(x)]; cc+=len(c);ee+=len(e)
   if c and e:paths.append(f['p'])
 if paths:
  setting='NONPRODUCTION' if all(nonprod(p) for p in paths) else 'PRODUCTION'; pf=f'FStarLang__FStar__{r["sha"]}.diff'; (OUT/'patches'/pf).write_text(df); rows.append([REPO,r['sha'],par,r['date'],r['sub'],r['n'],cc,ee,setting,';'.join(sorted(set(paths))),pf])
with (OUT/'strict_candidates.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']);w.writerows(rows)
(OUT/'window_failures.csv').write_text('start,end,exit_code\n'+'\n'.join(fails)+'\n')
prod=sum(x[8]=='PRODUCTION' for x in rows); np=sum(x[8]=='NONPRODUCTION' for x in rows)
(OUT/'summary.txt').write_text(f'commits_seen={len(recs)}\nstrict={len(rows)}\nproduction={prod}\nnonproduction={np}\nwindow_failures={len(fails)}\n')
PY
cat > "$OUT/MANIFEST.md" <<'EOF'
# r15 fixed-month salvage
Exact r14 six calendar-month windows and frozen F* gates. The only semantic-neutral change from failed r14 is defining the repository constant inside the Python process; r14's NameError is preserved separately.
EOF
sha256sum "$OUT/windows.txt" "$OUT/strict_candidates.csv" "$OUT/window_failures.csv" "$OUT/summary.txt" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/summary.txt"
