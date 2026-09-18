#!/usr/bin/env python3
"""Source-derived official SMT2 reset-initialized BMC covers; finite horizon, not induction."""
import pathlib,argparse,hashlib,json
ap=argparse.ArgumentParser();ap.add_argument('--official',type=pathlib.Path,required=True);ap.add_argument('--out',type=pathlib.Path,required=True);ap.add_argument('--max-depth',type=int,default=9);args=ap.parse_args();args.out.mkdir(exist_ok=True,parents=True)
assert args.max_depth==9
assert hashlib.sha256(args.official.read_bytes()).hexdigest()=='41a3d4eac9450eab902dfdc46ddc6482adde8bda3e6e9009429ea32dd5b39173'
base=args.official.read_text()
def s(i):return f'|q{i}|'
def n(name,i):return f'(|axis_pkt_fifo_n {name}| {s(i)})'
def contract(i):return f'(bvult (bvsub {n("wptr",i)} {n("commit_ptr",i)}) #b1000)'
def setup(k):
 a=[base]+[f'(declare-fun {s(i)} () |axis_pkt_fifo_s|)' for i in range(k+1)]+[f'(assert (|axis_pkt_fifo_i| {s(0)}))']
 a += [f'(assert (|axis_pkt_fifo_t| {s(i)} {s(i+1)}))' for i in range(k)]
 a += [f'(assert (|axis_pkt_fifo_u| {s(i)}))' for i in range(k+1)]
 return '\n'.join(a)+'\n'
ans={}
for k in range(args.max_depth+1):
 p=f'(and {n("rst_n",k)} (not {contract(k)}))'
 q=setup(k)+f'(assert {p})\n(check-sat)\n'
 if k==9:q+='(get-value ('+' '.join(n(name,i) for i in range(k+1) for name in ['wptr','rptr','commit_ptr','rst_n','s_axis_tvalid','s_axis_tlast','s_axis_tready'])+'))\n'
 name=f'old_contract_exclusion_depth{k}';(args.out/(name+'.smt2')).write_text(q);ans[name]=hashlib.sha256(q.encode()).hexdigest()
k=9
q=setup(k)+ '(assert (or '+' '.join(f'(not (|axis_pkt_fifo_a| {s(i)}))' for i in range(k+1))+'))\n(check-sat)\n'
name=f'old_any_assertion_failure_up_to_depth{k}';(args.out/(name+'.smt2')).write_text(q);ans[name]=hashlib.sha256(q.encode()).hexdigest()
(args.out/'reachability_meta.json').write_text(json.dumps({'official_original_sha256':'41a3d4eac9450eab902dfdc46ddc6482adde8bda3e6e9009429ea32dd5b39173','scope':'initial state + k official source-derived transitions, original old assumptions each state; finite horizon k<=9 only; k counts transitions, not testbench steps','query_sha256':ans},indent=2)+'\n')
print(json.dumps(ans,indent=2))