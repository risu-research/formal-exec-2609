# TabZilla CC18 membership: omnibus + structural mechanism analysis

## Exact-ID join

- Cleaned performance tasks: **104**
- Historical metafeature tasks parsed: **183**
- Exact OpenML task-ID overlap: **104** (46 CC18 / 58 outside)
- Cleaned tasks missing historical MFEs: **0**: none
- Meta rows that failed `__(task_id)__fold_k` parsing: **0**

## Prespecified structural composition audit

Global standardized 6-feature mean-shift norm = **0.9154**, fixed-count permutation p = **0.00645987** (B=50000).

| feature | SMD outside−CC18 | p | BH q |
|---|---:|---:|---:|
| log_nr_inst | -0.784 | 3.9999e-05 | 0.00024 |
| numeric_fraction | -0.370 | 0.061519 | 0.18456 |
| log_nr_attr | -0.265 | 0.18092 | 0.36183 |
| majority_class_fraction | 0.115 | 0.56199 | 0.84298 |
| log_nr_class | -0.024 | 0.90526 | 0.90526 |
| normalized_class_entropy | 0.043 | 0.8312 | 0.90526 |

## Membership-associated algorithm-relative profile shift

Raw T is the RMS across all algorithm-pair differences in the outside-minus-CC18 coefficient. Adjusted T is the same statistic after the six prespecified structural features enter a multivariate linear model. Conditional p uses Freedman–Lane residual permutation under the reduced structural-feature model. Cross-fit attenuation is an overfitting-resistant diagnostic.

| subset | raw T | adjusted T | attenuation | conditional p | cross-fit R² | cross-fit residual T | cross-fit attenuation |
|---|---:|---:|---:|---:|---:|---:|---:|
| all18 | 0.03425 | 0.02425 | 29.2% | 0.1209 | 0.089 | 0.01979 | 42.2% |
| minus_TabNet | 0.02885 | 0.02332 | 19.2% | 0.15326 | 0.070 | 0.01916 | 33.6% |
| minus_VIME | 0.02836 | 0.02061 | 27.3% | 0.22574 | 0.083 | 0.01602 | 43.5% |
| minus_TabNet_VIME | 0.01896 | 0.01869 | 1.4% | 0.32987 | 0.058 | 0.01447 | 23.7% |
| classical4 | 0.01281 | 0.00898 | 29.9% | 0.25913 | 0.051 | 0.00931 | 27.3% |

## Guardrail

These are finite historical benchmark-membership associations. Structural adjustment can show whether a small, prespecified set of observable task properties accounts for part of the profile shift, but it is not causal mediation and does not identify an effect of CC18 curation.
