#!/usr/bin/env python3
"""Decompose the exact official k<=9 safety disjunction into 18 equivalent property queries."""
import hashlib, json, pathlib, re, sys
src=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
raw=src.read_bytes();sha=hashlib.sha256(raw).hexdigest()
expected=''
# Source must be the unmodified reference from the certified independent bridge.
meta=json.loads(pathlib.Path(sys.argv[3]).read_text())
assert sha==meta['query_sha256']['old_any_assertion_failure_up_to_depth9'],(sha,meta['query_sha256'])
s=raw.decode();assert s.rstrip().endswith('(check-sat)')
assert s.count('(assert (or (not (|axis_pkt_fifo_a|')==1
prefix=s[:s.rfind('(assert (or (not (|axis_pkt_fifo_a|')]
assert prefix.count('(assert (|axis_pkt_fifo_u| |q')==10
assert prefix.count('(assert (|axis_pkt_fifo_t| |q')==9
assert prefix.count('(assert (|axis_pkt_fifo_i| |q0|))')==1
props=re.findall(r'^; yosys-smt2-assert (\d+) ([A-Za-z0-9_]+)$',s,re.M)
assert len(props)==18 and [int(i) for i,n in props]==list(range(18))
original_tail=s[len(prefix):]
original_terms=re.findall(r'\(not \(\|axis_pkt_fifo_a\| \|q(\d+)\|\)\)',original_tail)
assert list(map(int,original_terms))==list(range(10)),original_terms
for i,name in props:
    ix=int(i); terms=[f'(not (|axis_pkt_fifo_a {ix}| |q{k}|))' for k in range(10)]
    text=prefix+'(assert (or '+' '.join(terms)+'))\n(check-sat)\n'
    (out/f'{ix:02d}_{name}.smt2').write_text(text)
# Logical identity by construction: OR_{k=0..9} not AND_{i=0..17}(a_i(q_k))
# == OR_{i=0..17} OR_{k=0..9} not a_i(q_k). The full original source and SHA are sealed.
(out/'decomposition.json').write_text(json.dumps({'original_source_sha256':sha,'individual_queries':{f'{i:02d}_{n}':hashlib.sha256((out/f'{i:02d}_{n}.smt2').read_bytes()).hexdigest() for i,n in props},'equivalence':'De Morgan plus disjunction associativity; no source, assumption, initial, transition, or property function modified'},indent=2)+'\n')
print('EXACT_DISJUNCTIVE_DECOMPOSITION_PASS',len(props),'ORIGINAL_SHA256',sha)
