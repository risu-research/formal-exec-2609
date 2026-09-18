#!/usr/bin/env python3
"""Losslessly split exact original official packet-count property 12 into k=0..9 clock-snapshot disjuncts."""
import hashlib,json,pathlib,re,sys
source=pathlib.Path(sys.argv[1]);evidence=pathlib.Path(sys.argv[2]);out=pathlib.Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=True)
raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest();metadata=json.loads(evidence.read_text());assert sha==metadata['query_sha256']['old_any_assertion_failure_up_to_depth9']== 'e6dd616bc3ae825f56dde93b45faaa4fc1a322773b5b496a0524a3a1f1d6c279'
s=raw.decode(); needle='(assert (or (not (|axis_pkt_fifo_a|';assert s.count(needle)==1
prefix=s[:s.rfind(needle)];assert prefix.count('(assert (|axis_pkt_fifo_i| |q0|))')==1 and prefix.count('(assert (|axis_pkt_fifo_u| |q')==10 and prefix.count('(assert (|axis_pkt_fifo_t| |q')==9
assert [int(x) for x in re.findall(r'\(not \(\|axis_pkt_fifo_a\| \|q(\d+)\|\)\)',s[len(prefix):])]==list(range(10))
assert re.search(r'^; yosys-smt2-assert 12 a_pktcount_le_occ$',s,re.M)
orig=prefix+'(assert (or '+' '.join(f'(not (|axis_pkt_fifo_a 12| |q{i}|))' for i in range(10))+'))\n(check-sat)\n'
expected='9b138a1c98d8f975a742e01d2d766495e3b1583a80754bb7aa637d4065b69a28'
assert hashlib.sha256(orig.encode()).hexdigest()==expected,'must match previously failed 12_a_pktcount_le_occ predicate BYTES'
for k in range(10):
    q=prefix+f'(assert (not (|axis_pkt_fifo_a 12| |q{k}|)))\n(check-sat)\n'
    (out/f'packet_count_fail_at_state_{k:02d}.smt2').write_text(q)
manifest={'original_bmc_sha256':sha,'original_packet_count_or_sha256':expected,'disjuncts':{str(i):hashlib.sha256((out/f'packet_count_fail_at_state_{i:02d}.smt2').read_bytes()).hexdigest() for i in range(10)},'equivalence':'OR of these 10 independent per-state failures equals the exact original property 12 query, itself one of the 18 original any-assertion failures; same full initial, transition, assumptions'}
(out/'packet_depth_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('TEN_EXACT_CLOCK_DEPTH_DISJUNCTS_HASH_PINNED',sha,expected)
