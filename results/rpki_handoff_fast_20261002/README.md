# RPKI handoff kill/keep pilot — origin-pair deduplicated

- Raw single-origin changed prefixes: 692
- Persistent changed prefixes: 576
- Distinct persistent A→B origin pairs: 221
- Deterministic distinct-pair sample: 100

## Coarse results

- B_preauthorized_d0: **22/100 (22.0%)**
- B_invalid_d1: **4/100 (4.0%)**
- B_notfound_d1: **25/100 (25.0%)**
- B_valid_d1: **71/100 (71.0%)**
- A_lingering_valid_d1: **34/100 (34.0%)**
- A_lingering_valid_d2: **32/100 (32.0%)**
- dual_valid_d1: **30/100 (30.0%)**
- new-origin Invalid conditional on RPKI coverage at d1: **4/75 (5.3%)**

## Guardrail

This is a daily-snapshot feasibility pilot, not an exact timing or intent study. `A_lingering_valid` is not automatically stale authorization: failover, same-organization migration, or intentional multi-origin policy can justify retaining A. A full study must add multi-collector persistence, organization/transfer/failover stratification, and update-level timing.