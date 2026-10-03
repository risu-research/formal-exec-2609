# SIGMETRICS readiness campaign health audit — 2026-10-03

Scope: infrastructure/data-integrity audit only. No endpoint, P0–P5 policy, deadline, metric, weighting view, or confirmatory threshold was changed.

## Design identity
- Governing design: `experiments/sigmetrics-readiness/DESIGN.md` v1.1, amendment commit `0039b4892405f26a7247f930a1e8b80cbc7cbd7a`.
- Runs 1–2 predate the v1.1 amendment and are not primary confirmatory data.
- Run 3 (`37011297814`, 2026-10-02T13:10:57Z) is the first wave triggered by and collected after the v1.1 amendment commit; run 3 and later eligible waves are prospective confirmatory observations.
- The first inspected instrument-validation wave remains excluded from primary confirmatory inference.

## Population integrity
Downloaded and checksum-verified representative/current artifacts show:
- full waves: exactly 90 unique operators and exactly the frozen 24 sentinel flags;
- sentinel waves: exactly the same 24 unique sentinel operators;
- no observed population substitution.

## Artifact integrity and fields
For post-amendment runs 3–7, every completed measurement run produced one non-expired 90-day artifact. Downloaded artifacts contain `wave.jsonl`, `wave_meta.json`, and `SHA256SUMS.txt`; checked SHA-256 manifests validate.
Required fields are present, including `tools_ttl_ms`, `tools_cache_scope`, `tool_schema_hash`, `tool_names_hash`, and per-wave egress-vantage fields (`vantage_colo`, `vantage_country`, hashed IP).

Observed failure classes in checked full waves are operationally coherent HTTP classes (403 and 503); failures remain outcomes and no endpoints were replaced.

## Scheduling deviation
The workflow is configured for hourly cron at minute 17, but GitHub Actions did not deliver hourly schedule events reliably during the first day. Post-amendment completed measurement waves observed:
- run 3: 2026-10-02T13:10:57Z — full, push/amendment
- run 4: 2026-10-02T17:52:45Z — sentinel, scheduled
- run 5: 2026-10-02T22:14:44Z — sentinel, scheduled
- run 6: 2026-10-03T01:13:26Z — sentinel, scheduled
- run 7: 2026-10-03T06:35:12Z — full, scheduled

Thus the intended hourly sentinel cadence was not achieved; there are substantial missing hourly waves. The 6-hour full-panel intent was also only approximately realized because scheduled events were delayed/dropped. This is an infrastructure/scheduler deviation, not an outcome-driven change.

No endpoint or statistical design was retuned in response. Missing waves must be treated as missing observations and reported; they must not be imputed as endpoint failures.

## Label note
The probe payload still writes `campaign_id: SIGMETRICS-READINESS-LONGITUDINAL-v1` even after DESIGN.md v1.1. Eligibility for primary confirmation is therefore determined by amendment commit/time and run identity, not by that legacy payload label. This is a metadata-label inconsistency only; raw measurements and frozen population are unaffected.

## Audit disposition
Data integrity is good; schedule completeness is not. Continue collecting without changing scientific parameters. Any later infrastructure-only scheduling repair must be separately logged and must not retrospectively relabel missing waves or alter primary estimands.
