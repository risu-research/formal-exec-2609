#!/usr/bin/env python3
from datetime import datetime,timezone
import rpki_lifecycle_panel_v1 as v1

def fixed_ts(x):
    if x is None:return None
    if isinstance(x,(int,float)):return float(x)
    try:
        d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except:return None

v1.ts=fixed_ts
if __name__=='__main__':v1.main()
