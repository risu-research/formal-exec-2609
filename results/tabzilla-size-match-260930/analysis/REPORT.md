# TabZilla nonparametric size-matched / common-support sensitivity

Strict empirical common support: **71** tasks (**45 CC18 / 26 outside**), z(log-size) in [-0.416, 2.546].

Matching is 1:1 without replacement, uses **size only**, maximizes matched pairs under each caliper, then minimizes total absolute size distance. No performance outcome enters matching.

## All-18 matching sensitivity

| caliper (SD) | pairs | matched size SMD | mean |gap| SD | T | sign-flip p | bootstrap T 95% | cosine vs raw |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.05 | 17 | -0.002 | 0.017 | 0.03635 | 0.2428 | [0.01402, 0.08964] | 0.729 |
| 0.10 | 21 | -0.008 | 0.027 | 0.02697 | 0.58079 | [0.01737, 0.07545] | 0.914 |
| 0.20 | 23 | -0.024 | 0.041 | 0.02447 | 0.58779 | [0.01569, 0.06914] | 0.917 |
| 0.30 | 24 | 0.009 | 0.054 | 0.02501 | 0.51781 | [0.01588, 0.06765] | 0.913 |
| 0.50 | 24 | 0.009 | 0.054 | 0.02501 | 0.51575 | [0.01595, 0.06898] | 0.913 |
| inf | 26 | 0.119 | 0.147 | 0.02335 | 0.50609 | [0.01443, 0.06489] | 0.915 |

## Subset robustness at caliper 0.20 SD

| subset | common-support raw T | raw perm p | pairs | matched T | sign-flip p | size SMD |
|---|---:|---:|---:|---:|---:|---:|
| all18 | 0.02335 | 0.36109 | 23 | 0.02447 | 0.58779 | -0.024 |
| minus_TabNet | 0.02368 | 0.35899 | 23 | 0.02478 | 0.58783 | -0.024 |
| minus_VIME | 0.02262 | 0.36991 | 23 | 0.02268 | 0.66591 | -0.024 |
| minus_TabNet_VIME | 0.02303 | 0.36025 | 23 | 0.02304 | 0.66417 | -0.024 |
| classical4 | 0.01526 | 0.030739 | 23 | 0.01499 | 0.2424 | -0.024 |

## Size-stratified all-18 permutation sensitivity

- **quantile_q4**: T=0.02335, p=0.320134, mixed strata=4/4
- **quantile_q5**: T=0.02335, p=0.372233, mixed strata=5/5
- **quantile_q6**: T=0.02335, p=0.337913, mixed strata=6/6
- **quantile_q8**: T=0.02335, p=0.396572, mixed strata=8/8
- **quantile_q10**: T=0.02335, p=0.360693, mixed strata=10/10
- **contiguous_block_4**: T=0.02335, p=0.376212, mixed strata=13/18
- **contiguous_block_6**: T=0.02335, p=0.329313, mixed strata=12/12
- **contiguous_block_8**: T=0.02335, p=0.365093, mixed strata=9/9
- **contiguous_block_10**: T=0.02335, p=0.362853, mixed strata=7/8
- **contiguous_block_12**: T=0.02335, p=0.339073, mixed strata=6/6

## Guardrail

Matched and stratified tests are observational sensitivity analyses. They condition more tightly on observed task size but do not turn CC18 membership into a randomized treatment or establish a causal curation effect.
