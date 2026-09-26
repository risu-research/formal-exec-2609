#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT" && mkdir -p "$OUT/patches" "$OUT/repos"
WORK=/tmp/r10
rm -rf "$WORK" && mkdir -p "$WORK"
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
cat > /tmp/r10_one.py <<'PY'
from pathlib import Path
import subprocess,re,csv,sys
repo=sys.argv[1];out=Path(sys.argv[2]);work=Path(sys.argv[3]);safe=repo.replace('/','__');d=work/safe
cre=re.compile(r'\b(requires|ensures|requires_|ensures_|pre|post)\b'); pre=re.compile(r'\b(assert|assume|admit|lemma|by_tactic|calc|rewrite|unfold|norm|simp|squash|requires|ensures|requires_|ensures_|pre|post)\b')
exclude={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}
def run(a,t=180):
  try:return subprocess.run(a,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
  except subprocess.TimeoutExpired:
    class R:pass
    r=R();r.returncode=124;r.stdout='';r.stderr='TIMEOUT';return r
def nonprod(p):return any(x.lower() in exclude for x in p.replace('\\','/').split('/'))
def comment(s):return s.strip().startswith(('//','(*','*','*)'))
def parse(df):
  fs=[];cur=None;h=None
  for line in df.splitlines():
    if line.startswith('diff --git '):
      if cur:fs.append(cur)
      m=re.match(r'diff --git a/(.*?) b/(.*)',line);cur={'p':m.group(2) if m else '','a':0,'d':0,'h':[]};h=None
    elif cur is not None and line.startswith('--- ') and line.strip()=='--- /dev/null':cur['a']=1
    elif cur is not None and line.startswith('+++ ') and line.strip()=='+++ /dev/null':cur['d']=1
    elif cur is not None and line.startswith('@@'):h=[];cur['h'].append(h)
    elif cur is not None and h is not None and line[:1] in '+-' and not line.startswith(('+++','---')):h.append(line)
  if cur:fs.append(cur)
  return fs
def exe(s):
  s=s.strip()
  if not s or s in {'(',')','{','}','[',']',';'} or comment(s):return False
  if cre.search(s) or pre.search(s):return False
  if re.match(r'^(open|module|include|type|val|effect|class|instance)\b',s):return False
  return True
rd=out/'repos'/safe;rd.mkdir(parents=True,exist_ok=True)
cp=subprocess.run(['timeout','420','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
header='repo,status,contract_diff_commits,processed_rows,strict_total,strict_production,strict_nonproduction\n'
if cp.returncode:
  (rd/'error.txt').write_text(cp.stderr[-10000:]);(rd/'summary.csv').write_text(header+f'{repo},CLONE_FAIL,0,0,0,0,0\n');sys.exit()
sp=run(['git','log','--all','-1','--format=%H','--','*.fst','*.fsti'],120)
if sp.returncode or not sp.stdout.strip():(rd/'summary.csv').write_text(header+f'{repo},NO_FSTAR_SOURCE,0,0,0,0,0\n');sys.exit()
lg=run(['git','log','--all','--format=%H','--since=2024-01-01','--until=2026-09-26T23:59:59Z','-G(requires|ensures|requires_|ensures_|(^|[^A-Za-z0-9_])(pre|post)([^A-Za-z0-9_]|$))','--','*.fst','*.fsti'],480)
if lg.returncode:
  (rd/'error.txt').write_text(lg.stderr[-10000:]);(rd/'summary.csv').write_text(header+f'{repo},LOG_FAIL,0,0,0,0,0\n');sys.exit()
shas=list(dict.fromkeys(x.strip() for x in lg.stdout.splitlines() if x.strip()));rows=[];strict=[]
for sha in shas:
  p=run(['git','rev-parse',sha+'^'],30)
  if p.returncode:continue
  par=p.stdout.strip();m=run(['git','show','-s','--format=%cs%x09%s',sha],30).stdout.strip().split('\t',1);date=m[0] if m else '';sub=m[1] if len(m)>1 else ''
  ns=run(['git','diff','--numstat',par,sha,'--','*.fst','*.fsti'],60).stdout;changed=0
  for z in ns.splitlines():
    q=z.split('\t')
    if len(q)>=3 and q[0].isdigit() and q[1].isdigit():changed+=int(q[0])+int(q[1])
  df=run(['git','diff','--unified=3',par,sha,'--','*.fst','*.fsti'],90).stdout;paths=[];cc=ee=0
  for f in parse(df):
    if f['a'] or f['d']:continue
    for h in f['h']:
      ch=[x[1:] for x in h if x[:1] in '+-'];c=[x for x in ch if cre.search(x) and not comment(x)];e=[x for x in ch if exe(x)];cc+=len(c);ee+=len(e)
      if c and e:paths.append(f['p'])
  gate=bool(paths and changed<=200);setting=''
  if gate:
    setting='NONPRODUCTION' if all(nonprod(x) for x in paths) else 'PRODUCTION';pf=f'{safe}__{sha}.diff';(out/'patches'/pf).write_text(df);strict.append([repo,sha,par,date,sub,changed,cc,ee,setting,';'.join(sorted(set(paths))),pf])
  rows.append([repo,sha,par,date,sub,changed,cc,ee,int(gate),setting])
with (rd/'commits.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','strict_gate','setting']);w.writerows(rows)
with (rd/'strict.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']);w.writerows(strict)
prod=sum(x[8]=='PRODUCTION' for x in strict);np=sum(x[8]=='NONPRODUCTION' for x in strict);(rd/'summary.csv').write_text(header+f'{repo},OK,{len(shas)},{len(rows)},{len(strict)},{prod},{np}\n')
PY
export OUT WORK
cat "$OUT/frame.txt" | xargs -I{} -P4 bash -c 'python3 /tmp/r10_one.py "$1" "$OUT" "$WORK"' _ {}
{ echo 'repo,status,contract_diff_commits,processed_rows,strict_total,strict_production,strict_nonproduction'; find "$OUT/repos" -name summary.csv -exec tail -n +2 {} \; | sort; } > "$OUT/repo_summary.csv"
{ echo 'repo,commit,parent,date,subject,changed_source_lines,contract_changed_lines,exec_changed_lines,strict_gate,setting'; find "$OUT/repos" -name commits.csv -exec tail -n +2 {} \; | sort; } > "$OUT/contract_commits.csv"
{ echo 'repo,commit,parent,date,subject,changed_source_lines,contract_changed_lines,exec_changed_lines,setting,strict_paths,patch_file'; find "$OUT/repos" -name strict.csv -exec tail -n +2 {} \; | sort; } > "$OUT/strict_candidates.csv"
cat > "$OUT/MANIFEST.md" <<'EOF'
# r10 F* bounded-parallel fallback
Same frozen r08 20-repository frame, source gate, temporal window, literal tokens, strict structural gate, and production-path gate. Only execution topology changed: repository scans are executed with bounded parallelism (max 4). Original sequential r08 is retained.
EOF
sha256sum "$OUT/frame.txt" "$OUT/repo_summary.csv" "$OUT/contract_commits.csv" "$OUT/strict_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
