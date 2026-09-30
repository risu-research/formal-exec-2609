# TabZilla benchmark-membership interaction and selection audit

## Global algorithm-by-membership interaction

| subset              |   n_tasks |   n_cc18 |   n_outside |   n_algorithms |    stat_l2 |       p_l2 |   stat_max_abs |   p_max_abs |   permutations |
|:--------------------|----------:|---------:|------------:|---------------:|-----------:|-----------:|---------------:|------------:|---------------:|
| all                 |       104 |       46 |          58 |             18 | 0.00996862 | 0.00741985 |      0.057765  |  0.00965981 |          50000 |
| exclude_TabNet      |       104 |       46 |          58 |             17 | 0.00665652 | 0.0361193  |      0.061055  |  0.00451991 |          50000 |
| exclude_VIME        |       104 |       46 |          58 |             17 | 0.00643554 | 0.0258395  |      0.0593274 |  0.00263995 |          50000 |
| exclude_TabNet_VIME |       104 |       46 |          58 |             16 | 0.00269582 | 0.277454   |      0.0254523 |  0.442211   |          50000 |

The omnibus statistic is computed after centering each task across algorithms, so task-level overall difficulty is removed before comparing the CC18 and outside-CC18 algorithm-effect vectors. Membership labels are permuted across tasks while holding the 46/58 group sizes fixed.

## Historical PyMFE selection audit

- Joined cleaned tasks: **104/104** (46 CC18, 58 outside)
- Numeric PyMFE columns: **1604**
- Pre-prioritized/interpretable core features found: **28**

### Core feature shifts

| feature                                |   n_cc18 |   n_outside |    mean_cc18 |   mean_outside |   smd_cc18_minus_outside |   wasserstein_std |   ks_ecdf_distance |   robust_5_95_overlap |   ks_q_bh |
|:---------------------------------------|---------:|------------:|-------------:|---------------:|-------------------------:|------------------:|-------------------:|----------------------:|----------:|
| f__pymfe.info-theory.mut_inf.mean      |       17 |          36 |    0.0598372 |      0.237055  |                -0.673422 |          0.673588 |           0.517974 |              0.198461 | 0.060109  |
| f__pymfe.info-theory.eq_num_attr       |       17 |          36 |   55.4018    |     24.3102    |                 0.570957 |          0.582347 |           0.496732 |              0.354343 | 0.0790938 |
| f__pymfe.info-theory.mut_inf.sd        |       16 |          35 |    0.0534477 |      0.231715  |                -0.500738 |          0.502369 |           0.401786 |              0.129935 | 0.237363  |
| f__pymfe.general.attr_to_inst          |       46 |          58 |    0.0312969 |      0.118684  |                -0.401541 |          0.401634 |           0.313343 |              0.270718 | 0.107397  |
| f__pymfe.landmarking.one_nn.mean       |       39 |          47 |    0.712249  |      0.6506    |                 0.326096 |          0.327925 |           0.205674 |              0.771211 | 0.625561  |
| f__pymfe.general.cat_to_num            |       39 |          47 |    0.587851  |      1.84597   |                -0.310999 |          0.310999 |           0.275505 |              0.671975 | 0.269756  |
| f__pymfe.general.num_to_cat            |       17 |          36 |    1.40242   |      7.54844   |                -0.299033 |          0.312365 |           0.116013 |              0.220323 | 1         |
| f__pymfe.landmarking.linear_discr.mean |       39 |          47 |    0.711302  |      0.66079   |                 0.293351 |          0.350658 |           0.161484 |              0.681079 | 0.889792  |
| f__pymfe.info-theory.attr_ent.sd       |       16 |          35 |    0.628721  |      0.797585  |                -0.283435 |          0.290368 |           0.217857 |              0.721512 | 0.901984  |
| f__pymfe.landmarking.naive_bayes.mean  |       39 |          47 |    0.703165  |      0.659361  |                 0.263937 |          0.303291 |           0.199127 |              0.69684  | 0.660344  |
| f__pymfe.info-theory.attr_ent.mean     |       17 |          36 |    1.49736   |      1.90804   |                -0.263358 |          0.31627  |           0.163399 |              0.5356   | 1         |
| f__pymfe.general.freq_class.min        |       46 |          58 |    0.201948  |      0.234193  |                -0.210867 |          0.38382  |           0.206897 |              0.822411 | 0.51961   |
| f__pymfe.landmarking.best_node.mean    |       39 |          47 |    0.494066  |      0.540639  |                -0.20956  |          0.24303  |           0.177305 |              0.959485 | 0.816014  |
| f__pymfe.statistical.sd.mean           |       39 |          47 | 5204.34      |      2.122e+16 |                -0.206284 |          0.206284 |           0.26623  |              0.871383 | 0.326504  |
| f__pymfe.statistical.kurtosis.mean     |       39 |          42 |   58.1498    |    160.518     |                -0.186111 |          0.245448 |           0.228938 |              0.558308 | 0.526186  |
| f__pymfe.landmarking.random_node.mean  |       39 |          47 |    0.445213  |      0.482567  |                -0.174779 |          0.235532 |           0.262957 |              0.819585 | 0.329341  |
| f__pymfe.landmarking.worst_node.mean   |       39 |          47 |    0.422422  |      0.456213  |                -0.173996 |          0.218431 |           0.262957 |              0.771238 | 0.329341  |
| f__pymfe.general.freq_class.sd         |       46 |          58 |    0.221469  |      0.197792  |                 0.12624  |          0.199491 |           0.143928 |              0.772452 | 0.901984  |
| f__pymfe.info-theory.class_ent         |       46 |          58 |    1.48402   |      1.36971   |                 0.122199 |          0.240667 |           0.143928 |              0.902567 | 0.901984  |
| f__pymfe.general.nr_cat                |       46 |          58 |    9         |     12         |                -0.115032 |          0.210115 |           0.255622 |              0.554662 | 0.266133  |

## Guardrail

These are finite historical association diagnostics. CC18 membership was not randomized, and a membership contrast must not be described as a causal effect of any one curation rule.
