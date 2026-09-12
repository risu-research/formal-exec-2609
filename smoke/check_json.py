import json,sys
p=sys.argv[1]
d=json.load(open(p))
counts={"$assume":0,"$assert":0,"$cover":0}
for mod in d.get("modules",{}).values():
    for cell in mod.get("cells",{}).values():
        t=cell.get("type")
        if t in counts: counts[t]+=1
print(counts)
expected={"$assume":1,"$assert":1,"$cover":1}
if counts != expected:
    raise SystemExit(f"unexpected formal-cell inventory: {counts} != {expected}")
