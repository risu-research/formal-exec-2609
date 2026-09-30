# TabZilla CC18 size-mechanism closeout

## Frozen authority

- Historical TabZilla commit: `feca5e554148bdc4ac17c3379984c2a71244cd32`.
- Cleaned 104-task × 18-algorithm source SHA-256: `67b57c57eded5d4c05da4692c67f5c15e7999fb23f5f7125233bff75c3a5a23a`.
- Historical `TabSurvey/metafeatures.csv` SHA-256: `91515bcc6ed6a13ce10bd1085b8c027fff5e6490841e6068cf85563ce7affa64`.
- Main size closeout workflow run: `36662769813`.
- Main closeout execution-branch SHA at run time: `910e897ff7d6624c6acfd3a7d9232147f509aba6`.
- Targeted classical-4 conditional run: `36663219473`; frozen-result commit `55c41ea5c775878385b496864e89aab975b7124e`.
- Main size-bridge script SHA-256: `6a54aeb3c3e8b62680a75dd39c5fb682b9f706b16387d7120bfa342754b7c929`.
- Nonparametric matching script SHA-256: `43e1299296eae87afa1214c6874d72029989823080b5159194609751b215a2c8`.

## What the size bridge establishes

The fixed historical population contains 104 tasks: 46 CC18 and 58 outside. CC18 has a much larger median task size (1,263 instances) than the outside set (293.25), with outside-minus-CC18 standardized log-size difference = -0.784.

For the all-18 relative-performance profile:

- raw CC18-vs-outside interaction T = 0.03425;
- algorithm-relative performance varies strongly with task size: size-slope profile T/SD = 0.02196, permutation p = 0.00036;
- the profile shift predicted from the observed size difference has T = 0.01722 (50.3% of the raw T magnitude) and cosine 0.872 with the observed membership-shift vector;
- its projection on the observed vector is 43.9% of the raw vector;
- linear size adjustment reduces T to 0.02480 (27.6% attenuation), conditional Freedman-Lane p = 0.1495;
- quadratic size adjustment gives T = 0.02817, conditional p = 0.1050;
- coarse full-population size-stratified membership permutations give p = 0.113–0.190 rather than the unconditioned omnibus p ≈ 0.00675.

The size bridge therefore has all three links required for a descriptive mechanism: benchmark membership is associated with size; relative algorithm performance is associated with size; and the size-predicted profile shift is strongly aligned with the observed membership profile shift. This is mechanism evidence, not causal mediation.

## The support result is stronger than a simple mean/median imbalance

Strict empirical size common support retains 71 tasks: 45/46 CC18 tasks (97.8%) but only 26/58 outside tasks (44.8%). The excluded region is extremely asymmetric:

- 32 outside tasks, and zero CC18 tasks, lie below the CC18 minimum-size support; these outside tasks have roughly 26–362 instances while the CC18 minimum is 400;
- only one CC18 task lies above the outside upper support.

Thus 55.2% of the outside tasks occupy a low-size region with no CC18 counterpart. Within common support the median-size ordering reverses: CC18 median = 1,251, outside median = 1,541.25. The large full-population size imbalance is therefore principally a support/composition phenomenon rather than a small location shift spread uniformly through the overlap region.

For all 18 algorithms, restricting to common support reduces T from 0.03425 to 0.02335 (31.8% lower), with ordinary fixed-count membership permutation p ≈ 0.36. Size-stratified common-support tests across quantile and contiguous-size partitions all remain non-significant, approximately p = 0.32–0.40.

## Outcome-blind nonparametric matching

Matching used size only, never performance outcomes, on strict common support. It was 1:1 without replacement; for every prespecified caliper it first maximized the number of pairs and then minimized total absolute size distance. All calipers were reported rather than selected after seeing outcomes.

All-18 results:

| caliper (SD) | pairs | matched size SMD | T | paired sign-flip p |
|---|---:|---:|---:|---:|
| 0.05 | 17 | -0.002 | 0.03635 | 0.2428 |
| 0.10 | 21 | -0.008 | 0.02697 | 0.5808 |
| 0.20 | 23 | -0.024 | 0.02447 | 0.5878 |
| 0.30 | 24 | 0.009 | 0.02501 | 0.5178 |
| 0.50 | 24 | 0.009 | 0.02501 | 0.5158 |
| no finite caliper | 26 | 0.119 | 0.02335 | 0.5061 |

The central 0.10–0.50 SD schemes are highly size-balanced and yield stable T ≈ 0.023–0.027 with no paired-test evidence against the conditional null. The 0.05-SD scheme has near-perfect balance but only 17 pairs and correspondingly wider uncertainty.

## Important residual: classical tree models are not fully explained by size

A blanket statement that “size explains the whole interaction” is not supported. The CatBoost/LightGBM/RandomForest/XGBoost subset behaves differently.

- Full-population classical-4 T = 0.01281.
- Size slope is itself strong (T/SD = 0.01187, permutation p < 0.00004), and linear size adjustment reduces the membership T to 0.00865 with conditional p ≈ 0.288.
- Yet within strict common support the classical-4 T is 0.01526 and the ordinary fixed-count permutation p = 0.0307.
- Outcome-blind 0.20-SD matching keeps essentially the same magnitude (T = 0.01499) but, with 23 pairs, paired sign-flip p = 0.2424; across all matching calipers p ranges roughly 0.15–0.54.

To distinguish attenuation from matching power loss, a targeted 50,000-permutation common-support stratified test retained all 71 overlap tasks. Classical-4 p-values were:

- quantile strata q4/q5/q6/q8/q10: 0.0271 / 0.0430 / 0.0392 / 0.0517 / 0.0932;
- contiguous sorted-size blocks 4/6/8/10/12: 0.2331 / 0.0368 / 0.0276 / 0.0435 / 0.0411.

Thus the smaller classical-tree interaction survives many, but not all, reasonable size-conditioning granularities. It should be reported as a residual algorithm-family-specific pattern that task size alone does not robustly eliminate, while acknowledging sensitivity to the conditioning scheme.

## Scientific closeout

The strongest defensible conclusion is two-layered:

1. **Global support/composition mechanism.** The prominent 18-algorithm CC18-vs-outside interaction is substantially linked to a severe task-size support difference. More than half of the outside tasks are in a low-size region absent from CC18; algorithm-relative performance rotates with size; the size-predicted shift is strongly aligned with the observed shift; and the all-18 statistical signal does not survive common-support, size-stratified, or outcome-blind size-matched analyses.
2. **Residual family-specific interaction.** Task size is not an exhaustive explanation. A smaller classical-tree profile difference remains visible inside common support and survives several conditional-stratification schemes, though not every scheme and not the lower-powered paired matching tests.

Accordingly, do not claim that CC18 “causes” performance reversals, that size is a causal mediator, or that all benchmark-selection effects disappear after size control. The evidence supports a narrower and stronger statement: **historical benchmark membership changes the represented task-support distribution in a way that is associated with materially different algorithm-relative performance profiles; task size explains a substantial and structurally coherent part of the global profile shift, while a smaller residual tree-model interaction remains after size conditioning.**

## Frozen result locations

- `results/tabzilla-size-bridge-260930/analysis/`
- `results/tabzilla-size-match-260930/analysis/`
- `results/tabzilla-classical4-strata-260930/analysis/`

Each corresponding result tree contains provenance and SHA-256 manifests. Earlier pairwise, raw-alignment, omnibus, and six-feature mechanism results remain unchanged.
