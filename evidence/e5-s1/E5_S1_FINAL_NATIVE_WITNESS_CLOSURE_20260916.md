# E5–S1: same-RTL historical assumption counterfactual — final native-witness closure

Date 2026-09-16. Evidence qualified **ONLY** as independently replayed specific admissible-history difference, NOT original-property failure or shipped hardware bug.

## Immutable provenance

Original ZipCPU child commit `d511239e19be8fcc7f340a64554ea93699637e62`; original parent/child reconstructed as the same synthesizable RTL with historical formal `i_lock` assumption alone restored or removed. V2 uninstrumented original carrier: GitHub Actions run `35122112448`, artifact `10457767655`, ZIP SHA256 `9662c2a052d58312cca8d6906a36ca5aa9b2554b6e345344da6349ae60e0674f`; its internal SHA256SUMS was verified independently. V2 original full cvc5 pin query timed out, never a negative scientific result. The prospective v3 bounded replay protocol was committed before solver outcomes as `bef7ecaa0c6186bc4958dd9a4101dd100b91268c`. The v3 original runner cvc5 invocation produced an actual parser error (`--incremental` missing), and initial runner-summary log was hashed concurrently with `tee`; RED was sealed at `330a7136540915812d14428e62b24abe88752176`, and execution-only amendment at `1824e439ca32a85d85a25a5fda5f6e41e1dae0d9`.

Amended v3 run `35127292447`, scientific result closure `fb57007db6714d5a85bf35e95421a97d97284a4f`. Independently downloaded four original ZIPs; external digests and internal SHA256SUMS all verified: removed Z3 artifact `10459643031` SHA256 `9facc340e8ce367eed8406459ce34d121518bec323aa5aa2a534df5b660860b6`; restored Z3 artifact `10459553308` `b698c69bd5170154edb57680198075bb9aa3a3da4e02ec09c0305d11d36f82a1`; removed cvc5 artifact `10460265963` `7ad19bc3a16a37621cef3f39f11497cc2d86c8bea1c8b721193f68603c438c7e`; restored cvc5 artifact `10460236006` `8067dcb3c332310e84d863c7e59fa067cd1623f2c58439c5888ec16e652b0342`.

Native replay protocol (pre-result) `945c4a6b878b27e385bfd50d1dafe428c262991d`, first-witness projection code blob `36415bc95dd8d6dbd122022322906f93a0ff7d0a`. Native Icarus run `35128262134` artifact `10459987174` ZIP `f7826fb81a8128298b7f3a0c519fa48928e046a9f8d6114372c503269a2db2aa` reproduced `X` in output due original default-parameter `always @(*)` constant assignment with empty sensitivity, NOT native proof failure. RED closure `15c82331a2e2ace5766bc77047230f42a6c7647a`. Prospectively frozen alternate independent simulator protocol `d1273d1af9b5ee02628db1cbec1ba9b59989039a`, Verilator auditor blob `913d53884679e8899ba4962977ace20ad41becf2`. First Verilator attempt `35128623905` stopped before Verilator due GitHub shell `-e` remaining on during intentionally failing Icarus diagnostic; RED closure `3602f70c9292614568e9f35823a0022edfa1c872`, execution-only `set +e` amendment prospectively frozen at `6bd74988d405ac49b9e36fd58c58eec17aee3b5d`. Original RTL, witness, default parameters, clocks, sampling, testbench were never modified.

**Successful independent native RTL verification:** run `35128961034`, head commit `b4470385419f9df092b78b7cc53db7a726cf2e64`, artifact ID `10460487871`, ZIP SHA256 `f7fd8bf918e2b2fbfe50a896c4ef92218d66965d521bf35f7130459d05917d81`. Independently downloaded ZIP SHA matched GitHub digest. Internal `SHA256SUMS_VERILATOR` verified for all listed files. Exact first witness vectors SHA256 `e4bca93c1ed20dd9d92fcc79e803550d778f01764f1825a0380a8e060f082be3`; exact first testbench SHA256 `c4be215499857b3396e4652bc33e53e88b3447d6623be055bdc545d8ab4847c5`; extraction SMT2 SHA256 `340254abd12b605d272ccd7896001d47182846f80b3b4a7e1c7afd3b781d981e`. Unmodified original E_removed/pipemem.v, default parameters, no FORMAL or monitor/helper in independent Verilator compilation. ALL six sampled states × six independent output/internal cyc signals = **36/36 exact** agreement with first frozen SAT witness. VCD was not claimed; native simulation stdout and JSON comparison preserved. Verilator is 2-state, not independent full 4-state simulation or proof of entire reachable-state space.

## Exact frozen short trace

| State | busy | stb_gbl | stb_lcl | cyc_gbl | cyc_lcl | DUT internal cyc |
|--|--:|--:|--:|--:|--:|--:|
| q0 | 0 | 0 | 0 | 0 | 0 | 0 |
| q1 | 0 | 0 | 0 | 0 | 0 | 0 |
| q2 | 1 | 0 | 1 | 0 | 1 | 1 |
| q3 | 1 | 0 | 0 | 0 | 1 | 1 |
| q4 | 0 | 0 | 0 | 0 | 1 | 0 |
| q5 | 0 | 0 | 0 | 0 | 0 | 0 |

q2 i_lock=0, q3 i_lock=1: FIRST transition forbidden under the restored historical `if ((f_past_valid)&&($past(f_cyc))&&(!$past(i_lock))) ASSUME(!i_lock)` once previous f_cyc=1. At q4, notably **i_reset=1**. The RTL samples reset on the next rising edge, while `o_wb_cyc_lcl` also includes lock latch; a single sampled cycle extension does not establish a malfunction or infinite hang. Original `f_cyc` is directly assigned from DUT `cyc`, and native `dut.cyc` agrees with its six-state SMT values. No physically different output behavior under identical input was caused by the mere presence of a formal assumption: the assumption changes WHICH histories are admissible to the proof.

## Positive, negative and unresolved conclusions

- BOTH independently executed solvers Z3 and cvc5 completed all six-state monitor-free exact trace and direct-pin checks. `observed_cycle` (post-hoc old witness replay) E_removed SAT, E_restored UNSAT. Contradictory control UNSAT. `output_only_any` and `pin_any` SAT starting q3 in BOTH E_removed and E_restored. The prior broad claim that the endpoint output state itself is newly reachable on deletion is **REFUTED** and must not appear in the paper.
- Z3 completed q0..q39 for both arms, direct-pin endpoints SAT from q3 onward; cvc5 full q0..q39 tests TIMEOUT at ~q14 and were not 40-state confirmations. The original specific observed_cycle query has only six states; do not misrepresent its UNSAT as an unbounded or 40-state all-histories impossibility.
- Native Verilator confirmed the actual one fixed input/output witness independently of Yosys RTL translation. Both solvers STILL share Yosys's transition model, and native sim does NOT prove abstract helper f_outstanding (or all inputs), nor a historical safety property violation, nor real silicon behavior.
- Historical original property/coverage gate remains **PROPERTY_FAILURE_UNPROVEN**. G6 blind corpus statistics (143 items), G7/G8 immutable findings and original G9 Rocket attempts are not enlarged or numerically retrofitted by this separately pre-registered E5 S1 case. Observed-cycle pattern is post-hoc and must not be advertised as a prospectively blinded novel target.

## Next scientific decision

Preserve this result as a **qualified executable demonstration** and stop trying to promote the broad endpoint. Before any historical masked-safety claim, enumerate exact ORIGINAL historical property assertions/assumptions and prove actual violation along the fixed native-replayed trace or obtain independently generated SMT counterexample under original properties; if none or an original assertion is not meaningfully corresponding, explicitly report no known original-property failure. For DATE paper, prioritize synthesizing G6–G8 claim architecture, provenance-bound source-to-semantics contribution, honest baseline subsumption, and bounded nature of E5; do not run speculative successive modified constraints solely to make E5 look worse. Reuse the exact archive/run hashes above for follow-up, never a guessed current branch state.
