# Independent TabRepo replication — preregistered primary analysis

- Frozen raw metric: **roc_auc**
- Raw matched frame: **117 tasks (14 CC18 / 103 outside), 1090 frameworks**
- Full classification: **211 tasks (31 / 180), 437 frameworks**

## Q1 — Structural support

Full classification log1p(n) SMD: **-0.2797**; KS p **0.436163**; common support 194/211.
Raw-metric frame log1p(n) SMD: **-0.4120**; KS p **0.196561**; common support 110/117.

## Q2/Q3 — Interaction tests

| frame               | functional   |   unadjusted_p_l2 |   unadjusted_p_max_abs |   common_support_p_l2 |   effect_norm_ratio_common_to_unadjusted | stratified_p_l2                                                                |   freedman_lane_size_p_l2 |   freedman_lane_structural_p_l2 |
|:--------------------|:-------------|------------------:|-----------------------:|----------------------:|-----------------------------------------:|:-------------------------------------------------------------------------------|--------------------------:|--------------------------------:|
| matched raw frame   | raw          |          0.741013 |               0.428679 |              0.690015 |                                 1.00578  | {'3': 0.7300634968251587, '4': 0.7205139743012849, '5': 0.72026398680066}      |                  0.639318 |                        0.686466 |
| matched raw frame   | normalized   |          0.233788 |               0.318034 |              0.20404  |                                 1.02097  | {'3': 0.23228838558072096, '4': 0.20543972801359933, '5': 0.21883905804709763} |                  0.264787 |                        0.164442 |
| matched raw frame   | rank         |          0.20444  |               0.346283 |              0.20899  |                                 0.997486 | {'3': 0.24303784810759463, '4': 0.21573921303934804, '5': 0.22703864806759663} |                  0.296685 |                        0.239738 |
| full classification | normalized   |          0.211189 |               0.531323 |              0.20669  |                                 1.00265  | {'3': 0.2024398780060997, '4': 0.18774061296935154, '5': 0.19189040547972602}  |                  0.211789 |                        0.416229 |
| full classification | rank         |          0.254987 |               0.274986 |              0.183041 |                                 1.07688  | {'3': 0.17969101544922753, '4': 0.1688915554222289, '5': 0.18034098295085246}  |                  0.163892 |                        0.19474  |

## Guardrail

These are finite-frame observational membership contrasts. CC18 membership is not randomized and the results are not causal effects of suite curation.
