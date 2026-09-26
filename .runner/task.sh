#!/usr/bin/env bash
set -euo pipefail
OUT=.runner/out
rm -rf "$OUT"
mkdir -p "$OUT/patches"
WORK=/tmp/r08
rm -rf "$WORK"
mkdir -p "$WORK"
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
python3 - "$OUT" "$WORK" <<'PY'
from pathlib import Path
import subprocess, re, csv, sys, hashlib
out=Path(sys.argv[1]); work=Path(sys.argv[2])
repos=[x.strip() for x in (out/'frame.txt').read_text().splitlines() if x.strip()]
contract_re=re.compile(r'\b(requires|ensures|requires_|ensures_|pre|post)\b')
proof_re=re.compile(r'\b(assert|assume|admit|lemma|by_tactic|calc|rewrite|unfold|norm|simp|squash|requires|ensures|requires_|ensures_|pre|post)\b')
exclude_parts={'test','tests','example','examples','bench','benchmark','tutorial','generated','vendor'}

def run(cwd,args,timeout=240):
    try:
        return subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=timeout)
    except subprocess.TimeoutExpired as e:
        class R: pass
        r=R(); r.returncode=124; r.stdout=(e.stdout or '') if isinstance(e.stdout,str) else ''; r.stderr='TIMEOUT'
        return r

def path_nonprod(p):
    return any(seg.lower() in exclude_parts for seg in p.replace('\\','/').split('/'))

def is_comment(s):
    t=s.strip()
    return t.startswith(('//','(*','*','*)'))

def parse(diff):
    files=[]; cur=None; h=None
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            if cur: files.append(cur)
            m=re.match(r'diff --git a/(.*?) b/(.*)',line)
            cur={'path':m.group(2) if m else '','added':False,'deleted':False,'hunks':[]}; h=None
        elif cur is not None and line.startswith('--- '):
            if line.strip()=='--- /dev/null': cur['added']=True
        elif cur is not None and line.startswith('+++ '):
            if line.strip()=='+++ /dev/null': cur['deleted']=True
        elif cur is not None and line.startswith('@@'):
            h=[]; cur['hunks'].append(h)
        elif cur is not None and h is not None and line[:1] in ('+','-') and not line.startswith(('+++','---')):
            h.append(line)
    if cur: files.append(cur)
    return files

def substantive_exec(text):
    s=text.strip()
    if not s or s in {'(',')','{','}','[',']',';'}: return False
    if is_comment(s): return False
    if contract_re.search(s): return False
    if proof_re.search(s): return False
    if re.match(r'^(open|module|include|type|val|effect|class|instance)\b',s): return False
    return True

summary=[]; rows=[]; strict=[]
for repo in repos:
    safe=repo.replace('/','__'); d=work/safe
    cp=subprocess.run(['timeout','600','git','clone','--quiet','--filter=blob:none','--no-checkout',f'https://github.com/{repo}.git',str(d)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
    if cp.returncode!=0:
        (out/f'{safe}.clone_error.txt').write_text(cp.stderr[-12000:])
        summary.append([repo,'CLONE_FAIL',0,0,0,0,0])
        continue
    source_probe=run(d,['git','log','--all','-1','--format=%H','--','*.fst','*.fsti'],timeout=180)
    if source_probe.returncode!=0 or not source_probe.stdout.strip():
        summary.append([repo,'NO_FSTAR_SOURCE',0,0,0,0,0]); continue
    log=run(d,['git','log','--all','--format=%H','--since=2024-01-01','--until=2026-09-26T23:59:59Z','-G(requires|ensures|requires_|ensures_|(^|[^A-Za-z0-9_])(pre|post)([^A-Za-z0-9_]|$))','--','*.fst','*.fsti'],timeout=900)
    if log.returncode!=0:
        (out/f'{safe}.log_error.txt').write_text((log.stderr or '')[-12000:])
        summary.append([repo,'LOG_FAIL',0,0,0,0,0]); continue
    shas=[]
    for x in log.stdout.splitlines():
        x=x.strip()
        if x and x not in shas: shas.append(x)
    prod=nonprod=0; processed=0
    for sha in shas:
        par=run(d,['git','rev-parse',f'{sha}^'],timeout=60); parent=par.stdout.strip() if par.returncode==0 else ''
        if not parent: continue
        processed += 1
        meta=run(d,['git','show','-s','--format=%cs%x09%s',sha],timeout=60).stdout.strip().split('\t',1)
        date=meta[0] if meta else ''; subject=meta[1] if len(meta)>1 else ''
        num=run(d,['git','diff','--numstat',parent,sha,'--','*.fst','*.fsti'],timeout=120).stdout
        changed=0
        for ln in num.splitlines():
            ps=ln.split('\t')
            if len(ps)>=3 and ps[0].isdigit() and ps[1].isdigit(): changed += int(ps[0])+int(ps[1])
        diff=run(d,['git','diff','--unified=3',parent,sha,'--','*.fst','*.fsti'],timeout=300).stdout
        fs=parse(diff); contract_lines=0; exec_lines=0; paths=[]
        for f in fs:
            if f['added'] or f['deleted']: continue
            for h in f['hunks']:
                ch=[x[1:] for x in h if x[:1] in ('+','-')]
                c=[x for x in ch if contract_re.search(x) and not is_comment(x)]
                e=[x for x in ch if substantive_exec(x)]
                contract_lines += len(c); exec_lines += len(e)
                if c and e: paths.append(f['path'])
        gate=bool(paths and changed<=200)
        setting=''
        if gate:
            setting='NONPRODUCTION' if all(path_nonprod(p) for p in paths) else 'PRODUCTION'
            if setting=='PRODUCTION': prod+=1
            else: nonprod+=1
            pf=f'{safe}__{sha}.diff'; (out/'patches'/pf).write_text(diff)
            strict.append([repo,sha,parent,date,subject,changed,contract_lines,exec_lines,setting,';'.join(sorted(set(paths))),pf])
        rows.append([repo,sha,parent,date,subject,changed,contract_lines,exec_lines,int(gate),setting])
    summary.append([repo,'OK',len(shas),processed,prod+nonprod,prod,nonprod])

with (out/'repo_summary.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','status','contract_diff_commits','processed_rows','strict_total','strict_production','strict_nonproduction']); w.writerows(summary)
with (out/'contract_commits.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','strict_gate','setting']); w.writerows(rows)
with (out/'strict_candidates.csv').open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['repo','commit','parent','date','subject','changed_source_lines','contract_changed_lines','exec_changed_lines','setting','strict_paths','patch_file']); w.writerows(strict)
PY
cat > "$OUT/MANIFEST.md" <<'EOF'
# r08 cross-ecosystem holdout scan — F* arm

The private protocol was frozen before this scan. Frame: the captured first 20 non-archived/non-fork repositories in the official FStarLang GitHub organization. An automatic source gate records repositories with no reachable `.fst/.fsti` history as NO_FSTAR_SOURCE. Date window 2024-01-01 through 2026-09-26. Discovery uses actual `.fst/.fsti` history rather than PR text. Strict candidates follow the frozen modified-existing-file, <=200 changed-source-line, same-hunk contract+substantive-implementation gate. Test/example/benchmark/tutorial/generated/vendor matches are retained separately as NONPRODUCTION.

All clone, source-gate, and git-log failures are retained. Structural candidates are discovery-only and receive identity-blinded adjudication before any verifier replay.
EOF
sha256sum "$OUT/frame.txt" "$OUT/repo_summary.csv" "$OUT/contract_commits.csv" "$OUT/strict_candidates.csv" "$OUT/MANIFEST.md" > "$OUT/core_sha256.txt"
cat "$OUT/repo_summary.csv"
