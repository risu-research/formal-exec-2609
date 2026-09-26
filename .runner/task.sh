#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out; rm -rf "$OUT"; mkdir -p "$OUT/patches"; W=/tmp/vha; rm -rf "$W"; mkdir -p "$W"
cat > "$OUT/frame.txt" <<'EOF'
verus-lang/verified-ironkv
verus-lang/verified-memory-allocator
verus-lang/verified-node-replication
microsoft/verified-storage
EOF
printf 'repo,clone_status,rs_commits_window,contract_diff_commits,structural_candidates\n' > "$OUT/repo_summary.csv"
printf 'repo,commit,parent,date,subject,changed_rs_lines,contract_changed_lines,other_changed_lines,structural_candidate\n' > "$OUT/commit_candidates.csv"
python3 - "$OUT" "$W" <<'PY'
from pathlib import Path
import subprocess,re,csv,sys
out=Path(sys.argv[1]); work=Path(sys.argv[2]); repos=[x for x in out.joinpath('frame.txt').read_text().splitlines() if x]
cr=re.compile(r'\b(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)\b'); nr=re.compile(r'^\s*(//|/\*|\*|\*/|\{|\}|$)')
S=[];R=[]
for repo in repos:
 d=work/repo.replace('/','__'); cp=subprocess.run(['timeout','240','git','clone','--quiet','--single-branch','--filter=blob:none',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if cp.returncode: S.append([repo,'FAIL',0,0,0]); out.joinpath(repo.replace('/','__')+'.err').write_text(cp.stderr[-8000:]); continue
 def run(a,t=240):
  try:return subprocess.run(a,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=t)
  except subprocess.TimeoutExpired:
   class X: pass
   x=X();x.returncode=124;x.stdout='';x.stderr='TIMEOUT';return x
 win=['--since=2024-01-01','--until=2026-09-27']; allc=[x for x in run(['git','rev-list','HEAD',*win,'--','*.rs'],300).stdout.splitlines() if x]
 p=run(['git','log','HEAD',*win,'--format=%H','-G(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)','--','*.rs'],420)
 if p.returncode==124:S.append([repo,'PICKAXE_TIMEOUT',len(allc),0,0]);continue
 cs=[]
 for x in p.stdout.splitlines():
  if x and x not in cs:cs.append(x)
 st=0
 for sha in cs:
  par=run(['git','rev-parse',sha+'^']).stdout.strip();
  if not par:continue
  m=run(['git','show','-s','--format=%cs%x09%s',sha]).stdout.strip().split('\t',1); date=m[0] if m else ''; subj=m[1] if len(m)>1 else ''
  diff=run(['git','diff','--unified=3',par,sha,'--','*.rs'],300).stdout; ch=[l[1:] for l in diff.splitlines() if l.startswith(('+','-')) and not l.startswith(('+++','---'))]
  cc=[l for l in ch if cr.search(l)]; other=[l for l in ch if not cr.search(l) and not nr.match(l)]; flag=bool(cc and other)
  if flag:st+=1; out.joinpath('patches',repo.replace('/','__')+'__'+sha+'.diff').write_text(diff)
  R.append([repo,sha,par,date,subj,len(ch),len(cc),len(other),int(flag)])
 S.append([repo,'OK',len(allc),len(cs),st])
with out.joinpath('repo_summary.csv').open('a',newline='') as f:csv.writer(f).writerows(S)
with out.joinpath('commit_candidates.csv').open('a',newline='') as f:csv.writer(f).writerows(R)
PY
cat "$OUT/repo_summary.csv"
