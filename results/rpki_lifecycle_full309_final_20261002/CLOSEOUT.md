# Full-309 RPKI lifecycle closeout — final authority

## Decision

The 185→309 lifecycle extension is scientifically useful and should replace the prior lifecycle-enriched 185 subset as the primary lifecycle evidence. The extension completed all 309 frozen outcome-blind exact BGP transitions (>=3 RIPE RIS RRCs) with zero lifecycle-history errors.

The main gain is not a larger sample alone. The added 124 cases show that the prior 185 subset was strongly enriched for long old-origin authorization persistence. New-origin activation behavior is stable across the old and added samples, whereas old-origin retirement tails shorten materially in the complete outcome-blind panel. This makes the full-309 result more defensible and corrects an important selection asymmetry.

## Canonical lifecycle definition

For each prefix/origin/date history, matching authorization is the Boolean union of all covering VRPs authorizing that origin ASN. Overlapping or day-adjacent inclusive date intervals are merged. Lifecycle timing uses only the continuous merged interval covering the exact BGP transition date.

For the old origin, retirement is the end of that event-covering continuous authorization interval. A later reauthorization after a gap is not counted as continued lingering. This is stricter than the earlier `max(last observed authorization end)` formulation and avoids artificially lengthening persistence.

The RPKI history is date-granular. Same-day ROA/BGP ordering is therefore unresolved and must not be inferred.

## Full-309 activation result

Historical authorization reconstruction finds a matching new-origin authorization covering the exact BGP transition date in **133/309 (43.0%)** cases.

Among those 133 cases, **101 (75.9%)** began at least one calendar day earlier and **32 (24.1%)** begin on the same calendar date. The median preauthorization lead is **9 days** (p25 1; p75 132). The same-day group must remain unordered.

For the remaining events, a first matching authorization is reconstructed only after the transition in **74/309 (23.9%)**, while **102/309 (33.0%)** have no matching authorization reconstructed on or after the event in the available history. This last statement is explicitly a history-reconstruction result, not proof that no authorization existed in every historical validation source.

## Full-309 old-origin retirement result

Historical reconstruction finds a matching old-origin authorization interval covering the exact BGP transition date in **153/309 (49.5%)** cases. Of those 153, **98** remain right-censored at the 2026-10-02 study cutoff.

The event-covering continuous authorization interval has a median observed post-transition length of **104 days** (p25 35; p75 181; p90 289).

Among cases observable through each horizon, the old origin remains continuously authorized in:

- **115/153 (75.2%)** at 30 days;
- **87/126 (69.0%)** at 90 days;
- **39/76 (51.3%)** at 180 days.

These are authorization-persistence measurements only. They are not routing persistence, operator intent, stale-configuration labels, or traffic-impact measurements.

## Why the old 185 retirement numbers must be retired

The new-origin activation fraction is strikingly stable: prior185 80/185 = 43.24%; added124 53/124 = 42.74%. This supports the activation result rather than revealing a material enrichment effect.

Old-origin persistence is different. Under the stricter continuous-interval definition, the prior185 subset survives 30/90/180 days at 94/98 (95.9%), 70/77 (90.9%), and 32/45 (71.1%). The added124 survives at 21/55 (38.2%), 17/49 (34.7%), and 7/31 (22.6%). The prior lifecycle-enriched subset therefore strongly overrepresents long retirement tails.

The full309 values—75.2%, 69.0%, and 51.3%—are the primary quantities for the manuscript. The old 185 retirement values must not be presented as population-like lifecycle evidence.

## Endpoint consistency audit

The frozen exact ROV validator and the historical search endpoint are related but are not identical historical authorities. A full 309 cross-tab identifies **21/309 (6.8%)** cases with at least one event-day disagreement of the form `frozen validator = Valid` but `history search = no matching authorization`: nine on the old-origin side and fifteen on the new-origin side, with overlap between sides.

Every one of the 21 cases was independently checked against the RIPE NCC daily validated ROA archive using all five trust anchors, with zero archive errors. In the discordant sides, the archive agrees with the history Boolean in 6/9 old-origin disagreements and 12/15 new-origin disagreements, versus frozen-validator agreement in 3/9 and 3/15, respectively.

Therefore the paper must not silently impute history intervals from the frozen validator or vice versa. The frozen validator remains the authority for the exact-event ROV-state matrix; historical search remains the source for lifecycle interval reconstruction; measured discordance is disclosed. This separation is more defensible than forcing a synthetic single source.

## Invalid-run stability

For the **61** exact cases with frozen new-origin Invalid state and a later reconstructed matching authorization, rerunning the historical validator at day 0 reproduces the frozen Invalid state in **61/61 (100%)**. This makes the later-authorization Invalid-run analysis particularly stable against endpoint drift.

## Manuscript replacement rule

Replace the prior lifecycle-enriched 185 main result with the full309 outcome-blind exact-panel lifecycle result. Recommended compact wording:

> Historical authorization reconstruction found a matching new-origin authorization on the exact transition date in 133/309 cases; among those, 101 had begun at least one calendar day earlier. For the old origin, 153/309 had a matching authorization interval covering the transition date; among horizon-observable cases, 115/153 remained continuously authorized at 30 days, 87/126 at 90 days, and 39/76 at 180 days.

The paper should also state, compactly, that 21/309 cases showed an event-day discrepancy between the frozen validator and historical-search reconstruction and that the two sources were therefore kept separate rather than imputed into one another.

## What not to do next

Do not expand the lifecycle experiment merely to accumulate more cases. The full outcome-blind exact panel is now covered. A meaningful further jump would require qualitatively new evidence—operator ground truth, sub-day historical RPKI publication timing, controlled BGP experiments, or contemporaneous data-plane measurements. Additional archival counting within the same design is unlikely to improve the ICC paper and risks diluting the clean three-layer evidence architecture.

## Superseded diagnostics

The following intermediate checks are non-authoritative and must not be cited:

1. the first v1 regression against `rpki_exact185_policy_input_20261002.csv`, because that input contains only 61 policy-stage rows;
2. the v2 direct lifecycle-field mismatch count against the corrected exact-timing artifact, because that artifact does not store the refined lifecycle columns;
3. the `legacy_vs_contiguous_field_differences` object in the v3 summary, because the exact input lacks the legacy lifecycle fields used by that diagnostic.

The canonical scientific authorities are `rpki_lifecycle_full309_contiguous_v3_20261002`, `rpki_lifecycle_endpoint_consistency_v4b_20261002`, and this final closeout.
