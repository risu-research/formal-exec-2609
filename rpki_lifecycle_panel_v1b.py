#!/usr/bin/env python3
from datetime import datetime,timezone
from pathlib import Path
import shutil
import rpki_lifecycle_panel_v1 as v1

def fixed_ts(x):
    if x is None:return None
    if isinstance(x,(int,float)):return float(x)
    try:
        d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except:return None

v1.ts=fixed_ts

if __name__=='__main__':
    v1.main()
    import rpki_lifecycle_refine_v2 as v2
    v2.main()
    import rpki_delta_policy_v2 as dp
    dp.main()
    # Old successful workflow uploads only the v1 directory. Mirror final v2 outputs there
    # so a rerun of that known-good workflow recovers the complete evidence bundle.
    dst=Path('results/rpki_lifecycle_panel_v1_20261002')
    for src,name in [
        (Path('results/rpki_lifecycle_refine_v2_20261002/exact_lifecycle_v2.csv'),'v2_exact_lifecycle.csv'),
        (Path('results/rpki_lifecycle_refine_v2_20261002/summary.json'),'v2_summary.json'),
        (Path('results/rpki_lifecycle_refine_v2_20261002/errors.json'),'v2_errors.json'),
        (Path('results/rpki_delta_policy_v2_20261002/policy_frontier_v2.json'),'v2_policy_frontier.json')]:
        if src.exists():shutil.copy2(src,dst/name)
