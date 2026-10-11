#!/usr/bin/env python3
"""Stage34 replication of prespecified SSB 13-query gate on 26.6 vs 26.7.
Same runner physical 734-part stats / cache factorial from Stage32.
No changes to measurement or thresholds; only release pair and output paths.
"""
import pathlib
import db_13query_cache_upgrade_gate as P
P.S.OLD="26.6.8.7"
P.S.NEW="26.7.10.6"
P.ROOT=pathlib.Path("s34")
P.S.ROOT=P.ROOT
P.S.VOL="s34_26_6_to_26_7"
P.S.CT="s34_clickhouse"
P.S.base.ROOT=P.ROOT
P.S.base.VERSIONS=[P.S.OLD,P.S.OLD]
P.B.ROOT=P.ROOT
P.L.ROOT=P.ROOT
P.main()
