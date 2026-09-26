#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT"
mkdir -p "$OUT/patches"
WORK=/tmp/r07
rm -rf "$WORK"
mkdir -p "$WORK"
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
python3 - "$OUT" "$WORK" <<'PY'
from pathlib import Path
import subprocess, re, csv, sys, hashlib, os
out=Path(sys.argv[1]); work=Path(sys.argv[2])
repos=[x.strip() for x in (out/'frame.txt').read_text().splitlines() if x.strip()]
contract_re=re.compile(r'\b(requires|ensures|invariant|decreases|opens_invariants)\b')
proof_re=re.compile(r'\b(assert|assume|reveal|proof\s+fn|spec\s+fn|ghost|tracked|admit|lemma|invariant|decreases|requires|ensures|opens_invariants)\b')
exclude_parts={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}

def run(cwd,args,timeout=240):
    try:
        return subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=timeout)
    except subprocess.TimeoutExpired as e:
        class R: pass
        r=R(); r.returncode=124; r.stdout=(e.stdout or '') if isinstance(e.stdout,str) else ''; r.stderr='TIMEOUT'
        return r

def path_nonprod(p):
    parts=[x.lower() for x in p.replace('\\','/').split('/')]
    return any(x in exclude_parts for x in parts)

def parse_file_hunks(diff):
    files=[]; cur=None; hunk=None
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            if cur: files.append(cur)
            m=re.match(r'diff --git a/(.*?) b/(.*)',line)
            cur={'path':(m.group(2) if m else ''),'added':False,'deleted':False,'hunks':[]}
            hunk=None
        elif cur is not None and line.startswith('--- '):
            if line.strip()=='--- /dev/null': cur['added']=True
        elif cur is not None and line.startswith('+++ '):
            if line.strip()=='+++ /dev/null': cur['deleted']=True
        elif cur is not None and line.startswith('@@'):
            hunk=[]; cur['hunks'].append(hunk)
        elif cur is not None and hunk is not None and line[:1] in ('+','-') and not line.startswith(('+++','---')):
            hunk.append(line)
    if cur: files.append(cur)
    return files

def substantive_exec(text):
    s=text.strip()
    if not s or s in {'{','}','};','),','(',')','};'}: return False
    if s.startswith(('//','/*','*','*/','#[')): return False
    if contract_re.search(s): return False
    if proof_re.search(s): return False
    if re.match(r'^(use|mod|pub\s+mod|type|struct|enum|trait)\b',s): return False
    return True

summary=[]; commits=[]; strict=[]
for repo in repos:
    safe=repo.replace('/','__'); d=work/safe
    cp=subprocess.run(['timeout','600','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
    if cp.returncode!=0:
        (out/f'{safe}.clone_error.txt').write_text(cp.stderr[-12000:])
        summary.append([repo,'CLONE_FAIL',0,0,0,0,0])
        continue
    log=run(d,['git','log','--all','--format=%H','--since=2024-01-01','--until=2026-09-26T23:59:59Z','-G(requires|ensures|invariant|decreases|opens_invariants)','--','*.rs'],timeout=600)
    if log.returncode!=0:
        (out/f'{safe}.log_error.txt').write_text((log.stderr or '')[-12000:])
        summary.append([repo,'LOG_FAIL',0,0,0,0,0])
        continue
    shas=[]
    for x in log.stdout.splitlines():
        x=x.strip()
        if x and x not in shas: shas.append(x)
    prod=nonprod=0
    for sha in shas:
        par=run(d,['git','rev-parse',f'{sha}^'],timeout=60)
        parent=par.stdout.strip() if par.returncode==0 else ''
        if not parent: continue
        meta=run(d,['git','show','-s','--format=%cs%x09%s',sha],timeout=60).stdout.strip().split('\t',1)
        date=meta[0] if meta else ''
        subject=meta[1] if len(meta)>1 else ''
        num=run(d,['git','diff','--numstat',parent,sha,'--','*.rs'],timeout=120).stdout
        changed=0
        for ln in num.splitlines():
            ps=ln.split('\t')
            if len(ps)>=3 and ps[0].isdigit() and ps[1].isdigit(): changed += int(ps[0])+int(ps[1])
        diff=run(d,['git','diff','--unified=3',parent,sha,'--','*.rs'],timeout=240).stdout
        fs=parse_file_hunks(diff)
        contract_lines=0; exec_lines=0; strict_paths=[]
        for f in fs:
            if f['added'] or f['deleted']: continue
            for h in f['hunks']:
                ch=[x[1:] for x in h if x[:1] in ('+','-')]
                c=[x for x in ch if contract_re.search(x) and not x.strip().startswith(('//','/*','*'))]
                e=[x for x in ch if substantive_exec(x)]
                contract_lines += len(c); exec_lines += len(e)
                if c and e: strict_paths.append(f['path'])
        gate=bool(strict_paths and changed<=200)
        setting=''
        if gate:
            setting='NONPRODUCTION' if all(path_nonprod(p) for p in strict_paths) else 'PRODUCTION'
            if setting=='PRODUCTION': prod+=1
            else: nonprod+=1
            patch_name=f'{safe}__{sha}.diff'
            (out/'patches'/patch_name).write_text(diff)
            strict.append([repo,sha,parent,date,subject,changed,contract_lines,exec_lines,setting,';'.join(sorted(set(strict_paths))),patch_name])
        commits.append([repo,sha,parent,date,subject,changed,contract_lines,exec_lines,int(gate),setting])
    summary.append([repo,'OK',len(shas),len(commits),prod+nonprod,prod,nonprod])

with (out/'repo_summary.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','status','contract_diff_commits','cumulative_processed_rows','strict_total','strict_production','strict_nonproduction']); w.writerows(summary)
with (out/'contract_commits.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','strict_gate','setting']); w.writerows(commits)
with (out/'strict_candidates.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']); w.writerows(strict)
PY
cat > "$OUT/MANIFEST.md" <<'EOF'
# r07 cross-ecosystem holdout scan — Verus arm

The private holdout protocol was frozen before this history scan. Frame: the 8 unique source repositories named by the VeruSAGE-Bench source-project table. Date window 2024-01-01 through 2026-09-26. Discovery uses actual `.rs` Git diffs with literal Verus contract tokens, not PR text. Strict candidates satisfy the frozen <=200 changed-source-line, modified-existing-file, same-hunk contract+substantive-executable structural gate. Obvious test/example/benchmark/tutorial/generated/vendor paths are retained as NONPRODUCTION rather than silently dropped.

The structural gate is discovery-only. No verifier outcome is consulted here, and every strict candidate patch is preserved for later identity-blinded adjudication.
EOF
sha256sum "$OUT/frame.txt" "$OUT/repo_summary.csv" "$OUT/contract_commits.csv" "$OUT/strict_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
