# Fast RPKI handoff kill/keep pilot

- Raw single-origin changes: 692
- Persistent A→B replacements: 576
- Deterministic sample: 100

## Coarse results

- B_preauthorized_d0: **17/100 (17.0%)**
- B_invalid_d1: **3/100 (3.0%)**
- B_notvalid_d1: **22/100 (22.0%)**
- A_lingering_valid_d1: **43/100 (43.0%)**
- A_lingering_valid_d2: **41/100 (41.0%)**
- dual_valid_d1: **40/100 (40.0%)**

## Guardrail

This is a daily-snapshot feasibility pilot, not an exact timing study. `A_lingering_valid` is not automatically stale authorization because failover, same-organization migration, or intentional multi-origin policy may justify retaining A. The full study must add stronger persistence/intent filters and update-level timing.