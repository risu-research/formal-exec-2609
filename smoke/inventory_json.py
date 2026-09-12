import json, sys, hashlib

FORMAL = {"$assume", "$assert", "$cover", "$live", "$fair"}

p = sys.argv[1]
with open(p) as f:
    data = json.load(f)
counts = {k: 0 for k in sorted(FORMAL)}
modules = {}
for mname, mod in data.get("modules", {}).items():
    mc = {k: 0 for k in sorted(FORMAL)}
    for cell in mod.get("cells", {}).values():
        typ = cell.get("type")
        if typ in counts:
            counts[typ] += 1
            mc[typ] += 1
    if any(mc.values()):
        modules[mname] = mc
raw = open(p, "rb").read()
out = {
    "file": p,
    "sha256": hashlib.sha256(raw).hexdigest(),
    "formal_cells": counts,
    "modules": modules,
}
print(json.dumps(out, sort_keys=True, indent=2))
