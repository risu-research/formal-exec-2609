#!/usr/bin/env python3
"""Decompose exact original official k<=9 safety disjunction into 18 equivalent property queries."""
import hashlib,json,pathlib,re,sys
src=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
raw=src.read_bytes();sha=hashlib.sha256(raw).hexdigest()
meta=json.loads(pathlib.Path(sys.argv[3]).read_text())
assert sha==meta['query_sha256']['old_any_assertion_failure_up_to_depth9'],sha
s=raw.decode();assert s.rstrip().endswith('(check-sat)')
assert s.count('(assert (or (not (|axis_pkt_fifo_a|')==1
prefix=s[:s.rfind('(assert (or (not (|axis_pkt_fifo_a|')]
assert prefix.count('(assert (|axis_pkt_fifo_u| |q')==10
assert prefix.count('(assert (|axis_pkt_fifo_t| |q')==9
assert prefix.count('(assert (|axis_pkt_fifo_i| |q0|))')==1
props=re.findall(r'^; yosys-smt2-assert (\d+) ([A-Za-z0-9_]+)$',s,re.M)
assert len(props)==18 and [int(i) for i,n in props]==list(range(18))
original_terms=re.findall(r'\(not \(\|axis_pkt_fifo_a\| \|q(\d+)\|\)\)',s[len(prefix):])
assert list(map(int,original_terms))==list(range(10)),original_terms
queries={}
for index,name in props:
    ix=int(index); terms=[f'(not (|axis_pkt_fifo_a {ix}| |q{k}|))' for k in range(10)]
    text=prefix+'(assert (or '+' '.join(terms)+'))\n(check-sat)\n'
    p=out/f'{ix:02d}_{name}.smt2';p.write_text(text)
    queries[p.stem]=hashlib.sha256(p.read_bytes()).hexdigest()
# De Morgan and associativity: OR_k not AND_i a_i(q_k) == OR_i OR_k not a_i(q_k).
(out/'decomposition.json').write_text(json.dumps({'original_source_sha256':sha,'individual_queries':queries,'equivalence':'De Morgan plus OR associativity; source, assumptions, initial, transition, property functions unchanged'},indent=2)+'\n')
assert len(queries)==18
print('EXACT_DISJUNCTIVE_DECOMPOSITION_PASS',len(props),'ORIGINAL_SHA256',sha)
