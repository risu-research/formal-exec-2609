# TabZilla omnibus + historical metafeature probe

Cleaned matrix: 104 tasks (46 CC18 / 58 outside), 18 algorithms.

## Omnibus interaction

- **all18**: RMS pair-shift=0.03424587; max pair-shift=0.08949290; permutation p=0.0067496625 (B=20000).
- **minus_TabNet**: RMS pair-shift=0.02884553; max pair-shift=0.08949290; permutation p=0.03519824 (B=20000).
- **minus_VIME**: RMS pair-shift=0.02836270; max pair-shift=0.08765730; permutation p=0.026898655 (B=20000).
- **minus_TabNet_VIME**: RMS pair-shift=0.01895898; max pair-shift=0.05007426; permutation p=0.27878606 (B=20000).
- **classical4**: RMS pair-shift=0.01280983; max pair-shift=0.02128476; permutation p=0.02679866 (B=20000).

## Historical metafeature source

- shape: 1830 rows × 1605 columns
- id-like columns: 1
- structural-name candidates: 102

The probe does not interpret or select a mechanism model yet; it freezes the schema and task-ID join evidence first.
