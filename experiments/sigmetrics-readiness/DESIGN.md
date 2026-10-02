# Confirmatory longitudinal design: federated capability readiness

Campaign ID: `SIGMETRICS-READINESS-LONGITUDINAL-v1`  
Frozen before the first longitudinal observation on 2026-10-02.

## Why this design

The prior 40-operator pilot and disjoint 50-operator replication were exploratory. They established a reproducible readiness-composition signal but used only three nearby rounds. This campaign is confirmatory and must not retune its primary estimands after longitudinal data begin.

The same observation stream is used twice:
1. **Measurement:** temporal latency/failure/change behavior of the frozen operator population.
2. **Counterfactual policy replay:** compare readiness policies on exactly the same realized endpoint traces.

This avoids re-probing endpoints separately for each intervention and ensures policy comparisons share the same underlying network/server realization.

## Frozen population

- 90 operator-distinct public MCP endpoints.
- Exact union of the earlier 40-operator pilot and non-overlapping 50-operator replication.
- Do not replace disappearing or failing endpoints during the campaign. Failure is an outcome.
- A 24-endpoint sentinel panel is frozen for higher-frequency temporal estimation.

## Multi-rate observation schedule

Campaign window: 14 days.

- Sentinel 24: once per hour.
- Full panel 90: once every 6 hours.
- One read-only `server/discover` and one read-only `tools/list` per sampled endpoint per wave.
- Hard timeout: 8 seconds per method.
- No retries.
- Concurrency: 4.
- Randomized endpoint order within each wave, with the wave identifier as the deterministic shuffle seed.
- Record an external egress-vantage fingerprint per wave so runner-location changes can be controlled rather than mistaken for endpoint dynamics.

This yields approximately 336 observations per sentinel endpoint and 56 observations per non-sentinel endpoint if all scheduled waves run.

## Per-observation fields

For each method:
- start/end timestamps and elapsed milliseconds;
- HTTP status;
- protocol/parse success;
- error class;
- response size;
- `ttlMs` and `cacheScope` when present;
- normalized result hash.

For `tools/list` additionally:
- tool count;
- normalized full tool-schema hash;
- normalized tool-name-set hash.

No tool is invoked.

## Primary temporal estimands

On the sentinel panel:
- endpoint-level empirical p50, p95, p99 readiness latency;
- wave-level portfolio T50/T75/T90/T99/Tall for k in {1,2,4,8,16};
- temporal failure probability;
- latency variation after endpoint fixed effects;
- cross-endpoint covariance/correlation of log-latency residuals within aligned waves;
- common-mode wave factor (median standardized residual).

On the full panel:
- endpoint failure probability and Wilson interval;
- operator-level and capability-mass-weighted coverage;
- tool-catalog change incidence;
- distribution of advertised `ttlMs` / `cacheScope`.

p99 is reported as coarse unless at least 100 successful observations exist for the unit being estimated.

## Freshness ground truth

The campaign records full normalized tool-schema hashes, not only cache age.

For a cached snapshot from time `s` evaluated against a later successful observation at `t`:
- **verified fresh:** schema hash at `s` equals schema hash at `t`;
- **verified changed:** hashes differ;
- **unverifiable:** live observation at `t` failed.

Server-declared TTL is evaluated separately:
- a cache entry is **TTL-fresh** when age <= the last advertised positive `ttlMs`;
- an observed hash change between two probes less than the previous `ttlMs` apart is a conservative TTL-calibration violation.

## Counterfactual interventions

Replay all policies on the same frozen traces.

### P0 — wait-all live
Issue every live metadata request and block until every endpoint has either returned or hit the method timeout.

### P1 — deadline-bounded live
Return live metadata available by deadline D.
Pre-specified D grid: {250, 500, 750, 1000, 1500, 2000, 4000} ms.
No deadline is selected post hoc as the single winner; report the full surface.

### P2 — protocol-native TTL cache-first + asynchronous refresh
If a prior cache entry is still fresh under the endpoint's advertised positive `ttlMs`, make it immediately available and refresh asynchronously. Otherwise it is not counted as fresh at time zero.

### P3 — stale-while-revalidate cache-first
Make the most recent cached entry immediately available regardless of TTL, label its age/staleness explicitly, and refresh asynchronously. This is an availability-oriented baseline, not a claim that stale data are fresh.

### P4 — deadline + cached fallback
Wait up to D for fresh live metadata; for endpoints not fresh by D, fill from the latest cache when available. Report fresh and stale coverage separately.

### P5 — progressive refresh
Expose capability metadata as endpoints complete. Primary outputs are T50/T75/T90, time-integrated fresh coverage over the first 2 seconds, and time to the chosen coverage target rather than a single wait-all completion time.

## Primary policy metrics

For every policy and wave/portfolio:
- blocking latency;
- live-fresh capability coverage;
- total available capability coverage (fresh + cache);
- verified freshness fraction where ground truth is observable;
- stale fraction and cache-age distribution;
- missing capability fraction;
- request count / refresh load;
- probability of failing to reach 90% fresh coverage.

Capability coverage is reported both:
1. server-weighted; and
2. tool-count-weighted.

## Pareto analysis

Do not collapse latency, coverage, and freshness into an arbitrary single score.

Report Pareto frontiers for:
- blocking latency vs fresh coverage;
- blocking latency vs available coverage;
- blocking latency vs verified freshness;
- request load vs fresh coverage.

A policy is dominated only when another policy is no worse on every displayed dimension and strictly better on at least one.

## Time split

- Days 1–7: calibration/exploration for secondary adaptive-policy ideas only.
- Days 8–14: held-out temporal confirmation.
- The fixed P0–P5 policies and deadline grid above are evaluated on both halves without retuning.

Any new adaptive policy invented after data collection starts must be clearly labeled exploratory and evaluated on the held-out second half only if its parameters were fixed from the first half.

## Robustness checks

- remove the slowest 1–4 operators by calibration-period median latency;
- analyze sentinel and full-panel waves separately;
- control/stratify by recorded runner egress vantage;
- discovery-only vs tools-list-only components;
- server-weighted vs tool-count-weighted coverage;
- complete-case latency analysis plus failures treated explicitly as failures, never silently dropped;
- exclude endpoints whose public URL changed only in a sensitivity analysis, not from the primary frozen population.

## Confirmatory success criteria

The original composition claim is supported if, on held-out days 8–14:
1. median Tall/T50 at k=16 remains > 1.5 in both server- and tool-weighted views; and
2. P(T90 < Tall) at k=16 remains > 0.70; and
3. the result survives removal of the four slowest operators.

The intervention claim is descriptive/Pareto-based rather than a winner-take-all hypothesis. We will not declare one policy universally best; we will quantify the trade-off surface.
