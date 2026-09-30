# Post-hoc robustness: equal weighting by model family

**Status: post-hoc.** This check was specified only after the preregistered TabRepo replication outcomes were inspected. It is therefore not confirmatory and must not be presented as preregistered evidence.

Rationale: the frozen TabRepo matrix contains many hyperparameter configurations per model family. The preregistered framework-level omnibus statistic therefore weights families in proportion to the number of frozen configurations. A family-equalized check tests whether the null replication is an artifact of that multiplicity.

Deterministic family rule, applied to frozen framework names without using performance values: strip the terminal `_r<integer>_BAG_L1`, `_c<integer>_BAG_L1`, or `_BAG_L1` suffix; the remaining string is the family label. For each task and family, average the higher-is-better scores of all frozen complete-coverage configurations in that family. Then derive raw, within-task normalized, and within-task average-rank functionals across family means, task-center them, and repeat the same CC18-vs-outside L2 membership-permutation test. Report both the frozen raw ROC-AUC frame and the full-classification normalized/rank frame, plus the same outcome-blind common-size-support restriction.

No family is added, removed, merged, or split based on the replication outcomes.
