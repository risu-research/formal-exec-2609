#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT" && mkdir -p "$OUT/patches" "$OUT/repos"
WORK=/tmp/r09
rm -rf "$WORK" && mkdir -p "$WORK"
cat > "$OUT/frame.txt" <<'EOF'
anvil-verifier/anvil
verus-lang/verified-ironkv
verus-lang/verified-memory-allocator
verus-lang/verified-node-replication
matthias-brun/verified-nrkernel
mars-research/atmosphere
microsoft/verified-storage
secure-foundations/vest
EOF
cat > /tmp/r09_one.py <<'PY'
from pathlib import Path
import subprocess,re,csv,sys
repo=sys.argv[1]; out=Path(sys.argv[2]); work=Path(sys.argv[3]); safe=repo.replace('/','__'); d=work/safe
contract_re=re.compile(r'\b(requires|ensures|invariant|decreases|opens_invariants)\b')
proof_re=re.compile(r'\b(assert|assume|reveal|proof\s+fn|spec\s+fn|ghost|tracked|admit|lemma|invariant|decreases|requires|ensures|opens_invariants)\b')
exclude={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}
def run(args,t=180):
  try:return subprocess.run(args,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
  except subprocess.TimeoutExpired:
    class R:pass
    r=R();r.returncode=124;r.stdout='';r.stderr='TIMEOUT';return r
def nonprod(p): return any(x.lower() in exclude for x in p.replace('\\','/').split('/'))
def parse(diff):
  fs=[];cur=None;h=None
  for line in diff.splitlines():
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
  if not s or s in {'{','}','};','),','(',')'} or s.startswith(('//','/*','*','*/','#[')):return False
  if contract_re.search(s) or proof_re.search(s):return False
  if re.match(r'^(use|mod|pub\s+mod|type|struct|enum|trait)\b',s):return False
  return True
rd=out/'repos'/safe;rd.mkdir(parents=True,exist_ok=True)
cp=subprocess.run(['timeout','420','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
if cp.returncode!=0:
  (rd/'error.txt').write_text(cp.stderr[-10000:]); (rd/'summary.csv').write_text('repo,status,contract_diff_commits,strict_total,strict_production,strict_nonproduction\n'+f'{repo},CLONE_FAIL,0,0,0,0\n');sys.exit(0)
lg=run(['git','log','--all','--format=%H','--since=2024-01-01','--until=2026-09-26T23:59:59Z','-G(requires|ensures|invariant|decreases|opens_invariants)','--','*.rs'],420)
if lg.returncode!=0:
  (rd/'error.txt').write_text(lg.stderr[-10000:]); (rd/'summary.csv').write_text('repo,status,contract_diff_commits,strict_total,strict_production,strict_nonproduction\n'+f'{repo},LOG_FAIL,0,0,0,0\n');sys.exit(0)
shas=list(dict.fromkeys(x.strip() for x in lg.stdout.splitlines() if x.strip())); rows=[]; strict=[]
for sha in shas:
  p=run(['git','rev-parse',sha+'^'],30)
  if p.returncode:continue
  par=p.stdout.strip(); meta=run(['git','show','-s','--format=%cs%x09%s',sha],30).stdout.strip().split('\t',1); date=meta[0] if meta else '';sub=meta[1] if len(meta)>1 else ''
  ns=run(['git','diff','--numstat',par,sha,'--','*.rs'],60).stdout;changed=sum(int(a)+int(b) for a,b,*_ in (z.split('\t') for z in ns.splitlines()) if a.isdigit() and b.isdigit())
  df=run(['git','diff','--unified=3',par,sha,'--','*.rs'],90).stdout; paths=[];cc=ee=0
  for f in parse(df):
    if f['a'] or f['d']:continue
    for h in f['h']:
      ch=[x[1:] for x in h if x[:1] in '+-']; c=[x for x in ch if contract_re.search(x) and not x.strip().startswith(('//','/*','*'))]; e=[x for x in ch if exe(x)];cc+=len(c);ee+=len(e)
      if c and e:paths.append(f['p'])
  gate=bool(paths and changed<=200);setting=''
  if gate:
    setting='NONPRODUCTION' if all(nonprod(x) for x in paths) else 'PRODUCTION'; pf=f'{safe}__{sha}.diff';(out/'patches'/pf).write_text(df);strict.append([repo,sha,par,date,sub,changed,cc,ee,setting,';'.join(sorted(set(paths))),pf])
  rows.append([repo,sha,par,date,sub,changed,cc,ee,int(gate),setting])
with (rd/'commits.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','strict_gate','setting']);w.writerows(rows)
with (rd/'strict.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']);w.writerows(strict)
prod=sum(x[8]=='PRODUCTION' for x in strict);np=sum(x[8]=='NONPRODUCTION' for x in strict)
(rd/'summary.csv').write_text('repo,status,contract_diff_commits,strict_total,strict_production,strict_nonproduction\n'+f'{repo},OK,{len(shas)},{len(strict)},{prod},{np}\n')
PY
while IFS= read -r repo; do python3 /tmp/r09_one.py "$repo" "$OUT" "$WORK" & done < "$OUT/frame.txt"
wait
head -1 "$OUT/repos/$(head -1 "$OUT/frame.txt"|tr / _)"/summary.csv 2>/dev/null || echo 'repo,status,contract_diff_commits,strict_total,strict_production,strict_nonproduction' > /tmp/header
{ echo 'repo,status,contract_diff_commits,strict_total,strict_production,strict_nonproduction'; find "$OUT/repos" -name summary.csv -type f -exec tail -n +2 {} \; | sort; } > "$OUT/repo_summary.csv"
{ echo 'repo,commit,parent,date,subject,changed_source_lines,contract_changed_lines,exec_changed_lines,strict_gate,setting'; find "$OUT/repos" -name commits.csv -type f -exec tail -n +2 {} \; | sort; } > "$OUT/contract_commits.csv"
{ echo 'repo,commit,parent,date,subject,changed_source_lines,contract_changed_lines,exec_changed_lines,setting,strict_paths,patch_file'; find "$OUT/repos" -name strict.csv -type f -exec tail -n +2 {} \; | sort; } > "$OUT/strict_candidates.csv"
cat > "$OUT/MANIFEST.md" <<'EOF'
# r09 Verus parallel fallback
Same frozen r07 frame, date window, literal tokens, structural gate, and setting gate. Only execution topology changed: eight repository scans run independently in parallel. Original sequential r07 is retained for provenance/comparison.
EOF
sha256sum "$OUT/frame.txt" "$OUT/repo_summary.csv" "$OUT/contract_commits.csv" "$OUT/strict_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
