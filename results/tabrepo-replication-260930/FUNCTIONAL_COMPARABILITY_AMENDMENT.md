# Outcome-Blind Amendment: Matched-Frame Functional Comparability

This amendment is frozen after the matrix/support definition and before reading any `metric_error` or `metric_error_val` values.

The preregistration defines raw, within-task normalized, and rank functionals. Because the primary raw analysis is mechanically restricted to the largest homogeneous classification metric stratum while the primary normalized/rank analyses can use the full classification frame, a direct difference between their p-values could otherwise mix two effects: the reporting functional and a changed task/config population.

Therefore, in addition to the preregistered primary analyses, we will report a **secondary matched-frame functional comparison**:

- use exactly the frozen `raw_task_ids`;
- use exactly the frozen `raw_frameworks`;
- derive from the same fold-averaged higher-is-better score matrix:
  1. raw score;
  2. within-task affine normalized score in [0,1];
  3. within-task average rank score, larger = better;
- apply the identical task-centering, CC18-vs-outside interaction vector, omnibus L2/max statistics, membership permutation, common-support restriction, and size-stratified controls to all three.

This analysis is descriptive/sensitivity analysis and does not replace the preregistered primary full-classification normalized/rank analyses. No result-dependent task, framework, threshold, or transformation selection is permitted.
