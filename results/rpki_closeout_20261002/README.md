# RPKI lifecycle closeout — 2026-10-02

This directory is the compact authority for the completed population-expansion and exact-date lifecycle stage.

## Authoritative result hierarchy

1. `summary.json` in this directory: closeout counts, corrected exact-date distributions, and guardrails.
2. GitHub Actions run `37059101942`, artifact `rpki-exact-date-delta-v2-20261002` (artifact id `11249727883`): corrected 185-case exact multi-RRC lifecycle table and delta frontier.
3. `results/rpki_population_v1_20261002/`: frozen 52-week population enumeration.
4. `results/rpki_lifecycle_panel_v1_20261002/`: 500-case month-stratified screening/context panel. Its population/context counts remain useful, but its committed exact-resolution zero counts are superseded by the corrected v2 artifact because the earlier runner had a numeric RIPEstat timestamp parsing bug.

## Frozen population

Window: 2025-10-06 through 2026-09-28, 52 weekly snapshots.

- 35,938 raw single-origin changes
- 28,678 pre-stable changes
- 20,648 persistent prefix events
- 7,198 `(transition week, A->B)` clusters
- 7,046 clusters after excluding old-A recurrence within 8 weeks

The population is observed persistent origin replacement, not transfer ground truth.

## Screening and exact refinement

A deterministic month-stratified panel of 500 clusters produced 419 clean-context proxies after excluding 69 same-organization and 12 near-transfer cases. A prioritized exact subset produced 185 clean cases observed at three or more RRCs; corrected refinement completed with zero errors.

## Interpretation guardrails

- Lingering old-origin authorization is not automatically stale authorization.
- Date-granular RPKI history cannot order same-day BGP and ROA events.
- The prioritized 185-case exact subset supports mechanism/distribution claims, not unbiased ecosystem prevalence.
- Long old-origin authorization survival must not be reinterpreted as RPKI propagation delay.
- The data do not justify one symmetric fixed delta. Activation should verify new-origin authorization before routing; retirement should require routing quiescence plus operator-intent/failback logic and an independently measured RPKI propagation budget.
