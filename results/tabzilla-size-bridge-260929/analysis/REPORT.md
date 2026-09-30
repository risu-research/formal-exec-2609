# TabZilla task-size bridge analysis

Exact matched tasks: **104** (46 CC18 / 58 outside).
Historical fold-median task size: CC18 median **1263**, outside median **293**; log-size SMD outside−CC18 = **-0.784**.

## Size-to-performance bridge

| subset | raw membership T | size slope T / SD | slope p | predicted membership T from size | vector cosine | size-linear adjusted T | conditional p | size-quadratic adjusted T | conditional p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all18 | 0.03425 | 0.02196 | 0.00035999 | 0.01722 | 0.872 | 0.02480 | 0.14954 | 0.02817 | 0.10498 |
| minus_TabNet | 0.02885 | 0.01549 | 0.02248 | 0.01215 | 0.808 | 0.02402 | 0.17324 | 0.02897 | 0.091338 |
| minus_VIME | 0.02836 | 0.01895 | 0.00122 | 0.01486 | 0.812 | 0.02179 | 0.2211 | 0.02571 | 0.13038 |
| minus_TabNet_VIME | 0.01896 | 0.00807 | 0.46881 | 0.00633 | 0.441 | 0.02024 | 0.29485 | 0.02640 | 0.1138 |
| classical4 | 0.01281 | 0.01187 | 3.9999e-05 | 0.00931 | 0.826 | 0.00865 | 0.28801 | 0.01054 | 0.18162 |

## Size-stratified membership permutation — all18

- **q4**: p=0.170337, mixed bins=3/4; bin composition=[{'bin': 0, 'n': 26, 'cc18': 0, 'outside': 26}, {'bin': 1, 'n': 26, 'cc18': 12, 'outside': 14}, {'bin': 2, 'n': 26, 'cc18': 20, 'outside': 6}, {'bin': 3, 'n': 26, 'cc18': 14, 'outside': 12}]
- **q5**: p=0.112998, mixed bins=4/5; bin composition=[{'bin': 0, 'n': 21, 'cc18': 0, 'outside': 21}, {'bin': 1, 'n': 21, 'cc18': 5, 'outside': 16}, {'bin': 2, 'n': 20, 'cc18': 14, 'outside': 6}, {'bin': 3, 'n': 21, 'cc18': 15, 'outside': 6}, {'bin': 4, 'n': 21, 'cc18': 12, 'outside': 9}]
- **q6**: p=0.189536, mixed bins=5/6; bin composition=[{'bin': 0, 'n': 18, 'cc18': 0, 'outside': 18}, {'bin': 1, 'n': 18, 'cc18': 1, 'outside': 17}, {'bin': 2, 'n': 16, 'cc18': 11, 'outside': 5}, {'bin': 3, 'n': 18, 'cc18': 13, 'outside': 5}, {'bin': 4, 'n': 16, 'cc18': 12, 'outside': 4}, {'bin': 5, 'n': 18, 'cc18': 9, 'outside': 9}]

## Common size support

Restricting to common observed log-size support leaves **71** tasks (45 CC18 / 26 outside): T=0.02335, ordinary fixed-count membership permutation p=0.359133.

## Guardrail

This establishes a descriptive bridge only: CC18 membership is associated with task size, task size can be associated with algorithm-relative performance, and conditioning on size can attenuate the membership profile shift. It does not identify a causal effect of CC18 curation.
