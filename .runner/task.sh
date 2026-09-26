#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT/patches" "$OUT/repos"
WORK=/tmp/r12; rm -rf "$WORK"; mkdir -p "$WORK"
cat > "$OUT/frame.txt" <<'EOF'
FStarLang/FStar
FStarLang/karamel
FStarLang/fstar-mode.el
FStarLang/AlgoStar
FStarLang/steel
FStarLang/pulse
FStarLang/fstar-vscode-assistant
FStarLang/VimFStar
FStarLang/kuiper
FStarLang/pal
FStarLang/fstar-layer
FStarLang/proof-copilot
FStarLang/pulse-verified-gc
FStarLang/fstarlang.github.io
FStarLang/fstar-mcp
FStarLang/pulse-tutorial-24
FStarLang/pulse-sandbox
FStarLang/LowStar
FStarLang/fstar_dataset
FStarLang/3rdparty
EOF
cat >/tmp/r12.py <<'PY'
from pathlib import Path
import subprocess,re,csv,sys
repo,out_s,work_s=sys.argv[1:];out=Path(out_s);work=Path(work_s);safe=repo.replace('/','__');d=work/safe;rd=out/'repos'/safe;rd.mkdir(parents=True,exist_ok=True)
CRE=re.compile(r'\b(requires|ensures|requires_|ensures_|pre|post)\b'); PRE=re.compile(r'\b(assert|assume|admit|lemma|by_tactic|calc|rewrite|unfold|norm|simp|squash|requires|ensures|requires_|ensures_|pre|post)\b');EX={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}
def run(a,t=300):
 try:return subprocess.run(a,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
 except subprocess.TimeoutExpired:
  class R:pass
  r=R();r.returncode=124;r.stdout='';r.stderr='TIMEOUT';return r
def comment(s):return s.strip().startswith(('//','(*','*','*)'))
def nonprod(p):return any(x.lower() in EX for x in p.replace('\\','/').split('/'))
def exe(s):
 s=s.strip()
 return bool(s and s not in {'(',')','{','}','[',']',';'} and not comment(s) and not CRE.search(s) and not PRE.search(s) and not re.match(r'^(open|module|include|type|val|effect|class|instance)\b',s))
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
header='repo,status,contract_diff_commits,size_eligible,strict_total,strict_production,strict_nonproduction\n'
cp=subprocess.run(['timeout','420','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
if cp.returncode:(rd/'error.txt').write_text(cp.stderr[-10000:]);(rd/'summary.csv').write_text(header+f'{repo},CLONE_FAIL,0,0,0,0,0\n');sys.exit()
sp=run(['git','log','--all','-1','--format=%H','--','*.fst','*.fsti'],120)
if sp.returncode or not sp.stdout.strip():(rd/'summary.csv').write_text(header+f'{repo},NO_FSTAR_SOURCE,0,0,0,0,0\n');sys.exit()
lg=run(['git','log','--all','--since=2024-01-01','--until=2026-09-26T23:59:59Z','-G(requires|ensures|requires_|ensures_|(^|[^A-Za-z0-9_])(pre|post)([^A-Za-z0-9_]|$))','--format=@@@%H%x09%P%x09%cs%x09%s','--numstat','--','*.fst','*.fsti'],720)
if lg.returncode:(rd/'error.txt').write_text(lg.stderr[-10000:]);(rd/'summary.csv').write_text(header+f'{repo},LOG_FAIL,0,0,0,0,0\n');sys.exit()
recs=[];cur=None
for ln in lg.stdout.splitlines():
 if ln.startswith('@@@'):
  if cur:recs.append(cur)
  z=ln[3:].split('\t',3);cur={'sha':z[0],'parents':z[1] if len(z)>1 else '','date':z[2] if len(z)>2 else '','sub':z[3] if len(z)>3 else '','n':0}
 elif cur:
  z=ln.split('\t')
  if len(z)>=3 and z[0].isdigit() and z[1].isdigit():cur['n']+=int(z[0])+int(z[1])
if cur:recs.append(cur)
small=[r for r in recs if r['parents'].split() and r['n']<=200];strict=[]
for r in small:
 par=r['parents'].split()[0];df=run(['git','diff','--unified=3',par,r['sha'],'--','*.fst','*.fsti'],120).stdout;paths=[];cc=ee=0
 for f in parse(df):
  if f['a'] or f['d']:continue
  for h in f['hs']:
   ch=[x[1:] for x in h if x[:1] in '+-'];c=[x for x in ch if CRE.search(x) and not comment(x)];e=[x for x in ch if exe(x)];cc+=len(c);ee+=len(e)
   if c and e:paths.append(f['p'])
 if paths:
  setting='NONPRODUCTION' if all(nonprod(p) for p in paths) else 'PRODUCTION';pf=f'{safe}__{r["sha"]}.diff';(out/'patches'/pf).write_text(df);strict.append([repo,r['sha'],par,r['date'],r['sub'],r['n'],cc,ee,setting,';'.join(sorted(set(paths))),pf])
with (rd/'strict.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']);w.writerows(strict)
prod=sum(x[8]=='PRODUCTION' for x in strict);np=sum(x[8]=='NONPRODUCTION' for x in strict);(rd/'summary.csv').write_text(header+f'{repo},OK,{len(recs)},{len(small)},{len(strict)},{prod},{np}\n')
PY
export OUT WORK
cat "$OUT/frame.txt" | xargs -I{} -P4 bash -c 'python3 /tmp/r12.py "$1" "$OUT" "$WORK"' _ {}
{ echo 'repo,status,contract_diff_commits,size_eligible,strict_total,strict_production,strict_nonproduction'; find "$OUT/repos" -name summary.csv -exec tail -n +2 {} \; | sort; } > "$OUT/repo_summary.csv"
{ echo 'repo,commit,parent,date,subject,changed_source_lines,contract_changed_lines,exec_changed_lines,setting,strict_paths,patch_file'; find "$OUT/repos" -name strict.csv -exec tail -n +2 {} \; | sort; } > "$OUT/strict_candidates.csv"
cat > "$OUT/MANIFEST.md" <<'EOF'
# r12 optimized F* holdout scan
Same private-frozen r08/r10 frame/rules. Execution optimization only: one batch `git log -G --numstat` size pass per source-bearing repository, exact patches only for <=200-line commits, bounded parallelism 4. NO_FSTAR_SOURCE and failures remain explicit.
EOF
sha256sum "$OUT/frame.txt" "$OUT/repo_summary.csv" "$OUT/strict_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
