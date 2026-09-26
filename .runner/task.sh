#!/usr/bin/env bash
set -u
OUT=.runner/out
rm -rf "$OUT"
mkdir -p "$OUT/patches"
WORK=/tmp/r04
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

printf 'repo,clone_status,dfy_commits,contract_diff_commits,structural_candidates\n' > "$OUT/repo_summary.csv"
printf 'repo,commit,parent,date,subject,contract_changed_lines,other_changed_lines,structural_candidate\n' > "$OUT/commit_candidates.csv"

python3 - "$OUT" "$WORK" <<'PY'
from pathlib import Path
import subprocess, re, csv, sys, os, shlex
out=Path(sys.argv[1]); work=Path(sys.argv[2])
repos=[x.strip() for x in (out/'frame.txt').read_text().splitlines() if x.strip()]
contract_re=re.compile(r'\b(requires|ensures|invariant|modifies|reads|decreases)\b')
noise_re=re.compile(r'^\s*(//|/\*|\*|\*/|\{|\}|$)')
proofish_re=re.compile(r'^\s*(assert\b|assume\b|reveal\b|calc\b|lemma\b|ghost\b)')
summary=[]; rows=[]
for repo in repos:
    safe=repo.replace('/','__'); d=work/safe
    clone=['timeout','240','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)]
    cp=subprocess.run(clone, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if cp.returncode!=0:
        summary.append([repo,'FAIL',0,0,0])
        (out/f'{safe}.clone_error.txt').write_text(cp.stderr[-8000:])
        continue
    def run(args, timeout=240):
        return subprocess.run(args,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    # Count all commits touching Dafny files.
    p=run(['git','rev-list','--all','--','*.dfy'])
    all_commits=[x for x in p.stdout.splitlines() if x.strip()]
    # Pickaxe directly over diffs: independent of PR title/body text.
    p=run(['git','log','--all','--format=%H','-G(requires|ensures|invariant|modifies|reads|decreases)','--','*.dfy'], timeout=360)
    cands=[]
    for x in p.stdout.splitlines():
        x=x.strip()
        if x and x not in cands: cands.append(x)
    structural=0
    for sha in cands:
        parent=run(['git','rev-parse',f'{sha}^']).stdout.strip()
        if not parent: continue
        meta=run(['git','show','-s','--format=%cs%x09%s',sha]).stdout.strip().split('\t',1)
        date=meta[0] if meta else ''
        subj=meta[1] if len(meta)>1 else ''
        diff=run(['git','diff','--unified=0',parent,sha,'--','*.dfy'], timeout=240).stdout
        changed=[]
        for ln in diff.splitlines():
            if ln.startswith(('+++','---','@@','diff ','index ')): continue
            if ln.startswith(('+','-')):
                changed.append(ln[1:])
        contract=[ln for ln in changed if contract_re.search(ln)]
        other=[ln for ln in changed if not contract_re.search(ln) and not noise_re.match(ln)]
        # permissive structural sensitivity: at least one contract-token line and one other substantive source line.
        structural_flag=bool(contract and other)
        if structural_flag: structural += 1
        rows.append([repo,sha,parent,date,subj.replace('\n',' '),len(contract),len(other),int(structural_flag)])
        if structural_flag:
            # Preserve the exact patch for later blinded/manual adjudication.
            patch=(out/'patches'/f'{safe}__{sha}.diff')
            patch.write_text(diff)
    summary.append([repo,'OK',len(all_commits),len(cands),structural])

with (out/'repo_summary.csv').open('a',newline='') as f:
    csv.writer(f).writerows(summary)
with (out/'commit_candidates.csv').open('a',newline='') as f:
    csv.writer(f).writerows(rows)
PY

cat > "$OUT/MANIFEST.md" <<'EOF'
# Post-freeze diff-history sensitivity arm

This arm was specified after the primary 41-PR screening denominator had been frozen. It does not change that denominator.

Purpose: audit recall bias from GitHub PR text search by scanning actual `.dfy` Git diffs independently of PR titles/bodies.

Frame: exactly the same frozen ten repositories. For every reachable Git commit touching `.dfy`, `git log -G` identifies commits whose actual diffs change one of `requires|ensures|invariant|modifies|reads|decreases`. A permissive structural flag records whether the same commit also changes at least one other substantive `.dfy` source line. This flag is discovery-only, not evidence of executable-body co-evolution; candidates require manual adjudication.

All hits, zero-hit repositories, clone failures, and exact candidate patches are retained. The primary denominator remains 41 PR hits regardless of what this sensitivity arm finds.
EOF

sha256sum "$OUT/repo_summary.csv" "$OUT/commit_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
exit 0
