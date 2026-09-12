# ProofScope Carrier-Realization Gate V2

Status: preregistered before observing V2 carrier outcomes.

## Why V2 exists

The first carrier workflow run (`34708668094`) terminated before formal solving because the injected monitor used an undefined source macro (`ASSERT`) at the insertion site. That run is retained as an instrumentation failure and is not evidence for or against carrier realizability.

V2 removes source-macro dependence by injecting native formal `assert(...)` statements and separates bounded model checking from temporal induction. A failure in one carrier direction must not suppress the other direction or the lock-realization experiment.

## Fixed questions

For the natural ZipCPU pair

- R0 = `128cab89f5edaae4699184f50c1334598ebe904e`
- R1 = `d511239e19be8fcc7f340a64554ea93699637e62`

ask independently:

1. Does the R0 native proof world satisfy the R1 address contract?
2. Does the R1 native proof world satisfy the R0 address contract?
3. Is a violation of the removed R0 lock contract realizable in the R1 native proof world?

No expected answer is fixed for questions 1 or 2. The experiment reports what the solver shows.

## Outcome taxonomy for each address direction

- `REALIZED_COUNTEREXAMPLE`: BMC finds a violating trace.
- `PROVED_BMC_AND_INDUCTION`: BMC passes and induction passes.
- `INDUCTION_INCONCLUSIVE`: BMC passes but induction does not prove the monitor.
- `INSTRUMENTATION_FAILURE`: elaboration/solver invocation fails before a scientific result.

`INDUCTION_INCONCLUSIVE` must never be reported as unreachable or proved.

## Lock realization

The R1 model receives only a `cover(...)` probe for the behavior forbidden by the removed historical lock assumption. A reached cover is evidence that the expansion is realized under the R1 native proof world. Failure to reach within the bounded horizon is only bounded non-observation.

## Scientific hygiene

- Existing native assumptions remain enabled; these experiments study each revision's native proof world rather than unconstrained design behavior.
- The foreign address monitor is an assertion, never an added assumption.
- The original failed carrier run remains in history.
- All logs, VCDs when generated, metadata, tool versions, and SHA-256 digests are retained.
- No source-line or assumption-count matching is used for the upstream TNF classification; carrier experiments are a separate realization layer.
