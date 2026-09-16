# Amendment01: repair invocation and evidence-race only (pre-repair)

Parent first-run RED closure commit: `330a7136540915812d14428e62b24abe88752176`. Freeze BEFORE code changes. Previous full original evidence remains immutable.

Only permitted repairs: (1) invoke cvc5 with its documented `--incremental` command-line flag, retaining the exact frozen cvc5 1.3.4 archive and SHA-256; (2) remove `runner-summary.log` from the per-job SHA256SUMS because it is concurrently written by tee, or compute final checksums only after tee closes; (3) make a nonzero job conclusion whenever target checks are missing, unknown, parse-error, or timeout while still uploading raw evidence via `if: always()`; (4) avoid rerunning historical observational evidence as a new prospectively chosen finding. Keep all original RTL, Yosys SMT2 bytes, initial conditions, assumptions, targets, trace bounds, original candidate, cross-solver/cross-arm matrix, and 90-second per-mode timeout unchanged.

The Z3 outcomes from initial v3 run `35127091646` must be reported separately, not silently replaced by this repair. Before promoting anything, independently download and check the new GitHub artifact digest and ALL non-transient entries; verify results are complete for both solvers and arms. An eventual SAT remains a Yosys-derived model result, not a native RTL simulation certificate.
