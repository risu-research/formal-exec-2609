#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT"
mkdir -p "$OUT/patches"
WORK=/tmp/r05
rm -rf "$WORK"
mkdir -p "$WORK"

cat > "$OUT/frame.txt" <<'EOF'
microsoft/Ironclad
Consensys-Incorporated/evm-dafny
sun-wendy/DafnyBench
franck44/evm-dis
Mondego/dafny-synthesis
ChuyueSun/Clover
dafny-lang/libraries
lemmy/lets-prove-blocking-queue
mit-pdos/daisy-nfsd
vmware-labs/verified-betrfs
EOF
printf 'repo,clone_status,scan_status,dfy_commits,contract_diff_commits,structural_candidates\n' > "$OUT/repo_summary.csv"
printf 'repo,commit,parent,date,subject,contract_changed_lines,other_changed_lines,structural_candidate,patch_sha256\n' > "$OUT/commit_candidates.csv"

python3 - "$OUT" "$WORK" <<'PY'
from pathlib import Path
import subprocess, re, csv, sys, hashlib, gzip
out=Path(sys.argv[1]); work=Path(sys.argv[2])
repos=[x.strip() for x in (out/'frame.txt').read_text().splitlines() if x.strip()]
contract_re=re.compile(r'\b(requires|ensures|invariant|modifies|reads|decreases)\b')
noise_re=re.compile(r'^\s*(//|/\*|\*|\*/|\{|\}|$)')

def run(args,cwd=None,timeout=240):
    try:
        p=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        return p.returncode,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'),'OK'
    except subprocess.TimeoutExpired as e:
        so=(e.stdout or b'').decode('utf-8','replace') if isinstance(e.stdout,(bytes,bytearray)) else (e.stdout or '')
        se=(e.stderr or b'').decode('utf-8','replace') if isinstance(e.stderr,(bytes,bytearray)) else (e.stderr or '')
        return 124,so,se,'TIMEOUT'

for repo in repos:
    safe=repo.replace('/','__'); d=work/safe
    rc,so,se,st=run(['git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],timeout=240)
    if rc!=0:
        with (out/'repo_summary.csv').open('a',newline='') as f: csv.writer(f).writerow([repo,'FAIL',st,0,0,0])
        (out/f'{safe}.clone_error.txt').write_text(se[-12000:])
        continue
    # all Dafny-touching commits
    rc,so,se,st=run(['git','rev-list','--all','--','*.dfy'],cwd=d,timeout=300)
    all_count=len([x for x in so.splitlines() if x.strip()]) if rc==0 else -1
    if rc!=0:
        with (out/'repo_summary.csv').open('a',newline='') as f: csv.writer(f).writerow([repo,'OK',f'REVLIST_{st}',all_count,0,0])
        continue
    # independent diff-based pickaxe; no PR title/body text involved
    rc,so,se,st=run(['git','log','--all','--format=%H','-G(requires|ensures|invariant|modifies|reads|decreases)','--','*.dfy'],cwd=d,timeout=420)
    if rc!=0:
        with (out/'repo_summary.csv').open('a',newline='') as f: csv.writer(f).writerow([repo,'OK',f'PICKAXE_{st}',all_count,0,0])
        (out/f'{safe}.scan_error.txt').write_text(se[-12000:])
        continue
    seen=set(); cands=[]
    for x in so.splitlines():
        x=x.strip()
        if x and x not in seen: seen.add(x); cands.append(x)
    structural=0; scan_status='OK'
    for sha in cands:
        rc,parent,_,pst=run(['git','rev-parse',f'{sha}^'],cwd=d,timeout=20)
        parent=parent.strip()
        if rc!=0 or not parent: continue
        rc,meta,_,_=run(['git','show','-s','--format=%cs%x09%s',sha],cwd=d,timeout=20)
        parts=meta.strip().split('\t',1); date=parts[0] if parts else ''; subj=parts[1] if len(parts)>1 else ''
        rc,diff,err,dst=run(['git','diff','--unified=0',parent,sha,'--','*.dfy'],cwd=d,timeout=180)
        if rc!=0:
            scan_status='PARTIAL'; continue
        changed=[]
        for ln in diff.splitlines():
            if ln.startswith(('+++','---','@@','diff ','index ')): continue
            if ln.startswith(('+','-')): changed.append(ln[1:])
        contract=[ln for ln in changed if contract_re.search(ln)]
        other=[ln for ln in changed if not contract_re.search(ln) and not noise_re.match(ln)]
        structural_flag=bool(contract and other)
        if structural_flag: structural += 1
        digest=hashlib.sha256(diff.encode('utf-8','replace')).hexdigest()
        with (out/'commit_candidates.csv').open('a',newline='') as f:
            csv.writer(f).writerow([repo,sha,parent,date,subj.replace('\n',' '),len(contract),len(other),int(structural_flag),digest])
        if structural_flag:
            pp=out/'patches'/f'{safe}__{sha}.diff.gz'
            with gzip.open(pp,'wt',encoding='utf-8') as g: g.write(diff)
    with (out/'repo_summary.csv').open('a',newline='') as f:
        csv.writer(f).writerow([repo,'OK',scan_status,all_count,len(cands),structural])
PY

cat > "$OUT/MANIFEST.md" <<'EOF'
# Post-freeze diff-history sensitivity arm — robust rerun

The primary 41-PR screening denominator was frozen before this arm. This arm does not alter it.

Same ten repositories are cloned. Actual `.dfy` Git history is scanned with `git log -G` for changed `requires|ensures|invariant|modifies|reads|decreases` lines, independent of PR title/body text. A permissive structural flag requires at least one other substantive changed `.dfy` line in the same commit; it is discovery-only and must not be interpreted as an executable-body repair without manual adjudication.

Repository failures/timeouts are isolated and recorded. Candidate patches are gzip-preserved with hashes. This rerun fixes the first sensitivity attempt's UTF-8 decoding failure by decoding Git output with replacement and checkpointing every repository/candidate row as it is processed.
EOF
sha256sum "$OUT/repo_summary.csv" "$OUT/commit_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
