# Independent Replication Preregistration — TabRepo AutoML2024

Frozen before inspecting CC18-vs-outside outcome contrasts.

## Public source snapshot
- Project: AutoGluon TabRepo (now in autogluon/tabarena)
- Code branch: AutoML2024
- Code commit: `3785b6f7a7853d992627cf97908081d54d908bd3`
- Context: `D244_F3_C1530` / `2023_11_14`
- Results source: `https://tabrepo.s3.us-west-2.amazonaws.com/contexts/2023_11_14/configs.parquet`
- Task metadata: `data/metadata/task_metadata_244.csv` at the frozen commit, blob `0a68d470a0bf5b977b8d6f9aeeb1c499d483a2f6`
- Membership key: OpenML task id (`tid`)

## Population and inclusion
1. Start from the public dense TabRepo config result table and frozen task metadata.
2. Restrict to tasks represented in the result table with a resolvable OpenML task id.
3. Primary replication uses classification tasks only, because CC18 is a classification suite.
4. Use all algorithm configurations with complete task coverage on the retained primary task frame, subject only to deterministic pre-outcome schema/type filters needed to remove ensembles/baselines or non-comparable rows. No performance-based config selection.
5. If the full config matrix is too redundant for a stable global statistic, a secondary family-level analysis uses one deterministic default configuration per model family identified by naming/config metadata, never selected by performance.

## Structural support
Primary: `log1p(NumberOfInstances)`.
Secondary when available: `log1p(NumberOfFeatures)`, `log1p(NumberOfClasses)`, numeric/categorical feature fractions.

## Outcomes / functionals
All outcomes are oriented so larger is better before transformation.
- Raw: official test score/error converted to a consistent higher-is-better orientation. Primary pooled raw analysis requires a homogeneous metric/problem subset if metric scales differ across tasks.
- Normalized: within-task affine normalization of selected config scores to [0,1], 1=best and 0=worst; constant tasks map to 0.5.
- Rank: within-task average rank derived from the same selected score matrix, oriented higher=better.

## Interaction estimand
For each functional:
1. Center every task across selected configs.
2. Let d = mean(outside-CC18) minus mean(CC18) centered config scores.
3. Primary omnibus statistic: squared L2 norm of d.
4. Secondary statistic: max absolute element of d.
5. Fixed-size membership-label permutation across tasks, preserving observed group counts: 50,000 draws where feasible, at least 20,000 otherwise.

## Support shift
Before outcome analysis report group counts/ranges, SMD for log1p(n), KS/ECDF distance, common support, and deterministic quantile-support audit.

## Support control
Outcome-blind primary controls:
A. common-support restriction on log1p(n), based only on overlap of observed CC18/outside ranges;
B. size-stratified membership permutation within common support using tertiles/quartiles/quintiles;
C. Freedman-Lane residual permutation with fixed structural covariates when available.
No trimming threshold may be selected from an outcome p-value.

## Replication questions
Q1. Is CC18 membership associated with a measurable structural-support shift?
Q2. Does raw interaction attenuate after outcome-blind support control?
Q3. Do raw, normalized, and rank functionals yield materially different interaction conclusions?

Failure to reproduce any TabZilla pattern is a valid replication outcome. No config/task/threshold will be changed to force significance.
