#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT" && mkdir -p "$OUT/patches"
WORK=/tmp/verus_holdout
rm -rf "$WORK" && mkdir -p "$WORK"
cat > "$OUT/frame.txt" <<'EOF'
verus-lang/verified-ironkv
verus-lang/verified-memory-allocator
verus-lang/verified-node-replication
microsoft/verified-storage
matthias-brun/verified-nrkernel
asterinas/vostd
unsoundsystem/rlsf-verified
anvil-verifier/verus-tla
EOF
printf 'repo,clone_status,rs_commits_window,contract_diff_commits,structural_candidates\n' > "$OUT/repo_summary.csv"
printf 'repo,commit,parent,date,subject,changed_rs_lines,contract_changed_lines,other_changed_lines,structural_candidate\n' > "$OUT/commit_candidates.csv"
python3 - "$OUT" "$WORK" <<'PY'
from pathlib import Path
import subprocess, re, csv, sys
out=Path(sys.argv[1]); work=Path(sys.argv[2])
repos=[x.strip() for x in (out/'frame.txt').read_text().splitlines() if x.strip()]
contract_re=re.compile(r'\b(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)\b')
noise_re=re.compile(r'^\s*(//|/\*|\*|\*/|\{|\}|$)')
summary=[]; rows=[]
for repo in repos:
    safe=repo.replace('/','__'); d=work/safe
    cp=subprocess.run(['timeout','300','git','clone','--quiet','--filter=blob:none',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if cp.returncode!=0:
        summary.append([repo,'FAIL',0,0,0]); (out/f'{safe}.clone_error.txt').write_text(cp.stderr[-10000:]); continue
    def run(args, timeout=300):
        try:
            return subprocess.run(args,cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=timeout)
        except subprocess.TimeoutExpired as e:
            class R: pass
            r=R(); r.returncode=124; r.stdout=(e.stdout or '') if isinstance(e.stdout,str) else ''; r.stderr='TIMEOUT'; return r
    window=['--since=2024-01-01','--until=2026-09-27']
    p=run(['git','rev-list','--all',*window,'--','*.rs'],360)
    all_commits=[x.strip() for x in p.stdout.splitlines() if x.strip()]
    p=run(['git','log','--all',*window,'--format=%H','-G(requires|ensures|invariant|invariant_except_break|invariant_ensures|decreases|recommends|opens_invariants|no_unwind)','--','*.rs'],600)
    if p.returncode==124:
        summary.append([repo,'PICKAXE_TIMEOUT',len(all_commits),0,0]); (out/f'{safe}.pickaxe_error.txt').write_text('TIMEOUT\n'); continue
    cands=[]
    for x in p.stdout.splitlines():
        x=x.strip()
        if x and x not in cands: cands.append(x)
    structural=0
    for sha in cands:
        parent=run(['git','rev-parse',f'{sha}^']).stdout.strip()
        if not parent: continue
        meta=run(['git','show','-s','--format=%cs%x09%s',sha]).stdout.strip().split('\t',1)
        date=meta[0] if meta else ''; subj=meta[1] if len(meta)>1 else ''
        diff=run(['git','diff','--unified=3',parent,sha,'--','*.rs'],300).stdout
        changed=[]
        for ln in diff.splitlines():
            if ln.startswith(('+++','---','@@','diff ','index ')): continue
            if ln.startswith(('+','-')): changed.append(ln[1:])
        contract=[ln for ln in changed if contract_re.search(ln)]
        other=[ln for ln in changed if not contract_re.search(ln) and not noise_re.match(ln)]
        flag=bool(contract and other)
        if flag:
            structural+=1
            (out/'patches'/f'{safe}__{sha}.diff').write_text(diff)
        rows.append([repo,sha,parent,date,subj.replace('\n',' '),len(changed),len(contract),len(other),int(flag)])
    summary.append([repo,'OK',len(all_commits),len(cands),structural])
with (out/'repo_summary.csv').open('a',newline='') as f: csv.writer(f).writerows(summary)
with (out/'commit_candidates.csv').open('a',newline='') as f: csv.writer(f).writerows(rows)
PY
cat > "$OUT/MANIFEST.md" <<'EOF'
# Verus cross-ecosystem primary holdout scan
The repository frame and rules were frozen in the private evidence archive before this run. This scan uses actual `.rs` Git diffs in the fixed 2024-01-01..2026-09-26 window and does not use PR/commit text for inclusion. Structural flags are discovery-only and are not positive cases. All zeroes, timeouts, and failures are retained.
EOF
sha256sum "$OUT/repo_summary.csv" "$OUT/commit_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
