#!/usr/bin/env python3
"""Replay already frozen v3 incremental SMT2 queries without altering models/targets.

A short prefix is q0..q5; the original full bound is q0..q39. Timeouts
and partial check-sat outputs are NOT non-reachability certificates.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

if len(sys.argv) != 5:
    raise SystemExit('usage: runner.py ROOT OUT ARM SOLVER')
root, out, arm, solver = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
assert arm in ('E_removed', 'E_restored')
assert solver in ('z3', 'cvc5')
assert (root/'incremental/QUERY_MANIFEST.json').is_file()
out.mkdir(parents=True, exist_ok=True)
program = {'z3':['z3', '-smt2'], 'cvc5':['cvc5', '--lang', 'smt2']}[solver]
version = subprocess.run([program[0], '--version'], capture_output=True, text=True, timeout=15)
assert version.returncode == 0
results = {'schema':'e5-s1-v3-dual-horizon-runner-v1', 'arm':arm, 'solver':solver,
           'version':version.stdout.strip(), 'source_commit':'d511239e19be8fcc7f340a64554ea93699637e62',
           'prior_run':35122112448, 'frozen_protocol_commit':'bef7ecaa0c6186bc4958dd9a4101dd100b91268c',
           'queries':{}}
manifest = json.loads((root/'incremental/QUERY_MANIFEST.json').read_text())
assert manifest['schema'] == 'e5-s1-incremental-monitor-free-v3'

def query(mode, short):
    src = root/'incremental'/f'{arm}_{mode}.smt2'
    data = src.read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest['models'][arm]['modes'][mode]['sha256']
    if not short:
        return data, 40 if mode in ('pin_any','output_only_any') else 1
    assert mode in ('pin_any','output_only_any')
    lines = data.decode().splitlines(keepends=True)
    out_lines = []
    checks = 0
    for line in lines:
        if line.strip() == '(exit)':
            break
        out_lines.append(line)
        if line.strip() == '(check-sat)':
            checks += 1
        if checks == 6 and line.strip() == '(pop 1)':
            break
    assert checks == 6 and out_lines[-1].strip() == '(pop 1)'
    joined = ''.join(out_lines) + '(exit)\n'
    assert joined.count('(check-sat)') == 6
    assert '(declare-fun |q6|' not in joined
    return joined.encode(), 6

for mode, short in [('negative_control', False), ('observed_cycle',False),
                    ('output_only_any',True), ('pin_any',True),
                    ('output_only_any',False), ('pin_any',False)]:
    label = f'{mode}_'+('H6' if short else ('H40' if mode in ('pin_any','output_only_any') else 'H6'))
    data, expected = query(mode, short)
    smt = out/f'{label}.smt2'
    smt.write_bytes(data)
    started = time.monotonic()
    try:
        p = subprocess.run(program+[str(smt)], capture_output=True, text=True, timeout=90)
        rc = p.returncode
        raw = p.stdout + ('\nSTDERR:\n'+p.stderr if p.stderr else '')
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b''
        stderr = exc.stderr or b''
        raw = (stdout.decode(errors='replace') if isinstance(stdout,bytes) else stdout)
        raw += '\nTIMEOUT_EXPIRED\n' + (stderr.decode(errors='replace') if isinstance(stderr,bytes) else stderr)
        rc = None
        timed_out = True
    seconds = round(time.monotonic()-started,3)
    (out/f'{label}.log').write_text(raw)
    statuses = re.findall(r'(?m)^(sat|unsat|unknown)\s*$',raw)
    clean = (not timed_out and rc == 0 and len(statuses) == expected and
             all(s in ('sat','unsat') for s in statuses) and 'error' not in raw.lower())
    results['queries'][label] = {'exit':rc, 'timeout':timed_out, 'elapsed_seconds':seconds,
        'expected_check_sat':expected, 'reported_check_sat':len(statuses),
        'statuses':statuses,'complete':clean,'smt_sha256':hashlib.sha256(data).hexdigest(),
        'log_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    (out/'RESULTS.json').write_text(json.dumps(results,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'arm':arm,'solver':solver,'mode':label,'seconds':seconds,'complete':clean,
                      'checks':len(statuses),'first':statuses[:7]},sort_keys=True),flush=True)

(root/'incremental/QUERY_MANIFEST.json').exists() or sys.exit(2)
(out/'SHA256SUMS').write_text(''.join(
    f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
    for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
print('RUNNER_DONE; science verdict requires cross-arm/cross-solver independent audit',flush=True)
