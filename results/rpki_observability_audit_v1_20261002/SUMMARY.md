# Resolved-vs-unresolved observability audit (2026-10-02)

## Question
Does the >=3-RRC requirement that yields 309 collector-resolved cases from the frozen 419-case diagnostic panel induce observable selection relative to the 110 cases with <3 RRCs?

## Design
- Join the frozen 419-case selection, all 419 BGPlay reconstruction attempts, and the frozen 7,046-case weekly ROV census by `(event_date, representative_prefix, A, B)`.
- Resolved: `exact_rrc_n >= 3` (n=309). Unresolved: `<3` (n=110).
- Baseline diagnostics: event month/timing, cluster prefix count, representative-prefix length, pre-event stable observation count, post-event B-confirmation count, and full-frame frequencies of old A, new B, and A->B pair.
- Post-selection diagnostic only: coarse weekly ROV state and weekly old-Valid/new-Invalid indicator.
- Because the 419 cases are deterministic rather than a probability sample, report descriptive effect sizes rather than p-values.

## Results
Largest baseline differences (resolved minus unresolved; continuous SMDs use the transform frozen in `rpki_observability_audit_v1.py`):
- old-A frequency in the 7,046-event frame: signed SMD = -0.5863
- pre-event stable observations: signed SMD = +0.4099
- cluster prefix count: signed SMD = +0.3705
- representative-prefix length: signed SMD = -0.3472
- A->B pair frequency in the 7,046-event frame: signed SMD = +0.2995

Event-month distribution: total variation = 0.1268; Cramer's V = 0.1491.

Coarse weekly ROV-state distribution (post-selection diagnostic): total variation = 0.2864; Cramer's V = 0.3259.

Coarse weekly old-Valid/new-Invalid indicator:
- resolved: 6/309 = 1.9417%
- unresolved: 4/110 = 3.6364%
- resolved minus unresolved = -1.6946 percentage points
- rate ratio = 0.5340

## Interpretation
The >=3-RRC observability filter is not missing-completely-at-random with respect to the measured routing/frame covariates. Several baseline differences are non-negligible, and the coarse weekly ROV-state composition also differs. Therefore the 309 resolved cases should **not** be described as representative of the 419 panel, and 41/309 should not be extrapolated to the unresolved cases or to the Internet.

At the same time, the coarse weekly inversion proxy is *more frequent* in the unresolved group (4/110) than in the resolved group (6/309). This diagnostic provides no evidence that >=3-RRC resolution preferentially selects inversion-positive cases; if anything, the observed direction is opposite. It does not establish missing-at-random and does not justify an inverse correction.

The manuscript-safe interpretation is therefore:
1. retain 41/309 as a resolved-subset descriptive rate;
2. retain 41/419 and 39/419 only as conservative selected-panel lower bounds;
3. keep the 7,046-case weekly census as the observation-frame robustness layer;
4. explicitly acknowledge non-random observability rather than claiming covariate balance.

## Execution
The audit completed successfully in GitHub Actions run 37086868850. The temporary instrumentation of `rpki_outcomeblind_compare_v1.py` was subsequently restored to its original blob (`aea193c4776fb68218278547e5dd54fcadbcac34`).
