# E5 S1 monitor-free v3 Amendment01 — audited bounded result closure

Date: 2026-09-16. Parent pre-result protocol `bef7ecaa0c6186bc4958dd9a4101dd100b91268c`; first-run RED `330a7136540915812d14428e62b24abe88752176`; execution-only amendment `1824e439ca32a85d85a25a5fda5f6e41e1dae0d9`. Final amended workflow trigger `26ab733007d2cb7851dc9d43fd67135c8503f699`; Actions run https://github.com/risu-research/formal-exec-2609/actions/runs/35127292447 . Original v2 immutable carrier: run `35122112448`, artifact `10457767655`, archive sha256 `9662c2a052d58312cca8d6906a36ca5aa9b2554b6e345344da6349ae60e0674f`.

## Independent retrieval and checksums

Each artifact ZIP SHA-256 below matched the independently downloaded ZIP; each internal `SHA256SUMS` in the amended artifacts was verified, including the raw SMT query bytes, logs, solver versions and RESULTS.json. Unlike first v3 run, no concurrent tee-written summary file is in the checksum manifest.

| Arm | Solver | artifact ID | SHA-256 |
|---|---|---|---|
| E_removed | Z3 | 10459643031 | 9facc340e8ce367eed8406459ce34d121518bec323aa5aa2a534df5b660860b6 |
| E_restored | Z3 | 10459553308 | b698c69bd5170154edb57680198075bb9aa3a3da4e02ec09c0305d11d36f82a1 |
| E_removed | cvc5 1.3.4 | 10460265963 | 7ad19bc3a16a37621cef3f39f11497cc2d86c8bea1c8b721193f68603c438c7e |
| E_restored | cvc5 1.3.4 | 10460236006 | 8067dcb3c332310e84d863c7e59fa067cd1623f2c58439c5888ec16e652b0342 |

## Results (state indexing q0...q5; original full horizon q0...q39)

Both solvers independently completed ALL 6-state incremental checks for both arms. `negative_control`: UNSAT all four; `observed_cycle` (post-hoc reconstruction of the already-observed historical wave): E_removed SAT, E_restored UNSAT in both solvers. For `output_only_any` and `pin_any`: both arms UNSAT at q0..q2 and SAT at q3..q5 in both solvers. Thus **a broad output endpoint is reachable even WITH the historical assumption restored**. The older overly broad claim 'assumption removal makes the output-only condition newly reachable' is REFUTED and is forbidden.

Z3 completed all 40 check-sat stages in both arms and both endpoint modes, with UNSAT q0..q2 and SAT q3..q39. cvc5 produced statuses through q13 in both endpoint modes and arms, but TIMEOUT at 90 seconds on each full-40 case; its full-40 result is UNRESOLVED, and partial statuses must never be promoted as 40-state certification. The specific six-state observed trace exclusion needs no 40-state full scan, but does not imply that all six-state output-only endpoints are excluded.

## Scientific claim boundary / next gate

Permitted: 'On the same ZipCPU pipemem RTL, deleting a historical i_lock formal assumption admits one already-observed short input/state/output trace excluded by restoring that assumption, in an uninstrumented original RTL model, with Z3 and cvc5 agreement for the six-state trace query.' Qualify: the observed trace predicate is post-hoc, not a blinded prospective discovery; both solvers share Yosys translation and preserved formal helper semantics; SMT SAT alone is NOT independent native RTL simulation or an original-property failure. The DUT's physical outputs under the same input do not change just because a formal assumption is present/absent; the admissible-input/history set changes. An independent simulation and exact-input replay with first rejected assumption cycle, followed by a separate original-property evaluation if feasible, are the next scientific evidence gates. Do not claim shipped hardware bug, infinite hang, corruption, or any historical property violation. G6 blind corpus and G8 frozen results remain separate; E5 S1 must not be mixed into their sample counts.
