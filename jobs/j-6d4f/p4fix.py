#!/usr/bin/env python3
import pathlib
p=pathlib.Path(__file__).with_name('p4.py')
s=p.read_text()
s=s.replace("'pair_counts':dict(Counter((g['parent'],g['child']) for g in allg))", "'pair_counts':dict(Counter(g['parent']+'>'+g['child'] for g in allg))")
exec(compile(s,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})
