# Event-definition sensitivity closeout — 2026-10-02

## Scope
This closeout tests the event-definition choices that can be varied honestly and cheaply from the frozen, outcome-blind RPKI/BGP study artifacts. It does not retroactively tune event definitions using RPKI outcomes.

Canonical anchors remain:
- weekly frozen population: 7,046 no-recurrence clusters;
- exact resolved subset: 309 cases under the frozen >=3-RRC rule;
- canonical exact old-Valid/new-Invalid cases: 41 under exact classifier v2.

## 1. Pre-event stability threshold
Starting from the already-frozen 7,046 weekly census, impose progressively stricter pre-event stability requirements. This is a one-sided sensitivity test because cases excluded by the original >=3 rule cannot be recovered without rerunning the weekly snapshot enumerator.

| Minimum pre-event stable observations | Clusters | Weekly inversions | Rate | Cross-source reproduced inversions | Cross-source fraction |
|---:|---:|---:|---:|---:|---:|
| 3 | 7,046 | 119 | 1.6889% | 95 | 1.3483% |
| 4 | 6,510 | 109 | 1.6743% | 86 | 1.3210% |
| 5 | 6,015 | 103 | 1.7124% | 81 | 1.3466% |
| 6 | 5,627 | 96 | 1.7061% | 76 | 1.3506% |
| 8 | 5,015 | 84 | 1.6750% | 67 | 1.3360% |
| 10 | 4,473 | 74 | 1.6544% | 59 | 1.3190% |

Interpretation: the weekly signal is stable even after discarding more than one third of the canonical population with a 10-observation stability requirement. The original three-observation choice is not driving the weekly inversion rate.

## 2. Eight-week non-recurrence filter: on/off stress test
The stored event-cluster output does not retain exact recurrence timing, so a 4/8/12-week grid cannot be reconstructed honestly without rerunning the 52 weekly snapshots. A stronger cheap diagnostic is possible: remove the recurrence exclusion entirely by adding back and classifying all 152 clusters excluded because old A recurred within eight weeks.

- canonical 8-week non-recurrence population: 119/7,046 = 1.6889%;
- excluded recurrence cases: 5/152 = 3.2895%;
- recurrence filter removed entirely: 124/7,198 = 1.7227%;
- absolute change: +0.0338 percentage points;
- relative rate ratio: 1.0200.

Interpretation: recurrence-excluded cases are individually inversion-richer, but they are only 152 clusters. Removing the filter entirely changes the population weekly inversion rate by only about 2% relative. The eight-week exclusion is therefore not driving the population-level finding. This is an on/off sensitivity test, not a 4/8/12-week threshold estimate.

## 3. BGPlay withdrawal-to-B linkage window
The frozen exact rule accepts direct A->B changes and withdrawal-from-A followed by B within one hour. The frozen 419-case panel was refetched and evaluated under direct-only, 15-minute, one-hour, and six-hour linkage rules. Final interpretation is anchored to the canonical classifier-v2 set of 309 exact cases and 41 inversions; an earlier diagnostic that used a superseded 47-inversion v1 file is discarded.

| Linkage rule | Canonical 309 retained | Canonical 41 retained | Inversion median calendar-date changes vs 1h |
|---|---:|---:|---:|
| direct only | 279/309 (90.29%) | 37/41 (90.24%) | 1 |
| 15 min | 292/309 (94.50%) | 39/41 (95.12%) | 0 |
| 1 h canonical | 309/309 (100%) | 41/41 (100%) | 0 |
| 6 h | 309/309 (100%) | 41/41 (100%) | 0 |

The current one-hour refetch produced 310 resolved cases rather than the frozen 309 because one previously unresolved case (160.20.145.0/24, A30823->B198983, event week 2026-01-05) now exposes 23 RRCs through the live historical BGPlay interface. All 309 frozen canonical cases are still present. This single-case drift is a reason to keep the frozen reconstruction as study authority rather than silently replacing it with later live-API results.

Interpretation: the main exact result is not materially dependent on the one-hour linkage allowance. A much stricter 15-minute rule retains 95.1% of canonical inversions and does not change the median calendar date for any retained inversion. Direct-only is an intentionally severe stress test and still retains 90.2% of canonical inversions.

## 4. What is not worth rerunning for the ICC paper
- A complete post-persistence threshold grid cannot be reconstructed from the stored event outputs because the enumerator finalized candidates once the frozen B-confirmation requirement was met. A looser threshold would require re-enumerating snapshots; a stricter threshold would require longer post-event histories than were retained by the enumerator.
- A 4/8/12-week recurrence grid likewise requires rerunning the weekly population construction because exact old-A recurrence timing was not retained in the cluster output.
- Given the stability under stricter pre-event thresholds, complete removal of the recurrence filter, and stricter BGPlay linkage windows, rerunning all 52 snapshots solely for threshold sensitivity has low expected value for the current ICC claim.

## 5. External validity and consequence scope
The main population census remains a RouteViews rv2 weekly observation frame. A pre-existing independent routing cross-check sampled five clean-core exact cases and found the B announcement near the RIS transition in all three tested RouteViews update collectors (route-views2, route-views.eqix, route-views.linx) for all five cases. This is corroboration, not a second population census or full A->B per-peer reconstruction.

The paper should continue to avoid claiming observed traffic loss or operator intent. An old-Valid/new-Invalid state establishes a policy incompatibility for networks that reject Invalid routes; it does not establish that a particular historical user experienced an outage. Current RIPE Atlas measurements attached to historical transitions would introduce a temporal mismatch rather than repair this scope limitation.

## Closeout decision
For the ICC version, stop event-definition experimentation here unless the paper's central claim changes. The remaining full multi-source census and operator/data-plane ground truth are qualitatively larger extensions, not missing checks needed to support the current scoped claim.
