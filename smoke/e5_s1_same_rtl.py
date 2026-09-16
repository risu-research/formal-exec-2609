#!/usr/bin/env python3
"""Targeted S1 same-child-RTL study. Frozen protocol: proofscope-lab@9c08db70af060ad2a1dfdd42fcdb4acb2bc5bf20."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT=Path('/tmp/e5-s1-zipcpu')
OUT=Path(os.environ['GITHUB_WORKSPACE'])/'e5-s1-same-rtl'
CHILD='d511239e19be8fcc7f340a64554ea93699637e62'
PARENT='128cab89f5edaae4699184f50c1334598ebe904e'
ORIGINAL_BLOB='60f5c6c75e2f396ccb7391d6ea1c3c6f55fec22b'
ORIGINAL_YS_BLOB='7d50d1c975eec1b94e70b4cbcadd62ce81085f7d'
COMMENTED='//always @(posedge i_clk)\n//\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n//\t\t`ASSUME(!i_lock);\n'
REINSTATED='\talways @(posedge i_clk)\n\t\tif ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock)))\n\t\t\t`ASSUME(!i_lock);\n'
MONITOR='''
// E5 S1 study-only observation. Identical in both arms; no design output drives it.
`ifdef FORMAL
reg f_s1_onset;
initial f_s1_onset = 1'b0;
always @(posedge i_clk)
    if (i_reset)
        f_s1_onset <= 1'b0;
    else
        f_s1_onset <= f_past_valid && $past(f_cyc) && !$past(i_lock) && i_lock;
wire f_s1_endpoint;
assign f_s1_endpoint = f_s1_onset && !f_cyc && (f_outstanding == 0)
        && !o_wb_stb_gbl && !o_wb_stb_lcl
        && (o_wb_cyc_gbl || o_wb_cyc_lcl);
always @(posedge i_clk)
    cover(f_s1_endpoint);
`endif
'''

def cmd(args,cwd=None,timeout=400):
    return subprocess.run(args,cwd=cwd,text=True,stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,timeout=timeout,check=False)

def digest(blob): return hashlib.sha256(blob).hexdigest()

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    meta={'protocol':'9c08db70af060ad2a1dfdd42fcdb4acb2bc5bf20',
          'original_child':CHILD,'original_parent':PARENT,'arms':{},'result_class':'NOT_CLASSIFIED'}
    try:
        assert cmd(['git','rev-parse','HEAD'],ROOT).stdout.strip()==CHILD
        src=ROOT/'rtl/core/pipemem.v'
        ys=ROOT/'bench/formal/pipemem.ys'
        helper=ROOT/'rtl/ex/fwb_master.v'
        assert cmd(['git','hash-object',str(src)],ROOT).stdout.strip()==ORIGINAL_BLOB
        assert cmd(['git','hash-object',str(ys)],ROOT).stdout.strip()==ORIGINAL_YS_BLOB
        raw=src.read_bytes(); text=raw.decode('utf-8')
        assert text.count(COMMENTED)==1
        old=cmd(['git','show',f'{PARENT}:rtl/core/pipemem.v'],ROOT)
        assert old.returncode==0 and old.stdout.count(REINSTATED)==1,'parent-original assumption identity RED'
        assert re.search(r'\bcover\s*\(',text) is None,'unanticipated original DUT cover'
        assert re.search(r'\bcover\s*\(',helper.read_text()) is None,'unanticipated original helper cover'
        assert text.count('`define\tASSUME\tassume')==1
        assert 'read_verilog -D PIPEMEM -formal' in ys.read_text()
        assert ys.read_text().count('write_smt2 -wires pipemem.smt2')==1
        removed=text.replace(COMMENTED,COMMENTED+MONITOR,1)
        restored=text.replace(COMMENTED,REINSTATED+MONITOR,1)
        assert restored.replace(REINSTATED+MONITOR,COMMENTED+MONITOR,1)==removed
        assert removed.count('cover(f_s1_endpoint)')==restored.count('cover(f_s1_endpoint)')==1
        (OUT/'only-arm-diff.patch').write_text(''.join(difflib.unified_diff(
          removed.splitlines(True),restored.splitlines(True),fromfile='child_E_removed',tofile='child_E_restored')))
        meta.update({'original_child_sha256':digest(raw),
                     'original_task_sha256':digest(ys.read_bytes()),
                     'original_helper_sha256':digest(helper.read_bytes()),
                     'event':'f_s1_onset && !f_cyc && f_outstanding==0 && !o_wb_stb_gbl && !o_wb_stb_lcl && (o_wb_cyc_gbl||o_wb_cyc_lcl)',
                     'horizon':40,'solver':'z3','tool_compat':'dffunmap-before-write_smt2'})
        for name,experiment in [('E_removed',removed),('E_restored',restored)]:
            dst=OUT/name;dst.mkdir(exist_ok=True)
            (dst/'pipemem.v').write_bytes(experiment.encode())
            meta['arms'][name]={'source_sha256':digest(experiment.encode())}
            assert cmd(['git','reset','--hard',CHILD],ROOT).returncode==0
            assert cmd(['git','clean','-fdx'],ROOT).returncode==0
            assert cmd(['git','hash-object',str(src)],ROOT).stdout.strip()==ORIGINAL_BLOB
            src.write_bytes(experiment.encode())
            ys.write_text(ys.read_text().replace('write_smt2 -wires pipemem.smt2',
                                              'dffunmap\nwrite_smt2 -wires pipemem.smt2',1))
            (dst/'actual-task.ys').write_bytes(ys.read_bytes())
            (dst/'input-sha256.txt').write_text(
                f'{digest(src.read_bytes())}  pipemem.v\n'
                f'{digest(ys.read_bytes())}  actual-task.ys\n'
                f'{digest(helper.read_bytes())}  fwb_master.v\n')
            prep=cmd(['yosys','-l',str(dst/'yosys.log'),'-s','pipemem.ys'],ROOT/'bench/formal',timeout=180)
            (dst/'prep.stdout').write_text(prep.stdout)
            meta['arms'][name]['prep_exit']=prep.returncode
            smt=ROOT/'bench/formal/pipemem.smt2'
            if prep.returncode!=0 or not smt.is_file():
                meta['arms'][name]['status']='PREP_RED';continue
            (dst/'pipemem.smt2').write_bytes(smt.read_bytes())
            meta['arms'][name]['smt_sha256']=digest(smt.read_bytes())
            try:
                start=time.monotonic()
                proof=cmd(['yosys-smtbmc','--presat','-s','z3','-c','-t','40',
                           '--dump-vcd',str(dst/'trace.vcd'),str(smt)],ROOT/'bench/formal',timeout=320)
                meta['arms'][name]['solver_exit']=proof.returncode
                meta['arms'][name]['solver_elapsed_s']=round(time.monotonic()-start,3)
                (dst/'cover.log').write_text(proof.stdout)
                if 'Status: PASSED' in proof.stdout and proof.returncode==0:
                    meta['arms'][name]['status']='COVER_SAT_NEEDS_TRACE_REPLAY'
                elif 'Status: FAILED' in proof.stdout and proof.returncode!=0:
                    meta['arms'][name]['status']='NO_COVER_REPORTED_CHECK_LOG'
                else: meta['arms'][name]['status']='SOLVER_UNRESOLVED'
            except subprocess.TimeoutExpired as e:
                meta['arms'][name]['status']='SOLVER_TIMEOUT'
                b=e.stdout or b''
                (dst/'cover.log').write_text(b.decode('utf-8','replace') if isinstance(b,bytes) else b)
        meta['result_class']='BOTH_ARM_EVIDENCE_CAPTURED' if len(meta['arms'])==2 else 'INCOMPLETE'
    except Exception as exc:
        meta['result_class']='FAIL_CLOSED_EXECUTION_RED';meta['error']=repr(exc)
    finally:
        (OUT/'result.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n')
        try:
            cmd(['git','reset','--hard',CHILD],ROOT);cmd(['git','clean','-fdx'],ROOT)
        except Exception: pass
        entries=[]
        for p in sorted(OUT.rglob('*')):
            if p.is_file() and p.name!='SHA256SUMS':
                entries.append(f'{digest(p.read_bytes())}  {p.relative_to(OUT)}')
        (OUT/'SHA256SUMS').write_text('\n'.join(entries)+'\n')
    print(json.dumps(meta,indent=2,sort_keys=True))

if __name__=='__main__': main()
